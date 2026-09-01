# -*- coding: utf-8 -*-
"""
Aritmetik Tutarlılık Onarımı (Çözüm #2)
========================================
Görsel BDM'ler büyük sayıları okurken bazen bir basamak düşürmektedir
(örn. 173200.82 -> 17320.82). Ancak `quantity` ve `unit_price` daha güvenilir
okunduğundan, türetilmiş sayısal alanlar (kalem toplamı, ara toplam, genel
toplam) bileşenlerinden yeniden hesaplanarak deterministik biçimde onarılabilir.

İlke: türetilmiş alan ile hesaplanan değer arasında belirgin fark varsa,
hesaplanan (daha güvenilir) değer kullanılır. Bu, ek BDM çağrısı gerektirmez.
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

    # 2) Ara toplam (varsa) = kalem toplamları toplamı
    if "subtotal" in data and items:
        sub = _num(data.get("subtotal"))
        if sub is None or abs(sub - line_sum) > _TOL:
            data["subtotal"] = line_sum
            repairs += 1

    # 3) Genel toplam = ara toplam + vergi  (yoksa kalem toplamı)
    if items or "total_amount" in data:
        sub = _num(data.get("subtotal"))
        tax = _num(data.get("tax_amount"))
        if sub is not None and tax is not None:
            expected = round(sub + tax, 2)
        elif sub is not None:
            expected = sub
        else:
            expected = line_sum  # ör. PO: ayrı ara toplam/vergi yok
        ta = _num(data.get("total_amount"))
        if expected > 0 and (ta is None or abs(ta - expected) > _TOL):
            data["total_amount"] = expected
            repairs += 1

    return data
