"""
Field-level and document-level evaluation metrics for document extraction.
Supports exact match, semantic similarity, token F1, and numeric accuracy.
Includes Hungarian algorithm for order-invariant item matching.
"""
import re
import logging
from difflib import SequenceMatcher
from typing import Any
from itertools import product as iter_product

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    if not isinstance(text, str):
        text = str(text)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def exact_match(predicted: str, ground_truth: str) -> float:
    """Binary exact match after normalization. Returns 1.0 or 0.0."""
    return 1.0 if normalize_text(predicted) == normalize_text(ground_truth) else 0.0


def semantic_similarity(predicted: str, ground_truth: str) -> float:
    """SequenceMatcher ratio (0.0–1.0) on normalized strings."""
    p = normalize_text(predicted)
    g = normalize_text(ground_truth)
    if not g:
        return 1.0 if not p else 0.0
    return SequenceMatcher(None, p, g).ratio()


def token_f1(predicted: str, ground_truth: str) -> dict[str, float]:
    """
    Token-level precision, recall, F1 on word tokens.
    Returns {'precision': ..., 'recall': ..., 'f1': ...}.
    """
    pred_tokens = set(normalize_text(predicted).split())
    gt_tokens = set(normalize_text(ground_truth).split())

    if not gt_tokens:
        return {"precision": 1.0 if not pred_tokens else 0.0,
                "recall": 1.0, "f1": 1.0 if not pred_tokens else 0.0}
    if not pred_tokens:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}

    common = pred_tokens & gt_tokens
    precision = len(common) / len(pred_tokens)
    recall = len(common) / len(gt_tokens)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return {"precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4)}


def numeric_accuracy(predicted: Any, ground_truth: Any, tolerance: float = 0.01) -> float:
    """
    Compare two numeric values with relative tolerance.
    Returns 1.0 if within tolerance, else 0.0.
    """
    try:
        p = float(str(predicted).replace(",", "").strip())
        g = float(str(ground_truth).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0
    if g == 0:
        return 1.0 if p == 0 else 0.0
    return 1.0 if abs(p - g) / abs(g) <= tolerance else 0.0


# ---------------------------------------------------------------------------
# Aggregate evaluation
# ---------------------------------------------------------------------------

# Fields that should use numeric comparison
NUMERIC_FIELDS = {
    "quantity", "unit_price", "total", "subtotal", "tax_amount",
    "total_amount", "change_amount", "amount", "tax_rate",
}

# Fields in nested items[] lists
ITEM_FIELDS = {"description", "quantity", "unit_price", "total", "sku", "amount"}

# Address fields: use fuzzy matching (semantic similarity > 0.80 = exact match)
ADDRESS_FIELDS = {
    "vendor_address", "buyer_address", "supplier_address", "store_address",
}


def evaluate_document(predicted: dict, ground_truth: dict, doc_type: str) -> dict:
    """
    Compare predicted extraction against ground truth for a single document.
    Returns per-field scores + aggregate metrics.
    """
    results: dict[str, dict] = {}
    flat_pred, flat_gt = _flatten_with_item_matching(predicted, ground_truth)

    # Skip metadata fields (not extraction-relevant)
    skip = {"doc_type", "language", "confidence_scores", "confidence",
            "source", "receipt_number", "available_fields", "scenario"}

    # If ground truth declares available_fields (e.g. SROIE), only evaluate those
    available_fields = ground_truth.get("available_fields")

    all_em = []
    all_sim = []
    all_f1 = []

    for field, gt_val in flat_gt.items():
        if field in skip:
            continue
        if available_fields is not None and field not in available_fields:
            continue
        pred_val = flat_pred.get(field, "")

        gt_str = str(gt_val) if gt_val is not None else ""
        pred_str = str(pred_val) if pred_val is not None else ""

        # Choose metric based on field type
        base_field = field.split(".")[-1] if "." in field else field
        is_numeric = base_field in NUMERIC_FIELDS
        is_address = base_field in ADDRESS_FIELDS

        em = exact_match(pred_str, gt_str)
        sim = semantic_similarity(pred_str, gt_str)
        tf1 = token_f1(pred_str, gt_str)
        num = numeric_accuracy(pred_val, gt_val) if is_numeric else None

        # Numeric fields: use tolerance-based EM (±1% relative)
        if is_numeric and em == 0.0 and num is not None:
            em = num  # numeric_accuracy returns 1.0 if within tolerance

        # Address fuzzy matching: if similarity > 0.80, treat as exact match
        if is_address and em == 0.0 and sim >= 0.80:
            em = 1.0

        results[field] = {
            "exact_match": em,
            "semantic_similarity": round(sim, 4),
            "token_f1": tf1["f1"],
            "numeric_accuracy": num,
            "predicted": pred_str[:100],
            "ground_truth": gt_str[:100],
        }

        all_em.append(em)
        all_sim.append(sim)
        all_f1.append(tf1["f1"])

    n = max(len(all_em), 1)
    aggregate = {
        "exact_match_avg": round(sum(all_em) / n, 4),
        "semantic_similarity_avg": round(sum(all_sim) / n, 4),
        "token_f1_avg": round(sum(all_f1) / n, 4),
        "fields_evaluated": n,
    }

    return {"fields": results, "aggregate": aggregate}


def evaluate_batch(
    predictions: list[dict],
    ground_truths: list[dict],
    doc_types: list[str],
) -> dict:
    """
    Evaluate a batch of documents.
    Returns per-document results + overall aggregates.
    """
    doc_results = []
    agg_em, agg_sim, agg_f1 = [], [], []

    for pred, gt, dtype in zip(predictions, ground_truths, doc_types):
        res = evaluate_document(pred, gt, dtype)
        doc_results.append(res)
        agg_em.append(res["aggregate"]["exact_match_avg"])
        agg_sim.append(res["aggregate"]["semantic_similarity_avg"])
        agg_f1.append(res["aggregate"]["token_f1_avg"])

    n = max(len(agg_em), 1)
    return {
        "documents": doc_results,
        "overall": {
            "exact_match_avg": round(sum(agg_em) / n, 4),
            "semantic_similarity_avg": round(sum(agg_sim) / n, 4),
            "token_f1_avg": round(sum(agg_f1) / n, 4),
            "total_documents": n,
        },
    }


def aggregate_run(doc_results: list[dict]) -> dict:
    """
    Aggregate a whole measurement run.

    Each entry carries `doc_id`, `doc_type`, `verdict`, `has_data` and the
    per-document `em` / `sim` / `f1` (0.0 when the document produced no data).

    The point of this function: **a document that failed extraction stays in
    the denominator with a score of 0.** Dropping it inflates the headline —
    that is what let the 2026-09-02 run report 98.44% over 59 documents when
    the honest figure over all 60 was 96.80%.

    `overall` is the honest headline (all documents). `ok_only` restricts the
    same metric to documents the pipeline judged OK, kept for continuity with
    earlier reports — note OK means "no detectable problem", not "correct".
    """
    def _mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    def _block(rows: list[dict]) -> dict:
        return {
            "documents": len(rows),
            "exact_match_avg": _mean([r["em"] for r in rows]),
            "semantic_similarity_avg": _mean([r["sim"] for r in rows]),
            "token_f1_avg": _mean([r["f1"] for r in rows]),
        }

    verdicts: dict[str, int] = {}
    for r in doc_results:
        v = r.get("verdict", "UNKNOWN")
        verdicts[v] = verdicts.get(v, 0) + 1

    by_type: dict[str, list[dict]] = {}
    for r in doc_results:
        by_type.setdefault(r.get("doc_type", "unknown"), []).append(r)

    return {
        "documents": len(doc_results),
        "verdicts": verdicts,
        "no_data": sum(1 for r in doc_results if not r.get("has_data", True)),
        "overall": _block(doc_results),
        "ok_only": _block([r for r in doc_results if r.get("verdict") == "OK"]),
        "per_doc_type": {k: _block(v) for k, v in sorted(by_type.items())},
    }


def _item_similarity(pred_item: dict, gt_item: dict) -> float:
    """
    Compute similarity score between two item dicts.
    Uses weighted combination of field-level similarities.
    """
    if not pred_item or not gt_item:
        return 0.0

    all_keys = set(pred_item.keys()) | set(gt_item.keys())
    # Skip metadata keys
    skip_keys = {"confidence", "confidence_scores"}
    all_keys -= skip_keys

    if not all_keys:
        return 0.0

    scores = []
    for key in all_keys:
        p = pred_item.get(key)
        g = gt_item.get(key)
        if p is None and g is None:
            scores.append(1.0)
            continue
        if p is None or g is None:
            scores.append(0.0)
            continue

        base_field = key.split(".")[-1] if "." in key else key
        if base_field in NUMERIC_FIELDS:
            scores.append(numeric_accuracy(p, g, tolerance=0.02))
        else:
            scores.append(semantic_similarity(str(p), str(g)))

    return sum(scores) / len(scores) if scores else 0.0


def _match_items_hungarian(pred_items: list[dict], gt_items: list[dict]) -> list[tuple[int, int]]:
    """
    Find best one-to-one matching between predicted and ground truth items
    using a greedy/Hungarian-like approach to maximize total similarity.

    Returns list of (pred_idx, gt_idx) pairs.
    """
    if not pred_items or not gt_items:
        return []

    n_pred = len(pred_items)
    n_gt = len(gt_items)

    # Build similarity matrix
    sim_matrix = []
    for i in range(n_pred):
        row = []
        for j in range(n_gt):
            row.append(_item_similarity(pred_items[i], gt_items[j]))
        sim_matrix.append(row)

    # Greedy matching: pick highest similarity pair, remove both, repeat
    used_pred = set()
    used_gt = set()
    matches = []

    for _ in range(min(n_pred, n_gt)):
        best_score = -1
        best_pair = (-1, -1)
        for i in range(n_pred):
            if i in used_pred:
                continue
            for j in range(n_gt):
                if j in used_gt:
                    continue
                if sim_matrix[i][j] > best_score:
                    best_score = sim_matrix[i][j]
                    best_pair = (i, j)

        if best_pair[0] >= 0:
            matches.append(best_pair)
            used_pred.add(best_pair[0])
            used_gt.add(best_pair[1])

    return matches


def _flatten(d: dict, prefix: str = "") -> dict:
    """
    Flatten nested dict/list into dot-notation keys.
    e.g. items[0].description, items[1].quantity

    For 'items' lists, uses Hungarian matching when both pred/gt have items,
    so item ordering differences don't penalize accuracy.
    """
    flat = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten(v, key))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    flat.update(_flatten(item, f"{key}[{i}]"))
                else:
                    flat[f"{key}[{i}]"] = item
        else:
            flat[key] = v
    return flat


def _flatten_with_item_matching(predicted: dict, ground_truth: dict) -> tuple[dict, dict]:
    """
    Flatten both predicted and ground truth dicts, but use Hungarian matching
    for items lists so that the best-matching items are compared at the same index.

    Returns (flat_pred, flat_gt) with items reindexed for optimal alignment.
    """
    # Extract items before flattening
    pred_items = predicted.get("items", [])
    gt_items = ground_truth.get("items", [])

    # If both have items, find optimal matching
    if pred_items and gt_items and isinstance(pred_items, list) and isinstance(gt_items, list):
        matches = _match_items_hungarian(pred_items, gt_items)

        # Rebuild reindexed items for comparison
        reindexed_pred = {}
        reindexed_gt = {}

        for new_idx, (pred_idx, gt_idx) in enumerate(matches):
            pred_item = pred_items[pred_idx] if pred_idx < len(pred_items) else {}
            gt_item = gt_items[gt_idx] if gt_idx < len(gt_items) else {}

            if isinstance(pred_item, dict):
                for fk, fv in pred_item.items():
                    reindexed_pred[f"items[{new_idx}].{fk}"] = fv
            if isinstance(gt_item, dict):
                for fk, fv in gt_item.items():
                    reindexed_gt[f"items[{new_idx}].{fk}"] = fv

        # Add unmatched GT items
        matched_gt = {gt_idx for _, gt_idx in matches}
        extra_idx = len(matches)
        for j, gt_item in enumerate(gt_items):
            if j not in matched_gt and isinstance(gt_item, dict):
                for fk, fv in gt_item.items():
                    reindexed_gt[f"items[{extra_idx}].{fk}"] = fv
                extra_idx += 1

        # Flatten non-items fields
        pred_no_items = {k: v for k, v in predicted.items() if k != "items"}
        gt_no_items = {k: v for k, v in ground_truth.items() if k != "items"}

        flat_pred = _flatten(pred_no_items)
        flat_gt = _flatten(gt_no_items)

        flat_pred.update(reindexed_pred)
        flat_gt.update(reindexed_gt)

        return flat_pred, flat_gt

    # Fallback: standard flattening
    return _flatten(predicted), _flatten(ground_truth)
