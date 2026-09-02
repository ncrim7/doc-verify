# Measurement — 2026-09-02 (4) · prompt fix + reasoning_effort sweep

Two experiments, one variable each, on the same corpus. Supersedes
[`2026-09-02-reasoning-minimal.md`](2026-09-02-reasoning-minimal.md).
Closes P1-4.

| run | change from the previous run | EM (all 60) | perfect docs | field misses | runtime |
|---|---|---|---|---|---|
| 4 | baseline (`minimal`, stale prompt) | 97.66% | 28 / 60 | 46 | ~5.6 min |
| 5 | **prompt**: transcribe instead of "auto-correct" | 97.97% | 31 / 60 | 39 | ~5.6 min |
| **6** | **`reasoning_effort` `minimal` → `low`** | **98.23%** | **35 / 60** | **34** | ~8.3 min |

Zero silent failures and zero retries in all three. Corpus verified 0/1624
contaminated throughout; all residual misses are genuine.

## Experiment 1 — the prompt (run 4 → 5)

`SYSTEM_PROMPT` still told the model *"Turkish characters can be misread as
I / II / s / g … auto-correct these typos"* — a workaround for the font bug
closed in `3fdb53d`. Replaced with the opposite instruction: transcribe
character for character, `ı` and `i` are different letters, do not
transliterate or normalise.

Result: **+0.31 EM**, `description` misses 38 → 28 (−26%). The targeted effect
is real, but the churn is high — 25 misses fixed, 18 new ones appeared, 21
persisted. That churn is the finding: most of the residual error was stochastic
transcription noise, not prompt-driven rewriting, which is what pointed at
`reasoning_effort` as the next lever.

Committed regardless of the number: a workaround for a bug that no longer
exists does not belong in the code.

## Experiment 2 — reasoning budget (run 5 → 6)

Measured on the extraction path:

| setting | reasoning tokens | EM | perfect | runtime | silent failures |
|---|---|---|---|---|---|
| model default | 128 (trivial call) | — | — | ~22 min | **1–2 per run** |
| `minimal` | 0 | 97.97% | 31 / 60 | ~5.6 min | 0 |
| **`low`** | 64 | **98.23%** | **35 / 60** | ~8.3 min | 0 |

`low` is now the default (`src/config.py`, override with
`LLM_REASONING_EFFORT`). It buys +0.26 EM and +4 perfect documents for ~2.7
minutes, costs about $0.0015 more per 60-document run, and is still 2.6x
faster than the model default — which is not an option anyway, because that is
the setting whose reasoning tokens overflow the completion budget and produce
the silent-failure class.

### The apostrophe class — why the metric was not loosened

Run 5 had four `description` misses that were only a straight-to-curly
apostrophe: ground truth `(4'lü)`, model output `(4’lü)`. Typographically
equivalent, worth nothing to a bookkeeping user, but strict exact-match scores
them 0. The obvious move was to normalise apostrophes in `metrics.py` — it
already has numeric tolerance and address fuzzy matching, so it would have been
defensible.

It was left alone, because loosening a metric to raise a number is a habit
worth not starting. Run 6 then eliminated the class outright: **4 → 0.**
Normalising would have masked a real defect that a real fix removed.

## Residual anatomy — 34 misses, all genuine

`description` 30 · `buyer_name` 2 · `vendor_tax_id` 1 · `buyer_tax_id` 1.

Still all Turkish character handling, but the direction is no longer one-way:

```
toward ASCII      Zımba->Zimba  Bloğu->Blogu  Monitör->Monitor  Kamerası->Kamerasi
back the other    Grafik->Grafık  Diş->Dış  Bloğu->Blogı
other alphabets   Camașir (Romanian s-comma)  Filt<soft-hyphen>re
transcription     Zınba (m->n)  Kilittli (doubled)  Korütük (dropped r, added umlaut)
tax id            9993867749 -> 99938667749 ; 6499091334 -> 64990911334
```

Two things worth noting:

1. **Over-correction has appeared.** `Grafik` → `Grafık` and `Diş` → `Dış`
   invent Turkish characters that are not on the page. The new prompt tells the
   model to preserve Turkish letters; it may now be over-applying that. If the
   next prompt iteration targets this, the instruction should be symmetric:
   preserve what is printed, in both directions.
2. **The tax-id digit doubling is document-specific, not random.** Both tax ids
   on `invoice_0017` gained a duplicated digit (10 → 11 digits) in the same
   run, and the same document did it in the previous run too. For a financial
   product a silently wrong tax id matters; a length check (Turkish VKN/TCKN
   are 10 or 11 digits, and a doubled digit usually breaks the checksum) would
   catch it deterministically in `rule_based_verifier` — no LLM call needed.
   Queued as P1-6.

## Scope

Unchanged and still binding: **100% synthetic, clean digitally-rendered PDFs.**
No real, scanned or photographed documents. Real-world accuracy is unmeasured
(P1-5), and that qualifier travels with the number.
