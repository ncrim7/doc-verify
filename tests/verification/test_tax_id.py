"""
Tests for src.verification.tax_id — pure, deterministic, no dependencies.

Every case below mirrors a situation the module actually met on the real
pilot documents on 2026-09-03. The *digits* are synthetic — built to be
checksum-valid, or corrupted in exactly the way the model corrupted a real one.
The real ids identify real people and companies and this repository is public,
so they stay in `data/real/gt/`, which is gitignored. The algorithm does not
care which digits it is given, so the coverage is the same.
"""
import pytest

from src.verification.tax_id import (
    classify_tax_id, is_valid_tckn, is_valid_vkn, normalize_tax_id,
)

# --- synthetic stand-ins for the real pilot values --------------------------

VALID_VKN = ["1234567890",    # stands in for a VKN printed 3x on one invoice
             "0012345672",    # leading zeros, as on the utility bill
             "9876543217"]    # stands in for one read from a PDF text layer
VALID_TCKN = ["12345678950"]

# the three corruption SHAPES the model actually produced
MODEL_CORRUPTIONS = ["1234576890",    # two digits transposed
                     "1111111111",    # a digit dropped off an 11-digit id
                     "10000000000"]   # hallucinated


class TestCorpusShapes:
    @pytest.mark.parametrize("vkn", VALID_VKN)
    def test_a_well_formed_vkn_validates(self, vkn):
        assert is_valid_vkn(vkn)

    @pytest.mark.parametrize("tckn", VALID_TCKN)
    def test_a_well_formed_tckn_validates(self, tckn):
        assert is_valid_tckn(tckn)

    @pytest.mark.parametrize("bad", MODEL_CORRUPTIONS)
    def test_every_model_corruption_shape_is_caught(self, bad):
        assert classify_tax_id(bad)["valid"] is False

    def test_the_printed_placeholder_is_flagged(self):
        # A real invoice in the pilot prints 'TCKN: 11111111111' — a seller's
        # placeholder. Flagging it is a true positive: the buyer tax id on that
        # invoice is not a real one, and an accountant posting it needs to know.
        assert classify_tax_id("11111111111")["valid"] is False

    def test_a_foreign_tax_number_is_not_judged(self):
        # A US vendor's Turkey VAT registration in the pilot set is 9 digits.
        # Calling that invalid would be worse than saying nothing.
        v = classify_tax_id("701236788")
        assert v["valid"] is None
        assert v["kind"] == "unknown_length"


class TestNormalize:
    @pytest.mark.parametrize("raw,expected", [
        ("123 456 7890", "1234567890"),      # spaced, as on a company stamp
        ("123.456.7890", "1234567890"),
        ("123-456-7890", "1234567890"),
        ("  9876543217  ", "9876543217"),
        (9876543217, "9876543217"),          # model sometimes returns an int
    ])
    def test_punctuation_is_stripped(self, raw, expected):
        assert normalize_tax_id(raw) == expected

    @pytest.mark.parametrize("raw", ["VKN 1234567890", "TR12345678950",
                                     "yok", "", None, "12,34"])
    def test_non_digits_are_rejected(self, raw):
        # letters are NOT stripped on purpose: a label glued to the value means
        # the extractor grabbed the wrong span, which is worth surfacing
        assert normalize_tax_id(raw) is None


class TestVknAlgorithm:
    def test_wrong_length_is_false(self):
        assert not is_valid_vkn("123456789")
        assert not is_valid_vkn("12345678901")

    def test_non_digits_are_false(self):
        assert not is_valid_vkn("12345678xx")

    def test_leading_zeros_are_preserved_not_dropped(self):
        assert is_valid_vkn("0012345672")

    def test_every_single_digit_change_is_detected(self):
        # a check digit is only worth having if it catches the common error
        base = "1234567890"
        caught = sum(
            not is_valid_vkn(base[:i] + str(d) + base[i + 1:])
            for i in range(10) for d in range(10) if str(d) != base[i]
        )
        assert caught == 90, "a 10-digit VKN has 90 single-digit mutations"


class TestTcknAlgorithm:
    def test_wrong_length_is_false(self):
        assert not is_valid_tckn("1234567895")

    def test_leading_zero_is_false(self):
        assert not is_valid_tckn("02345678950")

    def test_non_digits_are_false(self):
        assert not is_valid_tckn("1234567895x")

    def test_every_single_digit_change_is_detected(self):
        base = "12345678950"
        mutations = [base[:i] + str(d) + base[i + 1:]
                     for i in range(11) for d in range(10) if str(d) != base[i]]
        # the leading digit going to 0 is caught by the length/zero rule, the
        # rest by the check digits — all 99 must fail
        assert all(not is_valid_tckn(m) for m in mutations)


class TestClassify:
    def test_not_numeric(self):
        v = classify_tax_id("Boğaziçi Kurumlar V.D.")
        assert v["kind"] == "not_numeric" and v["valid"] is None

    def test_none_is_not_numeric(self):
        assert classify_tax_id(None)["valid"] is None

    def test_valid_vkn_carries_no_reason(self):
        v = classify_tax_id("1234567890")
        assert (v["kind"], v["valid"], v["reason"]) == ("vkn", True, None)

    def test_invalid_carries_a_reason(self):
        assert classify_tax_id("1234576890")["reason"]
