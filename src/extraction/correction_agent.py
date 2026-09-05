"""
Correction agent for targeted field re-extraction.
Takes verification results, identifies problematic fields, and asks
the LLM to re-examine and correct only those specific fields.
"""
import json
import base64
import logging
import re
from pathlib import Path
from typing import Optional

from src.config import LLM_PROVIDERS
from src.extraction.llm_extractor import LLMExtractor
from src.extraction.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CORRECTION_PROMPT = """You previously extracted data from this document. A checker
found a problem with SOME of the fields. Re-read the document image and report
what those fields actually say.

Here is the previous extraction, for context only:
{extracted_json}

RE-READ ONLY THESE FIELDS — nothing else on the document is in question:
{field_issues}

Return a JSON object containing ONLY the fields listed above, using exactly the
keys shown. Do not include any other field. Do not repeat the whole document.

For each one, report what is PRINTED on the page. If, after looking again, the
previous value was right, return it unchanged — that is a valid answer and
often the correct one. Do not adjust a value to make it satisfy a rule, and do
not compute a value you cannot read. If a field is genuinely illegible, return
null for it.

Return ONLY valid JSON, no explanations."""


class CorrectionAgent:
    """
    Targeted re-extraction agent.
    Takes verification issues and asks LLM to re-examine specific fields.
    """

    def __init__(self, provider: str = "openai", model_override: Optional[str] = None):
        self.provider = provider
        self.config = LLM_PROVIDERS[provider].copy()
        if model_override:
            self.config["model"] = model_override
        self.base_extractor = LLMExtractor(provider=provider)
        logger.info("CorrectionAgent initialized: provider=%s, model=%s",
                     provider, self.config["model"])

    def correct(
        self,
        extracted: dict,
        pdf_path: str | Path,
        issues: list[dict],
        doc_type: str,
    ) -> dict:
        """
        Re-extract with focus on problematic fields.

        Args:
            extracted: Original extraction result
            pdf_path: Path to the PDF file
            issues: List of verification issues
            doc_type: Document type

        Returns:
            Corrected extraction dict
        """
        if not issues:
            return extracted

        pdf_path = Path(pdf_path)
        allowed = {i.get("field") for i in issues if i.get("field")}
        if not allowed:
            return extracted

        # Format issues for the prompt
        field_issues = self._format_issues(issues)

        # Build correction prompt
        prompt = CORRECTION_PROMPT.format(
            extracted_json=json.dumps(extracted, indent=2, ensure_ascii=False),
            field_issues=field_issues,
        )

        # Call LLM with the original image
        try:
            image_bytes = self.base_extractor._pdf_to_image(pdf_path)
            corrected = self._call_correction(image_bytes, prompt)

            if corrected:
                merged, applied, refused = self._merge_results(
                    extracted, corrected, allowed)
                logger.info("  Correction agent: %d field(s) applied%s",
                            len(applied),
                            f", {len(refused)} refused ({', '.join(sorted(refused))})"
                            if refused else "")
                return merged
            else:
                logger.warning("  Correction agent failed to parse response")
                return extracted

        except Exception as exc:
            logger.warning("  Correction agent error: %s", exc)
            return extracted

    def _format_issues(self, issues: list[dict]) -> str:
        """Format verification issues into human-readable text."""
        lines = []
        for issue in issues:
            field = issue.get("field", "unknown")
            msg = issue.get("message", "No details")
            severity = issue.get("severity", "info")
            lines.append(f"- [{severity.upper()}] Field '{field}': {msg}")
        return "\n".join(lines)

    def _call_correction(self, image_bytes: bytes, prompt: str) -> Optional[dict]:
        """Call LLM for correction."""
        import openai

        b64_image = base64.b64encode(image_bytes).decode("utf-8")

        client = openai.OpenAI(api_key=self.config["api_key"], timeout=120.0)
        call_kwargs = dict(
            model=self.config["model"],
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
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

        raw = resp.choices[0].message.content.strip()
        return self.base_extractor._parse_json_response(raw)

    _ITEM_FIELD = re.compile(r"^items\[(\d+)\]\.(\w+)$")

    def _merge_results(
        self, original: dict, corrected: dict, allowed: set[str],
    ) -> tuple[dict, set[str], set[str]]:
        """
        Apply the correction to the flagged fields ONLY.

        Returns (merged, applied, refused).

        This used to take every non-null value the model returned and write it
        over the original. Measured 2026-09-03, that let a correction asked to
        look at one tax id also rewrite `Post-it Not Bloğu` to `Blogu` and
        `Tevetoğlu A.Ş.` to `Tevetoglu A.Ş.` — the ASCII transliteration
        regression the extraction prompt had been fixed to prevent, coming back
        in through the correction pass on fields nobody had questioned.

        The allowlist is the structural fix: a field that was not flagged
        cannot be changed here, whatever the model returns. The prompt asks for
        the same restraint, but a prompt is a request and this is a guarantee.
        Refused keys are returned so the caller can log them — a model that
        keeps trying to rewrite unflagged fields is worth knowing about.
        """
        merged = json.loads(json.dumps(original))  # deep copy
        applied: set[str] = set()
        refused: set[str] = set()

        for key, value in corrected.items():
            if key == "items" and isinstance(value, list):
                # Item corrections are addressed as items[i].field, so unpack
                # the list into those paths and let the same allowlist decide.
                for i, item in enumerate(value):
                    if not isinstance(item, dict):
                        continue
                    for fld, val in item.items():
                        path = f"items[{i}].{fld}"
                        if path not in allowed or val is None:
                            if path not in allowed:
                                refused.add(path)
                            continue
                        items = merged.get("items")
                        if isinstance(items, list) and i < len(items) \
                                and isinstance(items[i], dict):
                            items[i][fld] = val
                            applied.add(path)
                continue

            if key not in allowed:
                refused.add(key)
                continue
            if value is None:
                # An explicit "I cannot read this" is not a correction. Keeping
                # the original leaves the issue standing, which is right: the
                # document still goes to a human.
                continue
            merged[key] = value
            applied.add(key)

        return merged, applied, refused
