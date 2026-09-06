# doc-verify

Self-verifying multimodal LLM pipeline that extracts structured data from
business documents (invoice / purchase order / receipt) and cross-checks a
PO against an invoice for business anomalies.

Origin: graduation project (defense complete). This repo is the productized
core — target customer is **bookkeeping / accounting offices**, not SAP shops.

## Stack & scope

- Python. OCR-free: PDF page -> PNG (PyMuPDF, 300 DPI) -> vision LLM -> JSON.
- LLM: OpenAI only by default (`LLM_MODEL`, default `gpt-5-nano`). Groq present
  but disabled in `src/config.py`.
- **No SAP / CAP / Fiori / OData / AIP.** Fully removed. Reference-only copies
  of the old ERP clients live in `archive/` as `.txt` (not importable).
- Poly-repo: mobile app, marketing site, web demo are separate repos.

## Layout

```
src/
  config.py              provider + dataset config (reads .env)
  extraction/            prompts, llm_extractor, arithmetic_repair (pure,
                         deterministic), correction_agent, self_consistency
  verification/          rule_based_verifier (pure), llm_verifier, hybrid_verifier
  matching/              po_invoice_matcher  <- pure stdlib, zero deps; the
                         product's core value: PO vs invoice discrepancy detection
  reporting/             humanizer  <- turns matcher output into plain-language
                         "pay / hold / review" advice (refactored from the old
                         ERP result_humanizer)
  evaluation/            metrics: field-level EM / semantic-sim / token-F1,
                         Hungarian item matching, numeric + address tolerance
  data_generation/       document_generator + validator (synthetic PDFs + GT)
scripts/
  generate_dataset.py    synthetic corpus  (--count N --seed S)
  split_dataset.py       stratified train/val/test
  run_full_pipeline.py   Extract -> verify -> correct -> evaluate; the measurement harness
  build_measure_manifest.py   one-off: all generated docs in one manifest
archive/                 SAP reference (not code), 3way-match design note
docs/adr/                architecture decision records
```

## Conventions

- snake_case files/functions, `*.py`. Tests: `tests/<mirrors src path>/test_*.py`.
- Numbers as plain floats (no separators); dates ISO 8601.
- Keep `arithmetic_repair.py` and `rule_based_verifier.py` deterministic and
  dependency-free — they are the free, model-agnostic safety net. Don't add
  LLM calls to them.
- Conventional commits: `feat:` `fix:` `refactor:` `chore:` `test:` `docs:`.

## Process (manual orchestration loop)

Run each change as: Research -> Plan -> **GATE 1 (user approves plan)** -> TDD
-> Review -> **GATE 2 (user approves diff + commit msg)** -> Commit.

- `tdd-workflow` for all new code (test first, >=80% coverage on changed code).
- `architecture-decision-records` for repo/model/API-shape decisions.
- Right-size ceremony: a 1-line fix does not need a plan gate.

## Skill notes

