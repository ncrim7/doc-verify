"""
Tests for the vertical slice: document in, actionable decision out.

All fakes — the point here is the wiring and the failure paths, not the model.
"""
from src.analyze import DocumentAnalyzer
from src.decision.engine import Verdict
from src.pipeline import DocumentPipeline, PipelineResult
from src.pipeline import Verdict as PipeVerdict


class FakeExtractor:
    def __init__(self, result):
        self._result = result

    def extract(self, pdf_path, doc_type):
        return self._result


def _pipe(extraction):
    return DocumentPipeline(extractor=FakeExtractor(extraction),
                            enable_correction=False)


def _clean_invoice():
    return {
        "invoice_number": "INV-1001", "date": "2026-03-15",
        "vendor_name": "Acme Ltd.", "vendor_tax_id": "1234567890",
        "items": [{"quantity": 2, "unit_price": 100.0, "total": 200.0}],
        "subtotal": 200.0, "tax_amount": 36.0, "total_amount": 236.0,
    }


class TestSlice:
    def test_a_clean_document_is_payable(self):
        r = DocumentAnalyzer(pipeline=_pipe(_clean_invoice())).analyze("x.pdf")
        assert r.verdict is Verdict.PAY
        assert r.decision.findings == []
        assert r.po_matched is False

    def test_a_broken_total_is_reviewed_not_held(self):
        # The gap is real and is reported, but one document cannot say whether
        # the supplier billed wrongly or we misread the page. Holding a payment
        # on our own uncertainty would blame the supplier for our reading.
        bad = _clean_invoice()
        bad["total_amount"] = 576.0          # subtotal 200 + tax 36 = 236
        r = DocumentAnalyzer(pipeline=_pipe(bad)).analyze("x.pdf")
        assert r.verdict is Verdict.REVIEW
        assert r.decision.financial_impact_try == 0.0
        assert r.decision.internal_discrepancy_try == 340.0

    def test_a_bogus_tax_id_goes_to_review_not_hold(self):
        bad = _clean_invoice()
        bad["buyer_tax_id"] = "11111111111"   # the real placeholder
        r = DocumentAnalyzer(pipeline=_pipe(bad)).analyze("x.pdf")
        assert r.verdict is Verdict.REVIEW
        assert any(f.kind == "TAX_ID_INVALID" for f in r.decision.findings)

    def test_extraction_failure_is_a_review_carrying_the_reason(self):
        r = DocumentAnalyzer(pipeline=_pipe({})).analyze("x.pdf")
        assert r.verdict is Verdict.REVIEW
        f = r.decision.findings[0]
        assert f.kind == "EXTRACTION_FAILED" and f.message

    def test_it_never_calls_the_matcher_without_a_po(self):
        class Boom:
            def match(self, *a, **k):
                raise AssertionError("matcher must not run without a PO")
        DocumentAnalyzer(pipeline=_pipe(_clean_invoice()),
                         matcher=Boom()).analyze("x.pdf")

    def test_a_po_adds_findings_without_becoming_the_spine(self):
        class FakeMatcher:
            def match(self, po, inv):
                return {"matches": [{
                    "po_item": {"quantity": 100, "unit_price": 50.0},
                    "invoice_item": {"quantity": 100, "unit_price": 53.40},
                    "discrepancies": [{"field": "unit_price", "po_value": 50.0,
                                       "invoice_value": 53.40,
                                       "severity": "critical", "message": "m"}],
                }]}
        r = DocumentAnalyzer(pipeline=_pipe(_clean_invoice()),
                             matcher=FakeMatcher()).analyze(
            "x.pdf", po_data={"po_number": "PO-1"})
        assert r.po_matched is True
        assert r.verdict is Verdict.HOLD
        assert r.decision.financial_impact_try == 340.0

    def test_document_and_po_findings_both_land(self):
        class FakeMatcher:
            def match(self, po, inv):
                return {"unmatched_invoice": [{"description": "Kargo",
                                               "total": 75.0}]}
        bad = _clean_invoice()
        bad["buyer_tax_id"] = "11111111111"
        r = DocumentAnalyzer(pipeline=_pipe(bad),
                             matcher=FakeMatcher()).analyze(
            "x.pdf", po_data={"po_number": "PO-1"})
        sources = {f.source for f in r.decision.findings}
        assert sources == {"document", "po_match"}

    def test_the_result_serialises(self):
        import json
        r = DocumentAnalyzer(pipeline=_pipe(_clean_invoice())).analyze("x.pdf")
        json.dumps(r.to_dict())

    def test_timings_are_recorded(self):
        r = DocumentAnalyzer(pipeline=_pipe(_clean_invoice())).analyze("x.pdf")
        assert "extract_sec" in r.timings and "total_sec" in r.timings
