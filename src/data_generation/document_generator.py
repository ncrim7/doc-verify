"""
Synthetic document generator for Invoice, Purchase Order, and Receipt documents.
Produces (PDF bytes, ground_truth dict) pairs with mixed Turkish/English content.

Uses DejaVu Sans (or Arial fallback) for full Turkish character support
(ç, ğ, ı, ö, ş, ü, İ, Ş, Ö, Ü, Ç, Ğ).
"""
import io
import json
import os
import random
import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from faker import Faker
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.enums import TA_RIGHT, TA_CENTER, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from src.config import DATA_CONFIG

logger = logging.getLogger(__name__)

DocType = Literal["invoice", "po", "receipt"]
Lang = Literal["tr", "en", "mixed"]


# ---------------------------------------------------------------------------
# Unicode font registration (Turkish character support: ç, ğ, ı, ö, ş, ü)
# ---------------------------------------------------------------------------
_FONT_REGISTERED = False
FONT_NAME = "Helvetica"        # fallback
FONT_NAME_BOLD = "Helvetica-Bold"

def _register_unicode_font():
    """Register a Unicode TTF font for Turkish character support in PDFs."""
    global _FONT_REGISTERED, FONT_NAME, FONT_NAME_BOLD

    if _FONT_REGISTERED:
        return

    fonts_dir = "C:/Windows/Fonts"
    # Priority: DejaVu Sans > Calibri > Arial
    font_candidates = [
        ("DejaVuSans", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
        ("Calibri", "calibri.ttf", "calibrib.ttf"),
        ("Arial", "arial.ttf", "arialbd.ttf"),
    ]

    for name, regular, bold in font_candidates:
        regular_path = os.path.join(fonts_dir, regular)
        bold_path = os.path.join(fonts_dir, bold)

        if os.path.exists(regular_path):
            try:
                pdfmetrics.registerFont(TTFont(name, regular_path))
                if os.path.exists(bold_path):
                    pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold_path))
                else:
                    pdfmetrics.registerFont(TTFont(f"{name}-Bold", regular_path))

                FONT_NAME = name
                FONT_NAME_BOLD = f"{name}-Bold"
                _FONT_REGISTERED = True
                logger.info("Registered Unicode font: %s (Turkish character support enabled)", name)
                return
            except Exception as e:
                logger.warning("Failed to register font %s: %s", name, e)
                continue

    logger.warning("No Unicode TTF font found — using Helvetica (Turkish characters may not render correctly)")
    _FONT_REGISTERED = True


# ---------------------------------------------------------------------------
# Label dictionaries
# ---------------------------------------------------------------------------
LABELS = {
    "tr": {
        "invoice_title":  "FATURA",
        "po_title":       "SATIN ALMA SİPARİŞİ",
        "receipt_title":  "FİŞ / MAKBUZ",
        "invoice_no":     "Fatura No",
        "po_no":          "Sipariş No",
        "receipt_no":     "Fiş No",
        "date":           "Tarih",
        "due_date":       "Vade Tarihi",
        "delivery_date":  "Teslimat Tarihi",
        "vendor":         "Satıcı",
        "supplier":       "Tedarikçi",
        "buyer":          "Alıcı",
        "tax_id":         "Vergi No",
        "description":    "Açıklama",
        "qty":            "Miktar",
        "unit_price":     "Birim Fiyat",
        "total":          "Toplam",
        "subtotal":       "Ara Toplam",
        "tax":            "KDV (%18)",
        "grand_total":    "Genel Toplam",
        "payment_terms":  "Ödeme Koşulları",
        "currency":       "TRY",
        "sku":            "SKU",
        "cashier":        "Kasiyer",
        "thank_you":      "Teşekkür ederiz!",
    },
    "en": {
        "invoice_title":  "INVOICE",
        "po_title":       "PURCHASE ORDER",
        "receipt_title":  "RECEIPT",
        "invoice_no":     "Invoice No",
        "po_no":          "PO Number",
        "receipt_no":     "Receipt No",
        "date":           "Date",
        "due_date":       "Due Date",
        "delivery_date":  "Delivery Date",
        "vendor":         "Vendor",
        "supplier":       "Supplier",
        "buyer":          "Buyer",
        "tax_id":         "Tax ID",
        "description":    "Description",
        "qty":            "Qty",
        "unit_price":     "Unit Price",
        "total":          "Total",
        "subtotal":       "Subtotal",
        "tax":            "Tax (18%)",
        "grand_total":    "Grand Total",
        "payment_terms":  "Payment Terms",
        "currency":       "TRY",
        "sku":            "SKU",
        "cashier":        "Cashier",
        "thank_you":      "Thank you for your business!",
    },
}


