from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from financial_analyst.wisdom.card import WisdomCard

_STATUSES = ("draft", "approved", "rejected")
_ID_RE = re.compile(r"EV-(\d+)")


def _default_wisdom_root() -> Path:
    """解析 wisdom 卡存储根, 镜像 ``memory_paths.default_memory_root()`` 的查找顺序,
    使 wisdom 与 memories 落在同一个 financial-analyst home 下:

        1. $FINANCIAL_ANALYST_HOME/wisdom   (显式 project-home 覆盖)
        2. <cwd>/wisdom                     (dev / 源码 checkout, 存在才用)
        3. ~/.financial-analyst/wisdom      (pip 用户默认)

    与 memories 不同, wisdom 是用户自行沉淀的 (非 bundled), 故不做 seeding;
    也刻意不复用 ``default_memory_root()`` 以免触发 memories 的 seed 副作用.
    """
    env = os.environ.get("FINANCIAL_ANALYST_HOME", "").strip()
    if env:
        return Path(env).expanduser() / "wisdom"
    cwd_wisdom = Path.cwd() / "wisdom"
    if cwd_wisdom.is_dir():
        return cwd_wisdom
    return Path.home() / ".financial-analyst" / "wisdom"


class WisdomStore:
    """经验卡存储. 状态即目录: draft/ approved/ rejected/ 各放对应 status 的卡."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_wisdom_root()
        for s in _STATUSES:
            (self.root / s).mkdir(parents=True, exist_ok=True)

    def _path_for(self, card: WisdomCard) -> Path:
        return self.root / card.status / f"{card.id}.md"

    def save(self, card: WisdomCard) -> Path:
        p = self._path_for(card)
        p.write_text(card.to_markdown(), encoding="utf-8")
        return p

    def load(self, card_id: str) -> WisdomCard:
        for s in _STATUSES:
            p = self.root / s / f"{card_id}.md"
            if p.exists():
                return WisdomCard.from_markdown(p.read_text(encoding="utf-8"))
        raise KeyError(card_id)

    def list_by_status(self, status: str) -> list[WisdomCard]:
        d = self.root / status
        if not d.is_dir():
            return []
        return [WisdomCard.from_markdown(p.read_text(encoding="utf-8"))
                for p in sorted(d.glob("*.md"))]

    def set_status(self, card_id: str, status: str,
                   reviewed_by: Optional[str] = None) -> None:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status}")
        card = self.load(card_id)
        old_path = self.root / card.status / f"{card_id}.md"
        card.status = status
        if reviewed_by is not None:
            card.reviewed_by = reviewed_by
        new_path = self.save(card)
        if old_path.exists() and old_path != new_path:
            old_path.unlink()

    def next_id(self) -> str:
        mx = 0
        for s in _STATUSES:
            for p in (self.root / s).glob("EV-*.md"):
                m = _ID_RE.match(p.stem)
                if m:
                    mx = max(mx, int(m.group(1)))
        return f"EV-{mx + 1:03d}"
