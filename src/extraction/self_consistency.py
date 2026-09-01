"""
Self-Consistency decoding for document extraction.
Runs multiple extraction passes with different temperatures and
uses majority voting to select the most consistent result.
"""
import json
import time
import logging
from pathlib import Path
from collections import Counter
from typing import Optional

from src.extraction.llm_extractor import LLMExtractor
from src.evaluation.metrics import normalize_text

logger = logging.getLogger(__name__)


class SelfConsistencyExtractor:
    """
    Runs N extraction passes and picks the most consistent answer per field.
    Uses majority voting for string fields and median for numeric fields.
    """

    def __init__(
        self,
        provider: str = "openai",
        strategy: str = "cot_enhanced",
        num_passes: int = 3,
        temperatures: Optional[list[float]] = None,
    ):
        self.provider = provider
        self.strategy = strategy
        self.num_passes = num_passes
        self.temperatures = temperatures or [0.0, 0.3, 0.5][:num_passes]

        # We'll create extractor per temperature
        self.base_extractor = LLMExtractor(provider=provider, strategy=strategy)
        logger.info("SelfConsistencyExtractor: %d passes, temps=%s",
                     num_passes, self.temperatures)

    def extract(self, pdf_path: str | Path, doc_type: str) -> dict:
        """
        Run multiple extraction passes and aggregate via majority vote.
        """
        pdf_path = Path(pdf_path)
        all_results: list[dict] = []

        for i, temp in enumerate(self.temperatures):
            logger.info("  Pass %d/%d (temp=%.1f)...", i + 1, self.num_passes, temp)
            result = self._extract_with_temp(pdf_path, doc_type, temp)
            if result:
                all_results.append(result)
            time.sleep(0.3)

        if not all_results:
            logger.warning("All passes failed for %s", pdf_path.name)
            return {}

        if len(all_results) == 1:
            return all_results[0]

        # Aggregate results via majority voting
        aggregated = self._majority_vote(all_results)
        logger.info("  Aggregated %d passes → %d fields", len(all_results), len(aggregated))
        return aggregated

    def _extract_with_temp(self, pdf_path: Path, doc_type: str, temperature: float) -> dict:
        """Single extraction pass with specified temperature."""
        import base64
        import openai
        from src.config import LLM_PROVIDERS
        from src.extraction.prompts import SYSTEM_PROMPT, build_extraction_prompt

        config = LLM_PROVIDERS[self.provider]
        image_bytes = self.base_extractor._pdf_to_image(pdf_path)
        b64_image = base64.b64encode(image_bytes).decode("utf-8")
        user_prompt = build_extraction_prompt(doc_type, self.strategy)

        try:
            if self.provider == "openai":
                client = openai.OpenAI(api_key=config["api_key"])
                call_kwargs = dict(
                    model=config["model"],
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
                    max_completion_tokens=4096,
                )
                cfg_temp = config.get("temperature", temperature)
                if cfg_temp is not None:
                    call_kwargs["temperature"] = cfg_temp
                resp = client.chat.completions.create(**call_kwargs)
                raw = resp.choices[0].message.content.strip()
                return self.base_extractor._parse_json_response(raw) or {}
            else:
                # For other providers, use base extractor
                return self.base_extractor.extract(pdf_path, doc_type)
        except Exception as exc:
            logger.warning("  Pass failed: %s", exc)
            return {}

    def _majority_vote(self, results: list[dict]) -> dict:
        """
        Aggregate multiple extraction results via majority voting.
        - String fields: most common value (normalized)
        - Numeric fields: median value
        - List fields (items): pick from the result with most items
        """
        aggregated = {}
        all_keys = set()
        for r in results:
            all_keys.update(r.keys())

        for key in all_keys:
            values = [r.get(key) for r in results if key in r]
            if not values:
                continue

            # Handle items list separately
            if key == "items" and all(isinstance(v, list) for v in values):
                # Pick the items list from the result that has the most complete items
                best_items = max(values, key=lambda v: len(v))
                # For each item position, majority vote across passes
                aggregated[key] = self._vote_items(values)
                continue

            # Numeric fields
            if all(self._is_numeric(v) for v in values if v is not None):
                nums = [float(str(v).replace(",", "")) for v in values if v is not None]
                if nums:
                    nums.sort()
                    aggregated[key] = nums[len(nums) // 2]  # median
                    continue

            # String fields: majority vote
            str_values = [normalize_text(str(v)) for v in values if v is not None]
            if str_values:
                counter = Counter(str_values)
                winner_normalized = counter.most_common(1)[0][0]
                # Return original (unnormalized) version
                for v in values:
                    if v is not None and normalize_text(str(v)) == winner_normalized:
                        aggregated[key] = v
                        break

        return aggregated

    def _vote_items(self, item_lists: list[list]) -> list:
        """Majority vote on item lists."""
        # Find max items count
        max_len = max(len(il) for il in item_lists)
        voted_items = []

        for idx in range(max_len):
            item_candidates = []
            for il in item_lists:
                if idx < len(il):
                    item_candidates.append(il[idx])

            if not item_candidates:
                continue

            # For each field in the item, majority vote
            voted_item = {}
            all_item_keys = set()
            for ic in item_candidates:
                if isinstance(ic, dict):
                    all_item_keys.update(ic.keys())

            for field in all_item_keys:
                field_vals = [ic.get(field) for ic in item_candidates
                              if isinstance(ic, dict) and field in ic]
                if not field_vals:
                    continue

                if all(self._is_numeric(v) for v in field_vals if v is not None):
                    nums = [float(str(v).replace(",", ""))
                            for v in field_vals if v is not None]
                    if nums:
                        nums.sort()
                        voted_item[field] = nums[len(nums) // 2]
                else:
                    counter = Counter(str(v) for v in field_vals if v is not None)
                    winner = counter.most_common(1)[0][0]
                    # Try to return original type
                    for v in field_vals:
                        if str(v) == winner:
                            voted_item[field] = v
                            break

            voted_items.append(voted_item)

        return voted_items

    @staticmethod
    def _is_numeric(val) -> bool:
        if val is None:
            return False
        try:
            float(str(val).replace(",", ""))
            return True
        except (ValueError, TypeError):
            return False
