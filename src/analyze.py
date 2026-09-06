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
    duplicates: Any = None
    timings: dict = field(default_factory=dict)

    @property
    def verdict(self) -> Verdict:
        return self.decision.verdict

    def to_dict(self) -> dict:
        return {
            "decision": self.decision.to_dict(),
            "extraction": self.extraction.to_dict(),
            "po_matched": self.po_matched,
            "duplicates": (self.duplicates.to_dict()
                           if self.duplicates is not None else None),
            "timings": dict(self.timings),
        }


class DocumentAnalyzer:
    """
    Orchestrates the slice. Components are injectable so the decision path can
    be tested without touching a network.
    """

    def __init__(self, pipeline: Any = None, matcher: Any = None,
                 store: Any = None, record: bool = False):
        self._pipeline = pipeline
        self._matcher = matcher
        self.store = store
        # Recording is opt-in and defaults OFF. A document that was just
        # flagged as a probable duplicate must not quietly join the history it
        # was checked against — the caller decides after a person has looked.
        self.record = record

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
        channel: str = "email_pdf",
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

        # Against the document memory, when there is one. This is the check
        # two independent customers put first, ahead of anything the earlier
        # design led with.
        duplicates = None
        canonical = None
        if self.store is not None:
            from src.ledger.canonical import CanonicalDocument
            from src.ledger.duplicate import check_duplicates, to_findings
            t1 = time.time()
            canonical = CanonicalDocument.from_extraction(
                extraction.data, channel=channel, source_ref=str(pdf_path),
                doc_type=doc_type)
            duplicates = check_duplicates(canonical, self.store)
            findings += to_findings(duplicates, canonical)
            timings["ledger_sec"] = round(time.time() - t1, 2)

        po_matched = False
        if po_data:
            t2 = time.time()
            match = self.matcher.match(po_data, extraction.data)
            findings += from_po_match(match)
            po_matched = True
            timings["match_sec"] = round(time.time() - t2, 2)

        decision = decide(findings, extraction.data)
        timings["total_sec"] = round(sum(timings.values()), 2)

        if self.record and self.store is not None and canonical is not None:
            self.store.record(canonical)

        return AnalysisResult(decision=decision, extraction=extraction,
                              po_matched=po_matched, duplicates=duplicates,
                              timings=timings)
