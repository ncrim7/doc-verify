"""
Main dataset generation script.

Usage:
    python scripts/generate_dataset.py              # full run (120 docs)
    python scripts/generate_dataset.py --test        # 3 samples per type only
    python scripts/generate_dataset.py --count 60    # custom count (20 per type)
    python scripts/generate_dataset.py --seed 7      # custom seed
"""
import sys
import json
import logging
import argparse
from pathlib import Path

# Make src importable when running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATASET_CONFIG, OUTPUT_DIRS
from src.data_generation.document_generator import DocumentGenerator
from src.data_generation.validator import DataValidator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate synthetic document dataset")
    p.add_argument("--count",  type=int, default=DATASET_CONFIG["total_documents"],
                   help="Total documents to generate (must be divisible by 3)")
    p.add_argument("--seed",   type=int, default=DATASET_CONFIG["seed"])
    p.add_argument("--output-dir", default=".", help="Project root directory")
    p.add_argument("--test",   action="store_true",
                   help="Test mode: generate only 3 samples per type (9 total)")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if args.test:
        per_type = 3
        logger.info("=== TEST MODE: generating 3 samples per type ===")
    else:
        per_type = args.count // 3
        logger.info("=== FULL RUN: %d docs per type (%d total) ===", per_type, per_type * 3)

    root     = Path(args.output_dir).resolve()
    gen      = DocumentGenerator(seed=args.seed)
    validator = DataValidator()

    type_dir_map = {
        "invoice": root / OUTPUT_DIRS["invoices"],
        "po":      root / OUTPUT_DIRS["purchase_orders"],
        "receipt": root / OUTPUT_DIRS["receipts"],
    }

    # Ensure output directories exist
    for d in type_dir_map.values():
        d.mkdir(parents=True, exist_ok=True)
    (root / OUTPUT_DIRS["metadata"]).mkdir(parents=True, exist_ok=True)

    total_generated = 0
    total_valid     = 0
    total_invalid   = 0

    for doc_type, out_dir in type_dir_map.items():
        logger.info("--- Generating %d %s documents ---", per_type, doc_type)
        for i in range(1, per_type + 1):
            doc_id   = f"{doc_type}_{i:04d}"
            pdf_path = out_dir / f"{doc_id}.pdf"
            json_path = out_dir / f"{doc_id}.json"

            pdf_bytes, gt = gen.generate(doc_type)

            pdf_path.write_bytes(pdf_bytes)
            json_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")

            is_valid, errors = validator.validate_document(gt, doc_type)
            total_generated += 1
            if is_valid:
                total_valid += 1
                logger.debug("  [OK] %s", doc_id)
            else:
                total_invalid += 1
                logger.warning("  [FAIL] %s — %s", doc_id, "; ".join(errors))

        logger.info("  Done: %d/%d valid", per_type - total_invalid, per_type)

    # Quality report
    logger.info("--- Building quality report ---")
    report = validator.generate_quality_report(root / "data/raw")
    report_path = root / OUTPUT_DIRS["metadata"] / "quality_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # Final summary
    logger.info("")
    logger.info("=" * 50)
    logger.info("SUMMARY")
    logger.info("  Total generated : %d", total_generated)
    logger.info("  Valid           : %d", total_valid)
    logger.info("  Invalid         : %d", total_invalid)
    logger.info("  Status          : %s", report.get("status", "?"))
    logger.info("  Quality report  : %s", report_path)
    logger.info("=" * 50)

    if total_invalid > 0:
        logger.error("Validation failures detected — check quality_report.json")
        sys.exit(1)

    logger.info("Dataset generation complete!")


if __name__ == "__main__":
    main()
