"""用户炼出的因子库: 持久化 DSL 字符串, 加载时 compile_factor 重建并注册 (family='user')。"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _default_factors_root() -> Path:
    home = os.environ.get("FINANCIAL_ANALYST_HOME")
    base = Path(home) if home else (Path.home() / ".financial-analyst")
    return base / "factors"


class UserFactorStore:
    """JSON-backed store of user factors. Each entry:
    {name, family, expr, description, parsed, created, kpis}. ``expr`` (the DSL string)
    is the source of truth — compute fns are recompiled on load (AlphaSpec.compute can't
    be serialized)."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_factors_root()
        self.path = self.root / "user_factors.json"

    def load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("user_factors.json 损坏, 当空处理: %s", e)
            return []

    def save(self, entries: List[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def _unique_name(self, name: str, taken: set) -> str:
        name = name or "usr_factor"
        if name not in taken:
            return name
        i = 2
        while f"{name}_{i}" in taken:
            i += 1
        return f"{name}_{i}"

    def add(self, entry: dict) -> dict:
        entries = self.load()
        entry = dict(entry)
        entry["name"] = self._unique_name(entry.get("name", ""), {e["name"] for e in entries})
        entries.append(entry)
        self.save(entries)
        self.register_one(entry)
        return entry

    def list(self) -> List[dict]:
        return self.load()

    def remove(self, name: str) -> bool:
        entries = self.load()
        kept = [e for e in entries if e.get("name") != name]
        if len(kept) == len(entries):
            return False
        self.save(kept)
        from financial_analyst.factors.zoo import registry as _reg
        _reg._REGISTRY.pop(name, None)
        return True

    def register_one(self, entry: dict) -> None:
        from financial_analyst.factors.zoo.expr import compile_factor
        from financial_analyst.factors.zoo.registry import AlphaSpec, register, _REGISTRY
        name = entry["name"]
        _REGISTRY.pop(name, None)  # replace: recompiled compute is a new fn (avoids frozen-collision raise)
        register(AlphaSpec(name=name, family="user",
                           description=entry.get("description", ""),
                           formula_text=entry.get("expr", ""),
                           compute=compile_factor(entry["expr"])))

    def register_all(self) -> int:
        n = 0
        for entry in self.load():
            try:
                self.register_one(entry)
                n += 1
            except Exception as e:
                logger.warning("user 因子 %r 重建失败: %s", entry.get("name"), e)
        return n
