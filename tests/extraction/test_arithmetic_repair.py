"""Tests for src.extraction.arithmetic_repair — deterministic, dependency-free."""
from src.extraction.arithmetic_repair import repair_arithmetic, _num


class TestNum:
    def test_int_and_float(self):
        assert _num(3) == 3.0
        assert _num(2.5) == 2.5

    def test_bool_is_not_a_number(self):
        assert _num(True) is None
        assert _num(False) is None

    def test_string_strips_spaces_and_commas(self):
        assert _num("1 234.56") == 1234.56
        assert _num("1,234") == 1234.0        # note: comma is stripped, not decimal

    def test_garbage_is_none(self):
        assert _num("abc") is None
        assert _num(None) is None
        assert _num([1]) is None


class TestRepairArithmetic:
    def test_non_dict_passthrough(self):
        assert repair_arithmetic([1, 2, 3]) == [1, 2, 3]

    def test_a_printed_item_total_is_never_recomputed(self):
        # asserted the pre-P0-7 contract, where qty x unit_price overwrote the
        # printed line total. See TestDiscountedLine for why that fell.
        d = {"items": [{"quantity": 3, "unit_price": 10.0, "total": 31.0}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 31.0

    def test_missing_item_total_is_filled(self):
        d = {"items": [{"quantity": 4, "unit_price": 2.5}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 10.0

    def test_item_total_within_tolerance_is_left_alone(self):
        d = {"items": [{"quantity": 3, "unit_price": 10.0, "total": 30.005}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 30.005

    # NOTE: the three tests below asserted the pre-P0-4 contract, where a
    # computed value overwrote a printed one. That behaviour was deliberately
    # removed (see TestNeverOverwritesEvidence); these now pin the replacement.

    def test_subtotal_key_is_never_invented(self):
        without_key = {"items": [{"quantity": 2, "unit_price": 10, "total": 20}]}
        repair_arithmetic(without_key, "invoice")
        assert "subtotal" not in without_key

    def test_total_amount_is_subtotal_plus_tax_when_absent(self):
        d = {"items": [{"quantity": 1, "unit_price": 100, "total": 100}],
             "subtotal": 100, "tax_amount": 18}
        repair_arithmetic(d, "invoice")
        assert d["total_amount"] == 118.0

    def test_total_amount_falls_back_to_line_sum_for_po_when_absent(self):
        d = {"items": [{"quantity": 2, "unit_price": 25, "total": 50},
                       {"quantity": 1, "unit_price": 30, "total": 30}]}
        repair_arithmetic(d, "po")
        assert d["total_amount"] == 80.0

    def test_the_digit_drop_recovery_is_deliberately_gone(self):
        # The thesis case: 53 x 3267.94 = 173200.82 and the model wrote
        # 17320.82, one digit short. Recovering it was the original point of
        # this module. It is gone on purpose -- the same overwrite turned a
        # printed 20,83 into 69,42 on a real discounted invoice, and the
        # measured benefit of the whole repair layer was +0.00 pp across two
        # 60-document runs. An unproven gain does not justify a proven
        # fabrication. The mismatch is still raised as critical, so the
        # document reaches a human.
        d = {"items": [{"quantity": 53, "unit_price": 3267.94, "total": 17320.82}]}
        repair_arithmetic(d, "po")
        assert d["items"][0]["total"] == 17320.82

    def test_string_numbers_are_handled_when_filling_a_gap(self):
        d = {"items": [{"quantity": "3", "unit_price": "10.5"}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 31.5

    def test_a_string_zero_is_a_printed_value_not_a_gap(self):
        d = {"items": [{"quantity": "3", "unit_price": "10.5", "total": "0"}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == "0"

    def test_item_without_qty_or_price_is_skipped(self):
        d = {"items": [{"description": "x", "total": 5}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 5        # untouched

    def test_returns_same_object(self):
        d = {"items": []}
        assert repair_arithmetic(d, "invoice") is d


class TestNeverOverwritesEvidence:
    """
    P0-4. A value printed on the document is evidence; a value we compute is
    inference. Inference fills gaps; it never silently overwrites evidence.

    Found on a real telecom bill where the payable amount is printed twice,
    once highlighted, and the module replaced it with subtotal+tax — wrong by
    ~2x, verdict OK. The module's assumptions hold for a clean commercial
    invoice and not for a bill carrying discounts, a carried-over balance, a
    late fee and two tax bases.
    """

    def test_present_total_amount_is_not_overwritten(self):
        d = {"items": [{"quantity": 1, "unit_price": 990.01, "total": 990.01}],
             "subtotal": 990.01, "tax_amount": 141.82,
             "total_amount": 615.43}          # printed on the page
        repair_arithmetic(d, "invoice")
        assert d["total_amount"] == 615.43

    def test_absent_total_amount_is_still_filled(self):
        d = {"items": [{"quantity": 1, "unit_price": 100.0, "total": 100.0}],
             "subtotal": 100.0, "tax_amount": 18.0}
        repair_arithmetic(d, "invoice")
        assert d["total_amount"] == 118.0

    def test_present_subtotal_is_not_overwritten(self):
        d = {"items": [{"quantity": 1, "unit_price": 10.0, "total": 10.0}],
             "subtotal": 999.0}               # printed, disagrees with the items
        repair_arithmetic(d, "invoice")
        assert d["subtotal"] == 999.0

    def test_absent_subtotal_is_still_filled(self):
        d = {"items": [{"quantity": 2, "unit_price": 10.0, "total": 20.0}],
             "subtotal": None}
        repair_arithmetic(d, "invoice")
        assert d["subtotal"] == 20.0

    def test_item_level_repair_only_fills_a_gap(self):
        # the original justification was that qty and unit_price corroborate
        # the derived line total. They only do when nothing sits between them.
        present = {"items": [{"quantity": 53, "unit_price": 3267.94,
                              "total": 17320.82}]}
        repair_arithmetic(present, "invoice")
        assert present["items"][0]["total"] == 17320.82, "evidence was overwritten"

        absent = {"items": [{"quantity": 53, "unit_price": 3267.94}]}
        repair_arithmetic(absent, "invoice")
        assert absent["items"][0]["total"] == 173200.82, "a gap must still fill"

    def test_the_real_bill_shape_survives_intact(self):
        d = {"items": [{"description": "İnternet", "quantity": 1,
                        "unit_price": 990.01, "total": 990.01}],
             "subtotal": 990.01, "tax_amount": 141.82, "total_amount": 615.43}
        repair_arithmetic(d, "invoice")
        assert (d["total_amount"], d["subtotal"]) == (615.43, 990.01)

    def test_amount_payable_is_never_invented_from_the_total(self):
        # They are equal on almost every document, which is exactly why filling
        # one from the other is tempting and wrong: when the model fails to read
        # the payable line, that failure is information. Copying total_amount
        # over it would manufacture agreement and hide the miss.
        d = {"items": [{"quantity": 1, "unit_price": 100.0, "total": 100.0}],
             "subtotal": 100.0, "tax_amount": 18.0, "total_amount": 118.0}
        repair_arithmetic(d, "invoice")
        assert "amount_payable" not in d

    def test_the_discounted_line_that_closed_the_last_exception(self):
        """
        P0-7, from a real e-arşiv invoice (a Windows licence sold via n11):

            1 Adet × 69,4167 TL, %70 iskonto → Mal Hizmet Tutarı 20,83 TL

        Item-level repair was the one overwrite P0-4 deliberately kept, on the
        grounds that quantity and unit_price corroborate the derived total.
        They only do when nothing sits between them. Here a discount does, the
        schema has no field for it, and the repair replaced the printed 20,83
        with 69,42 — 3.3x. Worse, the fabricated line total then made the
        page's *correct* subtotal look wrong.
        """
        d = {"items": [{"description": "Windows 11 Pro Lisans", "quantity": 1,
                        "unit_price": 69.4167, "total": 20.83}],
             "subtotal": 20.83, "tax_amount": 4.17, "total_amount": 24.99}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 20.83
        assert d["subtotal"] == 20.83
        assert d["total_amount"] == 24.99

    def test_a_printed_amount_payable_is_left_alone(self):
        # the real telecom bill: payable exceeds the invoice total by a
        # carried-over balance, and that gap is the whole point of the field
        d = {"items": [{"quantity": 1, "unit_price": 990.01, "total": 990.01}],
             "subtotal": 990.01, "tax_amount": 141.82,
             "total_amount": 615.43, "amount_payable": 615.50}
        repair_arithmetic(d, "invoice")
        assert d["amount_payable"] == 615.50
        assert d["total_amount"] == 615.43
