"""
Invoice identity resolution and duplicate risk.

Not "is this a duplicate" — **"have we seen this economic event before, and how
sure are we?"** The difference matters because the wrong answer is cheap in one
direction and ruinous in the other.

## The failure mode we are designing against is not the missed duplicate

An ERP already warns on an exact repeat of a document number. It does not work,
and the reason is instructive: a person entering eighty invoices a day presses
Enter through the warning, because the same number legitimately recurs for a
credit note or a correction. **The check exists and is defeated by alert
fatigue.**

So a flag that fires often is worse than no flag. Precision in the top bucket
buys the right to be believed; recall does not. Everything below is arranged
around that.

## What actually happened

One real company, one month: three duplicate cases, two caught by a person
noticing the amount, **one paid — 42.000 TL**. The same invoice arrived once as
an e-Arşiv PDF and once as an e-Fatura, was entered by two people on different
days under different document numbers, and surfaced 25 days later when the
supplier said they had been paid twice.

No system saw both: the integrator only knows the XML it delivered, and the ERP
compared two strings that did not match.

## And the case that must NOT fire

Two invoices in our own corpus: same supplier, same 20,00 USD, one in April and
one in June. A monthly subscription. A rule built only on "same supplier, same
amount" calls that a duplicate every month until the user stops reading.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Optional

from src.decision.engine import Basis, Finding
from src.ledger.canonical import CanonicalDocument
from src.ledger.store import DocumentStore

__all__ = ["Risk", "Match", "DuplicateCheck", "check_duplicates"]


class Risk(StrEnum):
    """
    How much a person should care, which is a different axis from `Basis`.

    `Basis` says what kind of claim a finding makes. `Risk` says where it goes
    in the queue. Collapsing them would lose the epistemics; keeping them apart
    means the queue can sort by attention while each finding still states what
    it actually asserts.

    Deliberately three named levels and **no 0-100 score**. A number implies a
    calibration nobody has measured, which is the same mistake as an invented
    `confidence: 0.96`. A score comes when there is data to fit it to.
    """
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class Match:
    """One earlier document that resembles this one, and why."""
    row_id: int
    reason: str
    risk: Risk
    doc_number: Optional[str]
    issue_date: Optional[str]
    total_amount: Optional[float]
    channel: Optional[str]
    days_apart: Optional[int] = None
    same_channel: bool = False

    def to_dict(self) -> dict:
        return {
            "row_id": self.row_id, "reason": self.reason, "risk": self.risk.value,
            "doc_number": self.doc_number, "issue_date": self.issue_date,
            "total_amount": self.total_amount, "channel": self.channel,
            "days_apart": self.days_apart, "same_channel": self.same_channel,
        }


@dataclass
class DuplicateCheck:
    matches: list[Match]
    recurring: bool = False          # a monthly-shaped pattern, not a repeat
    recurring_note: str = ""

    @property
    def risk(self) -> Risk:
        if not self.matches:
            return Risk.LOW
        order = {Risk.HIGH: 0, Risk.MEDIUM: 1, Risk.LOW: 2}
        return min((m.risk for m in self.matches), key=lambda r: order[r])

    def to_dict(self) -> dict:
        return {"risk": self.risk.value, "recurring": self.recurring,
                "recurring_note": self.recurring_note,
                "matches": [m.to_dict() for m in self.matches]}


# A supplier that bills the same amount at a steady monthly rhythm is a
# subscription, a rent, a service contract, and the next one is expected rather
# than suspicious.
#
# The threshold is deliberately slow. The two errors here cost very different
# amounts: damping too eagerly hides a real duplicate — 42.000 TL in the case
# this feature was built for — while damping too late costs one extra MEDIUM
# row in a queue. So three PRIOR documents at a steady rhythm are required, not
# two: two points make a coincidence, three make a pattern.
_RECURRING_MIN_PRIOR = 3
_RECURRING_LO, _RECURRING_HI = 25, 35      # days between, "monthly"


def _days_between(a: Optional[date], b: Optional[str]) -> Optional[int]:
    if a is None or not b:
        return None
    try:
        return abs((a - date.fromisoformat(b)).days)
    except ValueError:
        return None


def _looks_recurring(rows: list[sqlite3.Row], amount_key: Optional[int]) -> bool:
    """Same amount, at a monthly rhythm, at least three times."""
    if amount_key is None:
        return False
    dates = sorted(
        date.fromisoformat(r["issue_date"]) for r in rows
        if r["issue_date"] and r["amount_key"] == amount_key
    ) if rows else []
    if len(dates) < _RECURRING_MIN_PRIOR:
        return False
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    return bool(gaps) and all(_RECURRING_LO <= g <= _RECURRING_HI for g in gaps)


def check_duplicates(doc: CanonicalDocument, store: DocumentStore,
                     window_days: int = 10) -> DuplicateCheck:
    """
    Look for earlier records of the same economic event.

    Three tiers, strongest first, and a document is reported once at its
    strongest reason:

      HIGH    same supplier, same document number
      HIGH    same supplier, same amount, and the numbers share a digit core —
              the short-form retype that an exact match misses
      MEDIUM  same supplier, same amount, within the window, numbers unrelated

    **Only the third uses a date window, and it is deliberately short.** Tiers
    one and two are identity: two records naming the same document are the same
    document whenever they arrive. Tier three is a guess from a coincidence of
    supplier and amount, so it needs the events to be close together to mean
    anything — the real case that prompted this feature had the two entries
    days apart, made by two people from two channels.

    Ten days also keeps monthly billing out of tier three entirely. A 30-day
    window would collect every subscription, rent and service contract in the
    ledger, and a queue that fills with those is a queue nobody reads.
    """
    seen: dict[int, Match] = {}

    # tier 1 — the same number, character for character after normalisation
    for r in store.by_identity(doc):
        seen[r["id"]] = Match(
            row_id=r["id"],
            reason="Aynı tedarikçi, aynı belge numarası.",
            risk=Risk.HIGH, doc_number=r["doc_number"],
            issue_date=r["issue_date"], total_amount=r["total_amount"],
            channel=r["channel"],
            days_apart=_days_between(doc.issue_date, r["issue_date"]),
            same_channel=r["channel"] == doc.channel,
        )

    # tier 2 — the retype. Only counts when the amount agrees too: a shared
    # digit tail on its own is a coincidence waiting to happen.
    if doc.amount_key is not None:
        for r in store.by_number_core(doc):
            if r["id"] in seen or r["amount_key"] != doc.amount_key:
                continue
            seen[r["id"]] = Match(
                row_id=r["id"],
                reason=("Aynı tedarikçi ve aynı tutar; belge numaraları farklı "
                        "yazılmış ama aynı sayıyı taşıyor."),
                risk=Risk.HIGH, doc_number=r["doc_number"],
                issue_date=r["issue_date"], total_amount=r["total_amount"],
                channel=r["channel"],
                days_apart=_days_between(doc.issue_date, r["issue_date"]),
                same_channel=r["channel"] == doc.channel,
            )

    # tier 3 — same supplier and amount, close in time, numbers unrelated
    for r in store.by_amount_near_date(doc, days=window_days):
        if r["id"] in seen:
            continue
        seen[r["id"]] = Match(
            row_id=r["id"],
            reason=(f"Aynı tedarikçi ve aynı tutar, {window_days} gün içinde. "
                    f"Belge numaraları farklı."),
            risk=Risk.MEDIUM, doc_number=r["doc_number"],
            issue_date=r["issue_date"], total_amount=r["total_amount"],
            channel=r["channel"],
            days_apart=_days_between(doc.issue_date, r["issue_date"]),
            same_channel=r["channel"] == doc.channel,
        )

    matches = sorted(seen.values(), key=lambda m: (m.risk != Risk.HIGH,
                                                   m.days_apart or 9999))
    result = DuplicateCheck(matches=matches)

    # The subscription damper. A steady monthly amount from one supplier is a
    # pattern, and the next one is expected. It never touches tier 1 or 2: an
    # identical document NUMBER is a repeat whatever the rhythm.
    if any(m.risk is Risk.MEDIUM for m in matches):
        history = store.supplier_history(doc)
        if _looks_recurring(history, doc.amount_key):
            result.recurring = True
            result.recurring_note = (
                "Bu tedarikçiden aynı tutar düzenli aylık aralıklarla geliyor; "
                "abonelik veya sabit hizmet bedeli olabilir.")
            for m in matches:
                if m.risk is Risk.MEDIUM:
                    m.risk = Risk.LOW
                    m.reason += " Düzenli aylık desene uyuyor."

    return result


def to_findings(check: DuplicateCheck, doc: CanonicalDocument) -> list[Finding]:
    """
    Turn the check into findings the decision layer already understands.

    `Basis.CROSS_SOURCE`, because this is two records disagreeing rather than
    one page disagreeing with itself — but with **no amount attached**. A
    suspected duplicate is not money the supplier is asking for beyond an
    agreement; it is money we might pay twice, and only a person can tell which
    of the two records is real. Pricing it would add the whole invoice to a
    total of discrepancies where it does not belong.

    So a HIGH match sets `blocks_payment` instead. The 42.000 TL that was
    actually lost left because nothing stopped the payment run — not because
    nobody could compute a difference.
    """
    out: list[Finding] = []
    for m in check.matches:
        if m.risk is Risk.LOW:
            severity = "info"
        elif m.risk is Risk.MEDIUM:
            severity = "warning"
        else:
            severity = "critical"
        when = f" ({m.issue_date})" if m.issue_date else ""
        channel = ""
        if m.channel and not m.same_channel:
            channel = " Belge başka bir kanaldan da alınmış."
        out.append(Finding(
            kind="POSSIBLE_DUPLICATE",
            severity=severity,
            field="_document",
            source="ledger",
            basis=Basis.CROSS_SOURCE,
            message=(f"{m.reason} Önceki belge: {m.doc_number or '?'}{when}."
                     f"{channel}"),
            expected=m.doc_number,
            actual=doc.doc_number,
            blocks_payment=(m.risk is Risk.HIGH),
        ))
    return out
