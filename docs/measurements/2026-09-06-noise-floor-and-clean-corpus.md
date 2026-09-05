# Measurement — 2026-09-06 · the noise floor, measured at last

Three identical runs. Same code, same corpus, same settings, nothing changed
between them.

| run | EM (n=60) | perfect | misses | REVIEW |
|---|---|---|---|---|
| 10 | 97.48% | 29 / 60 | 44 | 3 |
| 11 | 97.83% | 35 / 60 | 37 | 2 |
| 12 | 97.71% | 31 / 60 | 39 | 3 |

```
mean   97.67%
sigma   0.178 pp
range   97.48% – 97.83%   (spread 0.35 pp)
2 sigma 0.36 pp
```

**A code change that moves the headline by less than 0.36 pp is not evidence.**
Only **33 of 60** documents scored identically in all three runs — 45% of the
corpus is unstable between runs of the same code.

## A claim of mine that does not survive this

On 2026-09-02 I wrote that `reasoning_effort` `minimal` → `low` was supported by
evidence independent of the headline: *perfect documents 31 → 35, field misses
39 → 34.* Measured across identical runs, those two metrics range over **6
documents and 7 misses** respectively. Both of those "independent" signals sit
inside the noise.

What does survive is the comparison against the model default: 1–2 silent
failures per run and ~22 minutes, against 0 failures and 8.3 minutes for `low`.
That is structural, not a fraction of a percent. **`low` over the default is
established. `low` over `minimal` is not**, and `minimal` is 33% faster — worth
a proper three-runs-each comparison before the choice is treated as settled.

## The corpus was regenerated, so this is a new baseline

`_tax_id` now computes the VKN check digit (P0-6), which changes how many random
draws it makes and therefore the whole generated corpus. Runs 10 onward are not
comparable to 6, 7 or 9. Sample size was held at 60 so the sigma above applies
at the same scale as the historical figures.

Holding the random stream aligned with a discarded draw was considered and
rejected: it would have to live forever to stay useful, and run 9 is already
contaminated by 18 false REVIEWs.

## What the clean corpus revealed

### P1-6 has 100% precision here

Three documents flagged `tax_id_checksum_invalid`, and all three are genuine —
the ground-truth value passes the check digit and the model's does not:

```
8951343329  ->  89513433329
3374989417  ->  33749894117
7154516805  ->  71554516805
```

Every one is **digit doubling**, 10 digits becoming 11 — the class an earlier
note had flagged as "document-specific, not random". It is now caught
deterministically, with no model call. On the real corpus the same rule caught
a transposition of equal length, which a length check would have missed.

### The money tolerance costs nothing on synthetic input

Zero money misses across all three runs. Synthetic amounts are generated
exactly and read exactly, so tightening from 1% relative to kuruş-exact
(P1-7) changes nothing here — while on real documents it caught a genuine 0.07
TRY error that the old rule scored as a match. A metric fix with no downside is
rare enough to note.

### Two false-positive classes of my own, found and fixed

**`seller_tax_id_missing` fired on 20 of 20 purchase orders.** `PO_SCHEMA` does
not ask for a supplier tax id at all, so the rule complained about a field the
extractor was never told to produce. The justification for the rule is UBL-TR,
which mandates the seller's PartyIdentification on **e-fatura and e-arşiv
documents** — a purchase order is neither. I over-applied my own argument.
Restricted to invoices, and a test now asserts that every field this rule can
name exists in the schema the extractor is given, so the class cannot return.

**Issue counts before and after, across all three runs:**

| | before | after |
|---|---|---|
| `seller_tax_id_missing` (POs) | 60 | 0 |
| `tax_id_checksum_invalid` | 7 | 7 (all true positives) |
| `subtotal_mismatch` | 2 | 2 (genuine) |

Nine real issues across 180 document-runs.

## P1-8, answered without spending an API call

The pinned extractions let the verifier be re-run offline. On the clean corpus
the correction agent would fire on **0, 1 and 1 of 60** documents.

**Its trigger conditions essentially do not occur on clean synthetic input**, so
a fourth run with correction enabled would have been indistinguishable from
runs 10–12. The correction agent cannot be evaluated on this corpus at all —
only on documents that genuinely have problems, which means real ones. That is
now the honest statement of P1-8 rather than a number.

It also explains the historical record: runs 6 and 7 reported the agent firing
on 0 of 60. It was never dormant by accident. There was nothing for it to do.

## The real documents, end to end

`real_004` — the telecom bill that started all of this — now comes back
**REVIEW**, with `subtotal_mismatch` and `total_mismatch`. It used to come back
**OK carrying a fabricated total**. That is the whole arc of this work visible
in one document.

An honest negative result alongside it: the new `amount_payable` field did
**not** make the model distinguish the two totals. It returned 615.5 for both,
where the invoice total is 615.43. The schema now expresses the difference; the
model still conflates it. The system handles the document correctly anyway,
because the printed figures do not reconcile and the verifier says so. **We do
not need the model to be right. We need the system to notice when it is not** —
and on the document where it mattered, it did.

The tax id rules produce a graded response on the four real documents with no
false positives:

| document | severity | what |
|---|---|---|
| `real_001` | critical ×2 | a real transposition, and a placeholder printed by the seller |
| `real_002` | info | a foreign 9-digit registration — correctly not judged |
| `real_002` | warning | a label captured with the value, with the right digits suggested |
| `real_003`, `real_004` | warning | a seller VKN printed on the page and not read |

The label case is new: the model returned `TR TIN <digits>` for a tax id, which
used to raise `info` and let the document out as OK with a malformed id in it.
It is now a warning carrying the extracted digits. The signature is narrow —
exactly 10 or 11 digits plus a space — so a foreign VAT number written as one
token does not trip it.

## Scope, unchanged

60 clean, digitally rendered synthetic PDFs. `gpt-5-nano`, `reasoning_effort=low`,
correction disabled. Real-world accuracy remains unmeasured: five figures on the
same four documents now span 57.81% to 74.28%, and 11 of the 15 supplied
documents still have no ground truth.
