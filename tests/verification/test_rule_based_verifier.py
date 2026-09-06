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


class TestLineDiscount:
    """
    UBL-TR carries a line discount as an AllowanceCharge with
    ChargeIndicator=false, and LineExtensionAmount is the amount after it. The
    identity is quantity × unit_price − discount = total.

    Without the discount term the check was a false-alarm generator on real
    invoices: every discounted line failed it, correctly in arithmetic and
    uselessly in practice, because the schema could not say why. real_008 is
    the case — 1 × 69,4167 with a 70% discount of 48,59, giving 20,83.
    """

    def _inv(self, item):
        return {"invoice_number": "INV-1", "date": "2026-03-15",
                "vendor_name": "V", "items": [item],
                "subtotal": item.get("total"), "tax_amount": 0.0,
                "total_amount": item.get("total")}

    def test_the_real_discounted_line_now_reconciles(self):
        r = V.verify(self._inv({"quantity": 1, "unit_price": 69.4167,
                                "discount": 48.59, "total": 20.83}), "invoice")
        assert not any(i["rule"] == "item_total_mismatch" for i in r["issues"])
        assert r["valid"] is True

    def test_without_the_discount_field_the_same_line_still_flags(self):
        # the pre-discount behaviour, kept as a check that the term is what
        # changed rather than the tolerance
        r = V.verify(self._inv({"quantity": 1, "unit_price": 69.4167,
                                "total": 20.83}), "invoice")
        assert any(i["rule"] == "item_total_mismatch" for i in r["issues"])

    def test_a_wrong_total_still_flags_even_with_a_discount(self):
        r = V.verify(self._inv({"quantity": 1, "unit_price": 69.4167,
                                "discount": 48.59, "total": 30.00}), "invoice")
        issue = next(i for i in r["issues"] if i["rule"] == "item_total_mismatch")
        assert issue["expected"] == 20.83
        assert "−48.59" in issue["message"]

    def test_zero_discount_behaves_like_none(self):
        a = V.verify(self._inv({"quantity": 2, "unit_price": 10.0,
                                "discount": 0, "total": 20.0}), "invoice")
        b = V.verify(self._inv({"quantity": 2, "unit_price": 10.0,
                                "total": 20.0}), "invoice")
        assert a["issues"] == b["issues"] == []

    def test_a_negative_discount_is_a_warning(self):
        # UBL-TR carries an increase as a separate ChargeIndicator=true and our
        # schema has no field for one, so a negative here is a sign error
        r = V.verify(self._inv({"quantity": 1, "unit_price": 10.0,
                                "discount": -5.0, "total": 15.0}), "invoice")
        assert any(i["rule"] == "negative_discount" and i["severity"] == "warning"
                   for i in r["issues"])

    def test_a_non_numeric_discount_is_ignored_not_crashed(self):
        r = V.verify(self._inv({"quantity": 2, "unit_price": 10.0,
                                "discount": "yok", "total": 20.0}), "invoice")
        assert not any(i["rule"] == "item_total_mismatch" for i in r["issues"])

    def test_no_auto_correction_is_offered_for_a_discounted_line(self):
        r = V.verify(self._inv({"quantity": 1, "unit_price": 69.4167,
                                "discount": 48.59, "total": 30.00}), "invoice")
        assert r["auto_corrections"] == {}


def test_wrong_item_total_is_critical_but_not_autocorrected():
    # P0-7: quantity x unit_price is inference, the printed line total is
    # evidence. On a real invoice a 70% discount sat between them and writing
    # the product back replaced 20,83 with 69,42.
    d = _clean_invoice()
    d["items"][0]["total"] = 999.0
    r = V.verify(d, "invoice")
    assert any(i["rule"] == "item_total_mismatch" and i["severity"] == "critical"
               for i in r["issues"])
    assert "items[0].total" not in r["auto_corrections"]
    assert r["valid"] is False, "the mismatch has to reach a human"


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


