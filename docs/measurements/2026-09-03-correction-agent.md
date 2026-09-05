# Measurement — 2026-09-03 · the correction agent, and what a false positive exposed

The third agent had never actually run.

Runs 6 and 7 report the correction agent firing on **0 of 60** documents. Run 9,
after a new rule started flagging things, fired it on **39 of 60** — and its net
contribution was **−0.41 pp**: 8 documents damaged, 2 improved, and four
perfect extractions destroyed. Every synthetic accuracy figure this repository
has published measured a three-agent pipeline in which the third agent was
dormant.

## How it surfaced

P1-6 taught the verifier to check Turkish tax id check digits. The synthetic
generator produced ten random digits, and only one in ten of those satisfies a
VKN check digit. So 30 ids across 18 documents were flagged
`tax_id_checksum_invalid` — **all false positives**: the numbers really are
invalid, but because our generator invented them, not because the model misread
anything.

That was a generator defect. What it exposed was worse.

## The cascade

```
generator invents a tax id with no valid check digit
  -> P1-6 flags it: critical
    -> correction agent runs
      -> agent rewrites the LAST DIGIT until the checksum passes
        -> re-verification passes
          -> verdict OK
```

The evidence is unambiguous — every change was the check digit alone:

```
3218196001 -> 3218196002      6184959310 -> 6184959311
8121913619 -> 8121913610      3990916998 -> 3990916996
3634957885 -> 3634957886
```

`invoice_0015` is the one to remember: `reasons=[]`, verdict **OK**, and the
buyer tax id silently altered. The system's answer to "this number is provably
wrong, a human must look" was to make the number look right and report success.

This is P0-4 one layer up. A value printed on the page is evidence; the agent
replaced it with an inference that satisfied a constraint.

## It was not only the flagged fields

```
invoice_0005  items[3].description  'Post-it Not Bloğu 76x76' -> 'Post-it Not Blogu 76x76'
invoice_0008  buyer_name            'Tevetoğlu A.Ş.'          -> 'Tevetoglu A.Ş.'
invoice_0017  items[7].description  'LED Monitör 24" IPS'     -> 'LED Monitor 24" IPS'
invoice_0008  vendor_tax_id         '0164005242'              -> '0164000524'
```

None of those fields were flagged. No rule inspects a `description` or a
`buyer_name` for Turkish characters. The agent re-derived the whole document
and transliterated Turkish letters to ASCII — the exact regression class the
extraction prompt was fixed to prevent in P1-4, arriving through the correction
pass instead. And `0164005242 -> 0164000524` is not a check-digit computation
at all; it is a misread.

`pipeline.py` called step 4 "targeted re-extraction of flagged fields" and
`correction_agent.py` documented itself as correcting "only those specific
fields". The prompt said the opposite: *"Return a COMPLETE corrected JSON
object with ALL fields."* The merge took every non-null value it got back. The
architecture description and the implementation had diverged, and nothing
tested the gap.

## Six defects

| # | where | defect |
|---|---|---|
| 1 | `pipeline.py` | correction triggered on ANY issue, `info` included |
| 2 | `correction_agent.py` prompt | asked for every field, so the whole document was re-rolled |
| 3 | `correction_agent.py` merge | accepted every non-null value, flagged or not |
| 4 | `rule_based_verifier.py` | the checksum message read as an instruction to satisfy the checksum |
| 5 | `pipeline.py` | re-verification laundered the fabrication into OK |
| 6 | `document_generator.py` | synthetic tax ids were not checksum-valid |

Defects 4 and 6 are mine, introduced with P1-6 the same day. They exposed 1, 2,
3 and 5, which had been latent since the pipeline was written.

## The fixes

**Trigger** — only `critical` issues, and only ones marked correctable.

**An uncorrectable class** — an issue is correctable when re-reading the page
could plausibly produce the right value. A failed check digit is not: there is
no way to derive which digit was wrong. `UNCORRECTABLE_RULES` keeps those away
from the agent entirely. The P1-6 code already said "the check says *this is
wrong*, never *this is right*"; nothing enforced it until now.

**An allowlist merge** — a field that was not flagged cannot be written,
whatever the model returns. The prompt asks for the same restraint, but a
prompt is a request and only the code is a guarantee. Refused keys are logged:
a model that keeps trying to rewrite unflagged fields is worth knowing about.

**A diff guard** — every field the correction changed is compared against the
set that was flagged, and anything else becomes `unrequested_correction: <field>`,
a REVIEW reason in its own right. This is the structural piece: a fabrication
can no longer turn REVIEW into OK, because the act of rewriting an unrequested
field is itself grounds for review.

**A prompt that permits "unchanged"** — the old prompt asked for "CORRECTED
values", which presumes something must change. It now says that returning the
previous value is a valid and often correct answer, and that a genuinely
illegible field should come back `null` rather than guessed. `null` is treated
as "cannot read", so the original stands and the issue stays open.

**A generator that produces real VKNs** — a corpus whose documents cannot pass
a rule the product enforces does not measure the product, it measures the
generator.

## Corpus break

Fixing `_tax_id` changed how many random draws it makes, which shifts the whole
random stream: regenerating with the same seed produces a different corpus, so
run 10 onward is **not comparable to runs 6, 7 and 9**.

Keeping a discarded draw to hold the stream aligned was considered and
rejected. It would have to live forever to stay useful, and run 9 is already
contaminated by 18 false REVIEWs and the correction damage they triggered —
comparability with a contaminated baseline is not worth a permanent wart.

The corpus stays at 60 documents so that the noise floor measured on it applies
at the same sample size as the historical figures.

## What run 9 does and does not say

Its headline, 97.93%, is not comparable to run 7's 98.00%: 18 of its REVIEWs
are generator false positives and the correction damage follows from them.

The comparable figure is **raw extraction EM, before correction: 98.00% →
98.34%**, which isolates the prompt change (`amount_payable` plus the
instruction not to compute a printed total). That is +0.34 pp against a noise
floor of 0.23 pp — possibly a small improvement, not established.

## Still open

The correction agent's real contribution is unmeasured. Runs 10–12 establish
the noise floor at n=60 with correction off; a fourth run with it on, against
the same corpus, will say for the first time whether the third agent earns its
place. Until that lands, correction is measured off.
