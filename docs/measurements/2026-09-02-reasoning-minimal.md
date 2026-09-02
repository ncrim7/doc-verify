# Measurement — 2026-09-02 (3) · reasoning_effort=minimal, unit price rendered

Supersedes [`2026-09-02-post-render-fix.md`](2026-09-02-post-render-fix.md).
Closes P0-1(b).

Two changes since that run, and they are **not** independent — the first had to
land before the second could be judged fairly:

1. **Generator**: receipts now print `qty x unit_price` (`4 x 274.16`). Before
   this, `unit_price` was in the ground truth but on no receipt page — the same
   contamination class as `currency`, missed because the render-integrity test
   only checked text fields, not numeric ones.
2. **Extractor**: `reasoning_effort="minimal"` for gpt-5 models,
   `max_completion_tokens` 4096 → 8192, and one retry when a response will not
   parse.

## Corpus integrity

**0 / 1624** scored ground-truth values missing from the rendered pages — text
*and* numeric. The previous check covered 596 fields (text only) and is what
let the receipt `unit_price` bug through.

## Setup

| | |
|---|---|
| Model | `gpt-5-nano`, `direct`, `reasoning_effort=minimal`, `max_completion_tokens=8192` |
| Corpus | 60 synthetic docs, `seed=42`, regenerated |
| Command | `run_full_pipeline.py --split measure --delay 0.3 --checkpoint` |
| Runtime | **~5.6 min** (was ~22) |
| Raw result | `2026-09-02-reasoning-minimal.results.json` |

## Result

**Headline: 97.66% field-level exact match over all 60 documents.**

| | run 2 (full reasoning) | run 3 (minimal, unit-price bug) | **run 4 (this)** |
|---|---|---|---|
| EM, all documents | 96.79% | 92.20% | **97.66%** |
| Semantic similarity | 99.85%* | 95.94% | 99.75% |
| Token F1 | 99.08%* | 92.05% | 98.77% |
| Silent failures | 1 | 0 | **0** |
| Retries needed | — | 0 | **0** |
| Runtime | ~22 min | ~6 min | ~5.6 min |
| invoice / PO / receipt | 92.59 / 98.48 / 99.31* | 97.36 / 97.06 / 82.18 | **97.34 / 97.13 / 98.51** |

\* run 2's sim/F1 and per-type figures are OK-only (59 docs); its all-document
EM is 96.79%.

Run 3 is kept in the table because it is the evidence that the receipt collapse
was contamination, not the model: the only difference between run 3 and run 4
is the rendered unit-price column, and receipts went 82.18% → 98.51%.

## This is a trade-off, not a clean win

| | run 2 | run 4 |
|---|---|---|
| Field misses | 30 | **46** |
| Documents scoring a perfect 1.0 | 36 / 59 | **28 / 60** |

All-document EM went *up* because losing one document to a silent failure cost
more than 16 extra field misses. Per-document quality went *down*.

The character of the errors changed too. Under full reasoning the misses were
clean normalisations (`Zımba` → `Zimba`). Under `minimal` they include genuine
transcription noise:

```
Post-it Not Bโลğü          Thai glyphs
Toplanti Masasi Kisĭlik    Latin Extended
Aydinlatma Armatu̇ru        combining dot above
Ofis Sandalyəsi            schwa
Tavuk Göggsü / Kilittli    doubled letter
buyer_tax_id 6499091334 -> 64990091334   inserted digit (10 -> 11)
due_date     2025-11-13  -> ''            field dropped
```

Removing the model's reasoning budget makes it transcribe more literally and
more noisily. Worth keeping for now — the silent-failure class it eliminates is
a P0 and the speed is 4x — but it is not free.

## Error anatomy — 46 misses, all genuine

Verified against the rendered page text: **0 contaminated, 46 genuine.**

| field | count |
|---|---|
| `description` | 38 |
| `supplier_name` | 4 |
| `due_date` / `buyer_tax_id` / `buyer_name` / `store_name` | 1 each |

**83% are Turkish character corruption** — `ı`→`i`, `ğ`→`g`, `ö`→`o`, `ü`→`u`,
plus the noise above. This is now unambiguously the single remaining problem
(P1-4).

Two hypotheses to test next, one variable each:

1. `SYSTEM_PROMPT` still carries the thesis-era instruction *"Turkish
   characters can be misread as I/II/s/g … auto-correct these typos"*, written
   for a font bug that no longer exists. Replace with "copy verbatim, preserve
   every Turkish character, do not transliterate".
2. `reasoning_effort="low"` (64 reasoning tokens, measured) as a middle ground
   — possibly enough to stop the Thai-glyph class of noise without re-opening
   the token-budget failure.

## D1 — closed

0 silent failures, and **0 retries logged**: `reasoning_effort="minimal"`
prevents the token-budget overflow outright rather than the retry papering over
it. The retry stays in as a cause-agnostic net, and `DocumentPipeline` still
turns any unparseable result into REVIEW.

## D4 — verification still contributes nothing

0 issues, 0 auto-corrections, 0 correction-agent calls across 60 documents;
`raw` EM equals final EM exactly. Same finding as every previous run: on clean
synthetic input the rule verifier has nothing to catch.

## Scope

Still 100% synthetic. No real, scanned or photographed documents; real-world
accuracy remains unmeasured (P1-5). The marketing embargo can now be lifted on
the P0-1 ground — a failed extraction is flagged, not skipped — but the figure
still describes clean synthetic input only, and that qualifier must travel with
it.
