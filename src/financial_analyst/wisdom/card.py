from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

# 正文固定 4 段式 (与 strategy/wisdom/bilibili_notes.md 的 PoC 12 条一致)
BODY_SECTIONS = ["经验", "适用条件", "操作建议", "反例 / 边界"]


@dataclass
class WisdomCard:
    id: str
    title: str
    status: str = "draft"            # draft | approved | rejected
    quality_score: float = 0.0       # LLM 自评 0-1, 仅排序待审
    confidence: str = "中"            # 高/中/低, 经验置信
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    body: str = ""                   # 4 段式正文 markdown
    corroborates: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    created: str = ""
    reviewed_by: Optional[str] = None

    def to_markdown(self) -> str:
        fm = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "quality_score": self.quality_score,
            "confidence": self.confidence,
            "tags": self.tags,
            "source": self.source,
            "corroborates": self.corroborates,
            "conflicts": self.conflicts,
            "created": self.created,
            "reviewed_by": self.reviewed_by,
        }
        fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{self.body.strip()}\n"

    @classmethod
    def from_markdown(cls, text: str) -> "WisdomCard":
        if not text.lstrip().startswith("---"):
            raise ValueError("WisdomCard markdown missing YAML frontmatter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("WisdomCard markdown malformed frontmatter fences")
        fm = yaml.safe_load(parts[1]) or {}
        return cls(
            id=fm.get("id", ""),
            title=fm.get("title", ""),
            status=fm.get("status", "draft"),
            quality_score=float(fm.get("quality_score") or 0.0),
            confidence=fm.get("confidence", "中"),
            tags=fm.get("tags") or [],
            source=fm.get("source") or {},
            body=parts[2].strip(),
            corroborates=fm.get("corroborates") or [],
            conflicts=fm.get("conflicts") or [],
            created=fm.get("created", ""),
            reviewed_by=fm.get("reviewed_by"),
        )
