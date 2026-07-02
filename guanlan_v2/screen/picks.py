# -*- coding: utf-8 -*-
"""选股 picks 档案:每次 /screen/run 主路径落一行(append-only JSONL)——闭环的「跟踪对象」。

snapshot=true 的行是「正式选股」(P1 收益跟踪只认它们);其余为实验记录。
纯函数 + 模块级路径常量(便于测试 monkeypatch,对齐 seats/api.py _LEDGER_LOG 先例)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

PICKS_PATH = Path(__file__).resolve().parents[2] / "var" / "screen_picks.jsonl"


def append_pick(record: Dict[str, Any]) -> bool:
    """append 一行;任何异常吞掉回 False(绝不阻断选股),由调用方以 picks_recorded 显形。"""
    try:
        PICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with PICKS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001 — 落盘失败不阻断选股,调用方 picks_recorded=False 显形
        return False


def read_picks(snapshot_only: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    """读尾部 limit 条(新在前);坏行跳过(诚实容错);snapshot_only 只回正式选股行。"""
    cap = max(1, min(int(limit or 50), 500))
    out: List[Dict[str, Any]] = []
    try:
        if not PICKS_PATH.exists():
            return out
        for ln in reversed(PICKS_PATH.read_text(encoding="utf-8").splitlines()):
            if not ln.strip():
                continue
            try:
                r = json.loads(ln)
            except Exception:  # noqa: BLE001 — 坏行跳过
                continue
            if snapshot_only and not r.get("snapshot"):
                continue
            out.append(r)
            if len(out) >= cap:
                break
    except Exception:  # noqa: BLE001 — 读失败 = 已收集的(或空),诚实降级
        return out
    return out