def test_no_rule_produces_an_auto_correction_any_more():
    """
    The provenance principle removed them one by one: subtotal and
    total_amount in P0-4, item totals in P0-7. What is left is the honest end
    state -- there is no value we can compute that we are entitled to write
    over what the document prints.

    The mechanism survives for FORMAT NORMALISATION, which is safe: turning
    '15/03/2026' into '2026-03-15' changes representation, not meaning. It must
    never again carry a DERIVED VALUE. If this test starts failing, that is the
    question to answer before making it pass.
    """
    for doc_type, d in (
        ("invoice", {**_clean_invoice(), "items": [{"quantity": 2,
                                                    "unit_price": 100.0,
                                                    "total": 999.0}],
                     "subtotal": 111.0, "total_amount": 222.0}),
        ("po", {"po_number": "PO-1", "date": "2026-03-15", "supplier_name": "S",
                "items": [{"quantity": 2, "unit_price": 10.0, "total": 999.0}],
                "total_amount": 777.0}),
        ("receipt", {"receipt_number": "R-1", "date": "2026-03-15",
                     "store_name": "M",
                     "items": [{"quantity": 1, "unit_price": 10.0,
                                "total": 999.0}],
                     "subtotal": 111.0, "tax_amount": 1.8,
                     "total_amount": 222.0}),
    ):
        r = V.verify(d, doc_type)
        assert r["auto_corrections"] == {}, (
            f"{doc_type} produced {r['auto_corrections']} -- is it a format "
            f"normalisation, or a derived value?"
        )
        assert r["valid"] is False, f"{doc_type} must still flag the mismatch"


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


# P1-6: tax id check digits. Every case mirrors a situation met on the real
# pilot documents on 2026-09-03; the digits are synthetic stand-ins, because
# the real ones identify real people and companies and this repo is public.
# They live in data/real/gt/, which is gitignored.

