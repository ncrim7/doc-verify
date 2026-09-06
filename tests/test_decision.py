"""
Tests for the decision layer — the first module that answers "should I pay
this?" rather than "is the JSON right?".

The line between the three verdicts is WHOSE money:
    HOLD    two sources disagree about an amount — the supplier is asking for
            more than was agreed
    REVIEW  something is wrong that cannot be attributed to the supplier
    PAY     nothing critical
"""
import pytest

from src.decision.engine import (
    Decision, Finding, Verdict, decide, from_po_match, from_verification,
)
from src.reporting.humanizer import humanize_decision


def _f(kind="X", severity="critical", impact=None, fld="f", financial=True,
       line=None):
    """A finding. `financial` defaults True so the verdict tests read simply;
    the split between financial and internal has its own class below, as does
    the rule that stops one discrepancy being counted from several angles."""
    return Finding(kind=kind, severity=severity, field=fld,
                   source="po_match" if financial else "document",
                   message="m", amount_try=impact, is_financial=financial,
                   line=line)


class TestVerdictRule:
    def test_a_priced_critical_holds_the_payment(self):
        assert decide([_f(impact=340.0)]).verdict is Verdict.HOLD

    def test_an_unpriceable_critical_goes_to_review(self):
        assert decide([_f(impact=None)]).verdict is Verdict.REVIEW

    def test_nothing_found_means_pay(self):
        assert decide([]).verdict is Verdict.PAY

    def test_warnings_alone_never_hold_a_payment(self):
        # a product that stops on every warning gets switched off
        d = decide([_f(severity="warning", impact=500.0),
                    _f(severity="info", impact=20.0)])
        assert d.verdict is Verdict.PAY

    def test_a_rounding_sized_impact_is_not_priced(self):
        # 0.4 kuruş is not a reason to hold a payment
        assert decide([_f(impact=0.004)]).verdict is Verdict.REVIEW

    def test_findings_on_separate_lines_add_up(self):
        d = decide([_f(impact=340.0, line=0), _f(impact=-40.0, line=1),
                    _f(impact=None, line=2)])
        assert d.financial_impact_try == pytest.approx(300.0)

    def test_findings_at_document_level_do_not_add_up(self):
        # they are views of one discrepancy, not separate ones — see
        # TestTheSameMoneyIsCountedOnce
        d = decide([_f(impact=340.0), _f(impact=-40.0)])
        assert d.financial_impact_try == 340.0

    def test_a_negative_impact_still_holds(self):
        # under-invoiced is also wrong, and the sign is information
        d = decide([_f(impact=-340.0)])
        assert d.verdict is Verdict.HOLD
        assert d.financial_impact_try == -340.0


class TestWhoseMoney:
    """
    The distinction a real telecom bill taught. A subtotal that does not
    reconcile is a discrepancy, but on ONE document there is no way to tell
    whether the supplier billed wrongly or we misread the page — and on that
    bill, it was us. Reporting it as "financial impact" tells a bookkeeper
    their supplier overcharged them, which is a lie dressed as a number.
    """

    def test_an_internal_mismatch_does_not_hold_a_payment(self):
        d = decide([_f(impact=-516.57, financial=False)])
        assert d.verdict is Verdict.REVIEW
        assert d.financial_impact_try == 0.0
        assert d.internal_discrepancy_try == pytest.approx(-516.57)

    def test_a_cross_source_mismatch_does(self):
        d = decide([_f(impact=340.0, financial=True)])
        assert d.verdict is Verdict.HOLD
        assert d.financial_impact_try == 340.0
        assert d.internal_discrepancy_try == 0.0

    def test_the_two_totals_never_mix(self):
        d = decide([_f(impact=340.0, financial=True),
                    _f(impact=-516.57, financial=False)])
        assert d.financial_impact_try == 340.0
        assert d.internal_discrepancy_try == pytest.approx(-516.57)
        assert d.verdict is Verdict.HOLD

    def test_the_wording_does_not_blame_the_supplier_for_our_reading(self):
        h = humanize_decision(decide([_f(kind="SUBTOTAL_MISMATCH",
                                         impact=-516.57, financial=False)]))
        assert "Fazla faturalanan" not in h["text"]
        assert "Belge içi tutarsızlık" in h["text"]
        assert "bizim" in h["text"]      # names our own misreading as a cause

    def test_the_real_telecom_bill_is_review_not_hold(self):
        # real_004: subtotal and total do not reconcile because our extraction
        # misread a charge-line bill, not because TTNET overcharged
        v = {"issues": [
            {"rule": "subtotal_mismatch", "field": "subtotal",
             "severity": "critical", "message": "m",
             "expected": 1132.0, "actual": 615.43},
            {"rule": "total_mismatch", "field": "total_amount",
             "severity": "critical", "message": "m",
             "expected": 757.25, "actual": 615.5},
        ]}
        d = decide(from_verification(v))
        assert d.verdict is Verdict.REVIEW
        assert d.financial_impact_try == 0.0


