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

    def test_absolute_accuracy_does_not_scale_with_the_amount(self):
        assert M.absolute_accuracy(615.43, 615.43) == 1.0
        assert M.absolute_accuracy(615.44, 615.43) == 0.0   # one kuruş IS a miss
        assert M.absolute_accuracy(615.50, 615.43) == 0.0   # the real bill
        assert M.absolute_accuracy(100_500, 100_000) == 0.0
        assert M.absolute_accuracy("abc", 5) == 0.0
        # the point: a relative rule passes all three failures above
        assert M.numeric_accuracy(615.44, 615.43) == 1.0
        assert M.numeric_accuracy(615.50, 615.43) == 1.0
        assert M.numeric_accuracy(100_500, 100_000) == 1.0

    def test_float_noise_is_absorbed_but_a_kurus_is_not(self):
        # 615.44 - 615.43 is 0.010000000000048 in IEEE 754. The epsilon must be
        # small enough that this still reads as a real difference.
        assert M.absolute_accuracy(0.1 + 0.2, 0.3) == 1.0
        assert M.absolute_accuracy(615.44, 615.43) == 0.0
        assert M.absolute_accuracy(1234.56, 1234.56) == 1.0
        assert M.absolute_accuracy("1,234.56", 1234.56) == 1.0


class TestMoneyIsScoredToTheKurus:
    """
    A relative tolerance on currency scales the allowance with the amount,
    which is exactly backwards: 1% of a 100,000 TRY invoice is 1,000 TRY of
    free slack, in a product whose whole job is catching financial
    discrepancies. Found when the model returned the payable amount 615.50 in
    the total_amount field instead of the invoice total 615.43 and scored a
    perfect match.
    """

    def _score(self, field, value):
        gt = {"doc_type": "invoice", "total_amount": 615.43, "subtotal": 500.0,
              "tax_amount": 115.43, "tax_rate": 0.20, "amount_payable": 615.43}
        pred = dict(gt)
        pred[field] = value
        return M.evaluate_document(pred, gt, "invoice")["fields"][field]["exact_match"]

    def test_seven_kurus_on_the_total_is_a_miss(self):
        assert self._score("total_amount", 615.50) == 0.0

    def test_one_kurus_is_a_miss(self):
        # ground truth is transcribed from the page or generated exactly, so a
        # one-kuruş difference has no legitimate source: the model read a
        # different digit
        assert self._score("total_amount", 615.44) == 0.0
        assert self._score("total_amount", 615.43) == 1.0

    def test_amount_payable_is_money_too(self):
        assert self._score("amount_payable", 615.50) == 0.0

    def test_subtotal_and_tax_are_money(self):
        assert self._score("subtotal", 502.0) == 0.0
        assert self._score("tax_amount", 116.0) == 0.0

    def test_a_rate_is_not_scored_with_a_relative_rule_either(self):
        # 1% of 0.20 is 0.002, so a relative rule would accept 0.198
        assert self._score("tax_rate", 0.198) == 0.0
        assert self._score("tax_rate", 0.20) == 1.0

    def test_item_money_fields_are_covered_by_the_dotted_path(self):
        gt = {"doc_type": "invoice",
              "items": [{"description": "x", "quantity": 1,
                         "unit_price": 1000.0, "total": 1000.0}]}
        pred = {"doc_type": "invoice",
                "items": [{"description": "x", "quantity": 1,
                           "unit_price": 1005.0, "total": 1005.0}]}
        f = M.evaluate_document(pred, gt, "invoice")["fields"]
        assert f["items[0].unit_price"]["exact_match"] == 0.0
        assert f["items[0].total"]["exact_match"] == 0.0

    def test_a_new_money_field_must_reach_the_money_list(self):
        """
        `discount` was added to the invoice schema and NOT to MONEY_FIELDS, so
        a model returning 0 was scored against a ground truth of 0.0 by string
        comparison and marked wrong. Two real documents lost a field to it.

        The lesson generalises: every numeric field in a schema needs an entry
        here, or it is silently graded as text.
        """
        gt = {"doc_type": "invoice",
              "items": [{"quantity": 1, "unit_price": 10.0,
                         "discount": 0.0, "total": 10.0}]}
        pred = {"doc_type": "invoice",
                "items": [{"quantity": 1, "unit_price": 10.0,
                           "discount": 0, "total": 10.0}]}
        f = M.evaluate_document(pred, gt, "invoice")["fields"]
        assert f["items[0].discount"]["exact_match"] == 1.0

    def test_a_discount_is_still_money_and_scored_to_the_kurus(self):
        gt = {"doc_type": "invoice",
              "items": [{"quantity": 1, "unit_price": 69.4167,
                         "discount": 48.59, "total": 20.83}]}
        pred = {"doc_type": "invoice",
                "items": [{"quantity": 1, "unit_price": 69.4167,
                           "discount": 48.60, "total": 20.83}]}
        f = M.evaluate_document(pred, gt, "invoice")["fields"]
        assert f["items[0].discount"]["exact_match"] == 0.0

    def test_every_schema_number_field_is_declared_numeric(self):
        # a structural guard rather than a list to maintain by hand: pull the
        # field names the extractor is actually asked for and check the numeric
        # ones are known to the metric
        import json as _json
        import re as _re
        from src.extraction.prompts import SCHEMAS
        for doc_type, schema in SCHEMAS.items():
            # the schemas are JSON-shaped text with `number` as a bare token
            for name in _re.findall(r'"(\w+)":\s*number', schema):
                assert name in M.NUMERIC_FIELDS, (
                    f"{doc_type} schema asks for numeric '{name}' but the "
                    f"metric would grade it as text")

    def test_quantity_keeps_the_relative_rule(self):
        # counts are integers, so the tolerance never bites — left alone rather
        # than changed for the sake of symmetry
        gt = {"doc_type": "invoice",
              "items": [{"quantity": 100, "unit_price": 1.0, "total": 100.0}]}
        f = M.evaluate_document(dict(gt), gt, "invoice")["fields"]
        assert f["items[0].quantity"]["exact_match"] == 1.0


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

    def test_a_money_field_is_scored_to_the_kurus_not_to_a_percentage(self):
        # This asserted the old contract: 1179.99 against 1180.00 passed as
        # "within 1%". It is a kuruş out, and on this invoice the same rule
        # would have passed anything within 11.80 TRY. See
        # TestMoneyIsScoredToTheKurus for why that was wrong.
        gt = self._gt()
        pred = dict(gt)
        pred["total_amount"] = 1179.99
        res = M.evaluate_document(pred, gt, "invoice")
        assert res["fields"]["total_amount"]["exact_match"] == 0.0

        pred["total_amount"] = 1180.0
        res = M.evaluate_document(pred, gt, "invoice")
        assert res["fields"]["total_amount"]["exact_match"] == 1.0

    def test_a_string_formatted_amount_still_matches(self):
        # the model returns numbers as strings often enough that this matters
        gt = self._gt()
        pred = dict(gt)
        pred["total_amount"] = "1,180.00"
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


