"""
LLM-based document extraction pipeline.
Supports OpenAI (vision) and Groq (text-based) providers with automatic fallback.
"""
import io
import re
import json
import base64
import time
import logging
from pathlib import Path
from typing import Optional

from src.config import LLM_PROVIDERS
from src.extraction.prompts import SYSTEM_PROMPT, build_extraction_prompt
from src.extraction.arithmetic_repair import repair_arithmetic

logger = logging.getLogger(__name__)


class LLMExtractor:
    """
    Multi-provider LLM extractor for document images.
    Converts PDF → image → sends to LLM with structured prompt.
    """

    def __init__(self, provider: str = "openai", strategy: str = "direct"):
        """
        Args:
            provider: 'openai' or 'groq'
            strategy: 'direct', 'cot', or 'structured'
        """
        if provider not in LLM_PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")

        self.provider = provider
        self.strategy = strategy
        self.config = LLM_PROVIDERS[provider]
        self.call_log: list[dict] = []
        self.retries = 0              # unparseable responses that were retried
        self._last_usage: dict = {}   # populated by each _call_* method

        logger.info("LLMExtractor initialized: provider=%s, strategy=%s, model=%s",
                     provider, strategy, self.config["model"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, pdf_path: str | Path, doc_type: str) -> dict:
        """
        Extract structured data from a PDF document.

        Args:
            pdf_path: Path to the PDF file
            doc_type: 'invoice', 'po', or 'receipt'

        Returns:
            Extracted data as a dict matching the document schema
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Convert PDF → PNG image
        image_bytes = self._pdf_to_image(pdf_path)

        # Build prompt
        user_prompt = build_extraction_prompt(doc_type, self.strategy)

        # Langfuse tracing (optional — silently skipped when not configured)
        from contextlib import nullcontext
        from src.langfuse_client import get_langfuse
        lf = get_langfuse()
        trace_id = None

        self._last_usage = {}
        try:
            if lf:
                raw_response, elapsed, trace_id = self._extract_with_langfuse(
                    lf, image_bytes, user_prompt, pdf_path, doc_type
                )
            else:
                t0 = time.time()
                if self.config["backend"] == "openai":
                    raw_response = self._call_openai(image_bytes, user_prompt)
                elif self.config["backend"] == "groq":
                    raw_response = self._call_groq(image_bytes, user_prompt, pdf_path)
                else:
                    raise ValueError(f"Provider not implemented: {self.provider}")
                elapsed = time.time() - t0
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("Langfuse tracing error (non-fatal): %s", exc)
            t0 = time.time()
            if self.config["backend"] == "openai":
                raw_response = self._call_openai(image_bytes, user_prompt)
            elif self.config["backend"] == "groq":
                raw_response = self._call_groq(image_bytes, user_prompt, pdf_path)
            elapsed = time.time() - t0

        # Parse JSON from response. An unparseable response is retried exactly
        # once — the failure is stochastic (a different document failed on each
        # run, see docs/measurements/2026-09-02-post-render-fix.md D1), so a
        # second attempt usually succeeds. Bounded at one retry: an extraction
        # that still will not parse must reach the caller as {} so the pipeline
        # turns it into REVIEW rather than looping.
        extracted = self._parse_json_response(raw_response)
        if extracted is None:
            logger.warning("Unparseable %s response for %s — retrying once",
                           self.provider, pdf_path.name)
            self.retries += 1
            t_retry = time.time()
            try:
                raw_response = self._raw_call(image_bytes, user_prompt, pdf_path)
                elapsed += time.time() - t_retry
                extracted = self._parse_json_response(raw_response)
            except Exception as exc:      # noqa: BLE001 - retry is best-effort
                logger.warning("  retry failed: %s", exc)

        # Compute cost for call_log
        cost_per_1k = self.config.get("cost_per_1k_tokens", 0.01)
        total_tokens = self._last_usage.get("total_tokens", 0)
        cost_usd = round((total_tokens / 1000) * cost_per_1k, 7)

        # Log the call (trace_id stored for downstream use)
        self.call_log.append({
            "pdf": str(pdf_path),
            "doc_type": doc_type,
            "provider": self.provider,
            "model": self.config["model"],
            "strategy": self.strategy,
            "elapsed_sec": round(elapsed, 2),
            "success": extracted is not None,
            "trace_id": trace_id,
            "tokens_in":    self._last_usage.get("input_tokens", 0),
            "tokens_out":   self._last_usage.get("output_tokens", 0),
            "tokens_total": total_tokens,
            "cost_usd":     cost_usd,
        })

        if extracted is None:
            logger.error("Still unparseable after retry — %s (%s). Returning {} "
                         "so the pipeline flags it for review.",
                         pdf_path.name, self.provider)
            return {}

        # Aritmetik tutarlılık onarımı (Çözüm #2): büyük sayılarda basamak
        # düşürme hatalarını qty×unit_price ve ara toplam+vergi üzerinden
        # deterministik olarak düzeltir (ek BDM çağrısı yok).
        extracted = repair_arithmetic(extracted, doc_type)

        logger.info("Extracted %s via %s in %.1fs (%d fields)",
                     pdf_path.name, self.provider, elapsed, len(extracted))
        return extracted

    def extract_batch(
        self,
        manifest: list[dict],
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Extract from a list of manifest entries.
        Each entry needs 'pdf', 'doc_type' keys.
        """
        results = []
        items = manifest[:limit] if limit else manifest

        for i, entry in enumerate(items, 1):
            pdf_path = entry["pdf"]
            doc_type = entry["doc_type"]
            logger.info("[%d/%d] Extracting %s (%s)...",
                        i, len(items), Path(pdf_path).name, doc_type)
            try:
                result = self.extract(pdf_path, doc_type)
                results.append(result)
            except Exception as exc:
                logger.error("  Error: %s", exc)
                results.append({})

            # Rate limiting
            time.sleep(0.5)

        return results

    def get_stats(self) -> dict:
        """Return call statistics including cumulative token usage and cost."""
        if not self.call_log:
            return {"total_calls": 0}
        successes = sum(1 for c in self.call_log if c["success"])
        times = [c["elapsed_sec"] for c in self.call_log if c["success"]]
        return {
            "total_calls":    len(self.call_log),
            "successful":     successes,
            "failed":         len(self.call_log) - successes,
            "avg_time_sec":   round(sum(times) / max(len(times), 1), 2),
            "provider":       self.provider,
            "model":          self.config["model"],
            "strategy":       self.strategy,
            "tokens_in":      sum(c.get("tokens_in", 0) for c in self.call_log),
            "tokens_out":     sum(c.get("tokens_out", 0) for c in self.call_log),
            "tokens_total":   sum(c.get("tokens_total", 0) for c in self.call_log),
            "cost_usd_total": round(sum(c.get("cost_usd", 0.0) for c in self.call_log), 6),
        }

    # ------------------------------------------------------------------
    # Langfuse-traced extraction helper
    # ------------------------------------------------------------------

    def _extract_with_langfuse(self, lf, image_bytes: bytes, user_prompt: str,
                                pdf_path, doc_type: str):
        """Run LLM call inside a Langfuse @observe trace to capture a real trace_id."""
        from langfuse import observe

        @observe(name="document_extraction")
        def _run():
            try:
                from langfuse import propagate_attributes
                with propagate_attributes(tags=[doc_type, self.provider],
                                          metadata={"pdf": pdf_path.name}):
                    pass
            except Exception:
                pass
            t0 = time.time()
            with lf.start_as_current_observation(
                name=f"llm_{self.provider}",
                as_type="generation",
                input={"model": self.config["model"], "strategy": self.strategy},
            ):
                if self.config["backend"] == "openai":
                    raw = self._call_openai(image_bytes, user_prompt)
                elif self.config["backend"] == "groq":
                    raw = self._call_groq(image_bytes, user_prompt, pdf_path)
                else:
                    raise ValueError(f"Provider not implemented: {self.provider}")

                if self._last_usage:
                    cost_per_1k = self.config.get("cost_per_1k_tokens", 0.01)
                    cost_usd = (self._last_usage.get("total_tokens", 0) / 1000) * cost_per_1k
                    try:
                        lf.update_current_generation(
                            usage_details={
                                "input":  self._last_usage.get("input_tokens", 0),
                                "output": self._last_usage.get("output_tokens", 0),
                                "total":  self._last_usage.get("total_tokens", 0),
                            },
                            cost_details={"total": round(cost_usd, 7)},
                            model=self.config["model"],
                        )
                    except Exception as _lfe:
                        logger.debug("Langfuse usage update failed: %s", _lfe)

            elapsed = time.time() - t0
            tid = lf.get_current_trace_id()
            return raw, elapsed, tid

        return _run()

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    def _raw_call(self, image_bytes: bytes, user_prompt: str, pdf_path) -> str:
        """One provider call, no tracing wrapper. Used by the retry path."""
        if self.config["backend"] == "openai":
            return self._call_openai(image_bytes, user_prompt)
        if self.config["backend"] == "groq":
            return self._call_groq(image_bytes, user_prompt, pdf_path)
        raise ValueError(f"Provider not implemented: {self.provider}")

    def _call_openai(self, image_bytes: bytes, user_prompt: str) -> str:
        """Call OpenAI Vision API with image."""
        import openai

        client = openai.OpenAI(api_key=self.config["api_key"], timeout=120.0)
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        call_kwargs = dict(
            model=self.config["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{b64_image}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            max_completion_tokens=8192,
            response_format={"type": "json_object"},
        )
        temp = self.config.get("temperature", 0.0)
        if temp is not None:
            call_kwargs["temperature"] = temp
        effort = self.config.get("reasoning_effort")
        if effort:
            call_kwargs["reasoning_effort"] = effort
        resp = client.chat.completions.create(**call_kwargs)
        if resp.usage:
            self._last_usage = {
                "input_tokens":  resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
                "total_tokens":  resp.usage.total_tokens,
            }
        return resp.choices[0].message.content.strip()

    def _call_groq(self, image_bytes: bytes, user_prompt: str,
                    pdf_path: Path = None) -> str:
        """Call Groq API with native vision support or text fallback.
        Automatically rotates to api_key_2 on 429 rate limit errors.
        """
        from groq import Groq

        api_keys = [k for k in [
            self.config.get("api_key"),
            self.config.get("api_key_2"),
        ] if k]

        VISION_MODELS = [
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "qwen/qwen3.6-27b"
        ]

        last_exc = None
        for i, api_key in enumerate(api_keys):
            try:
                client = Groq(api_key=api_key)

                if self.config["model"] in VISION_MODELS:
                    if i == 0:
                        logger.info("  Using native vision for %s", self.config["model"])
                    else:
                        logger.info("  Retrying with backup API key (key rotation)")
                    import base64
                    b64_image = base64.b64encode(image_bytes).decode("utf-8")

                    resp = client.chat.completions.create(
                        model=self.config["model"],
                        messages=[
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": user_prompt},
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{b64_image}",
                                        }
                                    },
                                ],
                            },
                        ],
                        max_tokens=4096,
                        temperature=0.0,
                    )
                    if resp.usage:
                        self._last_usage = {
                            "input_tokens":  resp.usage.prompt_tokens,
                            "output_tokens": resp.usage.completion_tokens,
                            "total_tokens":  resp.usage.total_tokens,
                        }
                    return resp.choices[0].message.content.strip()
                else:
                    return self._groq_text_fallback(client, user_prompt, image_bytes, pdf_path)

            except Exception as exc:
                if "429" in str(exc) and i < len(api_keys) - 1:
                    logger.warning("  Rate limit on key %d, switching to key %d...", i+1, i+2)
                    last_exc = exc
                    continue
                raise

        raise last_exc

    def _groq_text_fallback(self, client, user_prompt: str,
                             image_bytes: bytes, pdf_path: Path = None) -> str:
        """Text-only Groq using PDF text extraction or OCR fallback."""
        ocr_text = ""

        # Method 1: Direct PDF text extraction via fitz (best for digital PDFs)
        if pdf_path and Path(pdf_path).exists():
            try:
                import fitz
                doc = fitz.open(str(pdf_path))
                ocr_text = "\n".join(page.get_text() for page in doc)
                doc.close()
                logger.info("  Using fitz text extraction (%d chars)", len(ocr_text))
            except Exception as exc:
                logger.warning("  fitz text extraction failed: %s", exc)

        # Method 2: Fall back to local pytesseract OCR.
        # No third-party OCR services — customer documents never leave for a
        # shared-key cloud OCR endpoint.
        if not ocr_text.strip():
            try:
                from PIL import Image
                import pytesseract
                img = Image.open(io.BytesIO(image_bytes))
                ocr_text = pytesseract.image_to_string(img, lang="tur+eng")
            except Exception as exc:
                logger.warning("  local OCR unavailable: %s", exc)
                ocr_text = f"[text extraction unavailable: {exc}]"

        combined_prompt = (
            f"{user_prompt}\n\n"
            f"--- TEXT EXTRACTED FROM DOCUMENT ---\n{ocr_text}\n---"
        )

        resp = client.chat.completions.create(
            model=self.config["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": combined_prompt},
            ],
            max_tokens=4096,
            temperature=0.0,
        )
        if resp.usage:
            self._last_usage = {
                "input_tokens":  resp.usage.prompt_tokens,
                "output_tokens": resp.usage.completion_tokens,
                "total_tokens":  resp.usage.total_tokens,
            }
        return resp.choices[0].message.content.strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pdf_to_image(self, pdf_path: Path) -> bytes:
        """Convert first page of PDF to PNG bytes."""
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(str(pdf_path))
            page = doc[0]
            # Render at 300 DPI for high quality OCR/vision
            mat = fitz.Matrix(300 / 72, 300 / 72)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            doc.close()
            return img_bytes
        except ImportError:
            # Fallback: try pdf2image
            try:
                from pdf2image import convert_from_path
                images = convert_from_path(str(pdf_path), dpi=300, first_page=1, last_page=1)
                buf = io.BytesIO()
                images[0].save(buf, format="PNG")
                return buf.getvalue()
            except ImportError:
                raise ImportError(
                    "Install PyMuPDF (pip install pymupdf) or "
                    "pdf2image (pip install pdf2image) for PDF→image conversion"
                )

    def _parse_json_response(self, text: str) -> Optional[dict]:
        """Extract JSON object from LLM response text."""
        if not text:
            return None

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code block
        patterns = [
            r"```json\s*\n?(.*?)\n?\s*```",
            r"```\s*\n?(.*?)\n?\s*```",
            r"\{[\s\S]*\}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    candidate = match.group(1) if match.lastindex else match.group(0)
                    return json.loads(candidate)
                except (json.JSONDecodeError, IndexError):
                    continue

        logger.warning("Could not parse JSON from response: %s...", text[:200])
        return None