- 40 self-contained skills are in `~/.claude/skills/`.
- `orch-*` family and `delivery-gate` are intentionally NOT installed (they need
  the ECC agent/command/rules stack that isn't set up). The loop above is run by
  hand instead.

## Current state — Phase 1 complete

- [x] 1.0 repo skeleton, SAP stack removed
- [x] 1.1 git init  (git 2.55 MinGit + Python 3.12.10 installed via winget)
- [x] 1.2 src/reporting/humanizer.py (10 tests, 95% cov); ocr.space "helloworld"
      leak removed from llm_extractor; llm_verifier render DPI 200->300;
      archive/3way-match-architecture.md
- [x] 1.3 config.py collapsed to one env-driven openai provider (LLM_MODEL,
      default gpt-5-nano); dead config removed (priority, per_type,
      LANGFUSE_CONFIG, TELEGRAM_CONFIG); ADR-0001 records the model choice
      (status: proposed -> accepted after 1.5); 7 config guard tests
- [x] 1.4 document_generator: real Turkish B2B/retail item catalogs (was Faker
      bs()/lorem); invariant=1 for byte-deterministic PDFs. 60-doc corpus
      regenerated (seed=42, tax_id fix now in the PDFs), split 36/12/12,
      scripts/build_measure_manifest.py -> data/processed/measure_manifest.json.
      data/ is gitignored — reproducible from seed + generator code.
- [x] 1.5 measured 2026-09-02 (gpt-5-nano, direct, 60-doc corpus, single pass):
      96.10% field-level EM — but INVALIDATED: a post-run page-text check found
      3 data-generation rendering bugs (currency never printed, Turkish glyphs
      dropped in item rows, buyer-address column clipped). The number is a
      contaminated floor, not a model measurement; re-run pending P0-2.
      ADR-0001 stays accepted (the floor already clears the switch-model bar).
      docs/measurements/2026-09-02-baseline.md
- [x] 1.6 full README; test net for the pure "safety-net" modules —
      arithmetic_repair 98%, rule_based_verifier 96%, po_invoice_matcher 92%,
      metrics 93%, humanizer 95%, document_generator 96%, config 100%
      (79 tests total). LLM-calling modules still need mocked-API tests (Phase 2).

## Phase 2 backlog — priority-ordered

**P0-1, P0-2, P0-4, P0-5, P0-6, P1-4, P1-6 and P1-7 are closed.**

Current figure: **98.22%** field-level EM over 60 synthetic documents, one run,
against a three-run baseline of **97.67% ± 0.178** measured on the same corpus
(`docs/measurements/2026-09-06-noise-floor-and-clean-corpus.md`, then
`2026-09-06-turkish-i-family.md`). Corpus regenerated 2026-09-06; every figure
before that measured a different corpus, a metric with a 1% relative tolerance
on money, and a pipeline whose third agent never ran, so **do not compare
across that line.**

**Real-world is the number that matters and it is 70.55% at n=15**, ranging
90.5% on clean digital PDFs to 15.1% on photographed dot-matrix bills. The
synthetic figure measures the pipeline, not the product.

Three qualifiers travel with the number:

- **quote it as a range, never a point.** σ = 0.178 pp, and only 33 of 60
  documents score identically across identical runs.
- **a change under 0.36 pp (2σ) is not evidence.** Single-run deltas below that
  have been wrong before — see the note above for two of my own.
- **clean synthetic input only** until P1-5. Real, scanned or photographed
  documents remain effectively unmeasured: five figures on the same four
  documents span 57.81% to 74.28%.

## Sprint A — the product slice (2026-09-06)

`src/decision/` and `src/analyze.py` are the first modules that answer **"should
I pay this?"** rather than "is the JSON right?". Everything before them was
measurement.

    belge -> çıkarım -> doğrulama -> bulgular -> KARAR -> Türkçe metin
                                        ^
                                        + PO eşleştirme, PO varsa

Three things this design settled, each learned by running it on real documents:

- **PO matching is a source, not the spine.** The 15 real documents are 11
  invoices and 4 receipts — **zero purchase orders**. Three-way matching came
  from an enterprise SAP context; whether a bookkeeping office holds POs at all
  is unverified. A slice that only worked with a PO would produce nothing on the
  documents we have.

- **HOLD is about whose money.** A cross-source disagreement (invoice vs PO)
  is the supplier asking for more than was agreed → HOLD. A document-internal
  mismatch is a discrepancy we cannot attribute — on the telecom bill that
  exposed it, the misreading was ours → REVIEW. Calling the second "financial
  impact" tells a bookkeeper their supplier overcharged them, which is a lie
  dressed as a number.

- **The same money is counted once.** One 3,40 TL price gap over 100 units
  appears as a price mismatch (340), a line-total gap (340) and inside the
  document total (1.408, with VAT). Summing gives 2.088 TL — six times the real
  figure, and someone would act on it. `_net_impact` takes the largest figure
  per line and sums across lines.

`Decision.confidence` is **coverage, not probability** — the share of extracted
fields any check can speak to. Inventing a 0.96 would undo the week spent
removing invented numbers, and 58% of measured field errors sit where no check
reaches. `unverifiable_fields` names the silence, and the output says so.

### P0 — must close before the product ships

- [x] **P0-4  `arithmetic_repair` fabricated totals on real documents.**
  On a telecom bill the payable amount is printed twice, once highlighted; the
  module overwrote it with `subtotal + tax`, wrong by ~2x, `verdict: OK`.
  Fixed by the provenance rule: a printed value is evidence, a computed one is
  inference, and inference fills gaps but never overwrites evidence. Fixing
  `arithmetic_repair` alone was not enough — the rule verifier's
  auto-corrections re-introduced the identical number one layer later.
  Item-level repair kept: quantity and unit_price are two independent values
  corroborating one derived value. Measured inert on synthetic input.

- [x] **P0-5  the correction agent fabricated, and rewrote unflagged fields.**
  `docs/measurements/2026-09-03-correction-agent.md`. It had fired on **0 of
  60** documents in runs 6 and 7 — the third agent had never actually run. When
  a new rule started flagging things it fired on 39 of 60, contributed
  **−0.41 pp**, damaged 8 documents against 2 improved, and destroyed four
  perfect extractions. Handed "this tax id fails its check digit" it rewrote
  the check digit until the checksum passed, re-verification accepted it, and
  the verdict became OK. It also transliterated Turkish characters on fields
  nobody had flagged. Fixed with four constraints: correction triggers only on
  critical **and correctable** issues; `UNCORRECTABLE_RULES` keeps check-digit
  failures away from the agent entirely; the merge is an allowlist; and any
  field changed but not flagged becomes `unrequested_correction`, a REVIEW
  reason in its own right — so a fabrication can no longer become OK.

- [x] **P0-6  the generator produced tax ids that could not be valid.**
  Ten random digits satisfy a VKN check digit one time in ten, so 30 ids across
  18 documents were flagged the moment P1-6 landed — all false positives, and
  the trigger for the whole P0-5 cascade. `_tax_id` now computes the check
  digit. **The corpus was regenerated, breaking comparability with every run
  before 10.**

- **P0-1  D1: silent extraction failure → generic "broken output = REVIEW".**
  - [x] **(a2) the rule, codified.** `src/pipeline.py` — `DocumentPipeline`
    returns a `PipelineResult` with an explicit `Verdict.OK | REVIEW`. It never
    returns OK unless extraction produced a non-empty dict *and* verification
    found no critical issue; exceptions, empty/non-dict results and missing
    required fields all become REVIEW with a reason. Cause-agnostic by design.
    18 tests, 96% coverage, components injectable (no API in tests).
    **This subsumes the old "currency P0-2": when a field is genuinely not on
    the page the system must emit `null` + flag, never guess.**
  - [x] **(a1) measurement honesty.** `run_full_pipeline.py` now runs the
    product path (`DocumentPipeline`) and aggregates with
    `metrics.aggregate_run`, which keeps a failed document in the denominator
    at 0. Headline moved **98.44% → 96.79%** (all 60 docs); 98.44% is retained
    as the "OK documents only" view. Per-type honesty: invoice 97.5% → 92.59%,
    because the one silent failure was an invoice — the old aggregation hid
    that entirely. `PipelineResult.raw` added so the raw-vs-final delta is
    still reportable. `apply_corrections` de-duplicated into `src/pipeline.py`.
  - [x] **(b) trigger removed.** `reasoning_effort="minimal"` for gpt-5 models
    (measured: 0 reasoning tokens vs 128 default), `max_completion_tokens`
    4096 → 8192, and one bounded retry when `_parse_json_response` returns
    None. Result: **0 silent failures, 0 retries needed** — minimal reasoning
    prevents the overflow outright rather than the retry hiding it. Runtime
    ~22 min → ~5.6 min. 7 mocked-API tests. **P0-1 CLOSED.**
    Trade-off recorded in the measurement report: per-field transcription gets
    noisier (46 misses vs 30, 28/60 perfect docs vs 36/59). Kept because the
    failure class it removes is a P0 and the headline EM still rose.
- **P0-2  fix the data-generation rendering bugs, then re-measure.**
  - [x] generator fixed: currency printed on the grand-total row
    (`TRY 600034.31`); item-table body rows given `FONTNAME` (Turkish glyphs);
    name/address cells wrapped in `Paragraph` (no clip) with XML-escape
    (`Shell&Turcas` no longer eaten by the parser). Render-integrity test
    (`test_rendered_pdf_contains_every_scored_field`) added — RED before,
    GREEN after; full-corpus check now **0/596** fields missing (was ~264).
    Corpus regenerated (seed=42).
  - [x] re-measured on the clean corpus (only the corpus changed, model config
    byte-identical): **98.44% field-level EM** (was 96.10%), 99.85% semantic,
    99.08% F1, 36/59 documents perfect (was 20/58). All 30 residual misses
    verified genuine — 0 contaminated.
    `docs/measurements/2026-09-02-post-render-fix.md`. **P0-2 CLOSED.**
- **P0-3  document the measurement scope.** DONE — the 2026-09-02 report carries
  the INVALIDATED banner, the D2 root cause, the synthetic-only scope, and the
  marketing embargo.

### P1 — in Phase 2, after P0

- [x] **P1-4  Turkish character handling.** Both hypotheses tested, one
  variable each (`docs/measurements/2026-09-02-reasoning-low.md`):
  (i) the stale "auto-correct Turkish typos" prompt instruction removed —
      +0.31 EM, `description` misses 38 → 28;
  (ii) `reasoning_effort` `minimal` → `low` — +0.26 EM, 35/60 perfect, and it
      eliminated the straight-to-curly apostrophe class outright (4 → 0).
  97.66% → **98.23%**. 34 misses remain, all genuine Turkish character
  handling. Two follow-ups fell out of it, below.
- [x] **P1-4b  invented Turkish characters.** Closed
  (`docs/measurements/2026-09-06-turkish-i-family.md`). The symmetric
  instruction — a worked example in *each* direction — took the invention class
  from **3 to 0**. The filing was half right: invention was 3 of 40 text misses
  while *dropping* was 22, so it had named the smaller half.

- **P1-4c  the dotless i — and prompting is finished as a tool for it.**
  Measured per letter against corpus frequency: **18 of 22 dropped diacritics
  are `ı` or `İ`**, the two letters with no counterpart in any other
  Latin-script language. Every letter shared with German or French is perfect —
  `ü` 81/81, `ç` 16/16, `Ç` 24/24, `Ş` 35/35. That rules out resolution: a
  breve on `ğ` is a smaller mark than a missing dot on `ı`, and `ğ` fares
  eleven times better. It is the model's Latin-script prior, not its eyes.

  Two targeted prompt attempts have now failed to move it — 22 → 21, with `ı`
  alone 19 of the remainder, spread across 11 distinct words rather than a few
  memorised wrongly. **It is the single largest error class left in the
  synthetic corpus.** The remaining levers are a different model, a different
  render, or a second source — a supplier and product master that can snap
  `Rafli` back to `Raflı`. That is the same "compare against something" answer
  P1-5 reached for invoice numbers, and it is now wanted twice.
- [x] **P1-6  tax-id checksum check.** `src/verification/tax_id.py`. A tax id
  carries its own check digit, which makes it the only field in the schema that
  can be verified against itself — no second source, no model call. On the real
  pilot it separated all four genuine ids from all three model corruptions, and
  made its first live catch the next run: a buyer tax id one digit off from the
  printed one, `REVIEW` as the only reason. No auto-correction is ever offered
  (there is no way to infer which digit was wrong) and lengths Turkey does not
  use are not judged, so a foreign 9-digit registration raises `info`, not
  `critical`. It also exposed P0-5 and P0-6.
- [x] **P1-7  the money metric had a 1% *relative* tolerance.** On a 100,000 TRY
  invoice that is ±1,000 TRY of free slack, in a product that exists to catch
  financial discrepancies. Every figure published before 2026-09-03 carried it
  on `subtotal`, `tax_amount`, `total_amount`, `unit_price` and line totals.
  Money is now compared exactly, to the kuruş; rates get a small absolute
  tolerance because a model that derives 94.59/472.97 instead of reading "20%"
  is not wrong. Measured on unchanged real-pilot output: 74.09% → 71.82%.

- [x] **P1-5  real-world validation.** Done at n=15, 202 scored fields
  (`docs/measurements/2026-09-06-real-world-n15.md`). The headline, 70.48%, is
  close to useless; **input condition explains almost everything**:

  | condition | EM | n |
  |---|---|---|
  | clean digital PDF | **90.5%** | 8 |
  | screen capture | 88.9% | 1 |
  | photo, crisp thermal print | 85.7% | 1 |
  | PDF, broken/missing text layer | 56.4% | 2 |
  | **photo, dot-matrix on faded thermal** | **15.1%** | 3 |

  Quote it as two sentences, never one number: 90% on clean digital documents,
  and photographed utility bills need a different answer — a portal feed or a
  re-capture prompt, not a better model. Receipts-vs-invoices (94% vs 62%) and
  English-vs-Turkish (93% vs 62%) look dramatic and are **confounded** by
  condition; do not quote them.

  It also measured **the safety net's ceiling: 42% of field errors are in
  principle catchable** (arithmetic or check digits), 58% are not (identifiers,
  names, addresses, dates). Five errors passed as OK, including `1OSB6CRZ` read
  as `10SB6CRZ` on two documents of the same format. Catching those needs a
  second source — e-arşiv XML, a supplier master, or a human — not a better
  rule.

  Ground-truth rules, now proven in anger: GT never comes from the system under
  test; a field a careful human cannot read is **excluded, never inferred**
  (real_012 gives up four fields to this); `available_fields` per document;
  documents and their GT are never committed; a convention not stated in the
  prompt may not be graded.

- **P1-5b  the photo class needs a product answer, not a model answer.** Three
  ASAT water bills, same issuer and layout, photographed seconds apart, score
  20/10/15%. A crumpled stained till receipt scores 86%. The variable is
  dot-matrix overprint on faded thermal paper, and no prompt work reaches it.
- **P1-8  does the correction agent earn its place?** Answered as far as
  synthetic data can: **it cannot be measured here.** Re-running the verifier
  offline over the pinned extractions of runs 10–12 shows it would fire on
  **0, 1 and 1 of 60** documents — its trigger conditions essentially do not
  occur on clean synthetic input, which is also why runs 6 and 7 recorded it
  firing zero times. Evaluating it needs documents that genuinely have
  problems, i.e. real ones, which makes it dependent on P1-5. Measurement runs
  use `--no-correction` meanwhile.
- **P1-8b  a flagged tax id should reach a human even after correction.**
  `tax_id_checksum_invalid` is currently `UNCORRECTABLE`, which is a restraint
  imposed by observed behaviour rather than by logic: a transposed digit *is*
  recoverable by looking at the page again, and refusing to try gives up the
  recovery that made P1-6 worth building. The better shape is to let correction
  **propose** a value while the issue stays open — the human gets a better
  candidate and still reviews it. Not done now because it would have been a
  third variable in an already large change set.
- **P1-9  schema gaps the real corpus exposed.** `total_amount` vs
  `amount_payable` is done. These are not, and they are ordinary shapes rather
  than edge cases — the schema was drawn from clean commercial invoices that do
  not look like Turkish utility and platform bills:

  | gap | documents |
  |---|---|
  | two tax bases on one document | real_004, real_005, real_015 |
  | charge lines / tiers with no quantity or unit price | real_004, real_005, real_012, real_013, real_014 |
  | no discount field — `qty × price` cannot reach the line total | real_008 |
  | `items[].total` undefined as tax-inclusive or exclusive | real_006, real_015 |
  | a payment receipt's link to the invoice it settles | real_009, real_010 |
  | a till receipt's fiscal ids (VD no, Z No, EKÜ No, MF serial) | real_013 |
  | a tax *kind* alongside the rate — BSMV is not KDV | real_011 |

  `items[].total` was the cheapest and it has been **tried and failed**. The
  prompt now states the UBL-TR rule — a line total is quantity × unit price less
  discount, excluding tax — and the field is graded again on all nine documents
  that have one. The model does not follow it: on `real_008` it returns
  `69.4167` (quantity × unit price) where the page prints `20,83`, ignoring a
  70% discount printed in its own column. **It derives instead of reading** —
  the same fabrication just removed from `repair_arithmetic` and the correction
  agent, still present in the model. The instruction stays because the GT grades
  the field, and a correct instruction the model ignores is a different problem
  from a missing one.

  **Then `items[].discount` was added, and it worked immediately.** Same GT,
  same metric: `real_008` went 70/75% → **95%**, with the model returning all
  four line numbers exactly and the document reconciling with zero issues.
  Overall 69.33/68.96% → 70.55%, about 3× the n=15 spread but one run, so do
  not quote it alone. The field is the amount (not the rate), matching UBL-TR's
  line `AllowanceCharge`; the verifier now checks
  `qty × price − discount = total`.

  **Keep the lesson, it generalises:** a prompt sentence describing the
  convention changed nothing on its targets; a schema field fixed them at once.
  The model can read four numbers off a line and cannot reliably follow an
  instruction to look elsewhere and derive one. When extraction fails on a
  shape the schema cannot express, reach for a field before more prompt.

  Two bugs fell out of it. The identity check caught an inconsistency in
  `real_015`'s own ground truth before it ever ran against a model (that issuer
  prints line figures tax-inclusive). And `discount` was added to the schema
  but not to `MONEY_FIELDS`, so `0` was compared as text against `0.0` — worth
  0.68 pp. A structural test now reads field names out of the schemas and
  asserts every `number` is known to the metric.

