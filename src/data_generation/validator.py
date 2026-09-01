"""
Validation module for generated document ground-truth annotations.
Checks required fields, format correctness, and semantic consistency.
"""
import json
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DocType = Literal["invoice", "po", "receipt"]

# Required fields per document type
REQUIRED_FIELDS: dict[str, list[str]] = {
    "invoice": [
        "doc_type", "language", "invoice_number", "date", "due_date",
        "vendor_name", "vendor_tax_id", "buyer_name",
        "items", "subtotal", "tax_rate", "tax_amount", "total_amount",
        "currency", "payment_terms",
    ],
    "po": [
        "doc_type", "language", "po_number", "date", "delivery_date",
        "supplier_name", "buyer_name",
        "items", "total_amount", "currency",
    ],
    "receipt": [
        "doc_type", "language", "receipt_number", "date",
        "store_name", "cashier",
        "items", "subtotal", "tax_rate", "tax_amount", "total_amount", "currency",
    ],
}

# Required fields per item (for each doc type)
ITEM_REQUIRED: dict[str, list[str]] = {
    "invoice": ["description", "quantity", "unit_price", "total"],
    "po":      ["sku", "description", "quantity", "unit_price", "total"],
    "receipt": ["description", "quantity", "unit_price", "total"],
}

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TAX_ID_PATTERN = re.compile(r"^\d{10}$")
VALID_CURRENCIES = {"TRY", "USD", "EUR"}
VALID_LANGUAGES  = {"tr", "en", "mixed"}


