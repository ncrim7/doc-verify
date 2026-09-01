"""
Train/Val/Test split with stratification by doc_type and language.
Ensures balanced representation across all splits.

Usage:
    python scripts/split_dataset.py
    python scripts/split_dataset.py --seed 42
"""
import sys
import json
import random
import shutil
import argparse
import logging
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATASET_CONFIG, OUTPUT_DIRS

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
SPLIT_DIR = Path("data/processed")
METADATA_DIR = Path("data/metadata")


def load_all_documents() -> list[dict]:
    """Scan raw directories and collect document metadata."""
    docs = []
    type_dirs = {
        "invoice": RAW_DIR / "invoices",
        "po": RAW_DIR / "purchase_orders",
        "receipt": RAW_DIR / "receipts",
    }
    for doc_type, dir_path in type_dirs.items():
        if not dir_path.exists():
            logger.warning("Directory not found: %s", dir_path)
            continue
        for json_path in sorted(dir_path.glob("*.json")):
            pdf_path = json_path.with_suffix(".pdf")
            if not pdf_path.exists():
                logger.warning("Missing PDF for %s", json_path.name)
                continue
            with open(json_path, "r", encoding="utf-8") as f:
                gt = json.load(f)
            docs.append({
                "doc_id": json_path.stem,
                "doc_type": doc_type,
                "language": gt.get("language", "unknown"),
                "json_path": str(json_path),
                "pdf_path": str(pdf_path),
            })
    return docs


def stratified_split(
    docs: list[dict],
    train_ratio: float = 0.60,
    val_ratio: float = 0.20,
    seed: int = 42,
) -> dict[str, list[dict]]:
    """
    Stratified split by (doc_type, language) groups.
    Returns {'train': [...], 'val': [...], 'test': [...]}.
    """
    rng = random.Random(seed)

    # Group by (doc_type, language)
    groups: dict[tuple, list] = defaultdict(list)
    for doc in docs:
        key = (doc["doc_type"], doc["language"])
        groups[key].append(doc)

    splits = {"train": [], "val": [], "test": []}

    for key, group_docs in sorted(groups.items()):
        rng.shuffle(group_docs)
        n = len(group_docs)
        n_train = max(1, round(n * train_ratio))
        n_val = max(1, round(n * val_ratio))
        # rest goes to test
        splits["train"].extend(group_docs[:n_train])
        splits["val"].extend(group_docs[n_train : n_train + n_val])
        splits["test"].extend(group_docs[n_train + n_val :])

    return splits


def save_splits(splits: dict[str, list[dict]]) -> None:
    """Write split manifests + copy files into processed/{split}/."""
    SPLIT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {}
    for split_name, docs in splits.items():
        split_path = SPLIT_DIR / split_name
        split_path.mkdir(parents=True, exist_ok=True)

        entries = []
        for doc in docs:
            # Copy PDF + JSON into split folder
            src_pdf = Path(doc["pdf_path"])
            src_json = Path(doc["json_path"])
            dst_pdf = split_path / src_pdf.name
            dst_json = split_path / src_json.name
            shutil.copy2(src_pdf, dst_pdf)
            shutil.copy2(src_json, dst_json)
            entries.append({
                "doc_id": doc["doc_id"],
                "doc_type": doc["doc_type"],
                "language": doc["language"],
                "pdf": str(dst_pdf),
                "json": str(dst_json),
            })

        manifest[split_name] = entries

        # Per-split manifest
        manifest_path = SPLIT_DIR / f"{split_name}_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
        logger.info("  %s: %d documents → %s", split_name, len(entries), manifest_path)

    # Global split summary
    summary = {
        "seed": DATASET_CONFIG["seed"],
        "total": sum(len(v) for v in splits.values()),
        "splits": {k: len(v) for k, v in splits.items()},
        "distribution": {},
    }
    for split_name, docs in splits.items():
        by_type = defaultdict(int)
        by_lang = defaultdict(int)
        for d in docs:
            by_type[d["doc_type"]] += 1
            by_lang[d["language"]] += 1
        summary["distribution"][split_name] = {
            "by_type": dict(by_type),
            "by_language": dict(by_lang),
        }

    summary_path = METADATA_DIR / "split_summary.json"
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    logger.info("\n  Split summary → %s", summary_path)

    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Stratified train/val/test split")
    p.add_argument("--seed", type=int, default=DATASET_CONFIG["seed"])
    args = p.parse_args()

    logger.info("=" * 50)
    logger.info("DATASET SPLIT (seed=%d)", args.seed)
    logger.info("=" * 50)

    docs = load_all_documents()
    logger.info("Found %d documents\n", len(docs))

    splits = stratified_split(
        docs,
        train_ratio=DATASET_CONFIG["splits"]["train"],
        val_ratio=DATASET_CONFIG["splits"]["val"],
        seed=args.seed,
    )

    summary = save_splits(splits)

    logger.info("\n" + "=" * 50)
    logger.info("SPLIT RESULTS")
    logger.info("=" * 50)
    for split_name, count in summary["splits"].items():
        dist = summary["distribution"][split_name]
        logger.info("  %s: %d docs | types: %s | langs: %s",
                     split_name, count,
                     dict(dist["by_type"]),
                     dict(dist["by_language"]))
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