class TestTheSameMoneyIsCountedOnce:
    """
    One 3,40 TL price difference over 100 units appears three times: as a price
    mismatch (340), as the line total being 340 higher, and inside the document
    total being 1.408 higher (340 plus VAT). Summing them gives 2.088 TL and
    tells a bookkeeper their supplier overcharged by six times the real figure.
    """

    def _brief_scenario(self):
        return {
            "matches": [{
                "po_item": {"quantity": 100, "unit_price": 50.00},
                "invoice_item": {"quantity": 100, "unit_price": 53.40},
                "discrepancies": [
                    {"field": "unit_price", "po_value": 50.00,
                     "invoice_value": 53.40, "severity": "critical",
                     "message": "m"},
                    {"field": "total", "po_value": 5000.0,
                     "invoice_value": 5340.0, "severity": "critical",
                     "message": "m"},
                ]}],
            "scalar_checks": [{"field": "total_amount", "po_value": 5000.0,
                               "invoice_value": 6408.0,
                               "severity": "critical", "message": "m"}],
        }

    def test_the_brief_scenario_reports_340_not_2088(self):
        d = decide(from_po_match(self._brief_scenario()))
        assert d.verdict is Verdict.HOLD
        assert d.financial_impact_try == 340.0

    def test_two_lines_add_up(self):
        m = {"matches": [
            {"po_item": {"quantity": 10, "unit_price": 10.0},
             "invoice_item": {"quantity": 10, "unit_price": 11.0},
             "discrepancies": [{"field": "unit_price", "po_value": 10.0,
                                "invoice_value": 11.0,
                                "severity": "critical", "message": "m"}]},
            {"po_item": {"quantity": 5, "unit_price": 20.0},
             "invoice_item": {"quantity": 5, "unit_price": 24.0},
             "discrepancies": [{"field": "unit_price", "po_value": 20.0,
                                "invoice_value": 24.0,
                                "severity": "critical", "message": "m"}]},
        ]}
        # 10x1 + 5x4 = 30, two separate discrepancies on two separate lines
        assert decide(from_po_match(m)).financial_impact_try == 30.0

    def test_a_document_level_figure_is_used_when_there_are_no_lines(self):
        m = {"scalar_checks": [{"field": "total_amount", "po_value": 5000.0,
                                "invoice_value": 5340.0,
                                "severity": "critical", "message": "m"}]}
        assert decide(from_po_match(m)).financial_impact_try == 340.0

    def test_internal_discrepancies_are_deduplicated_the_same_way(self):
        # real_004 raises both a subtotal and a total mismatch describing one
        # misreading; reporting their sum would overstate it
        v = {"issues": [
            {"rule": "subtotal_mismatch", "field": "subtotal",
             "severity": "critical", "message": "m",
             "expected": 1132.0, "actual": 615.43},
            {"rule": "total_mismatch", "field": "total_amount",
             "severity": "critical", "message": "m",
             "expected": 757.25, "actual": 615.5},
        ]}
        d = decide(from_verification(v))
        assert d.internal_discrepancy_try == pytest.approx(-516.57)