class TestTaxIdIntegration:
    def _with(self, **kw):
        d = _clean_invoice()
        d.update(kw)
        return d

    def test_valid_vkn_raises_nothing(self):
        r = V.verify(self._with(vendor_tax_id="1234567890"), "invoice")
        assert not any(i["field"] == "vendor_tax_id" for i in r["issues"])
        assert r["valid"] is True

    def test_transposed_vkn_is_critical_and_forces_review(self):
        r = V.verify(self._with(vendor_tax_id="1234576890"), "invoice")
        assert any(i["rule"] == "tax_id_checksum_invalid"
                   and i["severity"] == "critical" for i in r["issues"])
        assert r["valid"] is False, "a provably wrong tax id must reach a human"

    def test_dropped_digit_tckn_is_caught(self):
        r = V.verify(self._with(buyer_tax_id="1111111111"), "invoice")
        assert any(i["rule"] == "tax_id_checksum_invalid" for i in r["issues"])

    def test_printed_placeholder_is_caught(self):
        # a pilot invoice prints 'TCKN: 11111111111' — a seller's placeholder.
        # True positive, not a false one.
        r = V.verify(self._with(buyer_tax_id="11111111111"), "invoice")
        assert any(i["rule"] == "tax_id_checksum_invalid" for i in r["issues"])

    def test_foreign_tax_number_is_info_not_critical(self):
        # a 9-digit foreign registration in the pilot set must not block a doc
        r = V.verify(self._with(vendor_tax_id="701236788"), "invoice")
        ids = [i for i in r["issues"] if i["field"] == "vendor_tax_id"]
        assert ids and ids[0]["rule"] == "tax_id_unverifiable"
        assert ids[0]["severity"] == "info"
        assert r["valid"] is True

    def test_a_captured_label_is_a_warning_not_info(self):
        # the real case: the model returned 'TR TIN <digits>' for buyer_tax_id
        # and the document went out OK with a malformed id in it
        r = V.verify(self._with(buyer_tax_id="TR TIN 12345678950"), "invoice")
        issue = next(i for i in r["issues"] if i["field"] == "buyer_tax_id")
        assert issue["rule"] == "tax_id_label_captured"
        assert issue["severity"] == "warning"
        assert issue["suggested"] == "12345678950"
        assert r["valid"] is True, "malformed, but nothing is provably wrong"

    def test_non_numeric_tax_id_is_info(self):
        r = V.verify(self._with(vendor_tax_id="Boğaziçi Kurumlar V.D."), "invoice")
        assert any(i["rule"] == "tax_id_unverifiable" for i in r["issues"])
        assert r["valid"] is True

    def test_punctuated_tax_id_still_validates(self):
        # a company stamp in the pilot set prints it spaced, '123 456 7890'
        r = V.verify(self._with(vendor_tax_id="123 456 7890"), "invoice")
        assert not any(i["field"] == "vendor_tax_id" for i in r["issues"])

    @pytest.mark.parametrize("absent", [None, ""])
    def test_absent_tax_id_raises_nothing(self, absent):
        r = V.verify(self._with(buyer_tax_id=absent), "invoice")
        assert not any(i["field"] == "buyer_tax_id" for i in r["issues"])

    def test_field_not_present_raises_nothing(self):
        r = V.verify(_clean_invoice(), "invoice")
        assert not any("tax_id" in i["rule"] for i in r["issues"])

    def test_receipt_and_po_tax_id_fields_are_covered(self):
        po = {"po_number": "PO-1", "date": "2026-03-15", "supplier_name": "S",
              "items": [{"quantity": 1, "unit_price": 10.0, "total": 10.0}],
              "total_amount": 10.0, "supplier_tax_id": "1234576890"}
        assert any(i["rule"] == "tax_id_checksum_invalid"
                   for i in V.verify(po, "po")["issues"])

        rc = {"receipt_number": "R-1", "date": "2026-03-15", "store_name": "M",
              "items": [{"quantity": 1, "unit_price": 10.0, "total": 10.0}],
              "total_amount": 10.0, "store_tax_id": "1234576890"}
        assert any(i["rule"] == "tax_id_checksum_invalid"
                   for i in V.verify(rc, "receipt")["issues"])

    def test_no_auto_correction_is_offered_for_a_tax_id(self):
        # there is no way to infer the right digits — only a human can fix it
        r = V.verify(self._with(vendor_tax_id="1234576890"), "invoice")
        assert not any("tax_id" in k for k in r["auto_corrections"])


# Under UBL-TR the seller's VKN/TCKN is mandatory on every Turkish e-fatura and
# e-arşiv document. An absent one therefore means we failed to read it — which
# is what happened on a real telecom bill in the pilot, with the VKN printed
# plainly beside the tax office name, and the document still came out OK.

