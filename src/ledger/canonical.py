"""
Canonical financial document — one shape, whatever the channel.

An e-Fatura arrives as XML, an e-Arşiv as a PDF in an inbox, a small shop's
receipt as a WhatsApp photo. They are the same economic event and every check
downstream should be blind to how it arrived.

    XML   -> normalise ------\\
    PDF   -> extract -> normalise -> CanonicalDocument -> checks
    photo -> extract ---------/

Measured on a real 1.200-invoice month: ~71% XML, ~25% e-Arşiv PDF, ~4% paper.
The XML path needs no extraction at all, so building the checks on top of a
canonical shape rather than on top of the extractor is what keeps three quarters
of the volume from paying for OCR it does not need.

## Why identity is its own problem

The same invoice reaching a company twice through different channels is not
caught by comparing invoice numbers, because the number is not written the same
way twice:

    GIB2026000012345     as printed on the e-Fatura
    GIB-12345            as a person typed it from the PDF
    " GIB2026000012345"  with a leading space

An ERP comparing strings sees three different invoices and stays quiet. That
silence cost one real company 42.000 TL, discovered 25 days later when the
supplier said they had been paid twice.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

__all__ = ["CanonicalDocument", "normalise_name", "normalise_doc_number",
           "number_core", "Channel"]


class Channel:
    """How the document reached us. Kept as plain strings, not an enum: the
    list grows with each customer and a new channel must never need a code
    change to be recorded."""
    EFATURA = "efatura_xml"
    EARSIV_PDF = "earsiv_pdf"
    EMAIL_PDF = "email_pdf"
    PHOTO = "photo"
    PAPER = "paper"
    MANUAL = "manual"


# Turkish casefolding: İ/ı are not i/I. str.lower() maps İ to "i̇" (i + combining
# dot) which then compares unequal to "i". Handle the pair explicitly before
# folding the rest.
_TR_LOWER = str.maketrans({"İ": "i", "I": "ı", "Ş": "ş", "Ğ": "ğ",
                           "Ü": "ü", "Ö": "ö", "Ç": "ç"})
_LEGAL_SUFFIX = re.compile(
    r"\b(a\.?ş\.?|ltd\.?|şti\.?|san\.?|tic\.?|ve|a\.?s\.?|"
    r"anonim|limited|şirketi|sirketi)\b", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^0-9a-zçğıöşü]+")


def normalise_name(value: Any) -> str:
    """
    Fold a company name to something comparable.

    'TTNET ANONİM ŞİRKETİ', 'TTNET A.Ş.' and 'Ttnet AS' are one supplier. The
    legal-form words carry no identity and are dropped; what remains is the
    part a person would say out loud.
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFC", str(value)).translate(_TR_LOWER).lower()
    s = _LEGAL_SUFFIX.sub(" ", s)
    return " ".join(_NON_ALNUM.sub(" ", s).split())


def normalise_doc_number(value: Any) -> str:
    """
    Strip everything that is not a letter or digit, and casefold — with ASCII
    rules, NOT Turkish ones.

    A document number is a code, not a word. 'GIB' is an acronym, and under
    Turkish casefolding its I becomes ı, so 'GIB-2026/12345' and
    'gib202612345' would stop matching each other. Names get the Turkish
    treatment because they are language; numbers do not because they are not.
    """
    if value is None:
        return ""
    s = unicodedata.normalize("NFC", str(value)).lower()
    return _NON_ALNUM.sub("", s)


def number_core(value: Any) -> str:
    """
    The digit run at the end of a document number, without leading zeros.

    This is what survives a person retyping it. 'GIB2026000012345' and
    'GIB-12345' share the core '12345' as a suffix, which is the signature of
    a short-form entry — and exactly the case an exact-match check misses.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.lstrip("0")


@dataclass
class CanonicalDocument:
    """
    One financial document, however it arrived.

    Identity fields are the ones used to decide whether two records are the
    same economic event. Everything else travels along for the checks and for
    showing a person why something was flagged.
    """
    # --- identity -----------------------------------------------------------
    supplier_tax_id: Optional[str] = None
    supplier_name: Optional[str] = None
    doc_number: Optional[str] = None
    issue_date: Optional[date] = None
    total_amount: Optional[float] = None
    currency: str = "TRY"

    # --- provenance ---------------------------------------------------------
    channel: str = Channel.MANUAL
    source_ref: Optional[str] = None        # filename, message id, ETTN…
    doc_type: str = "invoice"

    # --- everything else ----------------------------------------------------
    raw: dict = field(default_factory=dict)

    # --- derived, computed once --------------------------------------------
    @property
    def supplier_key(self) -> str:
        """Prefer the tax id; it is the only supplier field with a check digit."""
        tax = re.sub(r"\D", "", self.supplier_tax_id or "")
        return tax or normalise_name(self.supplier_name)

    @property
    def number_key(self) -> str:
        return normalise_doc_number(self.doc_number)

    @property
    def number_core(self) -> str:
        return number_core(self.doc_number)

    @property
    def identity_key(self) -> str:
        """Exact identity: same supplier, same document number."""
        return f"{self.supplier_key}|{self.number_key}"

    @property
    def amount_key(self) -> Optional[int]:
        """Amount in kuruş, so float noise never splits one document in two."""
        if self.total_amount is None:
            return None
        return round(float(self.total_amount) * 100)

    def to_dict(self) -> dict:
        return {
            "supplier_tax_id": self.supplier_tax_id,
            "supplier_name": self.supplier_name,
            "doc_number": self.doc_number,
            "issue_date": self.issue_date.isoformat() if self.issue_date else None,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "channel": self.channel,
            "source_ref": self.source_ref,
            "doc_type": self.doc_type,
        }

    # --- builders -----------------------------------------------------------

    @classmethod
    def from_extraction(cls, data: dict, channel: str = Channel.EMAIL_PDF,
                        source_ref: Optional[str] = None,
                        doc_type: str = "invoice") -> "CanonicalDocument":
        """Build from whatever the extractor produced for a PDF or a photo."""
        d = data or {}
        number = (d.get("invoice_number") or d.get("receipt_number")
                  or d.get("po_number"))
        supplier = (d.get("vendor_name") or d.get("supplier_name")
                    or d.get("store_name"))
        tax = (d.get("vendor_tax_id") or d.get("supplier_tax_id")
               or d.get("store_tax_id"))
        return cls(
            supplier_tax_id=tax, supplier_name=supplier, doc_number=number,
            issue_date=_as_date(d.get("date")),
            total_amount=_as_float(d.get("total_amount")),
            currency=(d.get("currency") or "TRY"),
            channel=channel, source_ref=source_ref, doc_type=doc_type,
            raw=dict(d),
        )

    @classmethod
    def from_efatura(cls, fields: dict,
                     source_ref: Optional[str] = None) -> "CanonicalDocument":
        """
        Build from structured e-Fatura data — no extraction involved.

        Deliberately the same constructor shape as the PDF path. Roughly three
        quarters of a real company's volume comes in here, and it must not pay
        the cost or the error rate of the vision path.
        """
        return cls(
            supplier_tax_id=fields.get("supplier_tax_id"),
            supplier_name=fields.get("supplier_name"),
            doc_number=fields.get("doc_number") or fields.get("invoice_number"),
            issue_date=_as_date(fields.get("issue_date") or fields.get("date")),
            total_amount=_as_float(fields.get("total_amount")),
            currency=fields.get("currency") or "TRY",
            channel=Channel.EFATURA,
            source_ref=source_ref or fields.get("ettn"),
            raw=dict(fields),
        )


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _as_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(str(value).replace(" ", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
