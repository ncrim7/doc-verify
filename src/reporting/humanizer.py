"""
Match-result humanizer.

Turns the structured output of ``POInvoiceMatcher.match()`` into a plain-language
verdict a bookkeeping-office user can act on: approve the payment, hold it, or
review specific issues.

Refactored from the graduation project's ``erp/result_humanizer.py`` — the
anomaly taxonomy and Turkish advice text are kept; the SAP/ERP string parser
and Fiori links are dropped. Input is now the matcher dict directly.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Anomaly type -> business language (label / what it means / what to do)
# ---------------------------------------------------------------------------
_ANOMALY_INFO: dict[str, dict] = {
    "WRONG_VENDOR": {
        "label":  "Tedarikçi Uyuşmazlığı",
        "detail": "Faturadaki firma adı veya kimliği siparişle eşleşmiyor.",
        "action": "Doğru tedarikçiden yeni fatura talep edin ya da sipariş bilgilerini doğrulayın.",
    },
    "PRICE_MISMATCH": {
        "label":  "Fiyat Farkı",
        "detail": "Birim fiyat siparişteki rakamdan farklı.",
        "action": "Tedarikçiyle fiyat mutabakatı yapın; gerekirse siparişi revize edin.",
    },
    "TOTAL_MISMATCH": {
        "label":  "Toplam Tutar Farkı",
        "detail": "Satır toplamı veya genel toplam siparişteki rakamla uyuşmuyor.",
        "action": "Kalem fiyat ve miktarlarını kontrol edin; KDV kaynaklı fark olabilir.",
    },
    "QTY_MISMATCH": {
        "label":  "Miktar Uyuşmazlığı",
        "detail": "Faturalanan miktar, sipariş edilen miktarla uyuşmuyor.",
        "action": "Teslim alınan miktarı ambar kaydıyla karşılaştırın, farkı tedarikçiye bildirin.",
    },
    "DESC_MISMATCH": {
        "label":  "Kalem Açıklaması Farklı",
        "detail": "Eşleşen kalemin açıklaması faturada ve siparişte belirgin biçimde farklı.",
        "action": "Kalemin doğru ürün/hizmet olduğunu tedarikçiyle teyit edin.",
    },
    "DATE_ANOMALY": {
        "label":  "Tarih Sorunu",
        "detail": "Fatura tarihi sipariş tarihiyle mantıksal olarak çelişiyor.",
        "action": "Fatura tarihini ve sipariş tarihini belgeleri üzerinden doğrulayın.",
    },
    "CURRENCY_MISMATCH": {
        "label":  "Para Birimi Farklı",
        "detail": "Faturadaki para birimi siparişteki para birimiyle uyuşmuyor.",
        "action": "Sözleşmedeki para birimini ve faturayı karşılaştırın.",
    },
    "NOT_IN_PO": {
        "label":  "Siparişsiz Kalem",
        "detail": "Faturada sipariş dışında ek kalem bulunuyor.",
        "action": "Siparişe ek kalem ekleyin ya da tedarikçiden düzeltilmiş fatura isteyin.",
    },
    "MISSING_ITEM": {
        "label":  "Eksik Kalem",
        "detail": "Siparişteki bazı kalemler faturada yer almıyor.",
        "action": "Tedarikçiden eksik kalemleri içeren revize fatura isteyin.",
    },
    "TAX_RATE_MISMATCH": {
        "label":  "KDV Oranı Farkı",
        "detail": "Faturadaki vergi oranı beklenen orandan farklı.",
        "action": "Muhasebe departmanınızla vergi oranını doğrulayın.",
    },
    "UOM_MISMATCH": {
        "label":  "Ölçü Birimi Farklı",
        "detail": "Faturada kullanılan ölçü birimi sipariştekiyle uyuşmuyor.",
        "action": "Birim dönüşümünü kontrol edin veya tedarikçiyle teyit edin.",
    },
    "OTHER": {
        "label":  "Diğer Uyuşmazlık",
        "detail": "Fatura ile sipariş arasında bir fark tespit edildi.",
        "action": "Muhasebe departmanınızla görüşün.",
    },
}

_SEVERITY_ICONS = {"HIGH": "\U0001f534", "MEDIUM": "\U0001f7e1", "LOW": "\U0001f7e2"}

# matcher field -> anomaly code
_FIELD_TO_ANOMALY: dict[str, str] = {
    "quantity":              "QTY_MISMATCH",
    "unit_price":            "PRICE_MISMATCH",
    "total":                 "TOTAL_MISMATCH",
    "description":           "DESC_MISMATCH",
    "total_amount":          "TOTAL_MISMATCH",
    "currency":              "CURRENCY_MISMATCH",
    "supplier_vendor":       "WRONG_VENDOR",
    "_extra_invoice_line":   "NOT_IN_PO",
    "_missing_po_line":      "MISSING_ITEM",
}

# matcher summary status -> (verdict status code, criticality)
#   criticality: >=2 -> amber "review", <2 -> red "hold payment"
_STATUS_MAP: dict[str, tuple[str, int]] = {
    "APPROVE": ("MATCHED", 3),
    "REVIEW":  ("ANOMALY_DETECTED", 2),
    "REJECT":  ("ANOMALY_DETECTED", 1),
}


# ---------------------------------------------------------------------------
# Verdict text (status + criticality -> user-facing decision)
# ---------------------------------------------------------------------------
def _verdict(status: str, criticality: int) -> dict:
    if status == "MATCHED":
        return {
            "icon":     "✅",
            "title":    "ÖDEMEYİ ONAYLAYABİLİRSİNİZ",
            "subtitle": "Fatura ve sipariş kayıtlarıyla tam uyuşuyor.",
            "color":    "#22c55e",
            "action":   "Ödemeyi onaylayabilirsiniz.",
        }
    if status == "ANOMALY_DETECTED":
        crit = int(criticality or 0)
        if crit >= 2:
            return {
                "icon":     "⚠️",
                "title":    "İNCELEME GEREKLİ",
                "subtitle": "Dikkat gerektiren noktalar tespit edildi.",
                "color":    "#f59e0b",
                "action":   "Belirtilen sorunları gözden geçirin, ardından onaylayın veya reddedin.",
            }
        return {
            "icon":     "\U0001f6ab",
            "title":    "ÖDEME DURDURULDU",
            "subtitle": "Kritik sorun tespit edildi — acil inceleme gerekli.",
            "color":    "#ef4444",
            "action":   "Sorunlar çözülene kadar ödeme yapmayın.",
        }
    return {
        "icon":     "❓",
        "title":    status or "BİLİNMEYEN",
        "subtitle": "",
        "color":    "#aaaaaa",
        "action":   "",
    }


def _problem(field: str, severity: str, fallback_label: str, message: str) -> dict:
    """Build one user-facing problem entry from a matcher issue."""
    code = _FIELD_TO_ANOMALY.get(field, "OTHER")
    info = _ANOMALY_INFO.get(code, _ANOMALY_INFO["OTHER"])
    sev = "HIGH" if severity == "critical" else "MEDIUM"
    return {
        "severity": sev,
        "type":     code,
        "icon":     _SEVERITY_ICONS.get(sev, "⚪"),
        "label":    info["label"] or fallback_label,
        "detail":   info["detail"],
        "raw_desc": message,
        "action":   info["action"],
    }


def humanize_match(match_result: dict) -> dict:
    """
    Convert ``POInvoiceMatcher.match()`` output into a user-facing structure.

    Returns:
        {
          "verdict":       {icon, title, subtitle, color, action},
          "problems":      [{severity, type, icon, label, detail, raw_desc, action}],
          "has_problems":  bool,
          "telegram_text": str,   # Telegram Markdown v1
        }
    """
    summary = match_result.get("summary", {}) or {}
    status_code, criticality = _STATUS_MAP.get(
        summary.get("overall_status", "REVIEW"), ("ANOMALY_DETECTED", 2)
    )

    problems: list[dict] = []

    for m in match_result.get("matches", []) or []:
        for d in m.get("discrepancies", []) or []:
            problems.append(_problem(
                d.get("field", ""), d.get("severity", "warning"),
                d.get("label", d.get("field", "")), d.get("message", ""),
            ))

    for s in match_result.get("scalar_checks", []) or []:
        if s.get("severity") in ("critical", "warning"):
            problems.append(_problem(
                s.get("field", ""), s.get("severity", "warning"),
                s.get("label", s.get("field", "")), s.get("message", ""),
            ))

    for item in match_result.get("unmatched_invoice", []) or []:
        desc = item.get("description", "") if isinstance(item, dict) else ""
        problems.append(_problem(
            "_extra_invoice_line", "critical", "Siparişsiz Kalem",
            f"Faturada siparişte olmayan kalem: {desc}".strip().rstrip(":"),
        ))

    for item in match_result.get("unmatched_po", []) or []:
        desc = item.get("description", "") if isinstance(item, dict) else ""
        problems.append(_problem(
            "_missing_po_line", "warning", "Eksik Kalem",
            f"Siparişte olup faturada olmayan kalem: {desc}".strip().rstrip(":"),
        ))

    v = _verdict(status_code, criticality)
    return {
        "verdict":       v,
        "problems":      problems,
        "has_problems":  bool(problems),
        "telegram_text": _build_telegram(v, problems),
    }


# ---------------------------------------------------------------------------
# Telegram Markdown rendering (kept for the bot repo; safe to ignore elsewhere)
# ---------------------------------------------------------------------------
def _build_telegram(v: dict, problems: list[dict], meta: dict | None = None) -> str:
    def e(t: object) -> str:
        s = str(t)
        for ch in ("_", "*", "`", "["):
            s = s.replace(ch, f"\\{ch}")
        return s

    lines = [
        f"{v['icon']} *{e(v['title'])}*",
        f"_{e(v['subtitle'])}_",
        "",
    ]

    if problems:
        rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        ordered = sorted(problems, key=lambda p: rank.get(p["severity"], 3))
        lines.append("⚠️ *Tespit Edilen Sorunlar*")
        for p in ordered[:6]:
            lines.append(f"{p['icon']} *{e(p['label'])}*")
            if p.get("raw_desc"):
                lines.append(f"   _{e(str(p['raw_desc'])[:100])}_")
            lines.append(f"   → {e(p['action'])}")
            lines.append("")

    lines += [f"\U0001f4a1 *Yapmanız Gereken*", e(v["action"]), ""]

    if meta and meta.get("invoice_id"):
        lines.append(f"\U0001f194 `{e(meta['invoice_id'])}`")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n\n_\\.\\.\\. mesaj kısaltıldı_"
    return text