class TestCoverageIsNotConfidence:
    """
    `confidence` reports what could be checked, never a probability. 58% of
    field errors measured on real documents sit in fields nothing can check,
    and a verdict of PAY has to be honest about that.
    """

    def test_it_names_the_fields_nothing_can_check(self):
        d = decide([], {"invoice_number": "INV-1", "vendor_name": "V",
                        "total_amount": 100.0, "tax_amount": 18.0})
        assert d.unverifiable_fields == ["invoice_number", "vendor_name"]
        assert d.checked_fields == 2
        assert d.confidence == 0.5

    def test_empty_values_count_as_neither(self):
        d = decide([], {"invoice_number": None, "total_amount": 100.0})
        assert d.unverifiable_fields == [] and d.checked_fields == 1

    def test_no_extraction_gives_zero_rather_than_a_crash(self):
        assert decide([]).confidence == 0.0

    def test_items_and_metadata_are_not_counted(self):
        d = decide([], {"doc_type": "invoice", "language": "tr",
                        "_source": "x", "items": [1, 2], "total_amount": 5})
        assert d.checked_fields == 1 and d.unverifiable_fields == []


class TestFromVerification:
    def test_an_arithmetic_mismatch_is_priced(self):
        v = {"issues": [{"rule": "total_mismatch", "field": "total_amount",
                         "severity": "critical", "message": "m",
                         "expected": 1131.83, "actual": 615.43}]}
        f = from_verification(v)[0]
        assert f.kind == "TOTAL_MISMATCH"
        assert f.amount_try == pytest.approx(615.43 - 1131.83)
        assert f.is_financial is False, "one document cannot blame the supplier"

    def test_a_bad_tax_id_is_real_but_not_priced(self):
        v = {"issues": [{"rule": "tax_id_checksum_invalid",
                         "field": "buyer_tax_id", "severity": "critical",
                         "message": "m", "value": "11111111111"}]}
        f = from_verification(v)[0]
        assert f.kind == "TAX_ID_INVALID"
        assert f.amount_try is None and f.is_priced is False

    def test_the_real_bogus_tax_id_document_goes_to_review_not_hold(self):
        # real_006: a perfect extraction of a document carrying 11111111111.
        # Nothing to price, but a person must see it.
        v = {"issues": [{"rule": "tax_id_checksum_invalid",
                         "field": "buyer_tax_id", "severity": "critical",
                         "message": "m"}]}
        assert decide(from_verification(v)).verdict is Verdict.REVIEW

    def test_an_unknown_rule_still_becomes_a_finding(self):
        f = from_verification({"issues": [{"rule": "brand_new_rule",
                                           "severity": "warning"}]})[0]
        assert f.kind == "BRAND_NEW_RULE" and f.amount_try is None

    def test_no_verification_is_no_findings(self):
        assert from_verification({}) == [] and from_verification(None) == []


