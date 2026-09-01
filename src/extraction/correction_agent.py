"""
Correction agent for targeted field re-extraction.
Takes verification results, identifies problematic fields, and asks
the LLM to re-examine and correct only those specific fields.
"""
import json
import base64
import logging
from pathlib import Path
from typing import Optional

from src.config import LLM_PROVIDERS
from src.extraction.llm_extractor import LLMExtractor
from src.extraction.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CORRECTION_PROMPT = """You previously extracted data from this document, but some fields may be incorrect.

Here is the ORIGINAL extraction result:
{extracted_json}

The following fields were flagged as potentially incorrect:
{field_issues}

Please re-examine the document image carefully and provide CORRECTED values for the flagged fields.
Focus especially on:
- Reading numbers precisely (check decimal points, thousand separators)
- Reading addresses character by character
- Verifying item descriptions match exactly what's on the document
- Checking dates are in YYYY-MM-DD format

Return a COMPLETE corrected JSON object with ALL fields (not just the corrected ones).
The JSON must match this schema exactly — return ONLY valid JSON, no explanations."""


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
                logger.info("  Correction agent returned %d fields", len(corrected))
                # Merge: use corrected values but keep original for fields not in corrected
                merged = self._merge_results(extracted, corrected)
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
            max_completion_tokens=4096,
            response_format={"type": "json_object"},
        )
        temp = self.config.get("temperature", 0.0)
        if temp is not None:
            call_kwargs["temperature"] = temp
        resp = client.chat.completions.create(**call_kwargs)

        raw = resp.choices[0].message.content.strip()
        return self.base_extractor._parse_json_response(raw)

    def _merge_results(self, original: dict, corrected: dict) -> dict:
        """
        Merge corrected results with original.
        Prefer corrected values when they exist, keep original otherwise.
        """
        merged = json.loads(json.dumps(original))  # deep copy

        for key, value in corrected.items():
            if key == "items" and isinstance(value, list):
                # For items, merge item by item
                if isinstance(merged.get("items"), list):
                    for i, item in enumerate(value):
                        if i < len(merged["items"]) and isinstance(item, dict):
                            merged["items"][i].update(item)
                        elif isinstance(item, dict):
                            merged["items"].append(item)
                else:
                    merged["items"] = value
            elif value is not None:
                merged[key] = value

        return merged
