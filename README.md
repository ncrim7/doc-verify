# doc-verify

OCR-free, self-verifying document extraction for accounting offices.
Repo: https://github.com/ncrim7/doc-verify

A vision LLM reads an invoice / purchase order / receipt PDF into structured
JSON. A deterministic rule layer plus an optional second LLM pass catch
extraction errors; a targeted correction pass re-reads only the flagged
fields. A pure-Python matcher then compares a purchase order against an
invoice and reports business anomalies — price, quantity, vendor, currency,
missing or extra line — as plain-language "pay / hold / review" advice.

Origin: a graduation project, now being productised. Target users are
bookkeeping / accounting offices, so there is **no SAP / CAP / Fiori / OData**
anywhere in this repo.

## Pipeline

```
PDF ──▶ extract ──▶ rule verify ──▶ correction agent ──▶ structured JSON
        (vision     (+ arithmetic    (re-reads only
         LLM)        repair,          flagged fields)
                     deterministic)
                                                          │
                              PO ─────────┐               ▼
                              GR ─────────┼──▶ matcher ──▶ humanizer
                                          │    (PO vs      ("ÖDEMEYİ
                          invoice JSON ───┘     invoice)    ONAYLAYABİLİRSİNİZ"
                                                            / "İNCELEME GEREKLİ"
                                                            / "ÖDEME DURDURULDU")
```

- `arithmetic_repair` and `rule_based_verifier` are deterministic, dependency-free,
  and model-agnostic — the free safety net. They never call an LLM.
- `po_invoice_matcher` is pure stdlib. The Goods-Receipt (3-way) leg is not built
  yet — see `archive/3way-match-architecture.md`.

## Accuracy

**No usable number yet.** The 2026-09-02 run scored **96.10%** field-level EM on
a 60-doc synthetic corpus, but a post-run check found that three scored fields
(`currency`, item `description`, `buyer_address`) are absent or mangled in the
rendered PDFs — data-generation bugs, not model errors. 96.10% is a
contaminated *floor*; real accuracy is higher. A re-measurement is pending the
generator fix (Phase 2 P0-2).

Synthetic input only — no real / scanned / photographed documents; real-world
accuracy is unmeasured. Do not use any figure from this run in marketing, demo,
or sales material.

Detail: [`docs/measurements/2026-09-02-baseline.md`](docs/measurements/2026-09-02-baseline.md) ·
model choice: [`docs/adr/0001-extraction-model.md`](docs/adr/0001-extraction-model.md).

## Quick start

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

Copy-Item .env.example .env          # set OPENAI_API_KEY
python scripts/generate_dataset.py --count 60 --seed 42
python scripts/split_dataset.py --seed 42
python scripts/build_measure_manifest.py

python scripts/run_full_pipeline.py --split measure --strategy direct
```

`.env` keys: `OPENAI_API_KEY` (required), `LLM_MODEL` (default `gpt-5-nano`),
optional `LANGFUSE_*` for cost/latency tracing.

## Layout

| Path | |
|---|---|
| `src/extraction/` | prompts, `llm_extractor`, `arithmetic_repair`, `correction_agent`, `self_consistency` |
| `src/verification/` | `rule_based_verifier` (pure), `llm_verifier`, `hybrid_verifier` |
| `src/matching/` | `po_invoice_matcher` — PO vs invoice discrepancy detection |
| `src/reporting/` | `humanizer` — matcher output → plain-language advice |
| `src/evaluation/` | `metrics` — field EM / semantic sim / token F1, Hungarian item match |
| `src/data_generation/` | `document_generator` + `validator` — synthetic PDFs + ground truth |
| `scripts/` | dataset generation, split, `run_full_pipeline` (measurement harness) |
| `docs/adr/` · `docs/measurements/` | decisions and accuracy records |
| `archive/` | SAP reference (not code), 3-way-match design note |

## Testing

```powershell
python -m pytest -q                       # 79 tests
python -m pytest -q --cov=src --cov-report=term-missing
```

Pure modules (`arithmetic_repair`, `rule_based_verifier`, `po_invoice_matcher`,
`metrics`, `humanizer`, `document_generator`, `config`) are covered 92–100%.
The LLM-calling modules need mocked-API tests — Phase 2.

## Status

Phase 1 (cleanup + first run) complete. Phase 2 backlog is in `CLAUDE.md`,
priority-ordered. P0: (1) the silent extraction-failure safety-net gap;
(2) fix the three data-generation rendering bugs (currency never printed,
Turkish glyphs dropped in item rows, buyer-address column clipped) and re-run
the measurement — the current number does not measure the model. Then P1:
real-world validation run. Then P2/P3: data polish, `fitz` → `pymupdf`, mocked
LLM tests, the poly-repo apps, the 3-way GR leg, accounting-API integration.
