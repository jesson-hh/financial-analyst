"""经验卡存储(guanlan 自有)。

复制改造自引擎 ``financial_analyst/wisdom/store.py``:保留"状态即目录"
(draft/approved/rejected 各放对应 status 的卡)与 ``EV-NNN`` 自增 id,
但落 ``Card``(UI 形状),且根目录走 guanlan 自有的 ``GUANLAN_WISDOM_ROOT``
——经验卡是 guanlan 自有应用数据,不是 stock_data,不经 get_data_paths。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from guanlan_v2.cards.card import Card

_STATUSES = ("draft", "approved", "rejected")
_ID_RE = re.compile(r"EV-(\d+)")


def _default_root() -> Path:
    """解析卡存储根:

        1. $GUANLAN_WISDOM_ROOT            (显式覆盖)
        2. <repo>/.data/wisdom             (默认, store.py 上溯三级到 guanlan-v2 根)
    """
    env = os.environ.get("GUANLAN_WISDOM_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    # store.py 在 guanlan_v2/cards/ 下 → parent×3 = guanlan-v2 仓库根
    return Path(__file__).resolve().parent.parent.parent / ".data" / "wisdom"


class CardStore:
    """经验卡存储. 状态即目录: draft/ approved/ rejected/ 各放对应 status 的卡."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_root()
        for s in _STATUSES:
            (self.root / s).mkdir(parents=True, exist_ok=True)

    def _path_for(self, card: Card) -> Path:
        return self.root / card.status / f"{card.id}.md"

    def save(self, card: Card) -> Path:
        p = self._path_for(card)
        p.write_text(card.to_markdown(), encoding="utf-8")
        return p

    def load(self, card_id: str) -> Card:
        for s in _STATUSES:
            p = self.root / s / f"{card_id}.md"
            if p.exists():
                return Card.from_markdown(p.read_text(encoding="utf-8"))
        raise KeyError(card_id)

    def list_by_status(self, status: str) -> list[Card]:
        d = self.root / status
        if not d.is_dir():
            return []
        return [Card.from_markdown(p.read_text(encoding="utf-8"))
                for p in sorted(d.glob("*.md"))]

    def list_all(self) -> list[Card]:
        out: list[Card] = []
        for s in _STATUSES:
            out.extend(self.list_by_status(s))
        return out

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
