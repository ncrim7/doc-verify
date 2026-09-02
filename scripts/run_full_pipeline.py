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
    args = p.parse_args()

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
            "config": {
                "provider": args.provider,
                "model": args.model or LLM_PROVIDERS[args.provider]["model"],
                "strategy": args.strategy,
                "split": args.split,
                "correction_enabled": not args.no_correction,
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
