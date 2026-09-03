"""
Tests for src.pipeline — the safety net.

The invariant under test: the pipeline never returns OK unless extraction
produced a non-empty, structurally valid result AND verification found no
critical issue. Every failure path yields REVIEW with a reason. No code path
drops a document silently.

All tests inject fakes — no API calls.
"""
import pytest

from src.pipeline import DocumentPipeline, Verdict


# --- fakes ------------------------------------------------------------------

class FakeExtractor:
    def __init__(self, result=None, raises=None):
        self._result, self._raises = result, raises
        self.calls = 0

    def extract(self, pdf_path, doc_type):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._result


class FakeCorrector:
    def __init__(self, result=None, raises=None):
        self._result, self._raises = result, raises
        self.calls = 0

    def correct(self, extracted, pdf_path, issues, doc_type):
        self.calls += 1
        if self._raises:
            raise self._raises
        return self._result if self._result is not None else extracted


def _valid_invoice() -> dict:
    return {
        "invoice_number": "INV-1001",
        "date": "2026-03-15",
        "vendor_name": "Acme Ltd.",
        "items": [{"quantity": 2, "unit_price": 100.0, "total": 200.0}],
        "subtotal": 200.0,
        "tax_amount": 36.0,
        "total_amount": 236.0,
    }


def _pipe(extract_result=None, raises=None, corrector=None, **kw):
    return DocumentPipeline(
        extractor=FakeExtractor(extract_result, raises),
        corrector=corrector if corrector is not None else FakeCorrector(),
        **kw,
    )


# --- the invariant ----------------------------------------------------------

@pytest.mark.parametrize("broken", [{}, None, [], "", 0, [1, 2, 3], "not a dict"])
def test_any_broken_extraction_is_review_never_ok(broken):
    r = _pipe(broken).process("x.pdf", "invoice")
    assert r.verdict == Verdict.REVIEW
    assert r.reasons, "REVIEW must always carry a reason"
    assert r.data is None


def test_extractor_exception_is_review_and_does_not_propagate():
    r = _pipe(raises=RuntimeError("api exploded")).process("x.pdf", "invoice")
    assert r.verdict == Verdict.REVIEW
    assert r.error and "api exploded" in r.error
    assert any("extraction_error" in x for x in r.reasons)


def test_missing_required_field_is_review():
    bad = _valid_invoice()
    del bad["invoice_number"]
    r = _pipe(bad).process("x.pdf", "invoice")
    assert r.verdict == Verdict.REVIEW
    assert any("invoice_number" in x for x in r.reasons)


def test_clean_document_is_ok_with_no_reasons():
    r = _pipe(_valid_invoice()).process("x.pdf", "invoice")
    assert r.verdict == Verdict.OK
    assert r.reasons == []
    assert r.data["invoice_number"] == "INV-1001"


def test_ok_always_implies_data_present():
    r = _pipe(_valid_invoice()).process("x.pdf", "invoice")
    assert r.verdict != Verdict.OK or r.data is not None


# --- verification / correction behaviour ------------------------------------

def test_rule_auto_corrections_are_applied_to_data():
    bad = _valid_invoice()
    bad["items"][0]["total"] = 999.0            # should be 200
    r = _pipe(bad, corrector=FakeCorrector()).process("x.pdf", "invoice")
    assert r.data["items"][0]["total"] == 200.0


def test_correction_agent_runs_when_issues_found():
    bad = _valid_invoice()
    bad["items"][0]["total"] = 999.0
    corr = FakeCorrector()
    _pipe(bad, corrector=corr).process("x.pdf", "invoice")
    assert corr.calls == 1


def test_correction_agent_not_run_on_a_clean_document():
    corr = FakeCorrector()
    _pipe(_valid_invoice(), corrector=corr).process("x.pdf", "invoice")
    assert corr.calls == 0


def test_correction_agent_can_be_disabled():
    bad = _valid_invoice()
    bad["items"][0]["total"] = 999.0
    corr = FakeCorrector()
    _pipe(bad, corrector=corr, enable_correction=False).process("x.pdf", "invoice")
    assert corr.calls == 0


def test_correction_agent_failure_does_not_crash_and_stays_review():
    bad = _valid_invoice()
    del bad["vendor_name"]                       # critical, unfixable by rules
    r = _pipe(bad, corrector=FakeCorrector(raises=RuntimeError("boom"))).process(
        "x.pdf", "invoice")
    assert r.verdict == Verdict.REVIEW
    assert any("correction_error" in x for x in r.reasons)


def test_correction_that_repairs_the_document_yields_ok():
    bad = _valid_invoice()
    del bad["vendor_name"]
    r = _pipe(bad, corrector=FakeCorrector(result=_valid_invoice())).process(
        "x.pdf", "invoice")
    assert r.verdict == Verdict.OK


# --- serialisation ----------------------------------------------------------

def test_to_dict_round_trip_keeps_verdict_explicit():
    d = _pipe({}).process("x.pdf", "invoice").to_dict()
    assert d["verdict"] == "REVIEW"
    assert isinstance(d["reasons"], list) and d["reasons"]


def test_to_dict_is_json_serialisable():
    import json
    json.dumps(_pipe(_valid_invoice()).process("x.pdf", "invoice").to_dict())


def test_pipeline_does_not_fabricate_a_printed_total():
    """
    P0-4, end to end. arithmetic_repair no longer overwrites a printed total;
    the rule verifier's auto-corrections must not re-introduce the same
    fabrication one layer later, because apply_corrections writes them straight
    into the data.

    Shape taken from the real telecom bill: subtotal and tax do not add up to
    the printed total, because the bill also carries discounts, a carried-over
    balance and a late fee. The right answer is to flag it, not to invent one.
    """
    bill = {
        "invoice_number": "X-1", "date": "2026-07-31", "vendor_name": "V",
        "items": [{"description": "İnternet", "quantity": 1,
                   "unit_price": 990.01, "total": 990.01}],
        "subtotal": 990.01,
        "tax_amount": 141.82,
        "total_amount": 615.43,          # printed on the page, twice
    }
    r = _pipe(bill).process("x.pdf", "invoice")
    assert r.data["total_amount"] == 615.43, (
        f"a printed total was overwritten with {r.data['total_amount']}"
    )
    assert r.data["subtotal"] == 990.01, "a printed subtotal was overwritten"
    assert r.verdict == Verdict.REVIEW, "the mismatch has to reach a human"


def test_raw_snapshot_survives_auto_correction():
    bad = _valid_invoice()
    bad["items"][0]["total"] = 999.0
    r = _pipe(bad).process("x.pdf", "invoice")
    assert r.raw["items"][0]["total"] == 999.0      # untouched extraction
    assert r.data["items"][0]["total"] == 200.0     # repaired


def test_raw_is_none_when_extraction_failed():
    assert _pipe({}).process("x.pdf", "invoice").raw is None


def test_needs_human_tracks_the_verdict():
    assert _pipe({}).process("x.pdf", "invoice").needs_human is True
    assert _pipe(_valid_invoice()).process("x.pdf", "invoice").needs_human is False
