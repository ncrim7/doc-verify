# ADR-0001: Default extraction model is gpt-5-nano

**Date**: 2026-09-01
**Status**: proposed  <!-- -> accepted once step 1.5 measures it on the clean corpus -->
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
a single env var, so switching is a one-line change with no code edit. Step 1.5
runs one authoritative measurement on this model against the regenerated corpus;
that result promotes this ADR to **accepted** (or triggers ADR-0002 if nano-5
regresses materially versus nano-4.1).

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
