"""把 bilibili_notes(wisdom 视频经验)解析灌进"未验证"(draft)桶。

复用引擎 ``financial_analyst.wisdom.migrate.parse_notes_markdown``(引擎已 vendored
进仓库,import 复用不重写)。引擎 ``WisdomCard``(定性 4 段式)→ guanlan ``Card``
(UI 形状,落 draft)。幂等:id 已存在于任一桶(draft/approved/rejected)则跳过 ——
不复活用户验证后已移走的卡。

源文件默认只读引用 stocks(``GUANLAN_WISDOM_NOTES`` 可覆盖),不复制进仓库;
``ingest_*`` 只把解析后的卡**写进 guanlan 自有本地库**(.data/wisdom/draft)。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from guanlan_v2.cards.card import Card
from guanlan_v2.cards.store import CardStore

_CONF_MAP = {"高": 80, "中": 60, "低": 40}

# 默认视频经验源(只读引用 G:/stocks,不复制;可用 GUANLAN_WISDOM_NOTES 覆盖)
_DEFAULT_NOTES = r"G:/stocks/strategy/wisdom/bilibili_notes.md"


def card_from_wisdom(wc) -> Card:
    """引擎 WisdomCard → guanlan Card(status=draft,未验证)。

    body(4 段式)→ insight;confidence(高/中/低)→ conf(80/60/40);source.bvid → src;
    ic/expr 留空、verdict 占位"存疑"(量化验证"验"尚未接入)。
    """
    src = "B站"
    try:
        bvid = (wc.source or {}).get("bvid")
        if bvid:
            src = f"B站·{bvid}"
    except Exception:
        pass
    return Card(
        id=wc.id,
        title=wc.title,
        status="draft",
        cat="其他",
        tags=list(wc.tags or []),
        verdict="存疑",                       # 未验证占位(尚未过"验")
        conf=_CONF_MAP.get(getattr(wc, "confidence", "中"), 60),
        ic="",
        expr="",
        insight=wc.body or "",
        src=src,
        refs=[],
        created=getattr(wc, "created", "") or "",
        reviewed_by=None,
    )


def _exists_anywhere(store: CardStore, card_id: str) -> bool:
    try:
        store.load(card_id)
        return True
    except KeyError:
        return False


def ingest_notes_text(store: CardStore, text: str) -> dict:
    """解析 notes 全文 → 写入 draft 桶。幂等:已存在(任一桶)的 id 跳过。"""
    from financial_analyst.wisdom.migrate import parse_notes_markdown

    wcards = parse_notes_markdown(text)
    ingested = skipped = 0
    for wc in wcards:
        if _exists_anywhere(store, wc.id):
            skipped += 1
            continue
        store.save(card_from_wisdom(wc))
        ingested += 1
    return {"ingested": ingested, "skipped": skipped, "total": len(wcards)}


def ingest_notes_file(store: CardStore, notes_path: Optional[str] = None) -> dict:
    """从文件灌入(默认 GUANLAN_WISDOM_NOTES,否则 stocks 的 bilibili_notes.md)。"""
    path = notes_path or os.environ.get("GUANLAN_WISDOM_NOTES") or _DEFAULT_NOTES
    text = Path(path).read_text(encoding="utf-8")
    res = ingest_notes_text(store, text)
    res["path"] = str(path)
    return res
