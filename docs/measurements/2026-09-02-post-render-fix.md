# Measurement — 2026-09-02 (2) · after the render fix

Re-run of the Phase-1 measurement on a corpus where every scored field is
actually printed on the page. Supersedes
[`2026-09-02-baseline.md`](2026-09-02-baseline.md), which was invalidated by
three data-generation rendering bugs.

**One variable changed: the corpus.** Model, strategy, prompt, token ceiling
and pipeline are byte-identical to the invalidated run, so the delta is
attributable to the generator fix alone.

## Setup

| | |
|---|---|
| Model | `gpt-5-nano`, strategy `direct`, `temperature` unset, `max_completion_tokens=4096` |
| Pipeline | extract → rule-verify + auto-correct → correction-agent → evaluate |
| Corpus | 60 synthetic docs, `seed=42` (20 invoice / 20 PO / 20 receipt), regenerated after `3fdb53d` |
| Corpus integrity | **0 / 596** scored GT values missing from the rendered pages (was ~264) |
| Command | `run_full_pipeline.py --model gpt-5-nano --strategy direct --split measure --delay 0.3 --checkpoint` |
| Runtime | ~22 min |
| Raw result | `2026-09-02-post-render-fix.results.json` (this folder) |

## Result

| Metric | Invalidated run | **This run** | Δ |
|---|---|---|---|
| Field-level exact match | 96.10% | **98.44%** | **+2.34** |
| Semantic similarity | 99.11% | **99.85%** | +0.74 |
| Token F1 | 97.92% | **99.08%** | +1.16 |
| Docs scored | 58 / 60 | 59 / 60 | |
| Documents scoring a perfect 1.0 | 20 / 58 (34%) | **36 / 59 (61%)** | +27 pp |
| Lowest document | 0.842 | 0.895 | |

Per type: invoice 97.5% (n=19) · PO 98.5% (n=20) · receipt 99.3% (n=20).

Counting the one unparseable document as 0% gives **96.80%**.

Baseline == verified == corrected again (see D4).

## Error anatomy — 30 misses, all genuine

Every remaining miss was checked against the rendered page text
(`squash(gt_value) in squash(page_text)`). This is the check that was missing
from the first report.

| | count |
|---|---|
| Genuine model errors (GT **is** on the page) | **30** |
| Still contaminated (GT not on the page) | **0** |

By field: `description` 26 · `buyer_name` 2 · `buyer_tax_id` 1 · `store_name` 1.

### Dominant pattern (26 / 30 = 87%): Turkish dotless-ı normalised to i

The model reads the text but rewrites Turkish characters toward ASCII:

| Ground truth | Model output |
|---|---|
| `Zımba Teli No:10 (1000'li)` | `Zimba Teli No:10 (1000'li)` |
| `Toplantı Masası 6 Kişilik` | `Toplanti Masasi 6 Kişilik` |
| `Web Sitesi Bakım Hizmeti (Aylık)` | `Web Sitesi Bakım Hizmeti (Aylik)` |
| `Aydınlatma Armatürü LED Panel` | `Aydinlatma Armatürü LED Panel` |
| `Sıvı Çamaşır Deterjanı (2.5 L)` | `Sivi Çamaşır Deterjani (2.5 L)` |
| `Post-it Not Bloğu 76x76` | `Post-it Not Blogu 76x76` (`ğ`→`g`) |
| `LED Monitör 24" IPS` | `LED Monitor 24" IPS` (`ö`→`o`) |

It is **inconsistent, not a systematic transliteration** — `Çalışma Masasi`
keeps the first `ı` and converts the last; `Sıvı … Deterjani` keeps two and
converts one. That looks like a per-token slip, not a decoding rule.

### The rest (4)

- `Korutürk Seven Ltd.` → `Korütürk Seven Ltd.` — a diacritic *added* that is
  not on the page.
- `Çamurcuoğlu Sezgin Şti.` → `Çamurcuoğlu Sezin Şti.` — a dropped letter.
- `buyer_tax_id 7109324808` → `7109334808` — one digit.
- `Dizüstü Bilgisayar 14" i5` → `Dizüstü Bilgisayar 14\" i5` (×2) — a stray
  backslash survives JSON decoding; a serialisation artefact around the inch
  quote, not a reading error.

### Likely cause worth testing first

`SYSTEM_PROMPT` still carries this instruction:

> *The input may be rendered from documents where Turkish characters can be
> misread as 'I', 'II', 's', 'g' … (e.g. 'SatIcI' instead of 'Satıcı').
> **Auto-correct these typos to proper Turkish words based on context.***

That text was added (thesis `cozum_01`) as a workaround for the very font bug
fixed in `3fdb53d`. The pages now render `Satıcı` correctly, but the prompt
still tells the model to "auto-correct" what it sees — an open invitation to
rewrite correct Turkish. **The workaround for the bug we just closed may now be
the leading error source.**

Cheapest next experiment (P1-4): replace it with "copy the text verbatim;
preserve every Turkish character exactly (`ı` is not `i`); do not transliterate
or normalise", then re-measure. One variable, one run, ~$0.05.

## D1 recurrence — 1 silent failure (was 2)

`invoice_0014`: HTTP 200, unparseable JSON, `extract()` returned `{}`,
`run_full_pipeline.py:140` skipped it — verification never ran, nothing
flagged. Different document than last time (`invoice_0017`, `po_0001`), which
confirms the cause is **stochastic reasoning-token overflow**, not
document-specific. Still P0-1.

## D4 — verification still contributes nothing here

0 issues, 0 auto-corrections, 0 correction-agent calls across 59 documents.
`arithmetic_repair` normalises the maths inside `extract()`, and the model
populates every required field, so the rule verifier has nothing to catch on
clean synthetic input. Consistent with the thesis RQ3 finding.

## Scope and usage

Still **100% synthetic**, clean digitally-rendered PDFs. No real, scanned or
photographed documents — real-world accuracy remains unmeasured (P1-5).

**The marketing embargo still applies.** P0-2 is closed, but P0-1 is not: one
document in sixty still disappears silently. Until a broken extraction is
routed to REVIEW rather than skipped, no accuracy figure supports the product's
core claim.

## Reproducibility

`seed=42` + `src/data_generation/*` at `3fdb53d` + the config above.
`data/` and `results/` are gitignored; this report and the committed
`*.results.json` are the durable record.