class TestAggregateRun:
    """A failed document must be counted as 0, never dropped from the average."""

    def _doc(self, doc_id, doc_type="invoice", verdict="OK", has_data=True,
             em=1.0, sim=1.0, f1=1.0):
        return {"doc_id": doc_id, "doc_type": doc_type, "verdict": verdict,
                "has_data": has_data, "em": em, "sim": sim, "f1": f1}

    def test_failed_document_stays_in_the_denominator(self):
        docs = [self._doc("a", em=1.0), self._doc("b", em=1.0),
                self._doc("c", verdict="REVIEW", has_data=False,
                          em=0.0, sim=0.0, f1=0.0)]
        out = M.aggregate_run(docs)
        assert out["documents"] == 3
        # rounded to 4 dp by aggregate_run
        assert out["overall"]["exact_match_avg"] == pytest.approx(0.6667, abs=1e-4)

    def test_ok_only_view_excludes_the_failure(self):
        docs = [self._doc("a", em=1.0), self._doc("b", em=1.0),
                self._doc("c", verdict="REVIEW", has_data=False,
                          em=0.0, sim=0.0, f1=0.0)]
        out = M.aggregate_run(docs)
        assert out["ok_only"]["exact_match_avg"] == pytest.approx(1.0)
        assert out["ok_only"]["documents"] == 2

    def test_verdict_counts_and_no_data_count(self):
        docs = [self._doc("a"),
                self._doc("b", verdict="REVIEW", em=0.5, sim=0.5, f1=0.5),
                self._doc("c", verdict="REVIEW", has_data=False,
                          em=0.0, sim=0.0, f1=0.0)]
        out = M.aggregate_run(docs)
        assert out["verdicts"] == {"OK": 1, "REVIEW": 2}
        assert out["no_data"] == 1

    def test_review_with_data_still_scores_its_real_value(self):
        docs = [self._doc("a", verdict="REVIEW", em=0.6, sim=0.6, f1=0.6)]
        out = M.aggregate_run(docs)
        assert out["overall"]["exact_match_avg"] == pytest.approx(0.6)

    def test_per_doc_type_includes_failures(self):
        docs = [self._doc("a", doc_type="po", em=1.0),
                self._doc("b", doc_type="po", verdict="REVIEW",
                          has_data=False, em=0.0, sim=0.0, f1=0.0)]
        out = M.aggregate_run(docs)
        assert out["per_doc_type"]["po"]["documents"] == 2
        assert out["per_doc_type"]["po"]["exact_match_avg"] == pytest.approx(0.5)

    def test_empty_input_is_safe(self):
        out = M.aggregate_run([])
        assert out["documents"] == 0
        assert out["overall"]["exact_match_avg"] == 0.0


def test_evaluate_batch_averages_documents():
    gt1 = {"invoice_number": "A", "total_amount": 100.0}
    gt2 = {"invoice_number": "B", "total_amount": 200.0}
    pred_ok = dict(gt1)
    pred_half = {"invoice_number": "WRONG", "total_amount": 200.0}
    out = M.evaluate_batch([pred_ok, pred_half], [gt1, gt2], ["invoice", "invoice"])
    assert out["overall"]["total_documents"] == 2
    assert 0.0 < out["overall"]["exact_match_avg"] < 1.0
