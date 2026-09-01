"""Tests for src.evaluation.metrics — the measurement harness itself."""
import pytest

from src.evaluation import metrics as M


class TestPrimitives:
    def test_exact_match_normalizes(self):
        assert M.exact_match("  ABC  ", "abc") == 1.0
        assert M.exact_match("a  b", "a b") == 1.0
        assert M.exact_match("ABC", "ABD") == 0.0

    def test_semantic_similarity_bounds(self):
        assert M.semantic_similarity("hello", "hello") == 1.0
        assert M.semantic_similarity("", "") == 1.0
        assert M.semantic_similarity("x", "") == 0.0
        assert 0.0 < M.semantic_similarity("Kağıdı", "Kağıt") < 1.0

    def test_token_f1(self):
        r = M.token_f1("a b c", "a b d")   # values are rounded to 4 dp
        assert r["precision"] == pytest.approx(0.6667, abs=1e-4)
        assert r["recall"] == pytest.approx(0.6667, abs=1e-4)
        assert r["f1"] == pytest.approx(0.6667, abs=1e-4)

    def test_numeric_accuracy_relative_tolerance(self):
        assert M.numeric_accuracy(100, 100.5) == 1.0        # 0.5% < 1%
        assert M.numeric_accuracy(100, 102) == 0.0          # 2% > 1%
        assert M.numeric_accuracy("1,234.56", 1234.56) == 1.0
        assert M.numeric_accuracy(0, 0) == 1.0
        assert M.numeric_accuracy("abc", 5) == 0.0


class TestEvaluateDocument:
    def _gt(self):
        return {
            "doc_type": "invoice", "language": "tr", "confidence": 1.0,
            "invoice_number": "INV-1", "total_amount": 1180.0,
            "vendor_address": "Atatürk Cad. No:12, Kadıköy, İstanbul",
            "items": [{"description": "Masa", "quantity": 2, "unit_price": 100.0,
                       "total": 200.0}],
        }

    def test_identical_scores_perfect(self):
        gt = self._gt()
        res = M.evaluate_document(dict(gt), gt, "invoice")
        assert res["aggregate"]["exact_match_avg"] == 1.0

    def test_metadata_fields_are_not_scored(self):
        gt = self._gt()
        pred = dict(gt)
        pred["confidence"] = 0.3            # different, but must be ignored
        pred["language"] = "en"
        res = M.evaluate_document(pred, gt, "invoice")
        assert "confidence" not in res["fields"]
        assert "language" not in res["fields"]

    def test_numeric_field_uses_tolerance_for_em(self):
        gt = self._gt()
        pred = dict(gt)
        pred["total_amount"] = 1179.99      # within 1%
        res = M.evaluate_document(pred, gt, "invoice")
        assert res["fields"]["total_amount"]["exact_match"] == 1.0

    def test_address_fuzzy_match_counts_as_exact(self):
        gt = self._gt()
        pred = dict(gt)
        pred["vendor_address"] = "Ataturk Cad. No 12 Kadikoy Istanbul"  # >0.80 sim
        res = M.evaluate_document(pred, gt, "invoice")
        assert res["fields"]["vendor_address"]["exact_match"] == 1.0

    def test_available_fields_restricts_scoring(self):
        gt = self._gt()
        gt["available_fields"] = ["invoice_number"]
        pred = {"invoice_number": "INV-1", "total_amount": 0.0}
        res = M.evaluate_document(pred, gt, "invoice")
        assert set(res["fields"]) == {"invoice_number"}
        assert res["aggregate"]["exact_match_avg"] == 1.0

    def test_item_order_does_not_penalize(self):
        gt = self._gt()
        gt["items"] = [
            {"description": "Masa", "quantity": 2, "unit_price": 100.0, "total": 200.0},
            {"description": "Sandalye", "quantity": 4, "unit_price": 50.0, "total": 200.0},
        ]
        pred = dict(gt)
        pred["items"] = list(reversed([dict(i) for i in gt["items"]]))
        res = M.evaluate_document(pred, gt, "invoice")
        assert res["aggregate"]["exact_match_avg"] == 1.0


def test_evaluate_batch_averages_documents():
    gt1 = {"invoice_number": "A", "total_amount": 100.0}
    gt2 = {"invoice_number": "B", "total_amount": 200.0}
    pred_ok = dict(gt1)
    pred_half = {"invoice_number": "WRONG", "total_amount": 200.0}
    out = M.evaluate_batch([pred_ok, pred_half], [gt1, gt2], ["invoice", "invoice"])
    assert out["overall"]["total_documents"] == 2
    assert 0.0 < out["overall"]["exact_match_avg"] < 1.0
