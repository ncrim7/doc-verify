"""Tests for src.matching.po_invoice_matcher — pure stdlib, the product's core."""
from src.matching.po_invoice_matcher import POInvoiceMatcher


def _po(items, **top):
    return {"items": items, "currency": "TRY", "supplier_name": "Acme Ltd.",
            "total_amount": sum(i["total"] for i in items), **top}


def _inv(items, **top):
    return {"items": items, "currency": "TRY", "vendor_name": "Acme Ltd.",
            "total_amount": sum(i["total"] for i in items), **top}


def _line(sku, desc, qty, price):
    return {"sku": sku, "description": desc, "quantity": qty,
            "unit_price": price, "total": round(qty * price, 2)}


M = POInvoiceMatcher()


def test_perfect_match_approves():
    items = [_line("SKU-1", "Ofis Sandalyesi", 10, 3200.0)]
    r = M.match(_po(items), _inv([dict(i) for i in items]))
    assert r["summary"]["overall_status"] == "APPROVE"
    assert r["summary"]["critical_issues"] == 0
    assert r["matches"][0]["discrepancies"] == []
    assert not r["unmatched_po"] and not r["unmatched_invoice"]


def test_unit_price_mismatch_is_critical_and_rejects():
    po = _po([_line("SKU-1", "Masa", 5, 1000.0)])
    inv = _inv([_line("SKU-1", "Masa", 5, 1300.0)])
    r = M.match(po, inv)
    d = r["matches"][0]["discrepancies"]
    assert any(x["field"] == "unit_price" and x["severity"] == "critical" for x in d)
    assert r["summary"]["overall_status"] == "REJECT"


def test_quantity_mismatch_is_critical():
    po = _po([_line("SKU-1", "Masa", 5, 1000.0)])
    inv = _inv([_line("SKU-1", "Masa", 8, 1000.0)])
    r = M.match(po, inv)
    d = r["matches"][0]["discrepancies"]
    assert any(x["field"] == "quantity" and x["severity"] == "critical" for x in d)


def test_extra_invoice_line_is_unmatched_and_triggers_review():
    po = _po([_line("SKU-1", "Masa", 2, 500.0)])
    inv = _inv([_line("SKU-1", "Masa", 2, 500.0),
                _line("SKU-9", "Danışmanlık", 1, 5000.0)])
    r = M.match(po, inv)
    assert len(r["unmatched_invoice"]) == 1
    assert r["summary"]["overall_status"] == "REVIEW"


def test_missing_invoice_line_is_unmatched_po():
    po = _po([_line("SKU-1", "Masa", 2, 500.0),
              _line("SKU-2", "Sandalye", 4, 300.0)])
    inv = _inv([_line("SKU-1", "Masa", 2, 500.0)])
    r = M.match(po, inv)
    assert len(r["unmatched_po"]) == 1
    assert r["summary"]["overall_status"] == "REVIEW"


def test_sku_match_beats_description():
    po = _po([_line("SKU-42", "aaaaaaaa", 1, 10.0)])
    inv = _inv([_line("SKU-42", "zzzzzzzz", 1, 10.0)])
    r = M.match(po, inv)
    assert len(r["matches"]) == 1        # matched on SKU despite different desc


def test_fuzzy_description_match_without_sku():
    po = _po([{"description": "Ofis Sandalyesi Ergonomik", "quantity": 1,
               "unit_price": 100.0, "total": 100.0}])
    inv = _inv([{"description": "Ofis Sandalyesi (Ergonomik)", "quantity": 1,
                 "unit_price": 100.0, "total": 100.0}])
    r = M.match(po, inv)
    assert len(r["matches"]) == 1


def test_currency_alias_tl_equals_try():
    po = _po([_line("SKU-1", "Masa", 1, 100.0)], currency="TL")
    inv = _inv([_line("SKU-1", "Masa", 1, 100.0)], currency="TRY")
    r = M.match(po, inv)
    cur = [c for c in r["scalar_checks"] if c["field"] == "currency"][0]
    assert cur["match"] is True


def test_currency_real_mismatch_is_critical():
    po = _po([_line("SKU-1", "Masa", 1, 100.0)], currency="USD")
    inv = _inv([_line("SKU-1", "Masa", 1, 100.0)], currency="TRY")
    r = M.match(po, inv)
    cur = [c for c in r["scalar_checks"] if c["field"] == "currency"][0]
    assert cur["match"] is False and cur["severity"] == "critical"
    assert r["summary"]["overall_status"] == "REJECT"


def test_vendor_name_diacritics_folded():
    po = _po([_line("SKU-1", "Masa", 1, 100.0)], supplier_name="Tevetoğlu San. Tic.")
    inv = _inv([_line("SKU-1", "Masa", 1, 100.0)], vendor_name="Tevetoglu San. Tic.")
    r = M.match(po, inv)
    v = [c for c in r["scalar_checks"] if c["field"] == "supplier_vendor"][0]
    assert v["match"] is True


def test_total_amount_diff_is_warning_not_critical():
    # invoice total includes VAT, PO total is pre-tax -> expected, must not be critical
    po = _po([_line("SKU-1", "Masa", 1, 1000.0)])                 # total 1000
    inv = _inv([_line("SKU-1", "Masa", 1, 1000.0)], subtotal=1000,
               total_amount=1180)                                  # +18% VAT
    r = M.match(po, inv)
    tot = [c for c in r["scalar_checks"] if c["field"] == "total_amount"][0]
    assert tot["severity"] != "critical"


def test_empty_both_sides_approves():
    r = M.match(_po([]), _inv([]))
    assert r["summary"]["overall_status"] == "APPROVE"
