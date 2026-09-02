# ADR-0001: Default extraction model is gpt-5-nano

**Date**: 2026-09-01
**Status**: accepted  <!-- measured 2026-09-02, see docs/measurements/2026-09-02-baseline.md -->
**Deciders**: project owner

## Context

The graduation-project code ran on `gpt-4.1-nano`. Every systematic error-analysis
and fix (the "cozum #1-2" prompt/schema work, +5.84 pts field-level EM) was tuned
on that model. The thesis, however, recommends `gpt-5-nano`: in the cross-model
campaign it matched the older nano generation on accuracy at roughly 1/59th the
per-document cost (Langfuse: ~$0.0004 vs ~$0.0236 per document). `gpt-5-nano`'s
accuracy on the *fixed* synthetic corpus has never actually been measured — the
thesis numbers predate the `tax_id` generator fix and use `gpt-4.1-nano` or
`gpt-5-mini`.

For a bookkeeping-office product, per-document cost is a first-order concern
(100k docs/month is ~$40 on gpt-5-nano vs ~$2,400 on gpt-4.1-nano).

## Decision

Default `LLM_MODEL=gpt-5-nano`, read from `.env` by `src/config.py`. The model is
a single env var, so switching is a one-line change with no code edit.

**Measured 2026-09-02** on a 100%-synthetic 60-doc corpus whose every scored
field — text and numeric — is verified present on the rendered page
(0/1624 missing): **98.23% field-level EM over all 60 documents**, 99.86%
semantic similarity, 99.11% token F1, zero silent failures, ~8.3 min per 60
documents. See `docs/measurements/2026-09-02-reasoning-low.md`.

`reasoning_effort` is set to `"low"`, measured against the alternatives:
the model default overflows the completion budget and produces the
silent-failure class; `"minimal"` removes that but transcribes more noisily
(97.97%, 31/60 perfect); `"low"` is the best measured (98.23%, 35/60) and is
still 2.6x faster than the default.

(The first run reported 96.10%, but three generator rendering bugs meant the
model was scored on fields absent from the page. That report is retained and
marked invalidated.)

gpt-5-nano is **accepted**: 98.23% clears any "switch model" bar comfortably.
The remaining 34 field errors are almost all Turkish character handling and are
a prompt/decoding problem rather than a model-choice one. Fallback to
gpt-5-mini remains one `.env` line (ADR-0002). Real-world accuracy is still
unmeasured.

## Alternatives Considered

### gpt-4.1-nano (keep the graduation-project default)
- **Pros**: known behavior; all prompt/schema tuning was validated on it.
- **Cons**: ~59x the token cost of gpt-5-nano for comparable accuracy; older
  generation, will age out.
- **Why not**: cost. The tuning gains are prompt/schema level and expected to
  carry to nano-5; 1.5 verifies that rather than assuming it.

### gpt-5-mini
- **Pros**: the thesis's headline 92.14% field-level EM figure is on this model.
- **Cons**: ~5x the cost of gpt-5-nano.
- **Why not**: reserved as the fallback if gpt-5-nano's measured accuracy is
  unacceptable. Not the default on cost grounds.

### Groq / llama-4-scout
- **Pros**: open-weights, vendor-independent, very cheap.
- **Cons**: a second provider handling customer documents; lower measured
  accuracy in the thesis campaign.
- **Why not**: privacy surface. Kept as a commented, opt-in block in config.py.

## Consequences

### Positive
- Per-document cost drops ~59x versus the previous default.
- Model choice is one env var; no code path branches on it.

### Negative
- `temperature` must be `None` for gpt-5* (the API rejects an explicit value).
  Handled in `config.py` and already tolerated by every consumer
  (`.get("temperature")` + `if temp is not None`).

### Risks
- The cozum #1-2 gains were tuned on gpt-4.1-nano and might not fully transfer.
  **Mitigation**: step 1.5 measures it directly on the fixed corpus before any
  accuracy number is published; if it regresses, fall back to gpt-5-mini
  (ADR-0002) — a one-line `.env` change.
