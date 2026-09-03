"""
Turkish tax identifier validation — VKN (corporate) and TCKN (individual).

Why this is worth its own module: it is the only place in the pipeline where a
single extracted field can be checked against itself, with no second source and
no model call. A tax id carries its own check digit, so a transposed or dropped
digit is detectable *deterministically*. Nothing else in the extraction has
that property — a wrong invoice number or a wrong address is simply wrong, and
we can only notice it by comparing against something.

Measured on the real-document pilot (2026-09-03): all four genuine tax ids on
those documents validate, and all three model corruptions fail —

    a corporate VKN, printed 3x on one invoice          valid
    an individual TCKN, from a PDF text layer           valid
    a corporate VKN with two leading zeros              valid
    a corporate VKN, from a PDF text layer              valid
    the first of those with two digits transposed       INVALID
    an 11-digit id returned with a digit dropped        INVALID
    a hallucinated 10000000000                          INVALID

The digits themselves are not reproduced here or in the tests: they identify
real people and companies, and this repository is public. They live only in
`data/real/gt/`, which is gitignored. The tests use synthetic ids built to be
checksum-valid, which exercise the same algorithm.

One value printed on a real invoice does fail: a seller had put the placeholder
11111111111 in the buyer tax id field. Flagging it is not a false positive — an
accountant posting that invoice needs to know the id on it is not a real one.

The algorithms are the official ones from the Turkish Revenue Administration:

VKN (10 digits): for each of the first 9 digits, add its distance from the end,
take mod 10, multiply by 2^(distance), take mod 9 (mapping 0 to 9 for non-zero
inputs), sum, and the check digit is the complement to the next multiple of 10.

TCKN (11 digits): digit 10 is (7*sum(odd positions) - sum(even positions)) mod
10; digit 11 is sum of the first ten mod 10; the first digit cannot be 0.
"""
from typing import Optional

__all__ = ["classify_tax_id", "is_valid_vkn", "is_valid_tckn", "normalize_tax_id"]


def normalize_tax_id(value) -> Optional[str]:
    """
    Strip the punctuation people and OCR put in tax ids: spaces, dots, dashes,
    slashes. Returns None for anything that is not then a pure digit string.

    Deliberately does NOT strip letters — 'VKN' or 'TR' glued to the number is
    a sign the extractor grabbed the label along with the value, and that is
    worth surfacing rather than silently cleaning away.
    """
    if value is None:
        return None
    s = str(value).strip()
    for ch in " .-/_":
        s = s.replace(ch, "")
    return s if s.isdigit() else None


def is_valid_vkn(vkn: str) -> bool:
    """Check-digit validation for a 10-digit Turkish corporate tax number."""
    if len(vkn) != 10 or not vkn.isdigit():
        return False
    digits = [int(c) for c in vkn]
    total = 0
    for i in range(9):
        tmp = (digits[i] + 9 - i) % 10
        if tmp == 0:
            continue
        p = (tmp * pow(2, 9 - i)) % 9
        total += 9 if p == 0 else p
    return digits[9] == (10 - total % 10) % 10


def is_valid_tckn(tckn: str) -> bool:
    """Check-digit validation for an 11-digit Turkish national id number."""
    if len(tckn) != 11 or not tckn.isdigit() or tckn[0] == "0":
        return False
    d = [int(c) for c in tckn]
    if (sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def classify_tax_id(value) -> dict:
    """
    Classify an extracted tax id.

    Returns {'kind', 'normalized', 'valid', 'reason'} where kind is one of
    'vkn', 'tckn', 'unknown_length' or 'not_numeric'.

    `valid` is None — not False — when we cannot judge. A 9-digit id is not a
    Turkish tax number at all; it may be a perfectly good foreign VAT number
    (Anthropic's 701236788 on a real invoice in the pilot set). Reporting that
    as invalid would be worse than saying nothing, so length checks only fire
    for lengths Turkey actually uses.
    """
    normalized = normalize_tax_id(value)
    if normalized is None:
        return {"kind": "not_numeric", "normalized": None, "valid": None,
                "reason": "value is not a plain digit string"}

    n = len(normalized)
    if n == 10:
        ok = is_valid_vkn(normalized)
        return {"kind": "vkn", "normalized": normalized, "valid": ok,
                "reason": None if ok else "VKN check digit does not match"}
    if n == 11:
        ok = is_valid_tckn(normalized)
        return {"kind": "tckn", "normalized": normalized, "valid": ok,
                "reason": None if ok else "TCKN check digit does not match"}

    return {"kind": "unknown_length", "normalized": normalized, "valid": None,
            "reason": f"{n} digits — Turkish ids are 10 (VKN) or 11 (TCKN); "
                      f"not judged, may be a foreign tax number"}
