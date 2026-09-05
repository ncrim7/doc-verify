"""
Rule-based verification for extracted document data.
Checks mathematical consistency, format validity, and semantic plausibility.
"""
import re
import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RuleBasedVerifier:
    """
    Deterministic rule-based verifier for extracted document fields.
    Produces a structured report with severity levels and auto-corrections.
    """

    # Issues an LLM correction pass must never be given.
    #
    # The test is not "is this field wrong" — it is "does telling a model about
    # this invite it to manufacture an answer". A failed check digit does:
    # measured 2026-09-03, the agent responded by recomputing the check digit
    # on five documents rather than re-reading the page, and re-verification
    # then reported OK. In principle a transposed digit IS recoverable by
    # looking again, so this is a restraint imposed by observed behaviour, not
    # by logic — see P1-8b for the better shape, where correction may propose a
    # value but never clears the flag.
    #
    # Deliberately narrow. `seller_tax_id_missing` is NOT here: a VKN that is
    # printed on the page and was not read is exactly what re-reading fixes.
    # It stays out of the agent's way through its severity, not by being
    # mislabelled uncorrectable.
    UNCORRECTABLE_RULES = frozenset({"tax_id_checksum_invalid"})

    @classmethod
    def is_correctable(cls, issue: dict) -> bool:
        """Whether an issue may be handed to the LLM correction agent."""
        if "correctable" in issue:
            return bool(issue["correctable"])
        return issue.get("rule") not in cls.UNCORRECTABLE_RULES

    def verify(self, extracted: dict, doc_type: str) -> dict:
        """
        Run all applicable rules on extracted data.

        Returns:
            {
                'valid': bool,
                'issues': [...],
                'auto_corrections': {...},
                'severity_summary': {'critical': N, 'warning': N, 'info': N},
                'score': float (0-1),
            }
        """
        issues: list[dict] = []
        corrections: dict[str, Any] = {}

        # --- Common rules ---
        issues += self._check_required_fields(extracted, doc_type)
        issues += self._check_date_formats(extracted)
        issues += self._check_numeric_fields(extracted)

        # --- Type-specific rules ---
        if doc_type == "invoice":
            i, c = self._check_invoice_math(extracted)
            issues += i
            corrections.update(c)
        elif doc_type == "po":
            i, c = self._check_po_math(extracted)
            issues += i
            corrections.update(c)
        elif doc_type == "receipt":
            i, c = self._check_receipt_math(extracted)
            issues += i
            corrections.update(c)

        # --- Plausibility checks ---
        issues += self._check_plausibility(extracted, doc_type)
        issues += self._check_tax_ids(extracted, doc_type)

        # Severity summary
        severity = {"critical": 0, "warning": 0, "info": 0}
        for issue in issues:
            severity[issue.get("severity", "info")] += 1

        # Score: 1.0 = perfect, deductions per severity
        score = max(0.0, 1.0 - severity["critical"] * 0.20 - severity["warning"] * 0.05 - severity["info"] * 0.01)

        return {
            "valid": severity["critical"] == 0,
            "issues": issues,
            "auto_corrections": corrections,
            "severity_summary": severity,
            "score": round(score, 4),
        }

    # ------------------------------------------------------------------
    # Required fields
    # ------------------------------------------------------------------

    REQUIRED = {
        "invoice": ["invoice_number", "date", "vendor_name", "items", "total_amount"],
        "po": ["po_number", "date", "supplier_name", "items", "total_amount"],
        "receipt": ["receipt_number", "date", "store_name", "items", "total_amount"],
    }

    def _check_required_fields(self, extracted: dict, doc_type: str) -> list[dict]:
        issues = []
        for field in self.REQUIRED.get(doc_type, []):
            val = extracted.get(field)
            if val is None or val == "" or val == []:
                issues.append({
                    "rule": "required_field_missing",
                    "field": field,
                    "severity": "critical",
                    "message": f"Required field '{field}' is missing or empty",
                })
        return issues

    # ------------------------------------------------------------------
    # Date format
    # ------------------------------------------------------------------

    def _check_date_formats(self, extracted: dict) -> list[dict]:
        issues = []
        date_fields = ["date", "due_date", "delivery_date"]
        for field in date_fields:
            val = extracted.get(field)
            if val is None:
                continue
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(val)):
                issues.append({
                    "rule": "date_format_invalid",
                    "field": field,
                    "severity": "warning",
                    "message": f"Date '{val}' not in YYYY-MM-DD format",
                    "value": val,
                })
            else:
                try:
                    d = datetime.strptime(str(val), "%Y-%m-%d")
                    if d.year < 2000 or d.year > 2030:
                        issues.append({
                            "rule": "date_implausible",
                            "field": field,
                            "severity": "warning",
                            "message": f"Date '{val}' has implausible year",
                            "value": val,
                        })
                except ValueError:
                    issues.append({
                        "rule": "date_invalid",
                        "field": field,
                        "severity": "warning",
                        "message": f"Date '{val}' is not a valid date",
                        "value": val,
                    })
        return issues

    # ------------------------------------------------------------------
    # Numeric validation
    # ------------------------------------------------------------------

    def _check_numeric_fields(self, extracted: dict) -> list[dict]:
        issues = []
        numeric_top = ["subtotal", "tax_amount", "total_amount", "change_amount"]
        for field in numeric_top:
            val = extracted.get(field)
            if val is None:
                continue
            if not self._is_numeric(val):
                issues.append({
                    "rule": "numeric_invalid",
                    "field": field,
                    "severity": "warning",
                    "message": f"Field '{field}' value '{val}' is not numeric",
                    "value": val,
                })
            elif float(val) < 0:
                issues.append({
                    "rule": "negative_amount",
                    "field": field,
                    "severity": "warning",
                    "message": f"Field '{field}' has negative value: {val}",
                    "value": val,
                })
        return issues

    # ------------------------------------------------------------------
    # Invoice math: items → subtotal, subtotal + tax → total
    # ------------------------------------------------------------------

    def _check_invoice_math(self, extracted: dict) -> tuple[list[dict], dict]:
        issues = []
        corrections = {}
        items = extracted.get("items", [])

        # Item-level: qty * unit_price = total
        for i, item in enumerate(items):
            qty = self._to_float(item.get("quantity"))
            price = self._to_float(item.get("unit_price"))
            total = self._to_float(item.get("total"))
            if qty is not None and price is not None and total is not None:
                expected = round(qty * price, 2)
                if abs(expected - total) > 0.02:
                    issues.append({
                        "rule": "item_total_mismatch",
                        "field": f"items[{i}].total",
                        "severity": "critical",
                        "message": f"Item {i}: {qty}×{price}={expected}, got {total}",
                        "expected": expected,
                        "actual": total,
                    })
                    # No auto-correction. quantity x unit_price is inference;
                    # the printed line total is evidence. On a real e-arsiv
                    # invoice a 70% discount sits between them, and writing the
                    # product back replaced 20,83 with 69,42. The mismatch is
                    # already critical, so the document reaches a human --
                    # which is the whole point. P0-7.

        # Subtotal = sum of item totals
        subtotal = self._to_float(extracted.get("subtotal"))
        if subtotal is not None and items:
            item_sum = sum(self._to_float(it.get("total", 0)) or 0 for it in items)
            item_sum = round(item_sum, 2)
            if abs(item_sum - subtotal) > 0.02:
                issues.append({
                    "rule": "subtotal_mismatch",
                    "field": "subtotal",
                    "severity": "critical",
                    "message": f"Subtotal {subtotal} ≠ sum of items {item_sum}",
                    "expected": item_sum,
                    "actual": subtotal,
                })
                # No auto-correction. A subtotal printed on the page is
                # evidence; our sum is inference. Flag it, never overwrite it.
                # P0-4 — docs/measurements/2026-09-02-real-pilot.md

        # Total = subtotal + tax
        total = self._to_float(extracted.get("total_amount"))
        tax = self._to_float(extracted.get("tax_amount"))
        if total is not None and subtotal is not None and tax is not None:
            expected_total = round(subtotal + tax, 2)
            if abs(expected_total - total) > 0.02:
                issues.append({
                    "rule": "total_mismatch",
                    "field": "total_amount",
                    "severity": "critical",
                    "message": f"Total {total} ≠ subtotal({subtotal}) + tax({tax}) = {expected_total}",
                    "expected": expected_total,
                    "actual": total,
                })
                # No auto-correction — see above. On a bill carrying discounts,
                # a carried-over balance, a late fee or two tax bases this
                # mismatch is real information, not an error to paper over.

        return issues, corrections

    def _check_po_math(self, extracted: dict) -> tuple[list[dict], dict]:
        """PO: item totals + overall total check."""
        issues = []
        corrections = {}
        items = extracted.get("items", [])

        for i, item in enumerate(items):
            qty = self._to_float(item.get("quantity"))
            price = self._to_float(item.get("unit_price"))
            total = self._to_float(item.get("total"))
            if qty is not None and price is not None and total is not None:
                expected = round(qty * price, 2)
                if abs(expected - total) > 0.02:
                    issues.append({
                        "rule": "item_total_mismatch",
                        "field": f"items[{i}].total",
                        "severity": "critical",
                        "message": f"PO item {i}: {qty}×{price}={expected}, got {total}",
                        "expected": expected,
                        "actual": total,
                    })
                    # No auto-correction. quantity x unit_price is inference;
                    # the printed line total is evidence. On a real e-arsiv
                    # invoice a 70% discount sits between them, and writing the
                    # product back replaced 20,83 with 69,42. The mismatch is
                    # already critical, so the document reaches a human --
                    # which is the whole point. P0-7.

        total = self._to_float(extracted.get("total_amount"))
        if total is not None and items:
            item_sum = sum(self._to_float(it.get("total", 0)) or 0 for it in items)
            item_sum = round(item_sum, 2)
            if abs(item_sum - total) > 0.02:
                issues.append({
                    "rule": "total_mismatch",
                    "field": "total_amount",
                    "severity": "critical",
                    "message": f"PO total {total} ≠ sum of items {item_sum}",
                    "expected": item_sum,
                    "actual": total,
                })
                # No auto-correction — a printed PO total is evidence. P0-4.

        return issues, corrections

    def _check_receipt_math(self, extracted: dict) -> tuple[list[dict], dict]:
        """Receipt: item totals + subtotal + tax → total."""
        issues = []
        corrections = {}
        items = extracted.get("items", [])

        # Item: qty * unit_price = total
        for i, item in enumerate(items):
            qty = self._to_float(item.get("quantity"))
            price = self._to_float(item.get("unit_price"))
            total = self._to_float(item.get("total"))
            if qty is not None and price is not None and total is not None:
                expected = round(qty * price, 2)
                if abs(expected - total) > 0.02:
                    issues.append({
                        "rule": "item_total_mismatch",
                        "field": f"items[{i}].total",
                        "severity": "critical",
                        "message": f"Receipt item {i}: {qty}×{price}={expected}, got {total}",
                        "expected": expected,
                        "actual": total,
                    })
                    # No auto-correction. quantity x unit_price is inference;
                    # the printed line total is evidence. On a real e-arsiv
                    # invoice a 70% discount sits between them, and writing the
                    # product back replaced 20,83 with 69,42. The mismatch is
                    # already critical, so the document reaches a human --
                    # which is the whole point. P0-7.

        # Subtotal = sum of item totals
        subtotal = self._to_float(extracted.get("subtotal"))
        if subtotal is not None and items:
            item_sum = sum(self._to_float(it.get("total", 0)) or 0 for it in items)
            item_sum = round(item_sum, 2)
            if abs(item_sum - subtotal) > 0.02:
                issues.append({
                    "rule": "subtotal_mismatch",
                    "field": "subtotal",
                    "severity": "critical",
                    "message": f"Receipt subtotal {subtotal} ≠ sum of items {item_sum}",
                    "expected": item_sum,
                    "actual": subtotal,
                })

        # Total = subtotal + tax
        total = self._to_float(extracted.get("total_amount"))
        tax = self._to_float(extracted.get("tax_amount"))
        if total is not None and subtotal is not None and tax is not None:
            expected_total = round(subtotal + tax, 2)
            if abs(expected_total - total) > 0.02:
                issues.append({
                    "rule": "total_mismatch",
                    "field": "total_amount",
                    "severity": "critical",
                    "message": f"Receipt total {total} ≠ {subtotal}+{tax}={expected_total}",
                    "expected": expected_total,
                    "actual": total,
                })

        return issues, corrections

    # ------------------------------------------------------------------
    # Plausibility
    # ------------------------------------------------------------------

    def _check_plausibility(self, extracted: dict, doc_type: str) -> list[dict]:
        issues = []

        # Tax rate sanity (if both subtotal and tax exist)
        subtotal = self._to_float(extracted.get("subtotal"))
        tax = self._to_float(extracted.get("tax_amount"))
        if subtotal and tax and subtotal > 0:
            rate = tax / subtotal
            if rate > 0.50:
                issues.append({
                    "rule": "tax_rate_implausible",
                    "field": "tax_amount",
                    "severity": "warning",
                    "message": f"Tax rate {rate:.0%} seems too high (>{50}%)",
                })

        # Item count sanity
        items = extracted.get("items", [])
        if len(items) > 50:
            issues.append({
                "rule": "too_many_items",
                "field": "items",
                "severity": "info",
                "message": f"Document has {len(items)} items — unusually large",
            })

        # Total amount sanity
        total = self._to_float(extracted.get("total_amount"))
        if total is not None and total > 10_000_000:
            issues.append({
                "rule": "total_implausible",
                "field": "total_amount",
                "severity": "warning",
                "message": f"Total amount {total:,.2f} seems unusually large",
            })

        return issues

    # ------------------------------------------------------------------
    # Tax identifiers (P1-6)
    # ------------------------------------------------------------------

    TAX_ID_FIELDS = ("vendor_tax_id", "buyer_tax_id", "supplier_tax_id",
                     "store_tax_id", "tax_id")

    # A failed check digit means the number is provably wrong: either the model
    # misread it or the document carries a bogus one. Both need a human before
    # anything is posted, so this is critical. Flip to "warning" if throughput
    # ever matters more than correctness here — it is deliberately one constant.
    TAX_ID_SEVERITY = "critical"

    # An ABSENT seller tax id is a different case from a wrong one. Under UBL-TR
    # the seller's PartyIdentification (VKN for a company, TCKN for a person) is
    # mandatory on every Turkish e-fatura and e-arşiv document, so a missing one
    # means we failed to read it, not that the document lacks it. But it is not
    # critical: nothing is provably wrong, and a document should not be blocked
    # on a field we merely failed to find.
    MISSING_TAX_ID_SEVERITY = "warning"

    # Which party must carry a tax id, per document type.
    #
    # Invoices only. The justification for this rule is UBL-TR, which mandates
    # the seller's PartyIdentification on e-fatura and e-arşiv documents — a
    # purchase order is neither, and PO_SCHEMA does not even ask for a supplier
    # tax id. Including "po" here warned about a field the extractor was never
    # told to produce: 20 of 20 POs flagged, a 100% false positive rate, found
    # in run 10. Over-applying a good argument is still over-applying it.
    SELLER_TAX_ID_FIELD = {"invoice": "vendor_tax_id"}

    def _check_tax_ids(self, extracted: dict, doc_type: str = "") -> list[dict]:
        """
        Validate Turkish tax ids against their own check digit, and notice when
        a mandatory one is missing entirely.

        The check digit makes this the only self-verifying field in the schema
        — no second source and no model call needed. On the real-document pilot
        it separates all four genuine ids from all three model corruptions.

        Known limit: a 10-digit *foreign* numeric tax id (Russia's INN, say)
        would be judged by the VKN algorithm and could be flagged wrongly. For
        a Turkey-facing bookkeeping product the trade is worth it, and lengths
        Turkey does not use are explicitly not judged.
        """
        from src.verification.tax_id import classify_tax_id

        issues = self._check_seller_tax_id_present(extracted, doc_type)
        for field in self.TAX_ID_FIELDS:
            if field not in extracted:
                continue
            raw = extracted.get(field)
            if raw is None or str(raw).strip() == "":
                continue

            verdict = classify_tax_id(raw)
            if verdict["valid"] is True:
                continue
            if verdict["valid"] is None:
                # We cannot judge the number. Two different silences, though:
                # an unknown length may be a perfectly good foreign tax number,
                # whereas a label captured with the value is our own extraction
                # error and posts to the books malformed. The second earns a
                # warning; the first stays quiet.
                label_captured = verdict["kind"] == "label_captured"
                issues.append({
                    "rule": ("tax_id_label_captured" if label_captured
                             else "tax_id_unverifiable"),
                    "field": field,
                    "severity": "warning" if label_captured else "info",
                    "message": f"{field} '{raw}' not checked: {verdict['reason']}",
                    "value": raw,
                    "suggested": verdict["normalized"] if label_captured else None,
                })
                continue

            issues.append({
                "rule": "tax_id_checksum_invalid",
                "field": field,
                "severity": self.TAX_ID_SEVERITY,
                # Wording matters here: this message used to be handed verbatim
                # to the correction agent, which read "fails the check digit"
                # as an instruction and rewrote the last digit until it passed.
                # It now says what to DO, and `correctable` keeps it away from
                # the agent regardless.
                "message": (f"{field} '{raw}' does not satisfy the "
                            f"{verdict['kind'].upper()} check digit. A person "
                            f"must compare it against the document. Do not "
                            f"derive a replacement value."),
                "value": raw,
                "kind": verdict["kind"],
                "correctable": False,
            })
        return issues

    def _check_seller_tax_id_present(self, extracted: dict, doc_type: str) -> list[dict]:
        """
        Warn when a Turkish document has no seller tax id.

        Under UBL-TR the seller's PartyIdentification — VKN for a company,
        TCKN for an individual — is mandatory on every e-fatura and e-arşiv
        document. So on a Turkish invoice an absent one means the extraction
        missed it, and on a real telecom bill in the pilot it did exactly that,
        with the VKN printed plainly next to the tax office name. The document
        still came out OK.

        `currency == "TRY"` is a PROXY for "this is a Turkish document". The
        schema carries no country or language field, and inventing one here
        would be guessing. The proxy is deliberately narrow: a foreign invoice
        legitimately has no Turkish tax id, and flagging every one of them
        would train the user to ignore the warning. When a real country signal
        exists, this condition is the single line that changes.
        """
        field = self.SELLER_TAX_ID_FIELD.get(doc_type)
        if not field:
            return []
        if str(extracted.get("currency") or "").strip().upper() != "TRY":
            return []

        raw = extracted.get(field)
        if raw is not None and str(raw).strip() != "":
            return []

        return [{
            "rule": "seller_tax_id_missing",
            "field": field,
            "severity": self.MISSING_TAX_ID_SEVERITY,
            "message": (f"{field} is missing on a TRY document — the seller's "
                        f"VKN/TCKN is mandatory under UBL-TR, so it is most "
                        f"likely on the page and was not read"),
        }]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_numeric(val: Any) -> bool:
        try:
            float(str(val).replace(",", ""))
            return True
        except (ValueError, TypeError):
            return False

    @staticmethod
    def _to_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            return float(str(val).replace(",", ""))
        except (ValueError, TypeError):
            return None
