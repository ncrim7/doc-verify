"""
Run the full pipeline: Extract → Verify → Correct → Re-verify.
Tests the complete self-verifying agent loop.

Usage:
    python scripts/run_full_pipeline.py --provider openai --model gpt-4.1-mini
    python scripts/run_full_pipeline.py --provider openai --model gpt-4.1-nano --limit 5
"""
import sys
import json
import argparse
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.extraction.llm_extractor import LLMExtractor
from src.extraction.correction_agent import CorrectionAgent
from src.verification.rule_based_verifier import RuleBasedVerifier
from src.evaluation.metrics import evaluate_document
from src.config import LLM_PROVIDERS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

RESULTS_DIR = Path("results")


def load_manifest(split: str = "test") -> list[dict]:
    path = Path(f"data/processed/{split}_manifest.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ground_truth(entry: dict) -> dict:
    with open(entry["json"], "r", encoding="utf-8") as f:
        return json.load(f)


def apply_corrections(extracted: dict, corrections: dict) -> dict:
    """Apply auto-corrections from rule-based verifier."""
    import re as re_module
    corrected = json.loads(json.dumps(extracted))
    for key, value in corrections.items():
        if key.startswith("items["):
            m = re_module.match(r"items\[(\d+)\]\.(\w+)", key)
            if m:
                idx, field = int(m.group(1)), m.group(2)
                if idx < len(corrected.get("items", [])):
                    corrected["items"][idx][field] = value
        else:
            corrected[key] = value
    return corrected


def main() -> None:
    p = argparse.ArgumentParser(description="Full self-verifying pipeline")
    p.add_argument("--provider", default="openai")
    p.add_argument("--model", default=None, help="Model override (e.g., gpt-4.1-mini)")
    p.add_argument("--strategy", default="direct")
    p.add_argument("--split", default="test")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-correction", action="store_true", help="Skip correction agent")
    p.add_argument("--delay", type=float, default=0.0,
                   help="Seconds to wait between documents (for rate limiting)")
    p.add_argument("--checkpoint", action="store_true",
                   help="Save checkpoint after each doc and resume if interrupted")
    args = p.parse_args()

    logger.info("=" * 65)
    logger.info("FULL SELF-VERIFYING PIPELINE")
    logger.info("  Provider: %s | Model: %s | Strategy: %s",
                args.provider, args.model or "default", args.strategy)
    logger.info("=" * 65)

    manifest = load_manifest(args.split)
    if args.limit:
        manifest = manifest[:args.limit]

    # Checkpoint setup
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    provider_tag = args.provider.replace("-", "_")
    ckpt_path = RESULTS_DIR / f"checkpoint_{provider_tag}_{args.split}.json"
    checkpoint = {}
    if args.checkpoint and ckpt_path.exists():
        checkpoint = json.loads(ckpt_path.read_text(encoding="utf-8"))
        logger.info("Resuming from checkpoint: %d docs already done", len(checkpoint))


    # Model override
    original_model = None
    if args.model:
        original_model = LLM_PROVIDERS[args.provider]["model"]
        LLM_PROVIDERS[args.provider]["model"] = args.model

    try:
        # Initialize components
        extractor = LLMExtractor(provider=args.provider, strategy=args.strategy)
        verifier = RuleBasedVerifier()
        corrector = None if args.no_correction else CorrectionAgent(
            provider=args.provider, model_override=args.model
        )

        # Results storage
        results_baseline = []      # raw extraction
        results_verified = []      # after rule verification + corrections
        results_corrected = []     # after correction agent

        stats = defaultdict(int)

        for i, entry in enumerate(manifest, 1):
            doc_type = entry["doc_type"]
            pdf_path = entry["pdf"]
            doc_id = entry.get("doc_id", Path(pdf_path).stem)
            gt = load_ground_truth(entry)

            logger.info("\n[%d/%d] %s (%s)", i, len(manifest),
                        Path(pdf_path).name, doc_type)

            # Resume: skip already-processed docs
            if args.checkpoint and doc_id in checkpoint:
                logger.info("  [SKIP] already in checkpoint")
                saved = checkpoint[doc_id]
                results_baseline.append(saved["baseline"])
                results_verified.append(saved["verified"])
                results_corrected.append(saved["corrected"])
                stats["total_issues"] += saved.get("issues", 0)
                stats["auto_corrections"] += saved.get("auto_corrections", 0)
                stats["correction_calls"] += saved.get("correction_calls", 0)
                continue

            # --- Step 1: Extract ---
            extracted = extractor.extract(pdf_path, doc_type)
            if not extracted:
                logger.warning("  Extraction failed, skipping")
                results_baseline.append({})
                results_verified.append({})
                results_corrected.append({})
                continue

            # Evaluate baseline
            eval_baseline = evaluate_document(extracted, gt, doc_type)
            results_baseline.append(eval_baseline)

            # --- Step 2: Rule-based verification ---
            verif = verifier.verify(extracted, doc_type)
            rule_corrected = apply_corrections(extracted, verif["auto_corrections"])

            eval_verified = evaluate_document(rule_corrected, gt, doc_type)
            results_verified.append(eval_verified)

            stats["total_issues"] += len(verif["issues"])
            stats["auto_corrections"] += len(verif["auto_corrections"])

            # --- Step 3: Correction agent (if issues found) ---
            if corrector and verif["issues"]:
                logger.info("  %d issues found, running correction agent...",
                            len(verif["issues"]))
                final = corrector.correct(rule_corrected, pdf_path, verif["issues"], doc_type)
                stats["correction_calls"] += 1
            else:
                final = rule_corrected

            eval_corrected = evaluate_document(final, gt, doc_type)
            results_corrected.append(eval_corrected)

            # Log progress
            base_em = eval_baseline["aggregate"]["exact_match_avg"]
            final_em = eval_corrected["aggregate"]["exact_match_avg"]
            delta = final_em - base_em
            if abs(delta) > 0.001:
                logger.info("  EM: %.1f%% → %.1f%% (%+.1f%%)",
                            base_em * 100, final_em * 100, delta * 100)
            else:
                logger.info("  EM: %.1f%% (no change)", base_em * 100)

            # Checkpoint save
            if args.checkpoint:
                checkpoint[doc_id] = {
                    "baseline": eval_baseline,
                    "verified": eval_verified,
                    "corrected": eval_corrected,
                    "issues": len(verif["issues"]),
                    "auto_corrections": len(verif["auto_corrections"]),
                    "correction_calls": 1 if (corrector and verif["issues"]) else 0,
                }
                ckpt_path.write_text(
                    json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            # Rate limit delay
            if args.delay > 0:
                import time
                time.sleep(args.delay)

        # ------------------------------------------------------------------
        # Aggregate results
        # ------------------------------------------------------------------
        def avg_metric(results, key):
            vals = [r["aggregate"][key] for r in results if r and "aggregate" in r]
            return sum(vals) / max(len(vals), 1)

        n = len(manifest)
        metrics = {}
        for label, results in [("baseline", results_baseline),
                                ("verified", results_verified),
                                ("corrected", results_corrected)]:
            metrics[label] = {
                "em": avg_metric(results, "exact_match_avg"),
                "sim": avg_metric(results, "semantic_similarity_avg"),
                "f1": avg_metric(results, "token_f1_avg"),
            }

        logger.info("\n" + "=" * 65)
        logger.info("FULL PIPELINE RESULTS (%d docs)", n)
        logger.info("=" * 65)
        logger.info("%-25s %12s %12s %12s", "Stage", "Exact Match", "Semantic Sim", "Token F1")
        logger.info("-" * 65)
        for label in ["baseline", "verified", "corrected"]:
            m = metrics[label]
            logger.info("%-25s %11.2f%% %11.2f%% %11.2f%%",
                        label.capitalize(), m["em"]*100, m["sim"]*100, m["f1"]*100)
        logger.info("=" * 65)

        # Delta
        base_em = metrics["baseline"]["em"]
        final_em = metrics["corrected"]["em"]
        logger.info("\nTotal improvement: %.2f%% → %.2f%% (%+.2f%%)",
                    base_em * 100, final_em * 100, (final_em - base_em) * 100)
        logger.info("Issues found: %d | Auto-corrections: %d | Correction calls: %d",
                    stats["total_issues"], stats["auto_corrections"],
                    stats["correction_calls"])

        # Per doc type
        by_type = defaultdict(list)
        for idx, entry in enumerate(manifest):
            if idx < len(results_corrected) and results_corrected[idx]:
                by_type[entry["doc_type"]].append(
                    results_corrected[idx]["aggregate"]["exact_match_avg"]
                )

        logger.info("\nPer document type (after correction):")
        for dtype in sorted(by_type.keys()):
            scores = by_type[dtype]
            avg = sum(scores) / len(scores) * 100
            logger.info("  %s: %.1f%% EM (n=%d)", dtype, avg, len(scores))

        # Save results
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        model_tag = (args.model or "default").replace(".", "").replace("-", "")
        result_file = RESULTS_DIR / f"pipeline_{model_tag}_{args.strategy}_{ts}.json"

        output = {
            "timestamp": ts,
            "config": {
                "provider": args.provider,
                "model": args.model or LLM_PROVIDERS[args.provider]["model"],
                "strategy": args.strategy,
                "split": args.split,
                "num_documents": n,
                "correction_enabled": not args.no_correction,
            },
            "stages": {
                stage: {
                    "exact_match_avg": round(metrics[stage]["em"], 4),
                    "semantic_similarity_avg": round(metrics[stage]["sim"], 4),
                    "token_f1_avg": round(metrics[stage]["f1"], 4),
                }
                for stage in ["baseline", "verified", "corrected"]
            },
            "improvement": {
                "em_delta": round(final_em - base_em, 4),
                "sim_delta": round(metrics["corrected"]["sim"] - metrics["baseline"]["sim"], 4),
                "f1_delta": round(metrics["corrected"]["f1"] - metrics["baseline"]["f1"], 4),
            },
            "stats": dict(stats),
            "per_document": [
                {
                    "doc_id": manifest[i]["doc_id"],
                    "doc_type": manifest[i]["doc_type"],
                    "baseline_em": results_baseline[i]["aggregate"]["exact_match_avg"]
                    if results_baseline[i] and "aggregate" in results_baseline[i] else None,
                    "corrected_em": results_corrected[i]["aggregate"]["exact_match_avg"]
                    if results_corrected[i] and "aggregate" in results_corrected[i] else None,
                }
                for i in range(len(manifest))
            ],
        }

        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logger.info("\nResults saved → %s", result_file)

    finally:
        if original_model:
            LLM_PROVIDERS[args.provider]["model"] = original_model


if __name__ == "__main__":
    main()
