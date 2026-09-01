"""
ERP sonuç insanlaştırıcısı.

Teknik ERP çıktısını (ANOMALY_DETECTED, [HIGH] WRONG_VENDOR: ...) son kullanıcının
anlayabileceği iş diline çevirir.  Telegram ve Streamlit bu modülü paylaşır.
"""
import re

# ── Anomali tipi → iş dili ────────────────────────────────────────────────────
_ANOMALY_INFO: dict[str, dict] = {
    "WRONG_VENDOR": {
        "label":  "Tedarikçi Uyuşmazlığı",
        "detail": "Faturadaki firma adı veya kimliği siparişle eşleşmiyor.",
        "action": "Doğru tedarikçiden yeni fatura talep edin ya da sipariş bilgilerini doğrulayın.",
    },
    "PRICE_MISMATCH": {
        "label":  "Fiyat Farkı",
        "detail": "Birim fiyat ya da toplam tutar siparişteki rakamdan farklı.",
        "action": "Tedarikçiyle fiyat mutabakatı yapın; gerekirse siparişi revize edin.",
    },
    "QTY_MISMATCH": {
        "label":  "Miktar Uyuşmazlığı",
        "detail": "Faturalanan miktar, sipariş edilen veya teslim alınan miktarla uyuşmuyor.",
        "action": "Teslim alınan miktarı ambar kaydıyla karşılaştırın, farkı tedarikçiye bildirin.",
    },
    "DATE_ANOMALY": {
        "label":  "Tarih Sorunu",
        "detail": "Fatura tarihi sipariş tarihiyle mantıksal olarak çelişiyor.",
        "action": "Fatura tarihini ve sipariş tarihini belgeleri üzerinden doğrulayın.",
    },
    "CURRENCY_MISMATCH": {
        "label":  "Para Birimi Farklı",
        "detail": "Faturadaki para birimi sözleşmedeki para birimiyle uyuşmuyor.",
        "action": "Sözleşmedeki para birimini ve faturayı karşılaştırın.",
    },
    "DUPLICATE": {
        "label":  "Mükerrer Fatura",
        "detail": "Bu fatura daha önce sisteme girmiş olabilir.",
        "action": "Ödeme kayıtlarınızı inceleyin — fatura daha önce ödenmiş olabilir.",
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
    "WRONG_DOCUMENT": {
        "label":  "Yanlış Belge",
        "detail": "Yüklenen belge fatura değil.",
        "action": "Lütfen geçerli bir fatura belgesi yükleyin.",
    },
    "UNAUTHORIZED": {
        "label":  "Onaysız Kalem",
        "detail": "Faturada yetkisiz ya da onaylanmamış ürün veya hizmet var.",
        "action": "Satın alma departmanından bu kalem için onay talep edin.",
    },
}

_SEVERITY_ICONS = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}

# ── Karar metni (status + criticality → kullanıcı kararı) ─────────────────────
def _verdict(status: str, criticality: int) -> dict:
    if status == "MATCHED":
        return {
            "icon":     "✅",
            "title":    "ÖDEMEYİ ONAYLAYABİLİRSİNİZ",
            "subtitle": "Fatura, sipariş ve teslimat kayıtlarıyla tam uyuşuyor.",
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
            "icon":     "🚫",
            "title":    "ÖDEME DURDURULDU",
            "subtitle": "Kritik sorun tespit edildi — acil inceleme gerekli.",
            "color":    "#ef4444",
            "action":   "Sorunlar çözülene kadar ödeme yapmayın.",
        }
    if status in ("PROCESSING", "PENDING"):
        return {
            "icon":     "⏳",
            "title":    "ANALİZ DEVAM EDİYOR",
            "subtitle": "AI analizi henüz tamamlanmadı.",
            "color":    "#6c63ff",
            "action":   "Birkaç saniye sonra tekrar kontrol edin.",
        }
    if status == "TIMEOUT":
        return {
            "icon":     "⏱️",
            "title":    "ANALİZ TAMAMLANAMADI",
            "subtitle": "Zaman aşımı oluştu.",
            "color":    "#f59e0b",
            "action":   "Lütfen tekrar deneyin.",
        }
    return {
        "icon":     "❓",
        "title":    status or "BİLİNMEYEN",
        "subtitle": "",
        "color":    "#aaaaaa",
        "action":   "",
    }


