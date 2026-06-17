# -*- coding: utf-8 -*-
"""重生 _provenance.json —— 对其现有键(已登记的 vendored 文件)重算 SHA256 并回写。

用法(任一 vendored 产物刷新后跑,如 v4/主线/节奏 重新 vendor 之后):
    G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.strategy.regen_provenance

只更新**已登记文件**的哈希,不新增/删除条目(要增删 vendored 文件,先手动改 _provenance.json 键集再跑本脚本)。
漂移哨兵 tests/test_strategy_provenance.py 跑绿即对齐。
"""
from __future__ import annotations

import hashlib
import json

from guanlan_v2.strategy.paths import PROVENANCE_JSON


def _sha256(p) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest().upper()


def regen() -> dict:
    base = PROVENANCE_JSON.parent
    rows = json.loads(PROVENANCE_JSON.read_text(encoding="utf-8-sig"))
    out = {}
    for rel in rows:
        p = base / rel
        if not p.exists():
            raise FileNotFoundError(f"vendored 文件缺失: {rel}")
        out[rel] = _sha256(p)
    PROVENANCE_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=4), encoding="utf-8")
    return out


if __name__ == "__main__":
    for rel, h in regen().items():
        print(f"{h[:16]}  {rel}")
