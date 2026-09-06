"""
Prompt templates for structured document extraction via LLMs.
Supports invoice, purchase order, and receipt document types.
"""

SYSTEM_PROMPT = """You are an expert document extraction system.
You extract structured data from business documents with high accuracy.
You must return ONLY valid JSON — no markdown, no explanations, no extra text.
All numeric values must be plain numbers (no currency symbols, no thousand separators).
Dates must be in ISO 8601 format (YYYY-MM-DD).

MANDATORY FIELDS — these are almost always present on Turkish business documents
and MUST be extracted. Do NOT leave them null unless they are genuinely absent
after you have carefully scanned the WHOLE document:
- currency: the ISO currency CODE. If amounts use ₺ / TL or the document is Turkish,
  output "TRY". This is a currency code only — never put payment terms (e.g. "Net 60")
  or other text here.
- tax_rate: the VAT/KDV rate AS A DECIMAL FRACTION. 18% -> 0.18, 8% -> 0.08,
  1% -> 0.01, 20% -> 0.20. Never output 18 for 18%; output 0.18.
- vendor_tax_id / buyer_tax_id / supplier tax id: the tax identification number
  (Vergi No / VKN / TCKN), a 10- or 11-digit number located near each party's
  name or address. Scan the entire document; do not skip these.

Only use null when, after carefully scanning the entire document, the value is truly
not present.

TOTALS — A DOCUMENT CAN PRINT TWO DIFFERENT, BOTH-CORRECT TOTALS. Do not choose
between them; they are separate fields:
- total_amount: THIS document's own total including taxes — the sum of its own
  charge/line items. Labels: 'Vergiler Dahil Toplam Tutar', 'Toplam Fatura
  Tutarı', 'Genel Toplam', 'Total'.
- amount_payable: the amount actually due, which may additionally include a
  carried-over balance from another period, an old debt, or a rounding
  adjustment. Labels: 'Ödenecek Tutar', 'ÖDENECEK TOPLAM', 'Amount Due'.
On a utility or telecom bill these differ by lines such as 'Önceki Aydan Devir'
and 'Gelecek Aya Devir', which belong to the account balance and NOT to this
document's own total.
IF THE PAGE PRINTS ONLY ONE TOTAL — which is the normal case — put that same
value in BOTH fields. Never invent a difference that is not printed.

LINE AMOUNTS — every line follows one identity:

    total = quantity × unit_price − discount        (and excludes tax)

- `discount` is the discount AMOUNT on that line in currency, NOT a percentage.
  Labels: 'İskonto', 'İskonto Tutarı', 'İndirim', 'Discount'. If the page shows
  only a percentage, multiply it out. If the line has no discount, use 0.
- `unit_price` and `total` are both BEFORE tax. `total` is the line amount
  after the discount and before tax — the Turkish e-invoice
  LineExtensionAmount.
- Some issuers print the line's unit price and amount INCLUDING tax. If the
  page gives no pre-tax figure for that line, use null for the field rather
  than copying the gross number or deriving one.
- READ these numbers, do not derive them. When a discount applies,
  quantity × unit_price will NOT equal `total`, and that is correct — never
  adjust either one to make them agree.
- If the line's amount column is printed INCLUDING tax, do not copy it. Use the
  pre-tax figure if the page gives one (labels such as 'Vergiler Öncesi Toplam
  Tutar', 'KDV Matrahı', 'Mal Hizmet Toplam Tutarı'). If it gives none, use null
  rather than a guess.

`subtotal` is the sum of these line amounts: the document total after discounts
and before tax.

NEVER COMPUTE A TOTAL THAT IS PRINTED. If a printed subtotal or total disagrees
with the sum of the line items, report what is PRINTED. A real bill carries
discounts, late fees and carried-over balances that do not appear as line items,
so a mismatch is usually information, not an error. Reporting the true printed
value lets the mismatch be flagged; silently replacing it hides a real problem
behind an invented number.

TURKISH TEXT — TRANSCRIBE, DO NOT CORRECT. Copy every string exactly as it is
printed on the page, character for character.
- Preserve every Turkish letter as-is: ı ğ ş ç ö ü İ Ğ Ş Ç Ö Ü.
- 'ı' (dotless i) and 'i' (dotted i) are DIFFERENT letters — never swap them.
  The same holds for ğ/g, ş/s, ö/o, ü/u, ç/c.
- Do not transliterate to ASCII, do not normalise, do not fix spelling, do not
  translate. If the page says "Zımba Teli", output "Zımba Teli" — not "Zimba".
- Reproduce punctuation as printed, including quote and apostrophe characters.
- Preserve company-type suffixes exactly (A.Ş., Ltd. Şti., San. ve Tic. A.Ş.)
  — do not truncate them."""