# ── Anomali satırı parser ─────────────────────────────────────────────────────
_ANOMALY_RE = re.compile(r"\[(\w+)\]\s+(\w+):\s+(.+)")

def _parse_anomalies(anomaly_reason: str) -> list[dict]:
    problems = []
    for line in (anomaly_reason or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _ANOMALY_RE.match(line)
        if not m:
            problems.append({"severity": "LOW", "type": "OTHER",
                             "icon": _SEVERITY_ICONS.get("LOW", "⚪"),
                             "raw": line, "label": line, "detail": "", "raw_desc": "", "action": ""})
            continue
        severity, atype, desc = m.group(1), m.group(2), m.group(3)
        info = _ANOMALY_INFO.get(atype, {
            "label":  atype.replace("_", " ").title(),
            "detail": desc,
            "action": "Muhasebe departmanınızla görüşün.",
        })
        problems.append({
            "severity": severity,
            "type":     atype,
            "icon":     _SEVERITY_ICONS.get(severity, "⚪"),
            "label":    info["label"],
            "detail":   info["detail"],
            "raw_desc": desc,
            "action":   info["action"],
        })
    return problems


# ── Ana API ───────────────────────────────────────────────────────────────────

def humanize(erp: dict) -> dict:
    """
    ERP sonuç dict'ini son kullanıcı dostu yapıya çevirir.

    Input: ERPClient.push_invoice() çıktısı
    Output: {
        verdict: dict,       # icon, title, subtitle, color, action
        problems: list[dict],
        has_problems: bool,
        telegram_text: str,  # Telegram Markdown v1 formatında
    }
    """
    if not erp.get("success"):
        error = erp.get("error", "Bilinmeyen hata")
        return {
            "verdict": {
                "icon": "❌", "title": "ERP HATASI",
                "subtitle": error, "color": "#ef4444", "action": "ERP sistemini kontrol edin.",
            },
            "problems": [],
            "has_problems": True,
            "telegram_text": f"❌ *ERP Hatası*\n\n`{error}`",
        }

    status       = erp.get("status", "")
    criticality  = erp.get("criticality", 0)
    problems     = _parse_anomalies(erp.get("anomaly_reason", ""))
    v            = _verdict(status, criticality)

    telegram_text = _build_telegram(v, problems, erp)

    return {
        "verdict":      v,
        "problems":     problems,
        "has_problems": bool(problems),
        "telegram_text": telegram_text,
        "invoice_id":   erp.get("invoice_id"),
        "fiori_url":    erp.get("fiori_url"),
    }


def _build_telegram(v: dict, problems: list[dict], erp: dict) -> str:
    def e(t):
        for ch in ("_", "*", "`", "["):
            t = str(t).replace(ch, f"\\{ch}")
        return t

    lines = [
        f"{v['icon']} *{e(v['title'])}*",
        f"_{e(v['subtitle'])}_",
        "",
    ]

    if problems:
        high   = [p for p in problems if p["severity"] == "HIGH"]
        medium = [p for p in problems if p["severity"] == "MEDIUM"]
        low    = [p for p in problems if p["severity"] == "LOW"]
        ordered = high + medium + low

        lines.append("⚠️ *Tespit Edilen Sorunlar*")
        for p in ordered[:6]:
            lines.append(f"{p['icon']} *{e(p['label'])}*")
            if p.get("raw_desc"):
                # Show a brief version of the technical description
                short = e(p["raw_desc"][:100])
                lines.append(f"   _{short}_")
            lines.append(f"   → {e(p['action'])}")
            lines.append("")

    lines += [
        f"💡 *Yapmanız Gereken*",
        e(v["action"]),
        "",
    ]

    inv_id = erp.get("invoice_id")
    if inv_id:
        lines.append(f"🆔 `{e(str(inv_id))}`")

    fiori = erp.get("fiori_url")
    if fiori:
        lines.append(f"🖥️ `{fiori}`")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3950] + "\n\n_\\.\\.\\. mesaj kısaltıldı_"
    return text
