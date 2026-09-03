# -*- coding: utf-8 -*-
"""
Aritmetik Tutarlılık Onarımı (Çözüm #2)
========================================
Görsel BDM'ler büyük sayıları okurken bazen bir basamak düşürmektedir
(örn. 173200.82 -> 17320.82). Ancak `quantity` ve `unit_price` daha güvenilir
okunduğundan, türetilmiş sayısal alanlar (kalem toplamı, ara toplam, genel
toplam) bileşenlerinden yeniden hesaplanarak deterministik biçimde onarılabilir.

İLKE (P0-4 sonrası revize edildi):
    Sayfada yazan değer *kanıt*, bizim hesapladığımız *çıkarım*dır.
    Çıkarım boşluğu doldurur; kanıtı asla sessizce ezmez.

  - Kalem toplamı (qty × unit_price): ONARILIR. Yerel bir ilişkidir ve iki
    bağımsız değer tek bir türetilmiş değeri doğrular. Çözüm #2'nin asıl
    kazanımı buydu.
  - Ara toplam / genel toplam: yalnızca EKSİKSE doldurulur. Sayfada yazan ama
    hesapla çelişen bir toplam, modellemediğimiz bir yapının işaretidir
    (indirim, önceki aydan devir, gecikme bedeli, iki farklı vergi matrahı) —
    ezilmez, kural doğrulayıcıya bırakılır ve belge incelemeye düşer.

Gerçek bir telekom faturasında eski davranış, sayfada iki kez basılı (biri
vurgulu) ödenecek tutarı `ara toplam + vergi` ile ezip ~2 kat yanlış bir sayı
yazmış, üstelik belgeyi kendi içinde tutarlı hale getirdiği için kural
doğrulayıcıyı da kör edip `verdict: OK` ürettirmişti.
Bkz. docs/measurements/2026-09-02-real-pilot.md
"""
from typing import Any

_TOL = 0.01  # mutlak tolerans (yuvarlama farklarını yok say)


def _num(x: Any):
    """Sayıya çevirir; başarısızsa None."""
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        s = x.replace(" ", "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def repair_arithmetic(data: dict, doc_type: str = None) -> dict:
    """
    Belge sözlüğündeki türetilmiş sayısal alanları yerinde onarır.
    Döndürür: aynı (onarılmış) sözlük. (Çıktıyı kirletmemek için onarım sayısı
    sözlüğe yazılmaz; gerekirse repair_count() ile ölçülebilir.)
    """
    if not isinstance(data, dict):
        return data

    repairs = 0
    items = data.get("items")
    items = items if isinstance(items, list) else []

    # 1) Kalem toplamı = quantity × unit_price (tam türetilmiş alan)
    for it in items:
        if not isinstance(it, dict):
            continue
        q = _num(it.get("quantity"))
        u = _num(it.get("unit_price"))
        t = _num(it.get("total"))
        if q is not None and u is not None:
            calc = round(q * u, 2)
            if t is None or abs(t - calc) > _TOL:
                it["total"] = calc
                repairs += 1

    # Onarılmış kalem toplamlarının toplamı
    line_sum = round(sum((_num(it.get("total")) or 0.0)
                         for it in items if isinstance(it, dict)), 2)

    # 2) Ara toplam: YALNIZCA EKSİKSE doldurulur.
    #    Sayfada yazan ama kalem toplamıyla çelişen bir ara toplam ezilmez.
    if "subtotal" in data and items:
        if _num(data.get("subtotal")) is None:
            data["subtotal"] = line_sum
            repairs += 1

    # 3) Genel toplam: YALNIZCA EKSİKSE doldurulur. Aynı gerekçe.
    if items or "total_amount" in data:
        sub = _num(data.get("subtotal"))
        tax = _num(data.get("tax_amount"))
        if sub is not None and tax is not None:
            expected = round(sub + tax, 2)
        elif sub is not None:
            expected = sub
        else:
            expected = line_sum  # ör. PO: ayrı ara toplam/vergi yok
        if _num(data.get("total_amount")) is None and expected > 0:
            data["total_amount"] = expected
            repairs += 1

    return data
