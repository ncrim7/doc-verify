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
                    corrections[f"items[{i}].total"] = expected

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
                    corrections[f"items[{i}].total"] = expected

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
                    corrections[f"items[{i}].total"] = expected

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
