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

WARNING: The input may be rendered from documents where Turkish characters
(ç, ğ, ı, ö, ş, ü, İ) can be misread as 'I', 'II', 's', 'g', 'c' or dropped entirely
(e.g. 'SatIcI' instead of 'Satıcı', 'KoIullar' instead of 'Koşullar'). Auto-correct
these typos to proper Turkish words based on context. Preserve company-type suffixes
EXACTLY as written (A.Ş., Ltd. Şti., San. ve Tic. A.Ş.) — do not truncate them."""

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
      "total": number
    }
  ],
  "subtotal": number,
  "tax_rate": number,
  "tax_amount": number,
  "total_amount": number,
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
- Verify: quantity × unit_price = line total for each item

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
  - Calculate: quantity × unit_price = expected total
  - Read the actual total from the document
  - If calculated ≠ actual, use the CALCULATED value

STEP 5 — TOTALS VERIFICATION:
  - Sum all line item totals → this should equal subtotal
  - Read tax amount
  - Verify: subtotal + tax = total_amount
  - If mismatch, use the CALCULATED values

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
