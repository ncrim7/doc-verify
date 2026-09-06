"""
The vertical slice: a document goes in, a decision a person can act on comes out.

    PDF / photo
        -> extraction
        -> verification
        -> findings          (+ PO matching, when a PO exists)
        -> decision          ÖDE / BEKLET / İNCELE
        -> plain Turkish

Everything under `src/` before this module answers "is the JSON right?". This
is the first module that answers "should I pay this?", which is the only
question the customer has.

A purchase order is OPTIONAL and deliberately so. The 15 real documents
collected for this project are 11 invoices and 4 receipts — **not one purchase
order**. A pipeline that only produces value when a PO is present would produce
nothing on the documents we actually have, and three-way matching came from an
enterprise procurement context that a bookkeeping office may not share. Whether
these customers hold POs at all is an open question, so it is a source here,
never the spine.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.decision.engine import (
    Decision, Finding, Verdict, decide, from_po_match, from_verification,
)
from src.pipeline import DocumentPipeline, PipelineResult

__all__ = ["AnalysisResult", "DocumentAnalyzer"]


@dataclass
class AnalysisResult:
    decision: Decision
    extraction: PipelineResult
    po_matched: bool = False
    timings: dict = field(default_factory=dict)

    @property
    def verdict(self) -> Verdict:
        return self.decision.verdict

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.to_dict(),
            "extraction": self.extraction.to_dict(),
            "po_matched": self.po_matched,
            "timings": dict(self.timings),
        }


class DocumentAnalyzer:
    """
    Orchestrates the slice. Components are injectable so the decision path can
    be tested without touching a network.
    """

    def __init__(self, pipeline: Any = None, matcher: Any = None):
        self._pipeline = pipeline
        self._matcher = matcher

    @property
    def pipeline(self):
        if self._pipeline is None:
            self._pipeline = DocumentPipeline()
        return self._pipeline

    @property
    def matcher(self):
        if self._matcher is None:
            from src.matching.po_invoice_matcher import POInvoiceMatcher
            self._matcher = POInvoiceMatcher()
        return self._matcher

    def analyze(
        self,
        pdf_path: str | Path,
        doc_type: str = "invoice",
        po_data: Optional[dict] = None,
    ) -> AnalysisResult:
        timings: dict = {}

        t0 = time.time()
        extraction = self.pipeline.process(pdf_path, doc_type)
        timings["extract_sec"] = round(time.time() - t0, 2)

        # Extraction failing is not a payment decision. There is nothing to
        # judge, so it goes straight to a person with the reason attached.
        if extraction.data is None:
            return AnalysisResult(
                decision=Decision(
                    verdict=Verdict.REVIEW,
                    findings=[Finding(
                        kind="EXTRACTION_FAILED", severity="critical",
                        field="_document", source="document",
                        message="; ".join(extraction.reasons)
                                or "Belge okunamadı.",
                    )],
                ),
                extraction=extraction, timings=timings,
            )

        findings = from_verification(extraction.verification)

        po_matched = False
        if po_data:
            t1 = time.time()
            match = self.matcher.match(po_data, extraction.data)
            findings += from_po_match(match)
            po_matched = True
            timings["match_sec"] = round(time.time() - t1, 2)

        decision = decide(findings, extraction.data)
        timings["total_sec"] = round(sum(timings.values()), 2)
        return AnalysisResult(decision=decision, extraction=extraction,
                              po_matched=po_matched, timings=timings)
