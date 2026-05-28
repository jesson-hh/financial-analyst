from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore

# 匹配 "## EV-001 标题"  (排除 "## 索引" / "## 元信息" 等非卡片段)
_HEADER_RE = re.compile(r"^##\s+(EV-\d+)\s+(.+?)\s*$", re.MULTILINE)
_FIELD_RE = {
    "confidence": re.compile(r"\*\*置信\*\*[:：]\s*(\S+)"),
    "tags": re.compile(r"\*\*标签\*\*[:：]\s*(.+)"),
    "source": re.compile(r"\*\*来源\*\*[:：]\s*(.+)"),
}
_BVID_RE = re.compile(r"(BV[0-9A-Za-z]+)")
_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_SECTION_HEADS = ["经验", "适用条件", "操作建议", "反例 / 边界"]
# 段落边界 = 下一个【已知字段标题】, 不能用任意 "\n**". 否则正文里行首的 **粗体**
# 子结构 (如 **维度 A** / **阶段 1**) 会把段落提前截断, 丢掉其中的关键数字/细节.
_NEXT_FIELD = r"(?=\n\*\*(?:经验|适用条件|操作建议|反例|置信|标签|来源)|\Z)"


def _slice_sections(block: str) -> str:
    """从一段卡片正文里抽出 4 段式, 拼成标准 body.

    段落边界用已知字段标题界定 (而非任意 \\n**), 以保留正文内的 **粗体** 子结构.
    """
    out = []
    for head in _SECTION_HEADS:
        m = re.search(rf"\*\*{re.escape(head)}\*\*[:：]\s*(.+?){_NEXT_FIELD}", block, re.DOTALL)
        if m:
            out.append(f"## {head}\n{m.group(1).strip()}")
    return "\n\n".join(out)


def _parse_tags(raw: str) -> list[str]:
    return [t.lstrip("#").strip() for t in raw.split() if t.strip()]


def _parse_source(raw: str) -> dict:
    src: dict = {"platform": "bilibili"}
    bv = _BVID_RE.search(raw)
    if bv:
        src["bvid"] = bv.group(1)
    dt = _DATE_RE.search(raw)
    if dt:
        src["date"] = dt.group(1)
    return src


def parse_notes_markdown(text: str) -> list[WisdomCard]:
    """解析 bilibili_notes.md 全文 → approved 状态的 WisdomCard 列表."""
    headers = list(_HEADER_RE.finditer(text))
    cards: list[WisdomCard] = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        conf_m = _FIELD_RE["confidence"].search(block)
        tags_m = _FIELD_RE["tags"].search(block)
        src_m = _FIELD_RE["source"].search(block)
        cards.append(WisdomCard(
            id=h.group(1),
            title=h.group(2).strip(),
            status="approved",
            quality_score=1.0,
            confidence=conf_m.group(1) if conf_m else "中",
            tags=_parse_tags(tags_m.group(1)) if tags_m else [],
            source=_parse_source(src_m.group(1)) if src_m else {"platform": "bilibili"},
            body=_slice_sections(block),
            corroborates=[],
            conflicts=[],
            created="2026-05-28",
            reviewed_by="migrated",
        ))
    return cards


def migrate_file(notes_path: str, store: WisdomStore) -> int:
    text = Path(notes_path).read_text(encoding="utf-8")
    cards = parse_notes_markdown(text)
    for c in cards:
        store.save(c)
    return len(cards)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="financial_analyst.wisdom.migrate")
    p.add_argument("notes", help="bilibili_notes.md 路径")
    p.add_argument("--root", default=None, help="wisdom 存储根")
    args = p.parse_args(argv)
    store = WisdomStore(root=Path(args.root) if args.root else None)
    n = migrate_file(args.notes, store)
    print(f"[wisdom.migrate] 导入 {n} 张 approved 卡到 {store.root / 'approved'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
