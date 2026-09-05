"""
Measurement harness: run DocumentPipeline over a manifest and report accuracy.

It measures the product path — the same `DocumentPipeline` an application would
call — so the number describes what the product actually does.

A document that fails extraction is **counted as 0, not dropped**. Dropping it
is what let the 2026-09-02 run report 98.44% over 59 documents when the honest
figure over all 60 was 96.80%.

Usage:
    python scripts/run_full_pipeline.py --split measure
    python scripts/run_full_pipeline.py --split test --model gpt-5-mini --limit 5
"""
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.pipeline import DocumentPipeline, Verdict          # noqa: E402
from src.evaluation.metrics import evaluate_document, aggregate_run  # noqa: E402
from src.config import LLM_PROVIDERS                        # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

RESULTS_DIR = Path("results")
CHECKPOINT_FORMAT = 2


def load_manifest(split: str) -> list[dict]:
    with open(Path(f"data/processed/{split}_manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def load_ground_truth(entry: dict) -> dict:
    with open(entry["json"], encoding="utf-8") as f:
        return json.load(f)


def _score(extracted: dict | None, gt: dict, doc_type: str) -> tuple[float, float, float, list]:
    """
    Field-level EM / semantic sim / token F1 plus the list of fields that
    missed, so a run is diagnosable from its own output without a second
    API pass.
    """
    if not extracted:
        return 0.0, 0.0, 0.0, []
    ev = evaluate_document(extracted, gt, doc_type)
    agg = ev["aggregate"]
    misses = [
        {"field": name, "gt": fd["ground_truth"], "pred": fd["predicted"]}
        for name, fd in ev["fields"].items() if fd["exact_match"] == 0.0
    ]
    return (agg["exact_match_avg"], agg["semantic_similarity_avg"],
            agg["token_f1_avg"], misses)


def _rescore(path: Path, split: str) -> None:
    """
    Re-score a pinned run against the current ground truth and metric.

    The whole point is that the model output does not move: whatever changed
    between the pinned run and now is the only thing that can explain the
    difference. A run pinned before `data` was recorded cannot be re-scored,
    and says so rather than silently reporting a wrong number.
    """
    pinned = json.loads(path.read_text(encoding="utf-8"))
    rows_in = pinned["per_document"]
    if not any("data" in r for r in rows_in):
        logger.error("%s predates result pinning — it kept only the misses, so "
                     "a field that passed under the old rule left no trace and "
                     "this run cannot be re-scored. Re-run it.", path.name)
        raise SystemExit(2)

    by_id = {e["doc_id"]: e for e in load_manifest(split)}
    rows = []
    for r in rows_in:
        entry = by_id.get(r["doc_id"])
        if entry is None:
            logger.warning("  %s not in split '%s' — skipped", r["doc_id"], split)
            continue
        gt = load_ground_truth(entry)
        em, sim, f1, misses = _score(r.get("data"), gt, r["doc_type"])
        raw_em, _, _, _ = _score(r.get("raw"), gt, r["doc_type"])
        rows.append({**r, "em": em, "sim": sim, "f1": f1,
                     "raw_em": raw_em, "misses": misses})

    agg = aggregate_run(rows)
    old = pinned["aggregate"]["overall"]["exact_match_avg"]
    new = agg["overall"]["exact_match_avg"]
    logger.info("=" * 68)
    logger.info("RE-SCORED %s  (n=%d, no API calls)", path.name, agg["documents"])
    logger.info("  as pinned : %.2f%% EM", old * 100)
    logger.info("  as scored now: %.2f%% EM   delta %+.2f pp",
                new * 100, (new - old) * 100)
    logger.info("=" * 68)
    for dtype, blk in agg["per_doc_type"].items():
        logger.info("  %-9s %6.2f%% EM  (n=%d)",
                    dtype, blk["exact_match_avg"] * 100, blk["documents"])
    moved = [r for r, o in zip(rows, rows_in) if r["em"] != o["em"]]
    logger.info("\n%d of %d documents moved:", len(moved), len(rows))
    for r in moved:
        logger.info("  %-16s -> %.2f%%   misses: %s", r["doc_id"],
                    r["em"] * 100,
                    ", ".join(m["field"] for m in r["misses"]) or "none")


def main() -> None:
    p = argparse.ArgumentParser(description="Run the pipeline over a manifest")
    p.add_argument("--provider", default="openai")
    p.add_argument("--model", default=None, help="Model override (e.g. gpt-5-mini)")
    p.add_argument("--strategy", default="direct")
    p.add_argument("--split", default="measure")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-correction", action="store_true")
    p.add_argument("--delay", type=float, default=0.0)
    p.add_argument("--checkpoint", action="store_true",
                   help="Save after each document and resume if interrupted")
    p.add_argument("--score-only", metavar="PATH",
                   help="Re-score a pinned result file against the CURRENT "
                        "ground truth and metric. No API calls. Use this to "
                        "measure a GT or metric change on unchanged model "
                        "output — one variable per measurement.")
    args = p.parse_args()

    if args.score_only:
        _rescore(Path(args.score_only), args.split)
        return

    manifest = load_manifest(args.split)
    if args.limit:
        manifest = manifest[:args.limit]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ckpt_path = RESULTS_DIR / f"checkpoint_{args.provider.replace('-', '_')}_{args.split}.json"
    checkpoint: dict = {}
    if args.checkpoint and ckpt_path.exists():
        saved = json.loads(ckpt_path.read_text(encoding="utf-8"))
        if saved.get("_format") == CHECKPOINT_FORMAT:
            checkpoint = saved.get("docs", {})
            logger.info("Resuming: %d documents already done", len(checkpoint))
        else:
            logger.warning("Ignoring checkpoint written in an older format")

    original_model = None
    if args.model:
        original_model = LLM_PROVIDERS[args.provider]["model"]
        LLM_PROVIDERS[args.provider]["model"] = args.model

    logger.info("=" * 68)
    logger.info("PIPELINE MEASUREMENT")
    logger.info("  provider=%s  model=%s  strategy=%s  split=%s  docs=%d",
                args.provider, args.model or LLM_PROVIDERS[args.provider]["model"],
                args.strategy, args.split, len(manifest))
    logger.info("  reasoning_effort=%s  temperature=%s",
                LLM_PROVIDERS[args.provider].get("reasoning_effort"),
                LLM_PROVIDERS[args.provider].get("temperature"))
    logger.info("=" * 68)

    try:
        pipeline = DocumentPipeline(
            provider=args.provider,
            strategy=args.strategy,
            enable_correction=not args.no_correction,
        )

        rows: list[dict] = []
        for i, entry in enumerate(manifest, 1):
            doc_id = entry.get("doc_id", Path(entry["pdf"]).stem)
            doc_type = entry["doc_type"]
            logger.info("[%d/%d] %s (%s)", i, len(manifest),
                        Path(entry["pdf"]).name, doc_type)

            if doc_id in checkpoint:
                logger.info("  [skip] in checkpoint")
                rows.append(checkpoint[doc_id])
                continue

            gt = load_ground_truth(entry)
            result = pipeline.process(entry["pdf"], doc_type)

            em, sim, f1, misses = _score(result.data, gt, doc_type)
            raw_em, _, _, _ = _score(result.raw, gt, doc_type)

            row = {
                "doc_id": doc_id,
                "doc_type": doc_type,
                "verdict": result.verdict.value,
                "has_data": result.data is not None,
                "em": em, "sim": sim, "f1": f1,
                "raw_em": raw_em,
                "reasons": result.reasons,
                "corrected": result.corrected,
                "misses": misses,
                # The extraction itself, so a later change to the ground truth
                # or to the metric can be re-scored on the SAME model output.
                # Without it a metric fix and a model change land in one number
                # and neither can be attributed. Learned the hard way: the
                # money tolerance was found to be 1% *relative*, and no pinned
                # synthetic run could be re-scored to measure the damage,
                # because only the misses were kept and a field that passed
                # under the loose rule left no trace.
                "data": result.data,
                "raw": result.raw,
            }
            rows.append(row)

            if result.verdict is Verdict.OK:
                logger.info("  OK      EM %.1f%%", em * 100)
            else:
                logger.warning("  REVIEW  EM %.1f%%  <- %s",
                               em * 100, "; ".join(result.reasons[:3]))

            if args.checkpoint:
                checkpoint[doc_id] = row
                ckpt_path.write_text(
                    json.dumps({"_format": CHECKPOINT_FORMAT, "docs": checkpoint},
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")

            if args.delay > 0:
                import time
                time.sleep(args.delay)

        # ------------------------------------------------------------------
        agg = aggregate_run(rows)
        raw_em_all = round(sum(r["raw_em"] for r in rows) / max(len(rows), 1), 4)

        logger.info("\n" + "=" * 68)
        logger.info("RESULTS  (%d documents)", agg["documents"])
        logger.info("=" * 68)
        for verdict, n in sorted(agg["verdicts"].items()):
            logger.info("  verdict %-8s %3d", verdict, n)
        logger.info("    of which produced no data at all: %d", agg["no_data"])
        logger.info("-" * 68)
        o, ok = agg["overall"], agg["ok_only"]
        logger.info("  %-34s %8s %8s %8s", "", "EM", "Sim", "F1")
        logger.info("  %-34s %7.2f%% %7.2f%% %7.2f%%",
                    f"ALL documents (n={o['documents']})",
                    o["exact_match_avg"] * 100, o["semantic_similarity_avg"] * 100,
                    o["token_f1_avg"] * 100)
        logger.info("  %-34s %7.2f%% %7.2f%% %7.2f%%",
                    f"OK documents only (n={ok['documents']})",
                    ok["exact_match_avg"] * 100, ok["semantic_similarity_avg"] * 100,
                    ok["token_f1_avg"] * 100)
        logger.info("-" * 68)
        logger.info("  raw extraction EM (before repair/correction): %.2f%%",
                    raw_em_all * 100)
        logger.info("  delta from repair + correction:               %+.2f pp",
                    (o["exact_match_avg"] - raw_em_all) * 100)
        logger.info("=" * 68)
        logger.info("  ALL-documents EM is the honest headline. 'OK' means no")
        logger.info("  detectable problem, not 'correct'.")
        logger.info("=" * 68)

        logger.info("\nPer document type (all documents):")
        for dtype, blk in agg["per_doc_type"].items():
            logger.info("  %-9s %6.2f%% EM  (n=%d)",
                        dtype, blk["exact_match_avg"] * 100, blk["documents"])

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_tag = (args.model or LLM_PROVIDERS[args.provider]["model"]) \
            .replace(".", "").replace("-", "")
        out_path = RESULTS_DIR / f"pipeline_{model_tag}_{args.strategy}_{ts}.json"
        out_path.write_text(json.dumps({
            "timestamp": ts,
            # Record the *effective* config, not just the CLI flags — a result
            # file has to say which run it was. reasoning_effort in particular
            # is env-driven and was missing here, which made two runs
            # indistinguishable from their artefacts alone.
            "config": {
                "provider": args.provider,
                "model": args.model or LLM_PROVIDERS[args.provider]["model"],
                "strategy": args.strategy,
                "split": args.split,
                "correction_enabled": not args.no_correction,
                "reasoning_effort": LLM_PROVIDERS[args.provider].get("reasoning_effort"),
                "temperature": LLM_PROVIDERS[args.provider].get("temperature"),
            },
            "aggregate": agg,
            "raw_extraction_em": raw_em_all,
            "per_document": rows,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("\nResults saved -> %s", out_path)

    finally:
        if original_model:
            LLM_PROVIDERS[args.provider]["model"] = original_model


if __name__ == "__main__":
    main()
