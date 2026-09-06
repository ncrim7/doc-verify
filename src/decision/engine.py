"""
The decision layer.

Everything before this module answers "is the JSON right?". This one answers
the question the customer actually has: **should I pay this?**

Three verdicts, and the line between them is **whose** money:

    ÖDE     nothing critical found
    BEKLET  two sources disagree about an amount — the supplier is asking for
            money that was not agreed, and it can be named to the kuruş
    İNCELE  something is wrong that we cannot attribute to the supplier —
            a bad tax id, a missing field, or the document's own numbers not
            adding up

That last distinction is the one that took a real bill to learn. A subtotal
that does not reconcile is a discrepancy, but on ONE document there is no way
to tell whether the supplier billed wrongly or we misread the page — and on the
telecom bill that exposed it, it was us. Reporting that as "financial impact"
tells a bookkeeper their supplier overcharged them, which is a lie dressed as a
number. Only a CROSS-SOURCE disagreement is money; a document-internal one is
an inconsistency, and it goes to a person rather than stopping a payment.

Findings arrive from any source. Today two exist — the rule verifier reading a
single document, and the PO matcher comparing two — and the engine does not
care which. That matters: the real-document corpus contains **zero purchase
orders**, so a decision layer that only works with a PO would produce nothing
on the documents we actually have. Single-document findings are the spine; PO
matching is one more source when a PO exists.

## On confidence

`Decision.confidence` is NOT a probability and is not modelled. Inventing one
here would undo a week of removing invented numbers.

What it reports is coverage: the fraction of the extracted fields that any
check could speak to at all. Measured on 15 real documents, **42% of field
errors sit in fields with internal redundancy** — arithmetic or a check digit —
and the other 58% are invoice numbers, names, addresses and dates, which have
nothing to check them against. A verdict of ÖDE means "no check fired", never
"the document is correct", and `unverifiable_fields` names exactly where the
silence is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

__all__ = ["Verdict", "Finding", "Decision", "decide",
           "from_verification", "from_po_match"]


class Verdict(StrEnum):
    PAY = "PAY"
    HOLD = "HOLD"
    REVIEW = "REVIEW"


# Fields nothing in the system can check against anything else. Kept explicit
# rather than derived, because the honest claim is about what we CANNOT see.
_UNVERIFIABLE = frozenset({
    "invoice_number", "po_number", "receipt_number", "date", "due_date",
    "delivery_date", "vendor_name", "supplier_name", "store_name",
    "buyer_name", "vendor_address", "supplier_address", "store_address",
    "buyer_address", "payment_terms", "notes", "currency",
})


@dataclass
class Finding:
    """
    One thing worth telling a person about.

    `amount_try` is the size of the discrepancy in lira. `is_financial` says
    what that number MEANS, and the two are not the same thing — conflating
    them was a real bug, caught by running this on a real telecom bill.

    **Cross-source** (`is_financial=True`): an invoice line says 53,40 where
    the purchase order says 50,00, over 100 units. That is 340 TL the supplier
    is asking for beyond what was agreed. Real money, and a reason to stop a
    payment.

    **Document-internal** (`is_financial=False`): the page's own subtotal and
    total do not add up. The gap is real, but on one document there is no way
    to tell whether the supplier billed wrongly or we misread the page — and on
    the bill that exposed this, it was us. Calling that "financial impact"
    would tell a bookkeeper their supplier overcharged them when the truth is
    that our extraction is unreliable on that layout. It is the size of an
    inconsistency, not money at stake.
    """
    kind: str
    severity: str                      # critical | warning | info
    field: str
    source: str                        # "document" | "po_match"
    message: str
    expected: Any = None
    actual: Any = None
    amount_try: Optional[float] = None
    is_financial: bool = False
    line: Optional[int] = None         # item index; None = document level

    @property
    def is_priced(self) -> bool:
        return self.amount_try is not None and abs(self.amount_try) >= 0.01

    @property
    def holds_payment(self) -> bool:
        """Only a priced, cross-source finding is a reason to stop a payment."""
        return self.is_priced and self.is_financial and self.severity == "critical"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "severity": self.severity, "field": self.field,
            "source": self.source, "message": self.message,
            "expected": self.expected, "actual": self.actual,
            "amount_try": (round(self.amount_try, 2)
                           if self.amount_try is not None else None),
            "is_financial": self.is_financial,
        }


@dataclass
class Decision:
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)
    financial_impact_try: float = 0.0
    internal_discrepancy_try: float = 0.0
    checked_fields: int = 0
    unverifiable_fields: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> float:
        """
        Share of extracted fields that any check could speak to. Coverage, not
        probability — see the module docstring.
        """
        total = self.checked_fields + len(self.unverifiable_fields)
        return round(self.checked_fields / total, 3) if total else 0.0

    @property
    def priced(self) -> list[Finding]:
        return [f for f in self.findings if f.is_priced]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "financial_impact_try": round(self.financial_impact_try, 2),
            "internal_discrepancy_try": round(self.internal_discrepancy_try, 2),
            "coverage": self.confidence,
            "checked_fields": self.checked_fields,
            "unverifiable_fields": list(self.unverifiable_fields),
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def _num(x: Any) -> Optional[float]:
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(str(x).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def _money(x: Optional[float]) -> Optional[float]:
    """
    Round a computed impact to the kuruş, at the point it is computed.

    (53.40 - 50.00) x 100 is 339.9999999999999 in IEEE 754. Displaying that as
    340,00 TL and shipping 339.9999999999999 in the JSON would be two different
    answers to the same question, and the JSON is what an accounting system
    would import.
    """
    return None if x is None else round(x, 2)


# rule name -> (finding kind, can it be priced from expected/actual)
_DOC_KIND = {
    "total_mismatch": ("TOTAL_MISMATCH", True),
    "subtotal_mismatch": ("SUBTOTAL_MISMATCH", True),
    "item_total_mismatch": ("LINE_TOTAL_MISMATCH", True),
    "tax_id_checksum_invalid": ("TAX_ID_INVALID", False),
    "tax_id_label_captured": ("TAX_ID_MALFORMED", False),
    "seller_tax_id_missing": ("SELLER_TAX_ID_MISSING", False),
    "required_field_missing": ("FIELD_MISSING", False),
    "negative_amount": ("NEGATIVE_AMOUNT", False),
    "negative_discount": ("NEGATIVE_DISCOUNT", False),
    "tax_rate_implausible": ("TAX_RATE_IMPLAUSIBLE", False),
    "date_format_invalid": ("DATE_FORMAT", False),
    "date_implausible": ("DATE_IMPLAUSIBLE", False),
    "numeric_invalid": ("NOT_NUMERIC", False),
    "total_implausible": ("TOTAL_IMPLAUSIBLE", False),
}


def from_verification(verification: dict) -> list[Finding]:
    """
    Findings from one document, read on its own.

    An arithmetic mismatch is priced: the difference between what the page says
    and what its own numbers add up to is money that would be paid wrongly. A
    bad tax id is not priced — it is wrong, and it costs whatever a rejected
    filing costs, which is not ours to guess.
    """
    out: list[Finding] = []
    for issue in (verification or {}).get("issues", []):
        rule = issue.get("rule", "")
        kind, priceable = _DOC_KIND.get(rule, (rule.upper() or "OTHER", False))
        actual, expected = issue.get("actual"), issue.get("expected")
        impact = None
        if priceable:
            a, e = _num(actual), _num(expected)
            if a is not None and e is not None:
                impact = _money(a - e)   # positive: the page asks for more
        out.append(Finding(
            kind=kind,
            severity=issue.get("severity", "info"),
            field=issue.get("field", "?"),
            source="document",
            message=issue.get("message", ""),
            expected=expected, actual=actual,
            # NOT financial: on one document there is no way to tell whether
            # the supplier billed wrongly or we misread the page.
            amount_try=impact, is_financial=False,
        ))
    return out


def from_po_match(match_result: dict) -> list[Finding]:
    """
    Findings from comparing an invoice against a purchase order.

    This is where the impact figure becomes obvious and useful: a 3,40 TL unit
    price difference is a rounding argument, the same difference over 100 units
    is 340 TL and a reason not to pay.
    """
    out: list[Finding] = []
    field_kind = {
        "quantity": "QTY_MISMATCH",
        "unit_price": "PRICE_MISMATCH",
        "total": "LINE_TOTAL_MISMATCH",
        "description": "DESC_MISMATCH",
        "total_amount": "TOTAL_MISMATCH",
        "currency": "CURRENCY_MISMATCH",
    }

    for idx, m in enumerate(match_result.get("matches", []) or []
                            if match_result else []):
        inv_line = m.get("invoice_item") or m.get("invoice") or {}
        po_line = m.get("po_item") or m.get("po") or {}
        qty = _num(inv_line.get("quantity")) or _num(po_line.get("quantity"))
        price = _num(po_line.get("unit_price")) or _num(inv_line.get("unit_price"))

        for d in m.get("discrepancies", []) or []:
            fld = d.get("field", "")
            pv, iv = _num(d.get("po_value")), _num(d.get("invoice_value"))
            impact = None
            if pv is not None and iv is not None:
                if fld == "unit_price" and qty:
                    impact = _money((iv - pv) * qty)   # per-unit gap x units
                elif fld == "quantity" and price:
                    impact = _money((iv - pv) * price) # extra units x price
                elif fld in ("total", "total_amount"):
                    impact = _money(iv - pv)
            out.append(Finding(
                kind=field_kind.get(fld, "OTHER"),
                severity=d.get("severity", "warning"),
                field=fld, source="po_match",
                message=d.get("message", ""),
                expected=d.get("po_value"), actual=d.get("invoice_value"),
                # financial: two sources disagree, so the gap is real money
                amount_try=impact, is_financial=True, line=idx,
            ))

    for s in (match_result or {}).get("scalar_checks", []) or []:
        if s.get("severity") not in ("critical", "warning"):
            continue
        fld = s.get("field", "")
        pv, iv = _num(s.get("po_value")), _num(s.get("invoice_value"))
        out.append(Finding(
            kind=field_kind.get(fld, "OTHER"),
            severity=s.get("severity", "warning"),
            field=fld, source="po_match",
            message=s.get("message", ""),
            expected=s.get("po_value"), actual=s.get("invoice_value"),
            amount_try=_money(iv - pv) if (pv is not None and iv is not None) else None,
            is_financial=True,
        ))

    for item in (match_result or {}).get("unmatched_invoice", []) or []:
        desc = item.get("description", "") if isinstance(item, dict) else str(item)
        total = _num(item.get("total")) if isinstance(item, dict) else None
        out.append(Finding(
            kind="NOT_IN_PO", severity="critical", field="items",
            source="po_match",
            message=f"Faturada siparişte olmayan kalem: {desc}".rstrip(": "),
            actual=desc, amount_try=_money(total), is_financial=True,
        ))

    for item in (match_result or {}).get("unmatched_po", []) or []:
        desc = item.get("description", "") if isinstance(item, dict) else str(item)
        out.append(Finding(
            kind="MISSING_ITEM", severity="warning", field="items",
            source="po_match",
            message=f"Siparişte olup faturada olmayan kalem: {desc}".rstrip(": "),
            expected=desc,
        ))

    return out


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def _net_impact(priced: list[Finding]) -> float:
    """
    Add up money without counting the same money twice.

    One 3,40 TL price difference over 100 units shows up three times: as a
    PRICE_MISMATCH (340), as the line total being 340 higher, and inside the
    document total being 1.408 higher (340 plus its VAT). Summing all three
    gives 2.088 TL and tells a bookkeeper their supplier overcharged them by
    six times the real figure. That is a worse error than any accuracy
    percentage in this project, because someone would act on it.

    They are three views of one discrepancy, so:

      - within a line, take the LARGEST single figure — the others describe
        the same gap from a different angle
      - sum across lines
      - use the document-level figure only when there are no line-level ones,
        because a document total difference IS the line differences plus tax

    The result is the net amount at stake, once.
    """
    if not priced:
        return 0.0

    by_line: dict[int, list[Finding]] = {}
    document_level: list[Finding] = []
    for f in priced:
        if f.line is None:
            document_level.append(f)
        else:
            by_line.setdefault(f.line, []).append(f)

    if by_line:
        total = sum(max(g, key=lambda f: abs(f.amount_try)).amount_try
                    for g in by_line.values())
        return _money(total) or 0.0

    # nothing line-level: the document figure is the only view we have
    return _money(max(document_level,
                      key=lambda f: abs(f.amount_try)).amount_try) or 0.0

def decide(findings: list[Finding], extracted: Optional[dict] = None) -> Decision:
    """
    Turn findings into a verdict.

        HOLD    a critical finding we can price -> paying now loses that money
        REVIEW  a critical finding we cannot price -> a person must look
        PAY     no critical finding

    A warning never holds a payment on its own. Warnings are things worth
    seeing, not things worth stopping for, and a product that stops on every
    warning gets switched off.
    """
    findings = list(findings or [])
    criticals = [f for f in findings if f.severity == "critical"]

    financial = _net_impact([f for f in findings
                             if f.is_priced and f.is_financial])
    internal = _net_impact([f for f in findings
                            if f.is_priced and not f.is_financial])

    if any(f.holds_payment for f in findings):
        verdict = Verdict.HOLD
    elif criticals:
        verdict = Verdict.REVIEW
    else:
        verdict = Verdict.PAY

    checked, unverifiable = 0, []
    for key, value in (extracted or {}).items():
        if key.startswith("_") or key in ("doc_type", "language", "items"):
            continue
        if value is None or value == "":
            continue
        if key in _UNVERIFIABLE:
            unverifiable.append(key)
        else:
            checked += 1

    return Decision(
        verdict=verdict,
        findings=findings,
        financial_impact_try=financial,
        internal_discrepancy_try=internal,
        checked_fields=checked,
        unverifiable_fields=sorted(unverifiable),
    )
