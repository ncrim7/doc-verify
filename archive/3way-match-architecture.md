# 3-Way Match — design note

Reference for the anomaly-detection layer. The SAP clients in this folder
(`erp_client.py.txt`, `aip_client.py.txt`) are dead — kept only so the wiring
they implemented is not lost. The **logic** below is portable; the transport
(SAP CAP / OData) is not.

## What ships today: 2-way (PO ↔ Invoice)

`src/matching/po_invoice_matcher.py` — pure stdlib, no I/O. Input: two extracted
dicts (`po_data`, `invoice_data`). Output: `matches`, `unmatched_po`,
`unmatched_invoice`, `scalar_checks`, `summary`.

Line matching:
1. exact SKU match;
2. else fuzzy description similarity (`SequenceMatcher`, threshold 0.65);
3. per matched pair, compare `quantity` (tol 0.001), `unit_price` (tol 2%),
   `total` (tol 2%), and description similarity (< 0.80 → warning).

Scalar checks: `total_amount` (warning only — PO is pre-tax, invoice often
includes VAT), `currency` (critical; TL/TRY and other aliases normalised),
`supplier_name`↔`vendor_name` (Turkish diacritics folded before compare).

Verdict: any critical → `REJECT`; any warning or unmatched line → `REVIEW`;
else `APPROVE`.

`src/reporting/humanizer.py` turns that into `{verdict, problems[]}` in plain
Turkish — "ÖDEMEYİ ONAYLAYABİLİRSİNİZ" / "İNCELEME GEREKLİ" / "ÖDEME DURDURULDU"
plus, per issue, a label + what-it-means + what-to-do line.

## What's missing for true 3-way: the Goods Receipt (GR) leg

3-way match adds "what was actually delivered" between the order and the bill.
The rule set to add:

| Check | Rule | Severity |
|---|---|---|
| Over-billing vs delivery | `invoice.qty` ≤ `gr.qty_received` per line | critical |
| Partial delivery | `gr.qty_received` < `po.qty` → invoice should be partial too | warning |
| Price authority | `invoice.unit_price` == `po.unit_price` (GR carries no price) | critical |
| Receipt exists | a GR posted for the PO before the invoice date | warning |

Implementation shape: a sibling `gr_invoice_matcher.py` or a `gr_data` argument
to the existing matcher that runs the qty checks against received quantities
instead of ordered quantities, then merges discrepancies into the same
`matches[].discrepancies` / `summary` structure so the humanizer is unchanged.

## Porting note

The matcher and humanizer have zero SAP dependency and move to any backend as-is.
A production integration would feed them dicts from:
- an accounting API (QuickBooks / Xero / Logo / Paraşüt) for PO + GR records, and
- the extraction pipeline for the invoice.

The old `erp_client.py` did this against a local SAP CAP OData v4 service
(`localhost:4004`); `aip_client.py` against an in-house SAPUI5/PostgreSQL app
(`localhost:3001`). Both are out of scope for this product (target customers do
not run SAP) and are archived here for reference only.