INVOICE_SCHEMA = """{
  "doc_type": "invoice",
  "invoice_number": "string",
  "date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "vendor_name": "string",
  "vendor_tax_id": "string",
  "vendor_address": "string",
  "buyer_name": "string",
  "buyer_tax_id": "string",
  "buyer_address": "string",
  "items": [
    {
      "description": "string",
      "quantity": number,
      "unit_price": number,
      "discount": number,
      "total": number
    }
  ],
  "subtotal": number,
  "tax_rate": number,
  "tax_amount": number,
  "total_amount": number,
  "amount_payable": number,
  "currency": "string",
  "payment_terms": "string or null",
  "notes": "string or null"
}"""

PO_SCHEMA = """{
  "doc_type": "po",
  "po_number": "string",
  "date": "YYYY-MM-DD",
  "delivery_date": "YYYY-MM-DD or null",
  "supplier_name": "string",
  "supplier_address": "string",
  "buyer_name": "string",
  "buyer_address": "string",
  "items": [
    {
      "sku": "string or null",
      "description": "string",
      "quantity": number,
      "unit_price": number,
      "total": number
    }
  ],
  "total_amount": number,
  "currency": "string",
  "notes": "string or null"
}"""

RECEIPT_SCHEMA = """{
  "doc_type": "receipt",
  "receipt_number": "string",
  "date": "YYYY-MM-DD",
  "store_name": "string",
  "store_address": "string",
  "cashier": "string or null",
  "items": [
    {
      "description": "string",
      "quantity": number,
      "unit_price": number,
      "total": number
    }
  ],
  "subtotal": number,
  "tax_rate": number,
  "tax_amount": number,
  "total_amount": number,
  "currency": "string"
}"""

SCHEMAS = {
    "invoice": INVOICE_SCHEMA,
    "po": PO_SCHEMA,
    "receipt": RECEIPT_SCHEMA,
}

DOC_TYPE_NAMES = {
    "invoice": "Invoice / Fatura",
    "po": "Purchase Order / Satın Alma Siparişi",
    "receipt": "Receipt / Fiş-Makbuz",
}

# ---------------------------------------------------------------------------
# Party-role disambiguation — prepended per document type.
# Vision models tend to assign the first/left/top company box as the primary
# party regardless of its label. On a PO the ISSUER (buyer) is usually the
# letterhead, so this bias mislabels the buyer as the supplier. These notes
# force role assignment by LABEL/ROLE, never by position.
# ---------------------------------------------------------------------------
ROLE_HINTS = {
    "po": (
        "PARTY ROLE DISAMBIGUATION — assign strictly by label/role, NEVER by "
        "position on the page (first / top / left does NOT mean supplier):\n"
        "- supplier_name / supplier_address = the SELLER: the company the order "
        "is addressed TO and that will supply the goods. Labels: 'Tedarikçi', "
        "'Satıcı', 'Supplier', 'Vendor'.\n"
        "- buyer_name / buyer_address = the party that ISSUES / PLACES the order. "
        "Labels: 'Alıcı', 'Sipariş Veren', 'Sipariş Eden', 'Buyer'. This party is "
        "often the letterhead and may appear first/top/left — do NOT copy it into "
        "supplier_name.\n"
        "If two companies appear, decide supplier vs buyer only from these "
        "labels/roles.\n\n"
    ),
}


