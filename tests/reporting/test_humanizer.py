"""
Tests for src.reporting.humanizer — turns po_invoice_matcher output into
plain-language "pay / hold / review" advice for a bookkeeping-office user.

The humanizer's contract is a dict shaped like POInvoiceMatcher.match():
    matches[].discrepancies[]  -> per-line issues
    scalar_checks[]            -> currency / total / vendor issues
    unmatched_invoice[]        -> lines billed but not ordered
    unmatched_po[]             -> lines ordered but not billed
    summary.overall_status     -> APPROVE | REVIEW | REJECT
"""
from src.reporting.humanizer import humanize_match


def _clean_match() -> dict:
    return {
        "matches": [
            {"po_item": {}, "invoice_item": {}, "match_score": 1.0, "discrepancies": []}
        ],
        "unmatched_po": [],
        "unmatched_invoice": [],
        "scalar_checks": [
            {"field": "currency", "label": "Para Birimi", "match": True,
             "severity": "ok", "message": "Para birimi uyusuyor"},
        ],
        "summary": {"overall_status": "APPROVE", "match_rate": 1.0,
                    "matched_items": 1, "unmatched_po_items": 0,
                    "unmatched_invoice_items": 0, "critical_issues": 0,
                    "warnings": 0},
    }


def test_clean_match_approves_payment():
    out = humanize_match(_clean_match())
    assert out["has_problems"] is False
    assert out["problems"] == []
    assert "ONAYLAYAB" in out["verdict"]["title"]   # ODEMEYI ONAYLAYABILIRSINIZ
    assert out["verdict"]["icon"] == "✅"        # check mark
    assert isinstance(out["telegram_text"], str) and out["telegram_text"]


def test_scalar_ok_checks_are_ignored():
    out = humanize_match(_clean_match())            # has one severity="ok" check
    assert out["problems"] == []


def test_critical_price_discrepancy_holds_payment():
    m = _clean_match()
    m["matches"][0]["discrepancies"] = [{
        "field": "unit_price", "label": "Birim Fiyat", "po_value": 100,
        "invoice_value": 130, "diff_pct": 30.0, "severity": "critical",
        "message": "Birim Fiyat uyusmuyor: PO=100, Fatura=130 (%30.0 fark)",
    }]
    m["summary"]["overall_status"] = "REJECT"
    m["summary"]["critical_issues"] = 1
    out = humanize_match(m)
    assert out["has_problems"] is True
    p = out["problems"][0]
    assert p["type"] == "PRICE_MISMATCH"
    assert p["severity"] == "HIGH"
    assert "DURDUR" in out["verdict"]["title"]       # ODEME DURDURULDU
    assert out["verdict"]["icon"] == "\U0001f6ab"    # no entry


def test_quantity_discrepancy_maps_to_qty_mismatch():
    m = _clean_match()
    m["matches"][0]["discrepancies"] = [{
        "field": "quantity", "label": "Adet", "po_value": 10, "invoice_value": 12,
        "severity": "critical", "message": "Adet uyusmuyor",
    }]
    m["summary"]["overall_status"] = "REJECT"
    out = humanize_match(m)
    assert out["problems"][0]["type"] == "QTY_MISMATCH"


def test_extra_invoice_line_flagged_not_in_po():
    m = _clean_match()
    m["unmatched_invoice"] = [{"description": "Ekstra danismanlik", "quantity": 1,
                               "total": 5000}]
    m["summary"]["overall_status"] = "REVIEW"
    m["summary"]["unmatched_invoice_items"] = 1
    out = humanize_match(m)
    assert "NOT_IN_PO" in [p["type"] for p in out["problems"]]
    assert "NCELEME" in out["verdict"]["title"]      # INCELEME GEREKLI
    assert out["verdict"]["icon"] == "⚠️"   # warning


def test_missing_po_line_flagged_missing_item():
    m = _clean_match()
    m["unmatched_po"] = [{"description": "Teslim edilmemis kalem", "quantity": 3}]
    m["summary"]["overall_status"] = "REVIEW"
    out = humanize_match(m)
    assert "MISSING_ITEM" in [p["type"] for p in out["problems"]]


def test_currency_mismatch_is_high_severity():
    m = _clean_match()
    m["scalar_checks"] = [{
        "field": "currency", "label": "Para Birimi", "po_value": "USD",
        "invoice_value": "TRY", "match": False, "severity": "critical",
        "message": "Para birimi uyusmuyor: PO=USD, Fatura=TRY",
    }]
    m["summary"]["overall_status"] = "REJECT"
    out = humanize_match(m)
    p = next(x for x in out["problems"] if x["type"] == "CURRENCY_MISMATCH")
    assert p["severity"] == "HIGH"


def test_total_amount_warning_is_medium():
    m = _clean_match()
    m["scalar_checks"] = [{
        "field": "total_amount", "label": "Genel Toplam", "po_value": 1000,
        "invoice_value": 1180, "match": False, "severity": "warning",
        "message": "Toplam uyusmuyor (KDV farki olabilir)",
    }]
    m["summary"]["overall_status"] = "REVIEW"
    out = humanize_match(m)
    p = next(x for x in out["problems"] if x["type"] == "TOTAL_MISMATCH")
    assert p["severity"] == "MEDIUM"


def test_unknown_field_falls_back_to_other():
    m = _clean_match()
    m["matches"][0]["discrepancies"] = [{
        "field": "weird_new_field", "label": "?", "severity": "warning",
        "message": "x",
    }]
    m["summary"]["overall_status"] = "REVIEW"
    out = humanize_match(m)
    assert out["problems"][0]["type"] == "OTHER"
    assert out["problems"][0]["severity"] == "MEDIUM"


def test_every_problem_has_action_text():
    m = _clean_match()
    m["matches"][0]["discrepancies"] = [
        {"field": "unit_price", "label": "Birim Fiyat", "severity": "critical",
         "message": "x"},
        {"field": "description", "label": "Aciklama", "severity": "warning",
         "message": "y"},
    ]
    m["unmatched_invoice"] = [{"description": "z"}]
    m["summary"]["overall_status"] = "REJECT"
    out = humanize_match(m)
    assert out["problems"]
    for p in out["problems"]:
        assert p["action"] and isinstance(p["action"], str)
        assert p["label"] and p["type"]
