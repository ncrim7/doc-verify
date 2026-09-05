"""
Tests for src.pipeline — the safety net.

The invariant under test: the pipeline never returns OK unless extraction
produced a non-empty, structurally valid result AND verification found no
critical issue. Every failure path yields REVIEW with a reason. No code path
drops a document silently.

All tests inject fakes — no API calls.
"""
import json

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

def test_apply_corrections_still_writes_what_it_is_given():
    # No rule produces an auto-correction any more (P0-4, P0-7), so this
    # exercises the mechanism directly. It survives for format normalisation --
    # '15/03/2026' -> '2026-03-15' changes representation, not meaning -- and
    # must never again carry a derived value.
    from src.pipeline import apply_corrections
    d = {"date": "15/03/2026", "items": [{"total": 999.0}]}
    out = apply_corrections(d, {"date": "2026-03-15", "items[0].total": 200.0})
    assert out["date"] == "2026-03-15"
    assert out["items"][0]["total"] == 200.0
    assert d["date"] == "15/03/2026", "the input must not be mutated"


def test_a_wrong_item_total_reaches_a_human_instead_of_being_rewritten():
    bad = _valid_invoice()
    bad["items"][0]["total"] = 999.0
    r = _pipe(bad, corrector=FakeCorrector()).process("x.pdf", "invoice")
    assert r.data["items"][0]["total"] == 999.0, "a printed total was overwritten"
    assert r.verdict == Verdict.REVIEW


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


def test_raw_snapshot_is_taken_before_anything_touches_the_data():
    bad = _valid_invoice()
    del bad["vendor_name"]
    repaired = _valid_invoice()
    r = _pipe(bad, corrector=FakeCorrector(result=repaired)).process(
        "x.pdf", "invoice")
    assert "vendor_name" not in r.raw, "raw must be the extraction as returned"
    assert r.data["vendor_name"] == "Acme Ltd."


def test_raw_is_none_when_extraction_failed():
    assert _pipe({}).process("x.pdf", "invoice").raw is None


def test_needs_human_tracks_the_verdict():
    assert _pipe({}).process("x.pdf", "invoice").needs_human is True
    assert _pipe(_valid_invoice()).process("x.pdf", "invoice").needs_human is False


# --- the correction agent is not allowed to rewrite the document ------------
#
# Measured 2026-09-03: the agent was told to return every field and the merge
# took every non-null value, so it re-rolled whole documents on any issue.
# It ran on 39 of 60, damaged 8, improved 2, and destroyed four perfect
# extractions — including turning 'Post-it Not Bloğu' into 'Blogu' on a
# document whose only complaint was a tax id.

class RecordingCorrector:
    """Returns a fixed result and remembers what it was asked to fix."""

    def __init__(self, result=None):
        self._result = result
        self.calls = 0
        self.issues_seen = None

    def correct(self, extracted, pdf_path, issues, doc_type):
        self.calls += 1
        self.issues_seen = issues
        return self._result if self._result is not None else extracted


def _tax_id_invoice(**kw):
    d = _valid_invoice()
    d["currency"] = "TRY"
    d.update(kw)
    return d


class TestCorrectionIsConstrained:
    def test_a_failed_check_digit_is_never_sent_to_the_agent(self):
        # there is no way to derive the right digits, and asking for them is
        # what produced the fabrication: the model rewrote the last digit until
        # the checksum passed
        corr = RecordingCorrector()
        r = _pipe(_tax_id_invoice(vendor_tax_id="1234576890"),
                  corrector=corr).process("x.pdf", "invoice")
        assert corr.calls == 0
        assert r.verdict == Verdict.REVIEW
        assert any("tax_id_checksum_invalid" in x for x in r.reasons)

    def test_a_warning_does_not_trigger_an_llm_pass(self):
        # the old trigger was "any issue at all", info included
        corr = RecordingCorrector()
        _pipe(_tax_id_invoice(), corrector=corr).process("x.pdf", "invoice")
        assert corr.calls == 0, "a missing seller tax id is a warning, not a job"

    def test_a_correctable_critical_issue_still_triggers_it(self):
        bad = _valid_invoice()
        bad["items"][0]["total"] = 999.0
        corr = RecordingCorrector()
        _pipe(bad, corrector=corr).process("x.pdf", "invoice")
        assert corr.calls == 1

    def test_only_the_correctable_issues_are_handed_over(self):
        bad = _tax_id_invoice(vendor_tax_id="1234576890")
        del bad["invoice_number"]          # critical AND correctable
        corr = RecordingCorrector()
        _pipe(bad, corrector=corr).process("x.pdf", "invoice")
        assert corr.calls == 1
        rules = {i["rule"] for i in corr.issues_seen}
        assert "required_field_missing" in rules
        assert "tax_id_checksum_invalid" not in rules

    def test_a_change_to_an_unflagged_field_becomes_a_review_reason(self):
        bad = _valid_invoice()
        del bad["invoice_number"]                       # the only complaint
        rewritten = _valid_invoice()
        rewritten["vendor_name"] = "Acme Ltd"           # nobody asked for this
        r = _pipe(bad, corrector=RecordingCorrector(rewritten)).process(
            "x.pdf", "invoice")
        assert any("unrequested_correction: vendor_name" in x for x in r.reasons)
        assert r.verdict == Verdict.REVIEW

    def test_an_unrequested_item_rewrite_is_caught_too(self):
        # the real shape: asked about a total, changed a description
        bad = _valid_invoice()
        bad["items"][0]["total"] = 999.0
        rewritten = _valid_invoice()
        rewritten["items"][0]["description"] = "Post-it Not Blogu"
        r = _pipe(bad, corrector=RecordingCorrector(rewritten)).process(
            "x.pdf", "invoice")
        assert any("items[0].description" in x for x in r.reasons)

    def test_fixing_exactly_what_was_flagged_raises_nothing(self):
        bad = _valid_invoice()
        del bad["vendor_name"]
        r = _pipe(bad, corrector=RecordingCorrector(_valid_invoice())).process(
            "x.pdf", "invoice")
        assert not any("unrequested_correction" in x for x in r.reasons)
        assert r.verdict == Verdict.OK


class TestChangedFields:
    def test_detects_nested_and_item_paths(self):
        from src.pipeline import _changed_fields
        a = {"x": 1, "items": [{"d": "a", "t": 1.0}]}
        b = {"x": 1, "items": [{"d": "b", "t": 1.0}]}
        assert _changed_fields(a, b) == ["items[0].d"]

    def test_added_and_removed_keys_both_count(self):
        from src.pipeline import _changed_fields
        assert _changed_fields({"a": 1}, {"a": 1, "b": 2}) == ["b"]
        assert _changed_fields({"a": 1, "b": 2}, {"a": 1}) == ["b"]

    def test_identical_documents_have_no_diff(self):
        from src.pipeline import _changed_fields
        d = _valid_invoice()
        assert _changed_fields(d, json.loads(json.dumps(d))) == []
