"""Tests for src.verification.rule_based_verifier — pure, deterministic."""
import pytest

from src.verification.rule_based_verifier import RuleBasedVerifier

V = RuleBasedVerifier()


def _clean_invoice():
    return {
        "invoice_number": "INV-1001",
        "date": "2026-03-15",
        "vendor_name": "Acme Ltd.",
        "items": [{"quantity": 2, "unit_price": 100.0, "total": 200.0},
                  {"quantity": 1, "unit_price": 50.0, "total": 50.0}],
        "subtotal": 250.0,
        "tax_amount": 45.0,
        "total_amount": 295.0,
    }


def test_clean_invoice_is_valid_no_issues():
    r = V.verify(_clean_invoice(), "invoice")
    assert r["valid"] is True
    assert r["issues"] == []
    assert r["auto_corrections"] == {}
    assert r["score"] == 1.0


def test_missing_required_field_is_critical():
    d = _clean_invoice()
    del d["invoice_number"]
    r = V.verify(d, "invoice")
    assert r["valid"] is False
    assert any(i["rule"] == "required_field_missing" and i["field"] == "invoice_number"
               and i["severity"] == "critical" for i in r["issues"])


def test_empty_items_list_is_critical():
    d = _clean_invoice()
    d["items"] = []
    r = V.verify(d, "invoice")
    assert r["valid"] is False


def test_bad_date_format_is_warning():
    d = _clean_invoice()
    d["date"] = "15/03/2026"
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "date_format_invalid" and i["severity"] == "warning"
               for i in r["issues"])


def test_implausible_year_is_warning():
    d = _clean_invoice()
    d["date"] = "1970-01-01"
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "date_implausible" for i in r["issues"])


def test_wrong_item_total_yields_critical_and_autocorrection():
    d = _clean_invoice()
    d["items"][0]["total"] = 999.0                 # should be 200
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "item_total_mismatch" and i["severity"] == "critical"
               for i in r["issues"])
    assert r["auto_corrections"].get("items[0].total") == 200.0


# P0-4: a total or subtotal printed on the page is evidence; the sum we compute
# is inference. The verifier still raises the mismatch as critical — so the
# document reaches a human — but it must not hand back a correction, because
# apply_corrections would write the invented number straight into the data.
# On a real telecom bill this replaced a figure printed twice on the page.

def test_subtotal_mismatch_is_flagged_but_not_autocorrected():
    d = _clean_invoice()
    d["subtotal"] = 111.0
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "subtotal_mismatch" and i["severity"] == "critical"
               for i in r["issues"])
    assert "subtotal" not in r["auto_corrections"]
    assert r["valid"] is False


def test_total_mismatch_is_flagged_but_not_autocorrected():
    d = _clean_invoice()
    d["total_amount"] = 111.0
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "total_mismatch" and i["severity"] == "critical"
               for i in r["issues"])
    assert "total_amount" not in r["auto_corrections"]
    assert r["valid"] is False


def test_negative_amount_is_warning():
    d = _clean_invoice()
    d["tax_amount"] = -45.0
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "negative_amount" for i in r["issues"])


def test_non_numeric_amount_is_warning():
    d = _clean_invoice()
    d["total_amount"] = "çok para"
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "numeric_invalid" for i in r["issues"])


def test_implausible_tax_rate_is_warning():
    d = _clean_invoice()
    d["tax_amount"] = 200.0            # 200/250 = 80% > 50%
    d["total_amount"] = 450.0
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "tax_rate_implausible" for i in r["issues"])


def test_too_many_items_is_info():
    d = _clean_invoice()
    d["items"] = [{"quantity": 1, "unit_price": 1.0, "total": 1.0} for _ in range(60)]
    d["subtotal"] = 60.0
    d["tax_amount"] = 0.0
    d["total_amount"] = 60.0
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "too_many_items" and i["severity"] == "info"
               for i in r["issues"])


def test_po_total_mismatch_is_flagged_but_not_autocorrected():
    d = {"po_number": "PO-1", "date": "2026-03-15", "supplier_name": "S",
         "items": [{"quantity": 2, "unit_price": 10.0, "total": 20.0}],
         "total_amount": 999.0}
    r = V.verify(d, "po")
    assert any(i["rule"] == "total_mismatch" for i in r["issues"])
    assert "total_amount" not in r["auto_corrections"]


def test_item_level_corrections_are_kept():
    # the one repair with real corroboration: qty and unit_price are two
    # independent values confirming one derived one
    d = _clean_invoice()
    d["items"][0]["total"] = 999.0
    r = V.verify(d, "invoice")
    assert r["auto_corrections"]["items[0].total"] == 200.0


def test_receipt_subtotal_mismatch_flags_but_does_not_autocorrect():
    d = {"receipt_number": "R-1", "date": "2026-03-15", "store_name": "Migros",
         "items": [{"quantity": 1, "unit_price": 10.0, "total": 10.0}],
         "subtotal": 999.0, "tax_amount": 1.8, "total_amount": 11.8}
    r = V.verify(d, "receipt")
    assert any(i["rule"] == "subtotal_mismatch" for i in r["issues"])
    assert "subtotal" not in r["auto_corrections"]      # receipt asymmetry


def test_score_drops_with_severity():
    d = _clean_invoice()
    del d["invoice_number"]            # 1 critical
    r = V.verify(d, "invoice")
    assert r["score"] == pytest.approx(0.80, abs=1e-9)
