# Measurement — 2026-09-03 · P0-4 provenance, P1-6 tax ids, and the noise floor

Three findings, one of which invalidates part of how every previous number in
this repo was reported.

| run | corpus | change | EM |
|---|---|---|---|
| 6 | synthetic, n=60 | `reasoning_effort=low` | 98.23% |
| **7** | synthetic, n=60 | **+ P0-4 provenance fix** | **98.00%** |
| — | real, n=4 | pre-P0-4 | 57.81% |
| — | real, n=4 | post-P0-4 | 74.28% |
| **8** | real, n=4 | **+ P0-4, corrected GT, P1-6** | **65.86%** |

Read the rest of this note before quoting any of those.

## 1. P0-4 is inert on synthetic documents — including before the fix

The −0.23 pp between run 6 and run 7 is **not** the provenance fix. It cannot
be, and the logs prove it: both runs report

```
delta from repair + correction: +0.00 pp
```

Synthetic invoices are generated with internally consistent arithmetic. Before
the fix, `repair_arithmetic` overwrote the printed total with `subtotal + tax`
— which on a consistent document is *the same number*. After the fix it does
not overwrite at all. Same output, both ways. The repair layer has never earned
anything on this corpus.

That is the real verdict on P0-4: it did not just fix a bug, it removed a layer
whose measured contribution across both corpora is zero on synthetic and
negative on real.

### A confound that nearly got reported as a result

Run 6's pinned config says `reasoning_effort: None`, run 7's says `low`, which
looks like two variables moved at once. It is a recording artefact: the line
that logs the effective setting was added *after* run 6, so `None` means "not
captured", not "default". The runtimes settle it — 8.3 min and 8.5 min, and the
documented cost of `low` is 8.3 min against 5.6 for `minimal`. Both runs used
`low`.

The lesson stands anyway: a config field that can silently mean two different
things is worse than no field. It now logs at startup on every run.

## 2. The noise floor — every previous number here is n=1

Run 6 and run 7 are, in effect, the same behaviour on the same 60 documents.
They differ by **0.23 pp**.

Set the earlier claims against that:

| claim | measured delta | above the floor? |
|---|---|---|
| prompt fix (run 4 → 5) | +0.31 pp | barely |
| `reasoning_effort` `minimal` → `low` (run 5 → 6) | +0.26 pp | **no** |

The `low` decision has independent support that does not depend on the headline
— perfect documents 31 → 35, field misses 39 → 34, and the apostrophe class
going 4 → 0 — and it stands. **The EM delta alone does not.** No accuracy
improvement under roughly 0.5 pp should be attributed to a code change from a
single run, and none of these figures belongs in a sales conversation as a
before/after.

Establishing an actual σ needs three or more identical runs. Not done. Until it
is, the honest statement is a range, not a point.

## 3. Real documents: the variance is the finding, not the number

Three real-pilot runs on the same four documents: 57.81%, 74.28%, 65.86%. The
last one *should* have scored higher than the previous — two ground-truth
corrections landed in its favour — and it scored lower. Model variance on real
documents dwarfs every code change measured so far.

Worse, the first two are not comparable to the third and cannot be made so:
those runs pinned only scores, not the extracted data, so they cannot be
re-scored against corrected ground truth. `scripts/run_real_pilot.py` now pins
`data` and `raw` and takes `--score-only`, so from here a GT change and a model
change can be separated. That should have been true from the first real run.

**n=4 with a spread of 8–16 pp measures nothing.** It is a defect finder, and a
good one — everything below came out of it.

### Ground truth corrections made this round

Three GT decisions were penalising defensible model output. Two were wrong and
were changed; two were checked and kept.

