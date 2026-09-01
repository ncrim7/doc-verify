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

Rule: **no accuracy number goes into marketing, demo, or sales material until
P0-1 and P0-2 are closed.** The 2026-09-02 96.10% is a contaminated floor, not
a model measurement (P0-2). Until P0-1 is closed the number also does not
support the core claim ("does not silently return a wrong answer").

### P0 — must close before the product ships

- **P0-1  D1: silent extraction failure → generic "broken output = REVIEW".**
  `run_full_pipeline.py:140` does `if not extracted: continue` — verification
  never runs, nothing is flagged. Fix both layers:
  (a) any empty / unparseable / structurally-invalid extraction routes to a
      REVIEW verdict / human queue, generically — not tied to any one cause,
      and never a silent skip. Whatever production orchestrator gets built must
      not repeat `if not extracted: continue`. **This subsumes the old "currency
      P0-2": when a field is genuinely not on the page, the system must emit
      `null` + flag, never guess.**
  (b) immediate trigger: gpt-5-nano reasoning tokens overflow
      `max_completion_tokens=4096` -> `reasoning_effort="minimal"` (also cuts
      latency) + retry when `_parse_json_response` returns None.
- **P0-2  fix the data-generation rendering bugs, then re-measure.**
  - [x] generator fixed: currency printed on the grand-total row
    (`TRY 600034.31`); item-table body rows given `FONTNAME` (Turkish glyphs);
    name/address cells wrapped in `Paragraph` (no clip) with XML-escape
    (`Shell&Turcas` no longer eaten by the parser). Render-integrity test
    (`test_rendered_pdf_contains_every_scored_field`) added — RED before,
    GREEN after; full-corpus check now **0/596** fields missing (was ~264).
    Corpus regenerated (seed=42).
  - [ ] re-run `run_full_pipeline.py --split measure` on the clean corpus,
    write the result as a new `docs/measurements/*.md`, and update ADR-0001 /
    README with the real number.
- **P0-3  document the measurement scope.** DONE — the 2026-09-02 report carries
  the INVALIDATED banner, the D2 root cause, the synthetic-only scope, and the
  marketing embargo.

### P1 — in Phase 2, after P0

- **P1-4  residual model errors after P0-2.** Once the corpus renders correctly,
  measure what is actually the model: tax-id character slips (`Korutürk`→
  `Korütürk`), any real description normalisation. Small so far.
- **P1-5  real-world validation run.** A few dozen genuine (non-synthetic)
  documents, small extra measurement, update the scope note. Answers "is there
  a synthetic-vs-real gap".

### P2 — data / infra polish, as time allows

- **P2-6** realistic Turkish address templates; category-keyed line-item price
  ranges (masking tape != 4000 TL).
- **P2-7** `import fitz` -> `import pymupdf` (deprecation warning on 1.28.2).
- **P2-8** mocked-API tests for llm_extractor / llm_verifier / correction_agent
  / self_consistency / hybrid_verifier (0% covered); validator.py batch/report
  coverage (58%).

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