class TestSellerTaxIdPresence:
    def _tr(self, **kw):
        d = _clean_invoice()
        d["currency"] = "TRY"
        d.update(kw)
        return d

    def test_missing_vendor_tax_id_on_a_try_invoice_warns(self):
        r = V.verify(self._tr(), "invoice")
        issues = [i for i in r["issues"] if i["rule"] == "seller_tax_id_missing"]
        assert issues and issues[0]["field"] == "vendor_tax_id"
        assert issues[0]["severity"] == "warning"

    def test_it_warns_but_does_not_block(self):
        # nothing is provably wrong — a document must not be held on a field we
        # merely failed to find
        assert V.verify(self._tr(), "invoice")["valid"] is True

    @pytest.mark.parametrize("empty", [None, "", "   "])
    def test_empty_counts_as_missing(self, empty):
        r = V.verify(self._tr(vendor_tax_id=empty), "invoice")
        assert any(i["rule"] == "seller_tax_id_missing" for i in r["issues"])

    def test_present_vendor_tax_id_raises_nothing(self):
        r = V.verify(self._tr(vendor_tax_id="1234567890"), "invoice")
        assert not any(i["rule"] == "seller_tax_id_missing" for i in r["issues"])

    def test_a_foreign_invoice_is_not_warned(self):
        # the whole point of the currency proxy: a USD invoice legitimately has
        # no Turkish tax id, and warning on every one would train the user to
        # ignore the warning
        d = _clean_invoice()
        d["currency"] = "USD"
        r = V.verify(d, "invoice")
        assert not any(i["rule"] == "seller_tax_id_missing" for i in r["issues"])

    def test_no_currency_at_all_is_not_warned(self):
        # absence of the proxy is not evidence for it
        assert not any(i["rule"] == "seller_tax_id_missing"
                       for i in V.verify(_clean_invoice(), "invoice")["issues"])

    def test_a_purchase_order_is_not_checked(self):
        # UBL-TR mandates the seller's PartyIdentification on e-fatura and
        # e-arşiv documents. A PO is neither, and PO_SCHEMA does not ask for a
        # supplier tax id at all — warning there flagged 20 of 20 POs in run 10
        # about a field the extractor was never told to produce.
        d = {"po_number": "PO-1", "date": "2026-03-15", "supplier_name": "S",
             "items": [{"quantity": 1, "unit_price": 10.0, "total": 10.0}],
             "total_amount": 10.0, "currency": "TRY"}
        assert not any(i["rule"] == "seller_tax_id_missing"
                       for i in V.verify(d, "po")["issues"])

    def test_the_rule_only_names_fields_the_schema_asks_for(self):
        # the defect in one line: every field this rule can complain about must
        # exist in the schema the extractor is given
        from src.extraction.prompts import SCHEMAS
        for doc_type, field in RuleBasedVerifier.SELLER_TAX_ID_FIELD.items():
            assert field in SCHEMAS[doc_type], (
                f"{doc_type} schema does not ask for {field}, so its absence "
                f"is our omission, not the document's")

    def test_a_receipt_is_not_checked(self):
        # a retail receipt is not an e-fatura; the rule does not apply
        d = {"receipt_number": "R-1", "date": "2026-03-15", "store_name": "M",
             "items": [{"quantity": 1, "unit_price": 10.0, "total": 10.0}],
             "total_amount": 10.0, "currency": "TRY"}
        assert not any(i["rule"] == "seller_tax_id_missing"
                       for i in V.verify(d, "receipt")["issues"])

    def test_lowercase_currency_still_matches(self):
        r = V.verify(self._tr(currency="try"), "invoice")
        assert any(i["rule"] == "seller_tax_id_missing" for i in r["issues"])


class TestIsCorrectable:
    """
    Which issues an LLM correction pass may be given. The test is not "is the
    field wrong" but "does telling a model about this invite it to manufacture
    an answer" — measured 2026-09-03, a check-digit complaint did exactly that.
    """

    def test_a_failed_check_digit_is_not_correctable(self):
        r = V.verify({**_clean_invoice(), "vendor_tax_id": "1234576890"}, "invoice")
        issue = next(i for i in r["issues"]
                     if i["rule"] == "tax_id_checksum_invalid")
        assert V.is_correctable(issue) is False

    def test_a_missing_seller_tax_id_IS_correctable(self):
        # deliberately narrow: a VKN printed on the page that was not read is
        # exactly what re-reading fixes. It stays away from the agent through
        # its severity, not by being mislabelled uncorrectable — so that if it
        # is ever raised to critical, correction does the right thing.
        r = V.verify({**_clean_invoice(), "currency": "TRY"}, "invoice")
        issue = next(i for i in r["issues"]
                     if i["rule"] == "seller_tax_id_missing")
        assert V.is_correctable(issue) is True

    def test_ordinary_issues_default_to_correctable(self):
        d = _clean_invoice()
        del d["invoice_number"]
        issue = next(i for i in V.verify(d, "invoice")["issues"]
                     if i["rule"] == "required_field_missing")
        assert V.is_correctable(issue) is True

    def test_an_explicit_flag_wins_over_the_rule_list(self):
        assert V.is_correctable({"rule": "anything", "correctable": False}) is False
        assert V.is_correctable(
            {"rule": "tax_id_checksum_invalid", "correctable": True}) is True

    def test_an_issue_with_no_rule_is_correctable(self):
        assert V.is_correctable({}) is True