- **P0-7  item-level repair fabricated on a discounted line.** CLOSED. The one
  overwrite P0-4 deliberately kept, on the grounds that quantity and unit_price
  corroborate the derived total. They only do when nothing sits between them —
  a real e-arşiv invoice with a 70% discount turned a printed 20,83 into 69,42,
  and the fabricated line total then made the page's *correct* subtotal look
  wrong. Now fills only when absent, in both `arithmetic_repair` and the
  verifier's auto-corrections. The digit-drop recovery is gone with it: its
  measured benefit was +0.00 pp across two 60-document runs, so an unproven
  gain was buying a proven fabrication.

  Consequence worth knowing: **no rule produces an auto-correction any more.**
  The provenance principle removed them one by one — subtotal, total_amount,
  item totals. `apply_corrections` survives for *format normalisation*, which
  is safe, and a test pins the emptiness so the next derived value has to
  argue for itself.

### P2 — data / infra polish, as time allows

- **P2-6** realistic Turkish address templates; category-keyed line-item price
  ranges (masking tape != 4000 TL).
- **P2-7** `import fitz` -> `import pymupdf` (deprecation warning on 1.28.2).
- **P2-8** mocked-API tests for llm_extractor / llm_verifier / correction_agent
  / self_consistency / hybrid_verifier (0% covered); validator.py batch/report
  coverage (58%).
- **P2-9** `repair_arithmetic` runs inside `LLMExtractor.extract`, not in
  `DocumentPipeline`. Swap the extractor and the repair silently goes with it.
  Decide whether the pipeline should own it (idempotent, so safe to run twice).

### P3 — after the poly-repo split

- **P3-9** mobile app, web demo, marketing site (separate repos).
- **P3-10** 3-way match GR leg (archive/3way-match-architecture.md).
- **P3-11** accounting-API integration — **QuickBooks / Xero primary**
  (export-focused ICP); Paraşüt / Logo secondary / optional. Do not let this
  order drift toward Turkey-first without a decision.
- **P3-12** KVKK / DPA.

Toolchain (this machine): python `C:\Users\cirim\AppData\Local\Programs\Python\Python312\python.exe`,
git is MinGit (no credential helper — first `git push` needs a PAT or SSH).

Known debt carried from the graduation project:
- Dataset was never re-rendered after the `document_generator.py` tax_id fix
  (2026-06-27). All prior numbers reflect the pre-fix corpus.
