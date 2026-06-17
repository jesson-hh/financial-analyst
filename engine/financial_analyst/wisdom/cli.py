from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

from financial_analyst.wisdom.extractor import extract_cards
from financial_analyst.wisdom.store import WisdomStore


def _run_extract(args: argparse.Namespace) -> int:
    transcript = Path(args.transcript).read_text(encoding="utf-8")
    source = {
        "platform": args.platform,
        "up": args.up,
        "bvid": args.bvid,
        "date": args.date,
    }
    store = WisdomStore(root=Path(args.root) if args.root else None)
    existing = store.list_by_status("approved")
    cards = asyncio.run(extract_cards(transcript, source, existing=existing))
    today = _dt.date.today().isoformat()
    for card in cards:
        card.id = store.next_id()
        card.created = today
        card.source = source
        store.save(card)
    print(f"[wisdom] 抽取 {len(cards)} 张草稿卡, 待审总数 {len(store.list_by_status('draft'))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="financial_analyst.wisdom.cli")
    sub = p.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="转写文本 → 草稿经验卡")
    ex.add_argument("transcript", help="转写文本 .txt 路径")
    ex.add_argument("--platform", default="bilibili")
    ex.add_argument("--up", default="")
    ex.add_argument("--bvid", default="")
    ex.add_argument("--date", default="")
    ex.add_argument("--root", default=None, help="wisdom 存储根 (默认 ~/.financial-analyst/wisdom)")
    ex.set_defaults(func=_run_extract)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
