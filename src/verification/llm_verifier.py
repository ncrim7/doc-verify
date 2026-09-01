"""
LLM-based verification agent for hallucination detection.
Sends extracted data + original document image to an LLM for cross-checking.
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

logger = logging.getLogger(__name__)

VERIFICATION_SYSTEM_PROMPT = """You are a Quality Assurance auditor specializing in document data extraction.
Your job is to compare extracted data against the original document image and identify errors.

IMPORTANT DISTINCTIONS:
- HALLUCINATION: The extracted value contains information NOT present in the document (invented/fabricated)
- ERROR: The value exists in the document but was misread or misformatted
- CORRECT: The value matches what is in the document

You must return ONLY valid JSON — no markdown, no explanations."""

VERIFICATION_USER_PROMPT = """Compare the following extracted data against the original document image.

EXTRACTED DATA:
{extracted_json}

For each field, determine if the extraction is:
- "correct": matches the document
- "error": exists in document but was misread
- "hallucination": value was invented/not in document
- "missing": field exists in document but was not extracted

Return ONLY this JSON structure:
{{
  "verification_results": [
    {{
      "field_name": "field_name",
      "extracted_value": "the extracted value",
      "status": "correct|error|hallucination|missing",
      "confidence": 0.0 to 1.0,
      "notes": "brief explanation if not correct"
    }}
  ],
  "overall_confidence": 0.0 to 1.0,
  "hallucination_count": 0,
  "error_count": 0,
  "summary": "brief overall assessment"
}}"""


class LLMVerifier:
    """
    LLM-based verification agent.
    Cross-references extracted data against original document image.
    """

    def __init__(self, provider: str = "openai"):
        if provider not in LLM_PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")
        self.provider = provider
        self.config = LLM_PROVIDERS[provider]
        self.call_log: list[dict] = []
        logger.info("LLMVerifier initialized: provider=%s, model=%s",
                     provider, self.config["model"])

    def verify(self, extracted: dict, pdf_path: str | Path) -> dict:
        """
        Verify extracted data against the document image.

        Args:
            extracted: The extraction result dict
            pdf_path: Path to the original PDF

        Returns:
            Verification result with per-field assessments
        """
        pdf_path = Path(pdf_path)
        image_bytes = self._pdf_to_image(pdf_path)

        # Build prompt with extracted data
        extracted_clean = {k: v for k, v in extracted.items()
                          if k not in ("confidence_scores",)}
        extracted_json = json.dumps(extracted_clean, indent=2, ensure_ascii=False)
        user_prompt = VERIFICATION_USER_PROMPT.format(extracted_json=extracted_json)

        t0 = time.time()
        raw_response = self._call_llm(image_bytes, user_prompt)
        elapsed = time.time() - t0

        result = self._parse_json_response(raw_response)

        self.call_log.append({
            "pdf": str(pdf_path),
            "provider": self.provider,
            "elapsed_sec": round(elapsed, 2),
            "success": result is not None,
        })

        if result is None:
            logger.warning("Failed to parse verification response for %s", pdf_path.name)
            return self._fallback_result(extracted)

        # Ensure required keys
        result.setdefault("hallucination_count", 0)
        result.setdefault("error_count", 0)
        result.setdefault("overall_confidence", 0.5)
        result.setdefault("verification_results", [])

        # Recount from results
        hall_count = sum(1 for r in result["verification_results"]
                        if r.get("status") == "hallucination")
        err_count = sum(1 for r in result["verification_results"]
                        if r.get("status") == "error")
        result["hallucination_count"] = hall_count
        result["error_count"] = err_count
        result["hallucination_detected"] = hall_count > 0

        logger.info("Verified %s: confidence=%.2f, hallucinations=%d, errors=%d (%.1fs)",
                     pdf_path.name, result["overall_confidence"],
                     hall_count, err_count, elapsed)

        return result

    def get_issue_fields(self, verification: dict) -> list[dict]:
        """Extract fields with issues from verification result."""
        issues = []
        for item in verification.get("verification_results", []):
            if item.get("status") in ("hallucination", "error", "missing"):
                issues.append({
                    "field": item["field_name"],
                    "status": item["status"],
                    "extracted_value": item.get("extracted_value"),
                    "confidence": item.get("confidence", 0),
                    "notes": item.get("notes", ""),
                })
        return issues

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, image_bytes: bytes, user_prompt: str) -> str:
        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        if self.provider == "openai":
            import openai
            client = openai.OpenAI(api_key=self.config["api_key"], timeout=120.0)
            call_kwargs = dict(
                model=self.config["model"],
                messages=[
                    {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
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
                max_completion_tokens=4096,
            )
            temp = self.config.get("temperature", 0.0)
            if temp is not None:
                call_kwargs["temperature"] = temp
            resp = client.chat.completions.create(**call_kwargs)
            return resp.choices[0].message.content.strip()

        elif self.provider == "groq":
            from groq import Groq
            client = Groq(api_key=self.config["api_key"])
            resp = client.chat.completions.create(
                model=self.config["model"],
                messages=[
                    {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{b64_image}",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=4096,
                temperature=0.0,
            )
            return resp.choices[0].message.content.strip()

        raise ValueError(f"Provider not implemented: {self.provider}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pdf_to_image(self, pdf_path: Path) -> bytes:
        import fitz
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        mat = fitz.Matrix(200 / 72, 200 / 72)
        pix = page.get_pixmap(matrix=mat)
        img_bytes = pix.tobytes("png")
        doc.close()
        return img_bytes

    def _parse_json_response(self, text: str) -> Optional[dict]:
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
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
        return None

    def _fallback_result(self, extracted: dict) -> dict:
        """Return a safe fallback when LLM verification fails."""
        return {
            "verification_results": [],
            "overall_confidence": 0.0,
            "hallucination_count": 0,
            "error_count": 0,
            "hallucination_detected": False,
            "summary": "Verification failed — LLM did not return valid response",
        }
