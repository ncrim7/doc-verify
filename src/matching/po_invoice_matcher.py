"""
PO vs Invoice cross-document item matching.

Algorithm:
  1. Try exact SKU match first.
  2. Fall back to fuzzy description similarity (threshold 0.65).
  3. For each matched pair, compare quantity, unit_price, total within tolerance.
  4. Collect unmatched items from both sides.
  5. Compare top-level scalar fields (total_amount, currency, supplier/vendor name).
"""
import logging
from difflib import SequenceMatcher
from typing import Optional

logger = logging.getLogger(__name__)

_PRICE_TOL = 0.02   # 2% relative tolerance for prices
_QTY_TOL   = 0.001  # near-exact for quantities (float-safe)
_FUZZY_THR = 0.65   # minimum description similarity to accept a match

# TL and TRY both mean Turkish Lira — treat as identical
_CURRENCY_ALIASES: dict[str, str] = {
    'TL': 'TRY', 'T.L.': 'TRY', 'TL.': 'TRY',
    'TÜRK LİRASI': 'TRY', 'TURK LIRASI': 'TRY',
    'LIRA': 'TRY', 'YTL': 'TRY', 'YENİ TÜRK LİRASI': 'TRY',
    '$': 'USD', 'US$': 'USD', 'DOLAR': 'USD',
    '€': 'EUR', 'EURO': 'EUR',
    '£': 'GBP', 'STERLIN': 'GBP',
}

# Turkish diacritic → ASCII mapping for fuzzy vendor-name comparison
_TR_DIACRITIC_MAP = str.maketrans('ğĞşŞıİöÖüÜçÇ', 'gGsSiIoOuUcC')


def _normalize_currency(code: str) -> str:
    c = str(code).upper().strip()
    return _CURRENCY_ALIASES.get(c, c)


def _normalize_vendor(name: str) -> str:
    """Lowercase + strip Turkish diacritics for fuzzy comparison."""
    return name.lower().strip().translate(_TR_DIACRITIC_MAP)


def _text_sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, str(a).lower().strip(), str(b).lower().strip()).ratio()


def _numeric_close(a, b, tol: float = _PRICE_TOL) -> bool:
    try:
        fa, fb = float(a), float(b)
        if fa == 0 and fb == 0:
            return True
        denom = max(abs(fa), abs(fb), 1e-9)
        return abs(fa - fb) / denom <= tol
    except (TypeError, ValueError):
        return False


