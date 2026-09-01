"""
Build data/processed/measure_manifest.json — every generated document in one
manifest, for a single authoritative pipeline run (step 1.5).

run_full_pipeline.py reads data/processed/<split>_manifest.json, so:
    python scripts/run_full_pipeline.py --split measure ...
picks this file up with no code change.

Usage:
    python scripts/build_measure_manifest.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OUTPUT_DIRS  # noqa: E402

DOC_DIRS = {
    "invoice": OUTPUT_DIRS["invoices"],
    "po":      OUTPUT_DIRS["purchase_orders"],
    "receipt": OUTPUT_DIRS["receipts"],
}
OUT = Path("data/processed/measure_manifest.json")


def main() -> None:
    entries = []
    for doc_type, d in DOC_DIRS.items():
        for jp in sorted(Path(d).glob("*.json")):
            pdf = jp.with_suffix(".pdf")
            if not pdf.exists():
                print(f"  skip {jp.name}: no matching PDF")
                continue
            entries.append({
                "doc_id":   jp.stem,
                "doc_type": doc_type,
                "pdf":      str(pdf),
                "json":     str(jp),
            })

    if not entries:
        raise SystemExit("no documents found under data/raw/ — run generate_dataset.py first")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e["doc_type"]] = by_type.get(e["doc_type"], 0) + 1
    print(f"{len(entries)} docs -> {OUT}   {by_type}")


if __name__ == "__main__":
    main()
