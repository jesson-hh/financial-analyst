from __future__ import annotations

import json
import logging
from typing import Any, Optional

from financial_analyst.llm.client import LLMClient
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.prompts import build_extraction_messages

logger = logging.getLogger(__name__)

_REQUIRED_BODY_MARKERS = ("## 经验", "## 反例")  # 质量门: 必须有经验 + 反例段


def _passes_quality_gate(item: dict[str, Any]) -> bool:
    body = item.get("body", "") or ""
    if not all(marker in body for marker in _REQUIRED_BODY_MARKERS):
        return False
    if not (item.get("title") or "").strip():
        return False
    return True


async def extract_cards(
    transcript: str,
    source: dict[str, Any],
    existing: Optional[list[WisdomCard]] = None,
) -> list[WisdomCard]:
    """转写文本 → 草稿经验卡. id 留空 (由 store 落盘时 next_id 分配).

    Raises:
        json.JSONDecodeError: LLM 两次都返回非法 JSON 时 (绝不返回半成品脏卡).
    """
    existing = existing or []
    client = LLMClient.for_agent("wisdom")
    messages = build_extraction_messages(transcript, source, existing)

    data: Optional[dict] = None
    last_err: Optional[Exception] = None
    for attempt in range(2):
        resp = await client.chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = resp["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
            break
        except json.JSONDecodeError as e:
            last_err = e
            logger.warning("wisdom extract: bad JSON on attempt %d", attempt + 1)
    if data is None:
        assert last_err is not None
        raise last_err

    cards: list[WisdomCard] = []
    for item in data.get("cards", []):
        if not _passes_quality_gate(item):
            logger.info("wisdom extract: dropped low-quality card %r", item.get("title"))
            continue
        cards.append(WisdomCard(
            id="",
            title=item.get("title", "").strip(),
            status="draft",
            quality_score=float(item.get("quality_score") or 0.0),
            confidence=item.get("confidence", "中"),
            tags=item.get("tags") or [],
            source=source,
            body=item.get("body", "").strip(),
            corroborates=item.get("corroborates") or [],
            conflicts=item.get("conflicts") or [],
            created="",
            reviewed_by=None,
        ))
    return cards