def _round2(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class DocumentGenerator:
    """
    Generates synthetic PDF documents with ground-truth JSON annotations.

    Language distribution (from config):
        - 30% mixed  (random labels from tr+en)
        - 35% Turkish-only
        - 35% English-only
    """

    def __init__(self, seed: int = 42):
        self.seed = seed
        random.seed(seed)

        # Register Unicode font on first instantiation
        _register_unicode_font()

        self.faker_tr = Faker("tr_TR")
        self.faker_en = Faker("en_US")
        Faker.seed(seed)

        self.mix_prob    = DATA_CONFIG["language_mix_probability"]      # 0.30
        self.tr_prob     = DATA_CONFIG["turkish_only_probability"]       # 0.35
        # en_prob = 1 - mix_prob - tr_prob = 0.35

        self._doc_counters: dict[str, int] = {"invoice": 0, "po": 0, "receipt": 0}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, doc_type: DocType) -> tuple[bytes, dict]:
        """Generate a document of the given type. Returns (pdf_bytes, ground_truth)."""
        lang = self._pick_language()
        self._doc_counters[doc_type] += 1

        if doc_type == "invoice":
            return self.generate_invoice(lang)
        elif doc_type == "po":
            return self.generate_po(lang)
        else:
            return self.generate_receipt(lang)

    def generate_invoice(self, lang: Lang | None = None) -> tuple[bytes, dict]:
        """Generate an invoice PDF + ground truth."""
        lang = lang or self._pick_language()
        lbl  = self._labels(lang)
        fake = self._faker(lang)

        inv_no    = f"INV-{random.randint(10000, 99999)}"
        doc_date  = self._random_date()
        due_date  = doc_date + timedelta(days=random.choice([15, 30, 45, 60]))
        vendor    = self._company(lang)
        buyer     = self._company(lang)
        items     = self._line_items(fake, lang, count=random.randint(3, 8))
        subtotal  = _round2(sum(i["total"] for i in items))
        tax_rate  = 0.18
        tax_amt   = _round2(subtotal * tax_rate)
        total     = _round2(subtotal + tax_amt)
        pay_terms = random.choice(["Net 30", "Net 15", "Net 60", "Hemen Ödeme" if lang != "en" else "Immediate"])

        gt = {
            "doc_type":       "invoice",
            "language":       lang,
            "invoice_number": inv_no,
            "date":           doc_date.strftime("%Y-%m-%d"),
            "due_date":       due_date.strftime("%Y-%m-%d"),
            "vendor_name":    vendor["name"],
            "vendor_tax_id":  vendor["tax_id"],
            "vendor_address": vendor["address"],
            "buyer_name":     buyer["name"],
            "buyer_tax_id":   buyer["tax_id"],
            "buyer_address":  buyer["address"],
            "items":          items,
            "subtotal":       subtotal,
            "tax_rate":       tax_rate,
            "tax_amount":     tax_amt,
            "total_amount":   total,
            "currency":       lbl["currency"],
            "payment_terms":  pay_terms,
            "confidence":     1.0,  # ground truth → perfect confidence
        }

        pdf_bytes = self._render_invoice_pdf(gt, lbl)
        logger.debug("Generated invoice %s (lang=%s)", inv_no, lang)
        return pdf_bytes, gt

    def generate_po(self, lang: Lang | None = None) -> tuple[bytes, dict]:
        """Generate a purchase order PDF + ground truth."""
        lang = lang or self._pick_language()
        lbl  = self._labels(lang)
        fake = self._faker(lang)

        po_no         = f"PO-{random.randint(10000, 99999)}"
        doc_date      = self._random_date()
        delivery_date = doc_date + timedelta(days=random.randint(7, 30))
        supplier      = self._company(lang)
        buyer         = self._company(lang)
        items         = self._po_items(fake, lang, count=random.randint(2, 6))
        total         = _round2(sum(i["total"] for i in items))

        gt = {
            "doc_type":       "po",
            "language":       lang,
            "po_number":      po_no,
            "date":           doc_date.strftime("%Y-%m-%d"),
            "delivery_date":  delivery_date.strftime("%Y-%m-%d"),
            "supplier_name":  supplier["name"],
            "supplier_address": supplier["address"],
            "buyer_name":     buyer["name"],
            "buyer_address":  buyer["address"],
            "items":          items,
            "total_amount":   total,
            "currency":       lbl["currency"],
            "confidence":     1.0,
        }

        pdf_bytes = self._render_po_pdf(gt, lbl)
        logger.debug("Generated PO %s (lang=%s)", po_no, lang)
        return pdf_bytes, gt

    def generate_receipt(self, lang: Lang | None = None) -> tuple[bytes, dict]:
        """Generate a receipt PDF + ground truth."""
        lang = lang or self._pick_language()
        lbl  = self._labels(lang)
        fake = self._faker(lang)

        rcpt_no  = f"RCP-{random.randint(10000, 99999)}"
        doc_date = self._random_date()
        store    = self._company(lang)
        items    = self._receipt_items(fake, lang, count=random.randint(2, 7))
        subtotal = _round2(sum(i["total"] for i in items))
        tax_rate = 0.18
        tax_amt  = _round2(subtotal * tax_rate)
        total    = _round2(subtotal + tax_amt)
        cashier  = fake.first_name()

        gt = {
            "doc_type":    "receipt",
            "language":    lang,
            "receipt_number": rcpt_no,
            "date":        doc_date.strftime("%Y-%m-%d"),
            "store_name":  store["name"],
            "store_address": store["address"],
            "cashier":     cashier,
            "items":       items,
            "subtotal":    subtotal,
            "tax_rate":    tax_rate,
            "tax_amount":  tax_amt,
            "total_amount": total,
            "currency":    lbl["currency"],
            "confidence":  1.0,
        }

        pdf_bytes = self._render_receipt_pdf(gt, lbl)
        logger.debug("Generated receipt %s (lang=%s)", rcpt_no, lang)
        return pdf_bytes, gt

    # ------------------------------------------------------------------
    # PDF Rendering
    # ------------------------------------------------------------------

    def _render_invoice_pdf(self, gt: dict, lbl: dict) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        bold   = ParagraphStyle("bold", parent=styles["Normal"], fontName=FONT_NAME_BOLD)
        right  = ParagraphStyle("right", parent=styles["Normal"], fontName=FONT_NAME, alignment=TA_RIGHT)

        story = []
        story.append(Paragraph(lbl["invoice_title"], bold))
        story.append(Spacer(1, 6*mm))

        # Header info table
        header_data = [
            [f"{lbl['invoice_no']}:", gt["invoice_number"],
             f"{lbl['date']}:", gt["date"]],
            [f"{lbl['due_date']}:", gt["due_date"],
             f"{lbl['payment_terms']}:", gt["payment_terms"]],
            [f"{lbl['vendor']}:", gt["vendor_name"],
             f"{lbl['buyer']}:", gt["buyer_name"]],
            ["", gt["vendor_address"], "", gt["buyer_address"]],
            [f"{lbl['tax_id']}:", gt["vendor_tax_id"],
             f"{lbl['tax_id']}:", gt["buyer_tax_id"]],
        ]
        ht = Table(header_data, colWidths=[35*mm, 65*mm, 35*mm, 35*mm])
        ht.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FONT_NAME),
                                ("FONTSIZE", (0,0), (-1,-1), 9),
                                ("FONTNAME", (0,0), (0,-1), FONT_NAME_BOLD),
                                ("FONTNAME", (2,0), (2,-1), FONT_NAME_BOLD),
                                ("BOTTOMPADDING", (0,0), (-1,-1), 3)]))
        story.append(ht)
        story.append(Spacer(1, 8*mm))

        # Items table
        col_hdrs = [lbl["description"], lbl["qty"], lbl["unit_price"], lbl["total"]]
        rows = [col_hdrs]
        for item in gt["items"]:
            rows.append([item["description"], str(item["quantity"]),
                         f"{item['unit_price']:.2f}", f"{item['total']:.2f}"])
        rows.append(["", "", lbl["subtotal"],  f"{gt['subtotal']:.2f}"])
        rows.append(["", "", lbl["tax"],       f"{gt['tax_amount']:.2f}"])
        rows.append(["", "", lbl["grand_total"], f"{gt['total_amount']:.2f}"])

        it = Table(rows, colWidths=[90*mm, 20*mm, 40*mm, 30*mm])
        it.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2C3E50")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), FONT_NAME_BOLD),
            ("FONTSIZE",   (0, 0), (-1,-1), 9),
            ("GRID",       (0, 0), (-1, len(gt["items"])), 0.5, colors.grey),
            ("FONTNAME",   (2, -3), (-1, -1), FONT_NAME_BOLD),
            ("LINEABOVE",  (0, -3), (-1, -3), 1, colors.black),
            ("ALIGN",      (1, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, len(gt["items"])),
             [colors.white, colors.HexColor("#F2F3F4")]),
        ]))
        story.append(it)
        doc.build(story)
        return buf.getvalue()

    def _render_po_pdf(self, gt: dict, lbl: dict) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=20*mm, rightMargin=20*mm,
                                topMargin=20*mm, bottomMargin=20*mm)
        styles = getSampleStyleSheet()
        bold   = ParagraphStyle("bold", parent=styles["Normal"], fontName=FONT_NAME_BOLD)

        story = []
        story.append(Paragraph(lbl["po_title"], bold))
        story.append(Spacer(1, 6*mm))

        header_data = [
            [f"{lbl['po_no']}:", gt["po_number"],
             f"{lbl['date']}:", gt["date"]],
            [f"{lbl['delivery_date']}:", gt["delivery_date"],
             "", ""],
            [f"{lbl['supplier']}:", gt["supplier_name"],
             f"{lbl['buyer']}:", gt["buyer_name"]],
            ["", gt["supplier_address"], "", gt["buyer_address"]],
        ]
        ht = Table(header_data, colWidths=[35*mm, 65*mm, 35*mm, 35*mm])
        ht.setStyle(TableStyle([("FONTNAME", (0,0), (-1,-1), FONT_NAME),
                                ("FONTSIZE", (0,0), (-1,-1), 9),
                                ("FONTNAME", (0,0), (0,-1), FONT_NAME_BOLD),
                                ("FONTNAME", (2,0), (2,-1), FONT_NAME_BOLD)]))
        story.append(ht)
        story.append(Spacer(1, 8*mm))

        col_hdrs = [lbl["sku"], lbl["description"], lbl["qty"], lbl["unit_price"], lbl["total"]]
        rows = [col_hdrs]
        for item in gt["items"]:
            rows.append([item["sku"], item["description"], str(item["quantity"]),
                         f"{item['unit_price']:.2f}", f"{item['total']:.2f}"])
        rows.append(["", "", "", lbl["grand_total"], f"{gt['total_amount']:.2f}"])

        it = Table(rows, colWidths=[25*mm, 80*mm, 15*mm, 35*mm, 25*mm])
        it.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A5276")),
            ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
            ("FONTNAME",   (0, 0), (-1, 0), FONT_NAME_BOLD),
            ("FONTSIZE",   (0, 0), (-1,-1), 9),
            ("GRID",       (0, 0), (-1, len(gt["items"])), 0.5, colors.grey),
            ("FONTNAME",   (3, -1), (-1, -1), FONT_NAME_BOLD),
            ("ALIGN",      (2, 0), (-1, -1), "RIGHT"),
            ("ROWBACKGROUNDS", (0, 1), (-1, len(gt["items"])),
             [colors.white, colors.HexColor("#EBF5FB")]),
        ]))
        story.append(it)
        doc.build(story)
        return buf.getvalue()

    def _render_receipt_pdf(self, gt: dict, lbl: dict) -> bytes:
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=(80*mm, 200*mm),
                                leftMargin=5*mm, rightMargin=5*mm,
                                topMargin=5*mm, bottomMargin=5*mm)
        styles = getSampleStyleSheet()
        center = ParagraphStyle("center", parent=styles["Normal"], fontName=FONT_NAME,
                                alignment=TA_CENTER, fontSize=10)
        small  = ParagraphStyle("small",  parent=styles["Normal"], fontName=FONT_NAME, fontSize=8)
        bold_c = ParagraphStyle("bold_c", parent=styles["Normal"], fontName=FONT_NAME_BOLD,
                                alignment=TA_CENTER, fontSize=11)

        story = []
        story.append(Paragraph(lbl["receipt_title"], bold_c))
        story.append(Paragraph(gt["store_name"], center))
        story.append(Paragraph(gt["store_address"], center))
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(f"{lbl['receipt_no']}: {gt['receipt_number']}  |  {lbl['date']}: {gt['date']}", small))
        story.append(Paragraph(f"{lbl['cashier']}: {gt['cashier']}", small))
        story.append(Spacer(1, 4*mm))

        rows = [[lbl["description"], lbl["qty"], lbl["total"]]]
        for item in gt["items"]:
            rows.append([item["description"], str(item["quantity"]), f"{item['total']:.2f}"])
        rows.append([lbl["subtotal"], "", f"{gt['subtotal']:.2f}"])
        rows.append([lbl["tax"],      "", f"{gt['tax_amount']:.2f}"])
        rows.append([lbl["grand_total"], "", f"{gt['total_amount']:.2f}"])

        it = Table(rows, colWidths=[40*mm, 10*mm, 20*mm])
        it.setStyle(TableStyle([
            ("FONTNAME",  (0, 0), (-1, 0), FONT_NAME_BOLD),
            ("FONTSIZE",  (0, 0), (-1,-1), 8),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black),
            ("LINEABOVE", (0, -3), (-1, -3), 0.5, colors.black),
            ("FONTNAME",  (0, -1), (-1, -1), FONT_NAME_BOLD),
            ("ALIGN",     (1, 0), (-1, -1), "RIGHT"),
        ]))
        story.append(it)
        story.append(Spacer(1, 4*mm))
        story.append(Paragraph(lbl["thank_you"], center))
        doc.build(story)
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pick_language(self) -> Lang:
        r = random.random()
        if r < self.mix_prob:
            return "mixed"
        elif r < self.mix_prob + self.tr_prob:
            return "tr"
        else:
            return "en"

    def _labels(self, lang: Lang) -> dict:
        if lang == "mixed":
            tr, en = LABELS["tr"], LABELS["en"]
            return {k: random.choice([tr[k], en[k]]) for k in tr}
        return LABELS.get(lang, LABELS["en"])

    def _faker(self, lang: Lang) -> Faker:
        if lang == "en":
            return self.faker_en
        return self.faker_tr  # tr and mixed default to Turkish Faker

    def _company(self, lang: Lang) -> dict:
        fake = self._faker(lang)
        return {
            "name":    fake.company(),
            "address": fake.address().replace("\n", ", "),
            "tax_id":  self._tax_id(),
        }

    def _tax_id(self) -> str:
        """Generate a 10-digit Turkish-style tax ID."""
        return "".join([str(random.randint(0, 9)) for _ in range(10)])

    def _random_date(self) -> datetime:
        days_back = random.randint(0, 365)
        return datetime.now() - timedelta(days=days_back)

    def _line_items(self, fake: Faker, lang: Lang, count: int) -> list[dict]:
        items = []
        for _ in range(count):
            qty   = random.randint(1, 50)
            price = _round2(random.uniform(10, 5000))
            items.append({
                "description": fake.catch_phrase() if lang == "en" else fake.bs(),
                "quantity":    qty,
                "unit_price":  price,
                "total":       _round2(qty * price),
            })
        return items

    def _po_items(self, fake: Faker, lang: Lang, count: int) -> list[dict]:
        items = []
        for _ in range(count):
            qty   = random.randint(1, 100)
            price = _round2(random.uniform(10, 5000))
            sku   = f"SKU-{random.randint(1000, 9999)}"
            items.append({
                "sku":         sku,
                "description": fake.catch_phrase() if lang == "en" else fake.bs(),
                "quantity":    qty,
                "unit_price":  price,
                "total":       _round2(qty * price),
            })
        return items

    def _receipt_items(self, fake: Faker, lang: Lang, count: int) -> list[dict]:
        items = []
        for _ in range(count):
            qty   = random.randint(1, 5)
            price = _round2(random.uniform(5, 500))
            desc  = fake.word().capitalize() if lang == "en" else fake.word().capitalize()
            items.append({
                "description": desc,
                "quantity":    qty,
                "unit_price":  price,
                "total":       _round2(qty * price),
            })
        return items