def build_extraction_prompt(doc_type: str, strategy: str = "direct") -> str:
    """
    Build the user prompt for document extraction.

    Strategies:
      - 'direct': Simple extraction prompt
      - 'cot': Chain-of-Thought extraction (reason step by step)
      - 'structured': Explicit schema-guided extraction
    """
    schema = SCHEMAS[doc_type]
    type_name = DOC_TYPE_NAMES[doc_type]

    if strategy == "direct":
        return ROLE_HINTS.get(doc_type, "") + f"""Extract all structured data from this {type_name} document image.
Return the result as a JSON object matching this exact schema:

{schema}

Return ONLY the JSON object. No explanations."""

    elif strategy == "cot":
        return ROLE_HINTS.get(doc_type, "") + f"""Analyze this {type_name} document image step by step:

1. First, identify the document language (Turkish, English, or mixed).
2. Locate the document header and extract identification fields (number, dates).
3. Identify the parties involved (vendor/supplier/store and buyer).
4. Extract each line item with its details.
5. Extract totals, tax, and payment information.
6. Verify: do the line item totals sum to the subtotal?

After your analysis, return ONLY a JSON object matching this schema:

{schema}

Think step by step, then return ONLY the final JSON."""

    elif strategy == "structured":
        return ROLE_HINTS.get(doc_type, "") + f"""You are extracting data from a {type_name} document.

REQUIRED OUTPUT SCHEMA:
{schema}

EXTRACTION RULES:
- Extract every visible field from the document image
- Numbers: plain numeric values only (no currency symbols, no thousand separators)
- Dates: ISO 8601 format (YYYY-MM-DD)
- Missing fields: use null
- Items: extract ALL line items visible in the document
- Language: the document may be in Turkish, English, or mixed

Return ONLY the JSON object. No markdown fences, no explanations."""

    elif strategy == "few_shot":
        # Few-shot: provide an example extraction to guide the model
        example = _get_few_shot_example(doc_type)
        return ROLE_HINTS.get(doc_type, "") + f"""You are extracting data from a {type_name} document.

Here is an example of a correct extraction from a similar document:

EXAMPLE INPUT: A {type_name} document with header, line items, and totals.
EXAMPLE OUTPUT:
{example}

Now extract data from the provided document image using the same JSON schema.

RULES:
- Numbers: plain numeric values only (no currency symbols)
- Dates: ISO 8601 format (YYYY-MM-DD)
- Missing fields: use null
- Totals: report what is printed; never replace a printed figure with a
  computed one

Return ONLY the JSON object."""

    elif strategy == "cot_enhanced":
        # Enhanced CoT with verification steps and structured reasoning
        return ROLE_HINTS.get(doc_type, "") + f"""You must extract structured data from this {type_name} document.

Follow these steps EXACTLY in order:

STEP 1 — LANGUAGE IDENTIFICATION:
Read the document. Is it Turkish, English, or mixed? Note the language.

STEP 2 — HEADER EXTRACTION:
Find the document number (top of page, usually labeled Fatura No / Invoice # / PO # / Receipt #).
Find all dates (issue date, due date, delivery date).
Convert all dates to YYYY-MM-DD format.

STEP 3 — PARTY EXTRACTION:
Identify vendor/supplier/store name and address.
Identify buyer name and address (if present).
Find tax IDs (Vergi No / Tax ID) for each party.

STEP 4 — LINE ITEMS:
For EACH row in the items table:
  - Read description text exactly as written
  - Read quantity (integer)
  - Read unit price (decimal number)
  - Read the total as printed. Report what is printed, even if
    quantity × unit_price disagrees with it.

STEP 5 — TOTALS:
  - Read the subtotal, tax amount and total as PRINTED
  - Do not replace a printed figure with one you computed. A bill may carry
    discounts, a carried-over balance or a late fee that is not a line item,
    so a mismatch is usually information, not an error — and reporting the
    printed value is what allows it to be flagged downstream.

STEP 6 — OUTPUT:
Return the result as JSON matching this schema:
{schema}

CRITICAL: Return ONLY the final JSON. No explanations, no markdown."""

    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def _get_few_shot_example(doc_type: str) -> str:
    """Return a realistic few-shot example for the given doc type."""
    if doc_type == "invoice":
        return '''{
  "doc_type": "invoice",
  "invoice_number": "INV-2025-0042",
  "date": "2025-06-15",
  "due_date": "2025-07-15",
  "vendor_name": "Anadolu Teknoloji A.Ş.",
  "vendor_tax_id": "1234567890",
  "vendor_address": "İstanbul, Kadıköy, Moda Cad. No:12",
  "buyer_name": "Yıldız Holding",
  "buyer_tax_id": "9876543210",
  "buyer_address": "Ankara, Çankaya, Atatürk Blv. No:5",
  "items": [
    {"description": "Dizüstü Bilgisayar", "quantity": 5, "unit_price": 15000.00, "total": 75000.00},
    {"description": "Monitör 27 inch", "quantity": 5, "unit_price": 4500.00, "total": 22500.00}
  ],
  "subtotal": 97500.00,
  "tax_amount": 17550.00,
  "total_amount": 115050.00,
  "amount_payable": 115050.00,
  "currency": "TRY",
  "payment_terms": "30 gün",
  "notes": null
}'''
    elif doc_type == "po":
        return '''{
  "doc_type": "po",
  "po_number": "PO-2025-0108",
  "date": "2025-05-20",
  "delivery_date": "2025-06-20",
  "supplier_name": "Global Supply Ltd.",
  "supplier_address": "İzmir, Bornova, Sanayi Sit. No:8",
  "buyer_name": "Mega Market A.Ş.",
  "buyer_address": "Bursa, Nilüfer, OSB Mah. No:3",
  "items": [
    {"sku": "SKU-001", "description": "Office Chair Ergonomic", "quantity": 20, "unit_price": 3200.00, "total": 64000.00},
    {"sku": "SKU-002", "description": "Standing Desk", "quantity": 10, "unit_price": 5800.00, "total": 58000.00}
  ],
  "total_amount": 122000.00,
  "currency": "TRY",
  "notes": null
}'''
    else:  # receipt
        return '''{
  "doc_type": "receipt",
  "receipt_number": "R-20250615-001",
  "date": "2025-06-15",
  "store_name": "Migros Sanal Market",
  "store_address": "Ataşehir, İstanbul",
  "cashier": "Ayşe Yılmaz",
  "items": [
    {"description": "Süt 1L", "quantity": 2, "unit_price": 42.90, "total": 85.80},
    {"description": "Ekmek", "quantity": 1, "unit_price": 12.50, "total": 12.50}
  ],
  "subtotal": 98.30,
  "tax_rate": 0.08,
  "tax_amount": 7.86,
  "total_amount": 106.16,
  "currency": "TRY"
}'''
