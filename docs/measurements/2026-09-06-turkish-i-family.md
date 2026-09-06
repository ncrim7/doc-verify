# Measurement — 2026-09-06 · the Turkish i, measured letter by letter

P1-4b was filed as "the prompt made the model invent Turkish characters". That
was half right, and the smaller half.

## Diagnosis first

Classifying every text miss in run 13 by whether the letter skeletons match and
which way the diacritics moved:

| class | count |
|---|---|
| diacritic **dropped** (Raflı → Rafli) | **22** |
| other transcription error | 15 |
| diacritic **invented** (Sezgin → Sezgın) | **3** |

Invention was real but was 3 of 40. Dropping was 7× larger.

Broken down per letter, against how often each appears in the corpus:

| letter | in corpus | dropped | rate |
|---|---|---|---|
| **ı** | 221 | 16 | **7.2%** |
| **İ** | 7 | 2 | **28.6%** |
| ş | 49 | 2 | 4.1% |
| ö | 14 | 1 | 7.1% |
| ğ | 45 | 1 | 2.2% |
| ç Ç ü Ü Ş | 176 | **0** | **0.0%** |

**18 of the 22 losses are `ı` or `İ`** — the two letters unique to Turkish,
with no counterpart in any other Latin-script language. Every letter that also
exists in German or French survives perfectly: `ü` 81/81, `ç` 16/16, `Ç` 24/24,
`Ş` 35/35.

That rules out resolution as the cause. A breve on `ğ` is a smaller mark than a
missing dot on `ı`, and `ğ` fares eleven times better. What separates the two
groups is not how visible the mark is, it is whether the model's Latin-script
prior contains the letter at all.

The prompt at the time listed all twelve Turkish letters as equally important.
The evidence says ten of them were already fine.

## The change

The Turkish section was rewritten around the i family: the four-way
`ı i I İ` distinction spelled out as a table, an instruction to check the dot
before writing each one, and — the part P1-4b actually asked for — **explicit
symmetry**, with a worked example in each direction:

```
do not REMOVE dots:  "Raflı" is not "Rafli",  "İhsanoğlu" is not "Ihsanoğlu"
do not ADD    dots:  "Sezgin" is not "Sezgın", "Diş" is not "Dış"
```

The remaining ten letters keep one line between them.

## Result: half of it worked

| | run 13 | run 14 |
|---|---|---|
| EM (n=60) | 97.64% | **98.22%** |
| total misses | 41 | **33** |
| diacritics **invented** | 3 | **0** |
| diacritics **dropped** | 22 | **21** |
| other text | 15 | 14 |

**The invention class is gone: 3 → 0.** That is P1-4b, closed. The symmetry
instruction did exactly what it was written to do.

**The dropping class did not move: 22 → 21**, and `ı` alone is 19 of the
remaining 21. The dominant error mass is untouched.

So the headline is not mainly a diacritic story. Of the 8 misses that
disappeared, 3 are the invention class, 2 are the escape fix below, and about 3
are run-to-run variance. Against σ = 0.178 pp the +0.58 pp reads as ~3σ, but it
is one run, and **the miss taxonomy is the stronger evidence than the average**
— a class going from 3 to 0 in a direction the change specifically targeted is
worth more than half a point on a mean.

## The residual is not a lexical problem

The surviving `ı` losses are spread across **11 distinct words in 18
occurrences** — `Zımba` 5 (it is a frequent catalogue item), then `Kamerası`,
`Aylık`, `Yılmaz`, `Bandı`, `Aydınlatma`, `Sıvı`, `Kıyma` and others once each.
Diffuse, not a handful of words the model has memorised wrongly.

**Two targeted prompt attempts have now failed to move it.** The lever is
somewhere else: a different model, a different render, or a second source — a
supplier and product master that can snap `Rafli` back to `Raflı`, which is the
same "compare against something" answer the n=15 measurement reached for
invoice numbers. Prompting is finished as a tool for this class.

## A separate bug, fixed with certainty

Two misses in run 13 were not model errors at all:

```
GT  'Dizüstü Bilgisayar 14" i5'      PR  'Dizüstü Bilgisayar 14\" i5'
GT  "Beyaz Tahta Kalemi (4'lü)"      PR  "Beyaz Tahta Kalemi (4\'lü)"
```

The model over-escapes: it writes `14\\"` in the JSON, `json.loads` turns that
into a literal backslash plus a quote, and the backslash survives into the
value. `_parse_json_response` now strips a backslash that immediately precedes
a quote or apostrophe, recursively, in parsed values only.

This is **format normalisation** — it changes how a character is represented,
never which characters the page carries — the same category as `15/03/2026` →
`2026-03-15`. It must never grow into deriving a value; P0-4 and P0-7 are what
that costs.

Verified without spending anything: re-scoring **the same pinned extractions**
gives **97.642% → 97.728%, +0.085 pp**, with `invoice_0001` going 94.9% → 100%.
No stochasticity involved — same data, deterministic transform. That is the
cleanest measurement in this project.

## Scope

60 synthetic documents, `gpt-5-nano`, `reasoning_effort=low`, correction
disabled. One run for the prompt change; the escape fix is deterministic and
needs none. Real-document effect not measured separately — the real corpus
scores far fewer Turkish text fields, and its noise at n=15 is larger than the
effect being looked for.
