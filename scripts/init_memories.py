"""Extract validated markdown from G:/stocks → memories/<agent>/.

Run from project root. Safe to re-run (overwrites destination).
"""
from pathlib import Path
import sys

SRC = Path("G:/stocks")
DST = Path(__file__).resolve().parent.parent / "memories"

# Map: dst path relative to memories/, src path relative to G:/stocks/
MAPPING = {
    "fundamental-analyst/rating_system.md": "strategy/rating_system.md",
    "technical-analyst/factor_insights.md": "strategy/factor_insights.md",
    "technical-analyst/5min_10round_report.md": "strategy/factors/5min_10round_report.md",
    "bear-advocate/pitfalls.md": "strategy/pitfalls.md",
    "report-writer/rating_system.md": "strategy/rating_system.md",
    "quant-analyst/rules_learned.md": "strategy/rules_learned.md",
}

def main():
    extracted = 0
    skipped = 0
    for dst_rel, src_rel in MAPPING.items():
        src = SRC / src_rel
        dst = DST / dst_rel
        if not src.exists():
            print(f"SKIP missing: {src}")
            skipped += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"OK: {src.name} -> memories/{dst_rel}")
        extracted += 1
    print(f"\n{extracted} extracted, {skipped} skipped")

if __name__ == "__main__":
    main()
