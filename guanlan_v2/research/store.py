# -*- coding: utf-8 -*-
"""研究回路档案:runs(run 头+终态行)+ rounds(每轮一行)双 append-only JSONL。

照 screen/picks.py 三件套(P0 先例):模块级路径常量便于测试 monkeypatch;
append 吞异常返 bool(绝不阻断回路,调用方以 rounds_recorded 显形);
read 新在前/坏行跳过/limit 钳制。run 状态推导在读取时做:有终态行→done/error;
无终态行且非当前在跑→interrupted(9999 重启即中断,诚实显形,无需启动扫描)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

RUNS_PATH = Path(__file__).resolve().parents[2] / "var" / "research_runs.jsonl"
ROUNDS_PATH = Path(__file__).resolve().parents[2] / "var" / "research_rounds.jsonl"


def _append(path: Path, record: Dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001 — 落盘失败不阻断回路,调用方显形
        return False


def append_run(record: Dict[str, Any]) -> bool:
    """append 一行 run 事件(kind=start|end)。"""
    return _append(RUNS_PATH, record)


def append_round(record: Dict[str, Any]) -> bool:
    """append 一行轮次记录。"""
    return _append(ROUNDS_PATH, record)


def _read_lines(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        if not path.exists():
            return out
        for ln in path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                out.append(json.loads(ln))
            except Exception:  # noqa: BLE001 — 坏行跳过
                continue
    except Exception:  # noqa: BLE001 — 读失败=已收集的(或空),诚实降级
        return out
    return out


def read_runs(limit: int = 20, running_run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """合并 start/end 行 → 每 run 一条(新在前,按 start 出现序)。"""
    cap = max(1, min(int(limit or 20), 100))
    runs: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in _read_lines(RUNS_PATH):
        rid = str(r.get("run_id") or "")
        if not rid:
            continue
        if rid not in runs:
            runs[rid] = {}
            order.append(rid)
        runs[rid].update({k: v for k, v in r.items() if k != "kind"})
        if r.get("kind") == "end":
            runs[rid]["_ended"] = True
    out: List[Dict[str, Any]] = []
    for rid in reversed(order):
        row = runs[rid]
        if row.pop("_ended", False):
            row["status"] = "done" if row.get("ok") else "error"
        elif rid == running_run_id:
            row["status"] = "running"
        else:
            row["status"] = "interrupted"
        out.append(row)
        if len(out) >= cap:
            break
    return out


def read_rounds(run_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """读轮次行(新在前;可按 run_id 过滤)。"""
    cap = max(1, min(int(limit or 50), 200))
    out: List[Dict[str, Any]] = []
    for r in reversed(_read_lines(ROUNDS_PATH)):
        if run_id and str(r.get("run_id") or "") != run_id:
            continue
        out.append(r)
        if len(out) >= cap:
            break
    return out