class POInvoiceMatcher:
    """
    Compare a Purchase Order against an Invoice at the line-item level.

    Usage:
        matcher = POInvoiceMatcher()
        result = matcher.match(po_data, invoice_data)
        # result['summary']['overall_status'] → 'APPROVE' | 'REVIEW' | 'REJECT'
    """

    def __init__(
        self,
        price_tolerance: float = _PRICE_TOL,
        qty_tolerance: float = _QTY_TOL,
        fuzzy_threshold: float = _FUZZY_THR,
    ):
        self.price_tolerance = price_tolerance
        self.qty_tolerance = qty_tolerance
        self.fuzzy_threshold = fuzzy_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(self, po_data: dict, invoice_data: dict) -> dict:
        """
        Compare PO vs Invoice.

        Args:
            po_data: Extracted PO dict (from LLMExtractor / PipelineRunner)
            invoice_data: Extracted Invoice dict

        Returns:
            {
                'matches': list[dict],           # Matched pairs + discrepancies
                'unmatched_po': list[dict],      # PO items not found in invoice
                'unmatched_invoice': list[dict], # Invoice items not in PO
                'scalar_checks': list[dict],     # Top-level field comparisons
                'summary': dict,                 # Aggregated statistics
            }
        """
        po_items  = list(po_data.get("items")  or [])
        inv_items = list(invoice_data.get("items") or [])

        used_inv = set()
        matches, unmatched_po = [], []

        for po_item in po_items:
            idx = self._find_best(po_item, inv_items, used_inv)
            if idx is not None:
                used_inv.add(idx)
                inv_item = inv_items[idx]
                matches.append({
                    "po_item": po_item,
                    "invoice_item": inv_item,
                    "match_score": round(self._item_sim(po_item, inv_item), 4),
                    "discrepancies": self._compare_items(po_item, inv_item),
                })
            else:
                unmatched_po.append(po_item)

        unmatched_invoice = [
            item for i, item in enumerate(inv_items) if i not in used_inv
        ]

        scalar_checks = self._compare_scalars(po_data, invoice_data)
        summary = self._summarize(matches, unmatched_po, unmatched_invoice, scalar_checks)

        return {
            "matches": matches,
            "unmatched_po": unmatched_po,
            "unmatched_invoice": unmatched_invoice,
            "scalar_checks": scalar_checks,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _find_best(self, po_item: dict, inv_items: list, used: set) -> Optional[int]:
        po_sku = str(po_item.get("sku") or "").strip()

        # Phase 1 — exact SKU
        if po_sku:
            for i, inv in enumerate(inv_items):
                if i in used:
                    continue
                inv_sku = str(inv.get("sku") or "").strip()
                if inv_sku and po_sku.lower() == inv_sku.lower():
                    return i

        # Phase 2 — fuzzy description
        best_score = self.fuzzy_threshold
        best_idx = None
        po_desc = str(po_item.get("description") or "")
        for i, inv in enumerate(inv_items):
            if i in used:
                continue
            score = _text_sim(po_desc, str(inv.get("description") or ""))
            if score > best_score:
                best_score, best_idx = score, i

        return best_idx

    def _item_sim(self, po: dict, inv: dict) -> float:
        desc_sim = _text_sim(
            str(po.get("description", "")),
            str(inv.get("description", "")),
        )
        qty_ok = _numeric_close(po.get("quantity"), inv.get("quantity"), self.qty_tolerance)
        return (desc_sim + (1.0 if qty_ok else 0.5)) / 2

    def _compare_items(self, po: dict, inv: dict) -> list:
        issues = []

        for field, tol, label in [
            ("quantity",   self.qty_tolerance,   "Adet"),
            ("unit_price", self.price_tolerance,  "Birim Fiyat"),
            ("total",      self.price_tolerance,  "Satır Toplam"),
        ]:
            pv, iv = po.get(field), inv.get(field)
            if pv is None and iv is None:
                continue
            if pv is None or iv is None:
                where = "PO'da yok" if pv is None else "Faturada yok"
                issues.append({
                    "field": field, "label": label,
                    "po_value": pv, "invoice_value": iv,
                    "severity": "warning",
                    "message": f"{label}: {where}",
                })
                continue
            if not _numeric_close(pv, iv, tol):
                diff_pct = abs(float(pv) - float(iv)) / max(abs(float(pv)), 1e-9) * 100
                issues.append({
                    "field": field, "label": label,
                    "po_value": pv, "invoice_value": iv,
                    "diff_pct": round(diff_pct, 2),
                    "severity": "critical" if field in ("quantity", "unit_price") else "warning",
                    "message": f"{label} uyuşmuyor: PO={pv}, Fatura={iv} (%{diff_pct:.1f} fark)",
                })

        desc_sim = _text_sim(str(po.get("description", "")), str(inv.get("description", "")))
        if desc_sim < 0.80:
            issues.append({
                "field": "description", "label": "Açıklama",
                "po_value": po.get("description"),
                "invoice_value": inv.get("description"),
                "similarity": round(desc_sim, 3),
                "severity": "warning",
                "message": f"Açıklama benzerliği düşük: %{desc_sim*100:.0f}",
            })

        return issues

    def _compare_scalars(self, po_data: dict, inv_data: dict) -> list:
        checks = []

        # Total amount — compare invoice subtotal (pre-tax) against PO total first,
        # then fall back to full total. Difference is warning (not critical) because
        # PO totals are pre-tax while invoice totals often include VAT.
        po_total  = po_data.get("total_amount")
        inv_total = inv_data.get("total_amount")
        if po_total is not None and inv_total is not None:
            # If invoice has a subtotal, check that against PO total (tax-exclusive)
            inv_subtotal = inv_data.get("subtotal")
            subtotal_ok = (
                inv_subtotal is not None and
                _numeric_close(po_total, inv_subtotal, self.price_tolerance)
            )
            total_ok = _numeric_close(po_total, inv_total, self.price_tolerance)
            ok = total_ok or subtotal_ok
            if ok:
                msg = "Genel toplam uyuşuyor"
            elif subtotal_ok is False and inv_subtotal is not None:
                diff_pct = abs(float(po_total) - float(inv_subtotal)) / max(abs(float(po_total)), 1e-9) * 100
                msg = f"Toplam uyuşmuyor: PO={po_total}, Fatura net={inv_subtotal} (%{diff_pct:.1f} fark)"
            else:
                try:
                    diff_pct = abs(float(po_total) - float(inv_total)) / max(abs(float(po_total)), 1e-9) * 100
                    msg = f"Toplam uyuşmuyor: PO={po_total}, Fatura={inv_total} (%{diff_pct:.1f} — KDV farkı olabilir)"
                except (TypeError, ValueError):
                    msg = f"Toplam uyuşmuyor: PO={po_total}, Fatura={inv_total}"
            checks.append({
                "field": "total_amount", "label": "Genel Toplam",
                "po_value": po_total, "invoice_value": inv_total,
                "match": ok,
                "severity": "warning" if not ok else "ok",  # Never critical — tax differences expected
                "message": msg,
            })

        # Currency — normalize aliases (TL = TRY, etc.) before comparing
        po_cur_raw  = str(po_data.get("currency") or "").upper().strip()
        inv_cur_raw = str(inv_data.get("currency") or "").upper().strip()
        if po_cur_raw and inv_cur_raw:
            po_cur_norm  = _normalize_currency(po_cur_raw)
            inv_cur_norm = _normalize_currency(inv_cur_raw)
            ok = po_cur_norm == inv_cur_norm
            checks.append({
                "field": "currency", "label": "Para Birimi",
                "po_value": po_cur_raw, "invoice_value": inv_cur_raw,
                "match": ok,
                "severity": "critical" if not ok else "ok",
                "message": (
                    "Para birimi uyuşuyor"
                    if ok else
                    f"Para birimi uyuşmuyor: PO={po_cur_raw}, Fatura={inv_cur_raw}"
                ),
            })

        # Supplier name vs Vendor name — normalize Turkish diacritics before comparing
        # e.g. 'Tevetoglu San.' == 'Tevetoğlu San.' after normalization
        po_sup  = str(po_data.get("supplier_name") or po_data.get("vendor_name") or "")
        inv_ven = str(inv_data.get("supplier_name") or inv_data.get("vendor_name") or "")
        if po_sup and inv_ven:
            sim = _text_sim(_normalize_vendor(po_sup), _normalize_vendor(inv_ven))
            ok = sim >= 0.70
            checks.append({
                "field": "supplier_vendor", "label": "Tedarikçi Adı",
                "po_value": po_sup, "invoice_value": inv_ven,
                "similarity": round(sim, 3),
                "match": ok,
                "severity": "warning" if not ok else "ok",
                "message": (
                    f"Tedarikçi uyuşuyor: {po_sup!r}"
                    if ok else
                    f"Tedarikçi adı farklı: PO={po_sup!r}, Fatura={inv_ven!r}"
                ),
            })

        return checks

    def _summarize(self, matches, unmatched_po, unmatched_invoice, scalar_checks) -> dict:
        total_po_items = len(matches) + len(unmatched_po)
        match_rate = len(matches) / max(total_po_items, 1)

        all_disc = [d for m in matches for d in m["discrepancies"]]
        n_critical = len([d for d in all_disc if d["severity"] == "critical"])
        n_warnings  = len([d for d in all_disc if d["severity"] == "warning"])

        sc_critical = len([s for s in scalar_checks if s.get("severity") == "critical"])
        sc_warnings  = len([s for s in scalar_checks if s.get("severity") == "warning"])

        total_critical = n_critical + sc_critical
        total_warnings  = n_warnings + sc_warnings

        if total_critical > 0:
            status = "REJECT"
        elif total_warnings > 0 or unmatched_po or unmatched_invoice:
            status = "REVIEW"
        else:
            status = "APPROVE"

        return {
            "overall_status": status,
            "match_rate": round(match_rate, 4),
            "matched_items": len(matches),
            "unmatched_po_items": len(unmatched_po),
            "unmatched_invoice_items": len(unmatched_invoice),
            "critical_issues": total_critical,
            "warnings": total_warnings,
        }