class TestFromPoMatch:
    """The scenario from the product brief, priced."""

    def _match(self):
        return {"matches": [{
            "po_item": {"quantity": 100, "unit_price": 50.00},
            "invoice_item": {"quantity": 100, "unit_price": 53.40},
            "discrepancies": [{"field": "unit_price", "label": "Birim Fiyat",
                               "po_value": 50.00, "invoice_value": 53.40,
                               "severity": "critical",
                               "message": "Birim Fiyat uyuşmuyor"}],
        }]}

    def test_a_price_gap_is_multiplied_by_the_quantity(self):
        # 3,40 TL per unit is an argument; over 100 units it is 340 TL and a
        # reason not to pay
        f = from_po_match(self._match())[0]
        assert f.kind == "PRICE_MISMATCH"
        assert f.amount_try == pytest.approx(340.0)
        assert f.is_financial is True

    def test_that_scenario_holds_the_payment(self):
        d = decide(from_po_match(self._match()))
        assert d.verdict is Verdict.HOLD
        assert d.financial_impact_try == pytest.approx(340.0)

    def test_a_quantity_gap_is_multiplied_by_the_price(self):
        m = {"matches": [{
            "po_item": {"quantity": 100, "unit_price": 50.0},
            "invoice_item": {"quantity": 103, "unit_price": 50.0},
            "discrepancies": [{"field": "quantity", "po_value": 100,
                               "invoice_value": 103, "severity": "critical",
                               "message": "m"}]}]}
        assert from_po_match(m)[0].amount_try == pytest.approx(150.0)

    def test_an_extra_invoice_line_is_priced_at_its_own_total(self):
        m = {"unmatched_invoice": [{"description": "Kargo", "total": 75.0}]}
        f = from_po_match(m)[0]
        assert f.kind == "NOT_IN_PO" and f.amount_try == 75.0

    def test_a_missing_po_line_is_a_warning_with_no_price(self):
        m = {"unmatched_po": [{"description": "Masa"}]}
        f = from_po_match(m)[0]
        assert f.kind == "MISSING_ITEM" and f.severity == "warning"
        assert f.amount_try is None

    def test_a_tax_rate_difference_is_not_priced_into_a_hold(self):
        # PO %20 vs invoice %10 is worth seeing; the matcher keeps it a
        # warning because tax differences between a PO and an invoice are
        # ordinary, and a warning never holds a payment
        m = {"scalar_checks": [{"field": "tax_rate", "po_value": 0.20,
                                "invoice_value": 0.10, "severity": "warning",
                                "message": "m"}]}
        assert decide(from_po_match(m)).verdict is Verdict.PAY

    def test_an_empty_match_is_no_findings(self):
        assert from_po_match({}) == [] and from_po_match(None) == []


class TestHumanizeDecision:
    def test_it_leads_with_the_money(self):
        d = decide([Finding(kind="PRICE_MISMATCH", severity="critical",
                            field="unit_price", source="po_match",
                            message="m", expected=50.0, actual=53.40,
                            amount_try=340.0, is_financial=True)])
        h = humanize_decision(d)
        assert h["verdict"] == "HOLD"
        assert h["financial_impact_text"] == "340,00 TL"
        assert "ÖDEMEYİ BEKLETİN" in h["text"]
        assert "340,00 TL" in h["text"]
        assert "Fiyat Farkı" in h["text"]
        assert "Tedarikçiyle fiyat mutabakatı" in h["text"]

    def test_turkish_thousands_formatting(self):
        d = decide([_f(impact=1073.69)])
        assert humanize_decision(d)["financial_impact_text"] == "1.073,69 TL"

    def test_priced_problems_come_first(self):
        d = decide([_f(kind="TAX_ID_INVALID", impact=None),
                    _f(kind="PRICE_MISMATCH", impact=340.0)])
        assert humanize_decision(d)["problems"][0]["kind"] == "PRICE_MISMATCH"

    def test_a_clean_document_says_so_without_inventing_certainty(self):
        d = decide([], {"total_amount": 100.0, "invoice_number": "INV-1"})
        h = humanize_decision(d)
        assert h["verdict"] == "PAY"
        assert "ÖDEYEBİLİRSİNİZ" in h["text"]
        # and it still admits what it could not see
        assert "invoice_number" in h["blind_spots"]
        assert "Denetlenemeyen alanlar" in h["text"]

    def test_blind_spots_are_stated_even_on_a_hold(self):
        d = decide([_f(impact=340.0)], {"vendor_name": "V"})
        assert "vendor_name" in humanize_decision(d)["blind_spots"]

    def test_it_accepts_a_plain_dict_too(self):
        h = humanize_decision(decide([_f(impact=10.0)]).to_dict())
        assert h["verdict"] == "HOLD"