| decision | outcome |
|---|---|
| `items[0].description`: product name vs full cell | **changed to the full cell.** The prompt asks what is printed, not for a product name. Grading against an unstated convention penalises a correct read. |
| `total_amount` on the telecom bill: 615,43 vs 615,50 | **not scored.** Both are printed, both are correct, and the schema has one field for them. Which one a Turkish bookkeeper posts is a domain question the page cannot answer, and guessing is not allowed. |
| `buyer_name`: the honorific `SAYIN` prefixed to the name | **kept.** *Sayın* is a form of address, not part of a name. Genuine error. |
| `vendor_name`: two merged header lines | **kept.** Genuine error — and it did not recur in run 8, so it was run-specific. |

Excluding `total_amount` removes the field the model got wrong on the hardest
document, which flatters the score. That exclusion is recorded in the GT file's
`_schema_gaps` block so it stays visible rather than disappearing into an
average.

### Schema gaps the real documents exposed

None of these are model errors. The schema cannot express the document.

- **Two valid totals.** `Toplam Fatura Tutarı` and `ÖDENECEK TOPLAM` differ by a
  carried-over balance. Needs `amount_payable` alongside `total_amount`, or a
  `totals[]` structure.
- **Two tax bases.** KDV %20 on 472,97 and ÖİV %10 on 472,33 on one bill. One
  `tax_rate` field cannot hold that.
- **Charge lines have no quantity or unit price.** The schema requires both, so
  extracting them means inventing them.

## 4. P1-6 — tax id check digits, and the first real catch

A Turkish tax id carries its own check digit, which makes it **the only field
in the schema that can be verified against itself** — no second source, no
model call. Measured on the pilot corpus:

```
a corporate VKN, printed 3x on one invoice     valid
an individual TCKN, from a PDF text layer      valid
a corporate VKN with two leading zeros         valid
a corporate VKN, from a PDF text layer         valid
the first of those, two digits transposed      INVALID
an 11-digit id returned with a digit dropped   INVALID
a hallucinated 10000000000                     INVALID
```

Four out of four genuine, three out of three corruptions caught. In run 8 it
made its first live catch: the model returned a `buyer_tax_id` one digit off
from the one printed on `real_001`, the checksum failed, and the document went
to **REVIEW — as the only reason.** Without the check it would have been OK
with a wrong tax id.

The digits are not reproduced here, and the tests use synthetic checksum-valid
stand-ins: the real ones identify real people and companies, and this repository
is public. They live only in `data/real/gt/`, which is gitignored. This note was
first drafted with the real values in it — including the repository owner's own
national id number — and caught before the commit. Worth stating plainly,
because the `.gitignore` rules protect the corpus and not the prose about it.

Two deliberate restraints:

- **No auto-correction.** There is no way to infer which digit was wrong. The
  check says "this is wrong", never "this is right".
- **Lengths Turkey does not use are not judged.** A US vendor's 9-digit Turkey
  VAT registration on `real_002` returns `valid: None` and raises `info`, not
  `critical`. Calling a foreign tax number invalid would be worse than silence.

Severity is `critical`, in one constant (`TAX_ID_SEVERITY`). A failed check
digit means the number is provably wrong — the model misread it, or the
document carries a bogus one. Both need a human before anything is posted. The
alternative, `warning`, keeps throughput and lets a wrong tax id post silently;
the constant exists so that trade is a one-line decision, not a rewrite.

`11111111111` is printed on `real_001` — the seller put a placeholder in the
buyer tax id field of a real invoice. Flagging it is a **true positive**: an
accountant posting that invoice needs to know the id on it is not a real one.

### Open, not decided

`real_004` is a Turkish e-arşiv invoice with **no vendor tax id extracted at
all**, and its verdict is OK. The checksum rule cannot help — there is nothing
to check. Making `vendor_tax_id` a required field for invoices would catch it,
and would also flag foreign invoices that legitimately lack one. Not decided.

## Scope

Synthetic: 60 clean, digitally rendered PDFs, `gpt-5-nano`, `reasoning_effort=low`.
Real: 4 documents. Both figures carry every qualifier above. Real-world accuracy
remains **unmeasured** — 11 of the 15 supplied documents still have no ground
truth.
