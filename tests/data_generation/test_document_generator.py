"""Tests for the synthetic document generator after the 1.4 catalog change."""
import re

import pytest

from src.data_generation.document_generator import (
    DocumentGenerator, REAL_ITEMS, RECEIPT_ITEMS,
)
from src.data_generation.validator import DataValidator

TAX_ID = re.compile(r"^\d{10}$")


@pytest.fixture(scope="module")
def gen():
    return DocumentGenerator(seed=123)


def _item_math_ok(items):
    for it in items:
        assert round(it["quantity"] * it["unit_price"], 2) == it["total"]


def test_invoice_uses_catalog_descriptions_and_math(gen):
    _pdf, gt = gen.generate_invoice(lang="tr")
    assert gt["items"] and all(it["description"] in REAL_ITEMS for it in gt["items"])
    _item_math_ok(gt["items"])
    assert round(sum(it["total"] for it in gt["items"]), 2) == gt["subtotal"]
    assert round(gt["subtotal"] + gt["tax_amount"], 2) == gt["total_amount"]


def test_invoice_has_tax_ids_rate_and_currency(gen):
    _pdf, gt = gen.generate_invoice(lang="tr")
    assert TAX_ID.match(gt["vendor_tax_id"])
    assert TAX_ID.match(gt["buyer_tax_id"])
    assert gt["tax_rate"] == 0.18
    assert gt["currency"] == "TRY"


def test_po_items_have_sku_and_catalog_desc(gen):
    _pdf, gt = gen.generate_po(lang="en")
    _item_math_ok(gt["items"])
    for it in gt["items"]:
        assert it["sku"].startswith("SKU-")
        assert it["description"] in REAL_ITEMS
    assert round(sum(it["total"] for it in gt["items"]), 2) == gt["total_amount"]


def test_receipt_uses_retail_catalog(gen):
    _pdf, gt = gen.generate_receipt(lang="tr")
    assert all(it["description"] in RECEIPT_ITEMS for it in gt["items"])
    _item_math_ok(gt["items"])
    assert gt["cashier"]
    assert round(gt["subtotal"] + gt["tax_amount"], 2) == gt["total_amount"]


@pytest.mark.parametrize("doc_type", ["invoice", "po", "receipt"])
def test_generated_doc_passes_validator(gen, doc_type):
    _pdf, gt = gen.generate(doc_type)
    ok, errors = DataValidator().validate_document(gt, doc_type)
    assert ok, errors


def test_generation_is_deterministic_for_a_seed():
    a = DocumentGenerator(seed=7).generate_invoice()
    b = DocumentGenerator(seed=7).generate_invoice()
    assert a[1] == b[1]          # identical ground truth
    assert a[0] == b[0]          # identical PDF bytes


def test_pdf_is_nonempty(gen):
    pdf, _gt = gen.generate_invoice()
    assert pdf[:4] == b"%PDF" and len(pdf) > 1000


# --- render integrity: every scored field must actually be on the page -------
# (the 2026-09-02 measurement was invalidated because currency / description /
#  buyer_address were absent or mangled in the rendered PDF)

def _pdf_text(pdf_bytes: bytes) -> str:
    import pymupdf
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as d:
        return "".join(p.get_text() for p in d)


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


@pytest.mark.parametrize("doc_type", ["invoice", "po", "receipt"])
def test_rendered_pdf_contains_every_scored_field(gen, doc_type):
    pdf, gt = gen.generate(doc_type)
    page = _squash(_pdf_text(pdf))

    assert _squash(gt["currency"]) in page, "currency code is not printed on the page"

    for it in gt["items"]:
        assert _squash(it["description"]) in page, (
            f"item description not on page verbatim (glyph/wrap loss?): "
            f"{it['description']!r}"
        )

    for f in ("vendor_address", "buyer_address", "supplier_address", "store_address"):
        if f in gt:
            assert _squash(gt[f]) in page, f"{f} is clipped / not fully on the page"

    for f in ("vendor_tax_id", "buyer_tax_id", "invoice_number", "po_number",
              "receipt_number", "date"):
        if f in gt:
            assert _squash(str(gt[f])) in page, f"{f} not on the page"


def test_para_helper_keeps_xml_special_chars_literal():
    # a company name like "Shell&Turcas Petrol" must not be eaten by ReportLab's
    # Paragraph XML parser
    from src.data_generation.document_generator import _para
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table
    import io
    style = getSampleStyleSheet()["Normal"]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, invariant=1).build(
        [Table([[_para("Shell&Turcas Petrol <Ltd> A&B", style)]])]
    )
    assert _squash("Shell&Turcas Petrol <Ltd> A&B") in _squash(_pdf_text(buf.getvalue()))
