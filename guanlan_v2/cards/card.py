"""经验卡数据模型(guanlan 自有,对齐 cards UI 形状)。

复制改造自引擎 ``financial_analyst/wisdom/card.py``:保留 markdown+YAML frontmatter
的"状态即目录"落盘机制,但把字段从引擎的 quality_score/body/corroborates/conflicts
换成 cards UI(validation.jsx)实际用的超集 —— cat/verdict/conf/ic/expr/insight/
src/refs。只依赖 PyYAML。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

# 与 cards UI 一致的取值域(仅文档/校验参考, 不强制)
CATS = ("价量", "资金", "基本面", "风格", "情绪", "另类", "其他")
VERDICTS = ("通过", "存疑", "驳回")
STATUSES = ("draft", "approved", "rejected")


@dataclass
class Card:
    id: str
    title: str
    status: str = "approved"          # draft | approved | rejected(决定落哪个目录)
    cat: str = "其他"                  # 价量/资金/基本面/风格/情绪/另类/其他
    tags: list[str] = field(default_factory=list)
    verdict: str = "存疑"              # 通过 | 存疑 | 驳回(UI 结论)
    conf: int = 0                     # 0-100 整数(UI 置信度)
    ic: str = ""                      # 字符串保形, 如 "0.043"
    expr: str = ""                    # 因子表达式
    insight: str = ""                 # 洞察正文(= markdown body)
    src: str = ""                     # 来源类型: 研报/热帖/复盘/快讯/自定义
    refs: list[str] = field(default_factory=list)  # 关联 research/factor id
    created: str = ""
    reviewed_by: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """端点序列化用(含 insight)。"""
        return {
            "id": self.id, "title": self.title, "status": self.status,
            "cat": self.cat, "tags": self.tags, "verdict": self.verdict,
            "conf": self.conf, "ic": self.ic, "expr": self.expr,
            "insight": self.insight, "src": self.src, "refs": self.refs,
            "created": self.created, "reviewed_by": self.reviewed_by,
        }

    def to_markdown(self) -> str:
        fm = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "cat": self.cat,
            "tags": self.tags,
            "verdict": self.verdict,
            "conf": self.conf,
            "ic": self.ic,          # PyYAML 会给 "0.043" 这类"像数字的字符串"加引号保形
            "expr": self.expr,
            "src": self.src,
            "refs": self.refs,
            "created": self.created,
            "reviewed_by": self.reviewed_by,
        }
        fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{self.insight.strip()}\n"

    @classmethod
    def from_markdown(cls, text: str) -> "Card":
        if not text.lstrip().startswith("---"):
            raise ValueError("Card markdown missing YAML frontmatter")
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("Card markdown malformed frontmatter fences")
        fm = yaml.safe_load(parts[1]) or {}
        return cls(
            id=str(fm.get("id", "")),
            title=str(fm.get("title", "")),
            status=fm.get("status", "approved"),
            cat=fm.get("cat", "其他"),
            tags=fm.get("tags") or [],
            verdict=fm.get("verdict", "存疑"),
            conf=int(fm.get("conf") or 0),
            ic=str(fm.get("ic") if fm.get("ic") is not None else ""),
            expr=str(fm.get("expr") or ""),
            insight=parts[2].strip(),
            src=fm.get("src", ""),
            refs=fm.get("refs") or [],
            created=str(fm.get("created", "")),
            reviewed_by=fm.get("reviewed_by"),
        )
