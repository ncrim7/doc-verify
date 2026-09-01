# doc-verify

OCR-free, self-verifying document extraction for accounting offices.
Repo: https://github.com/ncrim7/doc-verify

A vision LLM reads an invoice / purchase order / receipt PDF into structured
JSON; a deterministic rule layer plus a second LLM pass catch extraction
errors; a targeted correction pass fixes flagged fields; and a pure-Python
matcher compares a purchase order against an invoice and reports business
anomalies (price / quantity / vendor / currency / missing or extra line) in
plain language.

> Status: Phase 1 (cleanup). Full README — install, usage, accuracy numbers,
> architecture diagram — lands in step 1.6. See `CLAUDE.md` for current state.

## Quick start (once Python is installed)

```powershell
python -m venv venv; .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env    # set OPENAI_API_KEY
python scripts/generate_dataset.py --count 60 --seed 42
python scripts/split_dataset.py --seed 42
python scripts/run_full_pipeline.py --split test --strategy direct
```
