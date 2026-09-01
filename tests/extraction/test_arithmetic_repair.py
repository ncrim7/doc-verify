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

    def test_wrong_item_total_is_recomputed(self):
        d = {"items": [{"quantity": 3, "unit_price": 10.0, "total": 31.0}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 30.0

    def test_missing_item_total_is_filled(self):
        d = {"items": [{"quantity": 4, "unit_price": 2.5}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 10.0

    def test_item_total_within_tolerance_is_left_alone(self):
        d = {"items": [{"quantity": 3, "unit_price": 10.0, "total": 30.005}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 30.005

    def test_subtotal_corrected_only_when_key_present(self):
        with_key = {"items": [{"quantity": 2, "unit_price": 10, "total": 20}],
                    "subtotal": 999}
        repair_arithmetic(with_key, "invoice")
        assert with_key["subtotal"] == 20.0

        without_key = {"items": [{"quantity": 2, "unit_price": 10, "total": 20}]}
        repair_arithmetic(without_key, "invoice")
        assert "subtotal" not in without_key

    def test_total_amount_is_subtotal_plus_tax(self):
        d = {"items": [{"quantity": 1, "unit_price": 100, "total": 100}],
             "subtotal": 100, "tax_amount": 18, "total_amount": 200}
        repair_arithmetic(d, "invoice")
        assert d["total_amount"] == 118.0

    def test_total_amount_falls_back_to_line_sum_for_po(self):
        d = {"items": [{"quantity": 2, "unit_price": 25, "total": 50},
                       {"quantity": 1, "unit_price": 30, "total": 30}],
             "total_amount": 999}
        repair_arithmetic(d, "po")
        assert d["total_amount"] == 80.0

    def test_digit_drop_scenario_from_the_thesis(self):
        # qty 53 * 3267.94 = 173200.82, model wrote 17320.82 (one digit dropped)
        d = {"items": [{"quantity": 53, "unit_price": 3267.94, "total": 17320.82}]}
        repair_arithmetic(d, "po")
        assert d["items"][0]["total"] == 173200.82

    def test_string_numbers_are_handled(self):
        d = {"items": [{"quantity": "3", "unit_price": "10.5", "total": "1"}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 31.5

    def test_item_without_qty_or_price_is_skipped(self):
        d = {"items": [{"description": "x", "total": 5}]}
        repair_arithmetic(d, "invoice")
        assert d["items"][0]["total"] == 5        # untouched

    def test_returns_same_object(self):
        d = {"items": []}
        assert repair_arithmetic(d, "invoice") is d
