# Measurement — 2026-09-02 (5) · real-document pilot (n=4)

First run against **real documents** rather than the synthetic corpus. Four
documents, chosen to span the range of conditions a bookkeeping office actually
receives. This is P1-5, at pilot scale.

**The documents themselves are never committed.** They carry tax ids, home
addresses, amounts and counterparty names. They live in a git-ignored directory;
only the aggregate result and anonymised error shapes appear here.

## Why n=4

A pilot, not a measurement. Ground truth for real documents has to be produced
and verified by hand; doing 15 documents' worth of that before the process was
proven would have wasted the effort if the process was wrong. It was worth
running: the four documents surfaced two defects that the 60-document synthetic
corpus never could.

## Ground-truth method

The rule that makes this test mean anything: **ground truth must not come from
the system under test.**

| document class | GT source | independence |
|---|---|---|
| digital PDF with a good text layer | the PDF's embedded text | strong — the pipeline never reads the text layer, it renders to an image and uses vision |
| screenshot / photo / PDF with a broken text layer | read visually, then verified by the document owner | weaker — mitigated by human verification |

Two further rules:

- **A field a careful human cannot read from the image is not scored.** On the
  photographed utility bill the single most important number — the amount
  payable — is not legible. Writing a guess into ground truth and grading the
  model against it would repeat, in a new form, the contamination that
  invalidated the first synthetic measurement.
- `available_fields` per document. A telecom bill has no `unit_price`; a
  residential subscriber has no `buyer_tax_id`. Only fields genuinely present
  are scored. Verified before spending any API call: scoring each GT against
  itself returns 1.0 and touches exactly the declared fields, no more.

## Result

| document | condition | EM | verdict |
|---|---|---|---|
| A | screenshot of an e-Arşiv invoice, sharp | 82.3% | OK |
| B | digital PDF, English, USD, clean | 88.9% | OK |
| C | phone photo, creased and faded utility bill | 20.0% | **REVIEW** |
| D | digital PDF, dense Turkish telecom bill, text layer is garbage | 40.0% | OK |
| **all four** | | **57.81%** | |

Synthetic corpus, same pipeline, same day: **98.23%**.

The safety net worked where it mattered: document C was routed to REVIEW
because required fields came back missing. P0-1 doing its job on a real
document, unprompted.

## The 15 misses, classified

| | count |
|---|---|
| genuine model error | 13 |
| ground-truth convention (arguably the model was right) | 1 |
| **defect in our own code** | **1** |

### The one in our own code — `arithmetic_repair`. This is the finding.

On the telecom bill, the amount payable is printed twice, once inside a
highlighted box. The pipeline returned neither. `arithmetic_repair` overwrote
the field with a computed value:

```
run 1   total_amount := subtotal + tax          -> wrong by ~2x
run 2   total_amount := sum(line items)         -> wrong by ~3x
page    the correct figure, printed, highlighted
verdict OK  — in both runs, with no issue raised
```

The module assumes `total = subtotal + tax` and `subtotal = Σ items`. That
holds for a clean commercial invoice. It does not hold for a utility or telecom
bill, where the total also carries discounts, a previous-month balance, a late
fee and two different tax bases.

So the module **destroys a correctly printed figure and substitutes a fabricated
one** — silently. Worse, it does this by making the document internally
consistent, which means the rule verifier's arithmetic checks then pass *by
construction*. Our deterministic safety net blinds our other safety net.

This is the same disease as D1 — a wrong answer returned with confidence — but
originating in our own code rather than the model. On synthetic data the module
only ever helped, which is precisely why it took real documents to expose it.
Raised to P0.

### The 13 genuine model errors

| class | shape | note |
|---|---|---|
| digit corruption | a 16-digit document number came back with an extra digit; a 10-digit tax id came back with two extra digits | **same class as the synthetic tax-id doubling — now confirmed across two independent corpora.** A length and checksum check in `rule_based_verifier` catches it deterministically, no LLM call (P1-6). Value revised sharply upward. |
| empty field | tax id, vendor address, due date returned as `""` | dense small print in a page corner, or a faded photo. The model does not find the field at all. |
| wrong field selected | the service number was returned as the invoice number | Turkish bills carry several labelled identifiers side by side. A real risk class. |
| letter/digit confusion | `O` read as `0` inside a document number | hard for a human too |
| Turkish characters | dotted `İ` flattened to `I`; a `C` turned into `Ç` in a surname | the known class, more frequent here than on synthetic |
| honorific absorbed | the Turkish form of address printed above a name was included in the name | |

## Also found: raster inputs were being upscaled

Not an accuracy finding but a production one, surfaced by the same documents and
fixed in the same session. `_pdf_to_image` rendered every input through a
300-DPI matrix. For a vector PDF that is right. For a photo or a screenshot —
already pixels — it upscaled roughly 4x, adding no information and multiplying
the API payload: a 291 KB photo became a 6.9 MB base64 blob. On a 12-megapixel
phone photo it produced a 234-megapixel image that fails outright.

Raster inputs now pass through at native resolution, capped on the long side.
Payload for the photo halved; for the screenshot it fell to a tenth. The PDF
path is byte-identical, so the synthetic measurement remains comparable.

Customers upload photos. Every synthetic document was a PDF, so this path had
never once been exercised.

## Scale caveat

**Four documents.** The error bars on 57.81% are enormous and it must not be
quoted as a real-world accuracy figure. What the pilot establishes is not a
number but a set of defect classes, and those are concrete and reproducible.

Widening to the remaining eleven documents is queued next.
