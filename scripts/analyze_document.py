"""
Run the product slice on a document and print the decision.

    python scripts/analyze_document.py <belge> [--type invoice|receipt|po]
                                                [--po <po.json>]
    python scripts/analyze_document.py --real            # the 15-document set

This is the first entry point that answers "should I pay this?" rather than
"is the JSON right?". A purchase order is optional — the real corpus contains
none, and the decision layer does not need one.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.analyze import DocumentAnalyzer          # noqa: E402
from src.reporting.humanizer import humanize_decision  # noqa: E402

MANIFEST = ROOT / "data" / "real" / "pilot_manifest.json"
RULE = "─" * 66


def show(doc_id: str, result) -> None:
    h = humanize_decision(result.decision)
    print(f"\n{RULE}")
    print(f"  {doc_id}")
    print(RULE)
    print(h["text"])
    cov = result.decision.confidence
    print(f"\n  [denetlenen {result.decision.checked_fields} alan · "
          f"kapsam %{cov*100:.0f} · {result.timings.get('total_sec', 0)}s"
          f"{' · PO ile karşılaştırıldı' if result.po_matched else ''}]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("document", nargs="?")
    ap.add_argument("--type", default="invoice")
    ap.add_argument("--po", help="JSON file holding a purchase order")
    ap.add_argument("--real", action="store_true",
                    help="run the whole real-document set")
    ap.add_argument("--json", action="store_true", help="machine output")
    args = ap.parse_args()

    analyzer = DocumentAnalyzer()
    po = json.loads(Path(args.po).read_text(encoding="utf-8")) if args.po else None

    if args.real:
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))
        results = []
        for e in entries:
            r = analyzer.analyze(ROOT / e["pages"][0], e["doc_type"])
            results.append((e["doc_id"], r))
            if not args.json:
                show(e["doc_id"], r)
        if args.json:
            print(json.dumps({d: r.to_dict() for d, r in results},
                             ensure_ascii=False, indent=2))
        else:
            print(f"\n{RULE}\n  ÖZET\n{RULE}")
            for verdict in ("PAY", "HOLD", "REVIEW"):
                ids = [d for d, r in results if r.verdict.value == verdict]
                if ids:
                    print(f"  {verdict:6s} {len(ids):2d}  {', '.join(ids)}")
            fin = sum(r.decision.financial_impact_try for _, r in results)
            internal = sum(r.decision.internal_discrepancy_try for _, r in results)
            print(f"\n  Fazla faturalanan (tedarikciye yuklenebilir) : {fin:,.2f} TL")
            print(f"  Belge ici tutarsizlik (kaynagi belirsiz)     : {internal:,.2f} TL")
        return 0

    if not args.document:
        ap.error("bir belge yolu ya da --real gerekli")
    r = analyzer.analyze(args.document, args.type, po_data=po)
    if args.json:
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    else:
        show(Path(args.document).name, r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
