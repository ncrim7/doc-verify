"""
Run the pipeline over the real-document pilot set and score it against
hand-built ground truth.

Why this exists as a script rather than an ad-hoc run: the synthetic runs are
pinned (`results/*.results.json` + a measurement note), and the real-document
figure — the one that actually matters for a sales conversation — was not.
That asymmetry is backwards. A number nobody can reproduce is not a
measurement.

Two modes:

    python scripts/run_real_pilot.py                  # extract + score, pins results
    python scripts/run_real_pilot.py --score-only PATH # re-score pinned extractions

`--score-only` matters because the ground truth is still moving. Every time a
GT convention is corrected, the score has to be recomputed on the SAME model
output — otherwise a GT fix and a model change land in one number and neither
can be attributed. One variable per measurement.

Scoring is gated by `available_fields`: a field that is not printed on the page,
or whose value our schema cannot express, is not scored. See the `_open_questions`
and `_schema_gaps` blocks in each GT file for why a field was excluded.
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.evaluation.metrics import evaluate_document          # noqa: E402
from src.pipeline import DocumentPipeline                     # noqa: E402

MANIFEST = ROOT / "data" / "real" / "pilot_manifest.json"
GT_DIR = ROOT / "data" / "real" / "gt"
RESULTS_DIR = ROOT / "results"


def load_manifest() -> list[dict]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def load_gt(doc_id: str) -> dict | None:
    path = GT_DIR / f"{doc_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def extract_all(entries: list[dict], delay: float) -> list[dict]:
    """Run the real pipeline once per document. Only page 0 is extracted —
    the schema describes a single document, not a multi-page bundle."""
    pipeline = DocumentPipeline()
    out = []
    for e in entries:
        doc_id = e["doc_id"]
        page = ROOT / e["pages"][0]
        print(f"  {doc_id} <- {page.name} ... ", end="", flush=True)
        t0 = time.time()
        result = pipeline.process(str(page), e["doc_type"])
        print(f"{result.verdict} ({time.time() - t0:.1f}s)")
        out.append({
            "doc_id": doc_id,
            "doc_type": e["doc_type"],
            "condition": e.get("condition"),
            "page": e["pages"][0],
            "verdict": str(result.verdict),
            "reasons": result.reasons,
            "data": result.data,
            "raw": result.raw,
            "error": result.error,
        })
        if delay:
            time.sleep(delay)
    return out


def score(extractions: list[dict]) -> dict:
    rows, missing_gt = [], []
    for ex in extractions:
        gt = load_gt(ex["doc_id"])
        if gt is None:
            missing_gt.append(ex["doc_id"])
            continue
        data = ex.get("data") or {}
        ev = evaluate_document(data, gt, ex["doc_type"])
        misses = [
            {"field": f, **{k: s[k] for k in ("predicted", "ground_truth", "semantic_similarity")}}
            for f, s in ev["fields"].items() if s["exact_match"] < 1.0
        ]
        rows.append({
            "doc_id": ex["doc_id"],
            "condition": ex.get("condition"),
            "verdict": ex["verdict"],
            "has_data": bool(data),
            "em": ev["aggregate"]["exact_match_avg"],
            "sim": ev["aggregate"]["semantic_similarity_avg"],
            "f1": ev["aggregate"]["token_f1_avg"],
            "fields_scored": ev["aggregate"]["fields_evaluated"],
            "misses": misses,
        })

    n = len(rows) or 1
    return {
        "documents": len(rows),
        "missing_gt": missing_gt,
        "exact_match_avg": round(sum(r["em"] for r in rows) / n, 4),
        "semantic_similarity_avg": round(sum(r["sim"] for r in rows) / n, 4),
        "token_f1_avg": round(sum(r["f1"] for r in rows) / n, 4),
        "fields_scored_total": sum(r["fields_scored"] for r in rows),
        "misses_total": sum(len(r["misses"]) for r in rows),
        "per_doc": rows,
    }


def report(summary: dict) -> None:
    print("\n" + "=" * 72)
    print(f"REAL PILOT — n={summary['documents']}")
    print("=" * 72)
    for r in summary["per_doc"]:
        print(f"\n{r['doc_id']}  [{r['condition']}]")
        print(f"  verdict={r['verdict']}  EM={r['em']:.2%}  "
              f"sim={r['sim']:.2%}  scored={r['fields_scored']} fields")
        for m in r["misses"]:
            print(f"    MISS {m['field']}")
            print(f"         gt   : {m['ground_truth']}")
            print(f"         pred : {m['predicted']}")
    print("\n" + "-" * 72)
    print(f"EM  {summary['exact_match_avg']:.2%}   "
          f"sim {summary['semantic_similarity_avg']:.2%}   "
          f"F1 {summary['token_f1_avg']:.2%}")
    print(f"{summary['misses_total']} misses over "
          f"{summary['fields_scored_total']} scored fields")
    if summary["missing_gt"]:
        print(f"NO GROUND TRUTH (not scored): {', '.join(summary['missing_gt'])}")
    print("n is tiny. This is a pilot, not a measurement — do not quote it.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--score-only", metavar="PATH",
                    help="re-score a pinned extraction file, no API calls")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--out", help="where to pin extractions (default: timestamped)")
    args = ap.parse_args()

    if args.score_only:
        pinned = json.loads(Path(args.score_only).read_text(encoding="utf-8"))
        extractions = pinned["extractions"]
        print(f"scoring pinned extractions from {args.score_only} "
              f"(run at {pinned.get('run_at', '?')})")
    else:
        entries = load_manifest()
        print(f"extracting {len(entries)} real documents")
        extractions = extract_all(entries, args.delay)
        out = Path(args.out) if args.out else (
            RESULTS_DIR / f"real_pilot_{datetime.now():%Y%m%d_%H%M%S}.results.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(
            {"run_at": datetime.now().isoformat(timespec="seconds"),
             "extractions": extractions}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"pinned -> {out}")

    summary = score(extractions)
    report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
