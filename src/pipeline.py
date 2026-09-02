"""
Single-document pipeline: extract → verify → correct → verdict.

This module exists to make one rule structural rather than a convention:

    **A document is never silently dropped and never implicitly "OK".**

Anything that is not a provably valid extraction with no critical verification
issue comes back as ``Verdict.REVIEW`` carrying the reason. The check is
cause-agnostic on purpose: it does not test for one known failure (a token
overflow, a particular model, a document type). Any future reason an extraction
comes back empty, malformed, or short of its required fields lands in the same
branch.

Background: `docs/measurements/2026-09-02-baseline.md` — the measurement harness
did `if not extracted: continue`, so two documents that failed extraction were
skipped without verification and without a flag. On a real customer document
the system would have reported success.

Note: `repair_arithmetic` currently runs inside `LLMExtractor.extract`, not
here. If the extractor is ever swapped, that repair goes with it — see the
Phase-2 backlog.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Optional

__all__ = ["Verdict", "PipelineResult", "DocumentPipeline", "apply_corrections"]


class Verdict(StrEnum):
    OK = "OK"
    REVIEW = "REVIEW"


@dataclass
class PipelineResult:
    verdict: Verdict
    doc_type: str
    pdf_path: str
    data: Optional[dict] = None
    raw: Optional[dict] = None      # extraction as returned, before any repair
    reasons: list[str] = field(default_factory=list)
    verification: Optional[dict] = None
    corrected: bool = False
    error: Optional[str] = None
    timings: dict = field(default_factory=dict)

    @property
    def needs_human(self) -> bool:
        return self.verdict is not Verdict.OK

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "doc_type": self.doc_type,
            "pdf_path": self.pdf_path,
            "data": self.data,
            "raw": self.raw,
            "reasons": list(self.reasons),
            "verification": self.verification,
            "corrected": self.corrected,
            "error": self.error,
            "timings": dict(self.timings),
        }


_ITEM_KEY = re.compile(r"items\[(\d+)\]\.(\w+)")


def apply_corrections(extracted: dict, corrections: dict) -> dict:
    """Apply the rule verifier's auto-corrections to a copy of the extraction."""
    out = json.loads(json.dumps(extracted))
    for key, value in (corrections or {}).items():
        m = _ITEM_KEY.match(key)
        if m:
            idx, fld = int(m.group(1)), m.group(2)
            items = out.get("items") or []
            if idx < len(items):
                items[idx][fld] = value
        else:
            out[key] = value
    return out


class DocumentPipeline:
    """
    Orchestrates one document. Components can be injected for testing; the real
    ones are constructed lazily only when they are actually needed, so building
    a pipeline with fakes touches neither config nor network.
    """

    def __init__(
        self,
        provider: str = "openai",
        strategy: str = "direct",
        enable_correction: bool = True,
        extractor: Any = None,
        verifier: Any = None,
        corrector: Any = None,
    ):
        self.provider = provider
        self.strategy = strategy
        self.enable_correction = enable_correction
        self._extractor = extractor
        self._verifier = verifier
        self._corrector = corrector

    # -- lazily constructed real components ---------------------------------

    @property
    def extractor(self):
        if self._extractor is None:
            from src.extraction.llm_extractor import LLMExtractor
            self._extractor = LLMExtractor(provider=self.provider,
                                           strategy=self.strategy)
        return self._extractor

    @property
    def verifier(self):
        if self._verifier is None:
            from src.verification.rule_based_verifier import RuleBasedVerifier
            self._verifier = RuleBasedVerifier()
        return self._verifier

    @property
    def corrector(self):
        if self._corrector is None:
            from src.extraction.correction_agent import CorrectionAgent
            self._corrector = CorrectionAgent(provider=self.provider)
        return self._corrector

    # -- the pipeline --------------------------------------------------------

    def process(self, pdf_path: str | Path, doc_type: str) -> PipelineResult:
        pdf_path = str(pdf_path)
        timings: dict = {}
        reasons: list[str] = []

        # 1. Extract. An exception here is a REVIEW, never an escape hatch.
        t0 = time.time()
        try:
            extracted = self.extractor.extract(pdf_path, doc_type)
        except Exception as exc:                      # noqa: BLE001 - deliberate
            timings["extract_sec"] = round(time.time() - t0, 2)
            return PipelineResult(
                verdict=Verdict.REVIEW, doc_type=doc_type, pdf_path=pdf_path,
                reasons=[f"extraction_error: {exc}"], error=str(exc),
                timings=timings,
            )
        timings["extract_sec"] = round(time.time() - t0, 2)

        # 2. Structural gate — cause-agnostic. Anything that is not a non-empty
        #    dict cannot be verified, so it cannot be OK.
        if not isinstance(extracted, dict) or not extracted:
            return PipelineResult(
                verdict=Verdict.REVIEW, doc_type=doc_type, pdf_path=pdf_path,
                reasons=[
                    f"extraction_empty: expected a non-empty object, got "
                    f"{type(extracted).__name__}"
                ],
                timings=timings,
            )

        raw = json.loads(json.dumps(extracted))   # snapshot before any repair

        # 3. Rule verification + deterministic auto-corrections.
        t1 = time.time()
        verification = self.verifier.verify(extracted, doc_type)
        data = apply_corrections(extracted, verification.get("auto_corrections"))
        timings["verify_sec"] = round(time.time() - t1, 2)

        # 4. Targeted re-extraction of flagged fields.
        corrected = False
        if self.enable_correction and verification.get("issues"):
            t2 = time.time()
            try:
                data = self.corrector.correct(
                    data, pdf_path, verification["issues"], doc_type
                )
                corrected = True
            except Exception as exc:                  # noqa: BLE001 - deliberate
                reasons.append(f"correction_error: {exc}")
            timings["correct_sec"] = round(time.time() - t2, 2)
            # Re-verify whatever we ended up with.
            verification = self.verifier.verify(data, doc_type)

        # 5. Verdict. Critical issues, or anything that went wrong along the
        #    way, mean a human looks at it.
        for issue in verification.get("issues", []):
            if issue.get("severity") == "critical":
                reasons.append(
                    f"{issue.get('rule', 'critical_issue')}: "
                    f"{issue.get('field', '?')}"
                )

        timings["total_sec"] = round(sum(v for v in timings.values()), 2)

        return PipelineResult(
            verdict=Verdict.REVIEW if reasons else Verdict.OK,
            doc_type=doc_type,
            pdf_path=pdf_path,
            data=data,
            raw=raw,
            reasons=reasons,
            verification=verification,
            corrected=corrected,
            timings=timings,
        )
