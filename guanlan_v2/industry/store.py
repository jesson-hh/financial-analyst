# -*- coding: utf-8 -*-
"""抽取库(append-only jsonl)+ ingest 状态(水位/失败清单/token 计量)。

坏行跳过不崩(chunks 表有 corrupt 历史的教训);state 原子写。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

_lock = threading.Lock()


def _store_dir() -> Path:
    d = os.environ.get("GL_INDUSTRY_STORE")
    p = Path(d) if d else Path(__file__).resolve().parent / "store"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _extractions_path() -> Path:
    return _store_dir() / "extractions.jsonl"


def _state_path() -> Path:
    return _store_dir() / "ingest_state.json"


def append_extraction(rec: dict) -> None:
    line = json.dumps(rec, ensure_ascii=False)
    with _lock:
        with open(_extractions_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")


def load_extractions(window_days: Optional[int] = None, now: Optional[str] = None) -> list:
    p = _extractions_path()
    if not p.exists():
        return []
    out = []
    cutoff = None
    if window_days is not None:
        import pandas as pd
        base = pd.Timestamp(now) if now else pd.Timestamp.now()
        cutoff = base - pd.Timedelta(days=window_days)
    for raw in p.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:  # noqa: BLE001 — 坏行跳过,诚实容错
            continue
        if cutoff is not None:
            ts = rec.get("publish_ts")
            try:
                import pandas as pd
                if ts is None or pd.Timestamp(str(ts)[:10]) < cutoff:
                    continue
            except Exception:  # noqa: BLE001
                continue
        out.append(rec)
    return out


_DEFAULT_STATE = {
    "watermark": None,
    "failed_docs": [],
    "totals": {"docs": 0, "prompt_tokens": 0, "completion_tokens": 0},
    "last_ingest_at": None,
}


def load_state() -> dict:
    p = _state_path()
    if not p.exists():
        return json.loads(json.dumps(_DEFAULT_STATE))
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — state 损坏按初始态,不崩
        return json.loads(json.dumps(_DEFAULT_STATE))
    for k, v in _DEFAULT_STATE.items():
        st.setdefault(k, json.loads(json.dumps(v)))
    return st


def save_state(state: dict) -> None:
    p = _state_path()
    tmp = p.parent / (p.name + ".tmp")     # 注意:不能用 with_suffix(会吃掉 .json)
    with _lock:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