class DataValidator:
    """Validates ground-truth JSON annotations for generated documents."""

    def validate_document(self, ground_truth: dict, doc_type: DocType) -> tuple[bool, list[str]]:
        """
        Validate a single ground-truth dict.

        Returns:
            (is_valid, errors)  — errors is an empty list when is_valid is True.
        """
        errors: list[str] = []

        errors.extend(self._check_required_fields(ground_truth, doc_type))
        if errors:
            return False, errors  # Stop early if structure is broken

        errors.extend(self._check_field_formats(ground_truth, doc_type))
        errors.extend(self._check_items(ground_truth, doc_type))
        errors.extend(self._check_math(ground_truth, doc_type))

        return len(errors) == 0, errors

    def batch_validate(self, documents_dir: str | Path) -> dict:
        """
        Validate all JSON annotation files in a directory.

        Returns a summary dict with counts and error details.
        """
        documents_dir = Path(documents_dir)
        results = {
            "total_docs": 0,
            "valid": 0,
            "invalid": 0,
            "errors": [],
        }

        json_files = list(documents_dir.glob("*.json"))
        if not json_files:
            logger.warning("No JSON files found in %s", documents_dir)
            return results

        for json_path in sorted(json_files):
            results["total_docs"] += 1
            try:
                gt = json.loads(json_path.read_text(encoding="utf-8"))
                doc_type = gt.get("doc_type", "invoice")
                is_valid, errs = self.validate_document(gt, doc_type)
            except Exception as exc:
                is_valid = False
                errs = [f"JSON parse error: {exc}"]

            if is_valid:
                results["valid"] += 1
            else:
                results["invalid"] += 1
                results["errors"].append({"doc": json_path.name, "errors": errs})

        return results

    def generate_quality_report(self, documents_base_dir: str | Path) -> dict:
        """
        Run batch_validate on all three sub-directories and build a quality report.
        """
        base = Path(documents_base_dir)
        sub_dirs = {
            "invoice": base / "invoices",
            "po":      base / "purchase_orders",
            "receipt": base / "receipts",
        }

        report: dict = {
            "generated_at": datetime.now().isoformat(),
            "per_type": {},
            "totals": {"total_docs": 0, "valid": 0, "invalid": 0},
            "language_distribution": {},
            "status": "UNKNOWN",
        }

        lang_counts: dict[str, int] = {}

        for doc_type, path in sub_dirs.items():
            if not path.exists():
                report["per_type"][doc_type] = {"error": f"Directory not found: {path}"}
                continue

            result = self.batch_validate(path)
            report["per_type"][doc_type] = result
            report["totals"]["total_docs"] += result["total_docs"]
            report["totals"]["valid"]      += result["valid"]
            report["totals"]["invalid"]    += result["invalid"]

            # Count language distribution
            for jf in sorted(path.glob("*.json")):
                try:
                    gt = json.loads(jf.read_text(encoding="utf-8"))
                    lang = gt.get("language", "unknown")
                    lang_counts[lang] = lang_counts.get(lang, 0) + 1
                except Exception:
                    pass

        total = report["totals"]["total_docs"]
        if total:
            report["language_distribution"] = {
                lang: {"count": cnt, "pct": round(cnt / total * 100, 1)}
                for lang, cnt in sorted(lang_counts.items())
            }

        report["status"] = "READY FOR TRAINING" if report["totals"]["invalid"] == 0 else "NEEDS REVIEW"
        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_required_fields(self, gt: dict, doc_type: DocType) -> list[str]:
        errors = []
        for field in REQUIRED_FIELDS.get(doc_type, []):
            if field not in gt or gt[field] is None or gt[field] == "":
                errors.append(f"Missing required field: '{field}'")
        return errors

    def _check_field_formats(self, gt: dict, doc_type: DocType) -> list[str]:
        errors = []

        # Date fields
        date_fields = {
            "invoice": ["date", "due_date"],
            "po":      ["date", "delivery_date"],
            "receipt": ["date"],
        }.get(doc_type, ["date"])

        for field in date_fields:
            val = gt.get(field, "")
            if val and not DATE_PATTERN.match(str(val)):
                errors.append(f"Invalid date format in '{field}': expected YYYY-MM-DD, got '{val}'")
            elif val:
                try:
                    datetime.strptime(val, "%Y-%m-%d")
                except ValueError:
                    errors.append(f"Invalid date value in '{field}': '{val}'")

        # Tax ID (invoice only)
        if doc_type == "invoice":
            for field in ["vendor_tax_id", "buyer_tax_id"]:
                val = gt.get(field, "")
                if val and not TAX_ID_PATTERN.match(str(val)):
                    errors.append(f"Invalid tax ID in '{field}': expected 10 digits, got '{val}'")

        # Currency
        currency = gt.get("currency", "")
        if currency and currency not in VALID_CURRENCIES:
            errors.append(f"Unknown currency: '{currency}'")

        # Language
        lang = gt.get("language", "")
        if lang and lang not in VALID_LANGUAGES:
            errors.append(f"Unknown language: '{lang}'")

        # Numeric sanity: totals must be positive
        for field in ["total_amount"]:
            val = gt.get(field)
            if val is not None and val <= 0:
                errors.append(f"'{field}' must be positive, got {val}")

        return errors

    def _check_items(self, gt: dict, doc_type: DocType) -> list[str]:
        errors = []
        items = gt.get("items", [])

        if not isinstance(items, list) or len(items) == 0:
            errors.append("'items' must be a non-empty list")
            return errors

        required_item_fields = ITEM_REQUIRED.get(doc_type, [])

        for idx, item in enumerate(items):
            for field in required_item_fields:
                if field not in item or item[field] is None:
                    errors.append(f"Item {idx}: missing field '{field}'")

            qty = item.get("quantity")
            if qty is not None and (not isinstance(qty, (int, float)) or qty <= 0):
                errors.append(f"Item {idx}: 'quantity' must be a positive number, got {qty!r}")

            price = item.get("unit_price")
            if price is not None and (not isinstance(price, (int, float)) or price <= 0):
                errors.append(f"Item {idx}: 'unit_price' must be positive, got {price!r}")

            total = item.get("total")
            if qty is not None and price is not None and total is not None:
                expected = round(qty * price, 2)
                if abs(total - expected) > 0.02:  # allow 2-cent rounding tolerance
                    errors.append(
                        f"Item {idx}: total mismatch — qty={qty} * price={price} = {expected}, stored={total}"
                    )

        return errors

    def _check_math(self, gt: dict, doc_type: DocType) -> list[str]:
        errors = []

        if doc_type in ("invoice", "receipt"):
            subtotal  = gt.get("subtotal")
            tax_amt   = gt.get("tax_amount")
            total     = gt.get("total_amount")

            if None not in (subtotal, tax_amt, total):
                expected = round(subtotal + tax_amt, 2)
                if abs(total - expected) > 0.02:
                    errors.append(
                        f"Math inconsistency: subtotal({subtotal}) + tax({tax_amt}) = {expected}, "
                        f"but total_amount = {total}"
                    )

            # Also verify items sum ≈ subtotal
            items = gt.get("items", [])
            if items and subtotal is not None:
                items_sum = round(sum(i.get("total", 0) for i in items), 2)
                if abs(items_sum - subtotal) > 0.02:
                    errors.append(
                        f"Subtotal mismatch: sum of item totals = {items_sum}, subtotal = {subtotal}"
                    )

        elif doc_type == "po":
            items = gt.get("items", [])
            total = gt.get("total_amount")
            if items and total is not None:
                items_sum = round(sum(i.get("total", 0) for i in items), 2)
                if abs(items_sum - total) > 0.02:
                    errors.append(
                        f"PO total mismatch: sum of item totals = {items_sum}, total_amount = {total}"
                    )

        return errors
