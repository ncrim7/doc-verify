"""
Tests for src.extraction.correction_agent — the merge, without the network.

The agent's LLM call is not exercised here; the merge is, because the merge is
where the damage happened. Measured 2026-09-03: `_merge_results` took every
non-null value the model returned and wrote it over the original, so a
correction asked to look at one tax id also rewrote 'Post-it Not Bloğu' to
'Blogu' and 'Tevetoğlu A.Ş.' to 'Tevetoglu A.Ş.' — the ASCII transliteration
regression the extraction prompt had been fixed to prevent, arriving through
the correction pass on fields nobody had questioned.

The prompt now asks for restraint. The allowlist enforces it. These tests pin
the enforcement, because a prompt is a request and only the code is a
guarantee.
"""
import pytest

from src.extraction.correction_agent import CorrectionAgent


@pytest.fixture
def agent():
    # __init__ builds an LLMExtractor and reads config; the merge needs neither,
    # so construct without running it.
    return CorrectionAgent.__new__(CorrectionAgent)


def _doc():
    return {
        "invoice_number": "INV-1001",
        "vendor_name": "Tevetoğlu A.Ş.",
        "vendor_tax_id": "1234567890",
        "items": [
            {"description": "Post-it Not Bloğu 76x76", "quantity": 2,
             "unit_price": 100.0, "total": 200.0},
            {"description": "LED Monitör 24\" IPS", "quantity": 1,
             "unit_price": 50.0, "total": 50.0},
        ],
    }


class TestMergeAllowlist:
    def test_a_flagged_field_is_applied(self, agent):
        merged, applied, refused = agent._merge_results(
            _doc(), {"invoice_number": "INV-1002"}, {"invoice_number"})
        assert merged["invoice_number"] == "INV-1002"
        assert applied == {"invoice_number"} and refused == set()

    def test_an_unflagged_field_is_refused(self, agent):
        merged, applied, refused = agent._merge_results(
            _doc(), {"vendor_name": "Tevetoglu A.S."}, {"invoice_number"})
        assert merged["vendor_name"] == "Tevetoğlu A.Ş.", "unflagged, must not move"
        assert applied == set() and refused == {"vendor_name"}

    def test_the_real_regression_cannot_happen_any_more(self, agent):
        # asked about the tax id, the model returned the whole document with
        # Turkish characters transliterated away
        response = {
            "vendor_tax_id": "1234567891",
            "vendor_name": "Tevetoglu A.S.",
            "items": [{"description": "Post-it Not Blogu 76x76"},
                      {"description": "LED Monitor 24\" IPS"}],
        }
        merged, applied, refused = agent._merge_results(
            _doc(), response, {"vendor_tax_id"})
        assert merged["vendor_tax_id"] == "1234567891"
        assert merged["vendor_name"] == "Tevetoğlu A.Ş."
        assert merged["items"][0]["description"] == "Post-it Not Bloğu 76x76"
        assert merged["items"][1]["description"] == "LED Monitör 24\" IPS"
        assert applied == {"vendor_tax_id"}
        assert refused == {"vendor_name", "items[0].description",
                           "items[1].description"}

    def test_item_paths_are_allowlisted_individually(self, agent):
        response = {"items": [{"total": 210.0, "description": "changed"}]}
        merged, applied, refused = agent._merge_results(
            _doc(), response, {"items[0].total"})
        assert merged["items"][0]["total"] == 210.0
        assert merged["items"][0]["description"] == "Post-it Not Bloğu 76x76"
        assert applied == {"items[0].total"}
        assert refused == {"items[0].description"}

    def test_null_means_illegible_and_keeps_the_original(self, agent):
        # an explicit "I cannot read this" is not a correction; the issue stays
        # standing and the document still reaches a human
        merged, applied, _ = agent._merge_results(
            _doc(), {"invoice_number": None}, {"invoice_number"})
        assert merged["invoice_number"] == "INV-1001"
        assert applied == set()

    def test_an_unchanged_answer_is_a_valid_answer(self, agent):
        merged, applied, refused = agent._merge_results(
            _doc(), {"vendor_tax_id": "1234567890"}, {"vendor_tax_id"})
        assert merged == _doc()
        assert applied == {"vendor_tax_id"} and refused == set()

    def test_the_original_is_not_mutated(self, agent):
        original = _doc()
        agent._merge_results(original, {"invoice_number": "X"}, {"invoice_number"})
        assert original["invoice_number"] == "INV-1001"

    def test_an_item_index_beyond_the_document_is_ignored(self, agent):
        merged, applied, _ = agent._merge_results(
            _doc(), {"items": [{}, {}, {"total": 9.0}]}, {"items[2].total"})
        assert len(merged["items"]) == 2
        assert applied == set()

    def test_an_empty_allowlist_returns_the_document_untouched(self, agent):
        merged, applied, refused = agent._merge_results(
            _doc(), {"vendor_name": "X"}, set())
        assert merged == _doc()
        assert applied == set() and refused == {"vendor_name"}


class TestCorrectNoOps:
    def test_no_issues_returns_the_extraction_unchanged(self, agent):
        d = _doc()
        assert agent.correct(d, "x.pdf", [], "invoice") is d

    def test_issues_without_a_field_name_are_a_no_op(self, agent):
        # nothing to allowlist means nothing may be written, so do not spend an
        # API call to find that out
        d = _doc()
        assert agent.correct(d, "x.pdf", [{"rule": "x"}], "invoice") is d
