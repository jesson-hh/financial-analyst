"""置信度校准(0612演习修复#3)。

把研判记录的 LLM 置信度钉到可证伪统计上:各置信档的真实 N 日方向命中率。
口径(诚实声明,所有展示处沿用):
  基准 = asof 当日(或其后首根)收盘价进、horizon 根 bar 后收盘价出;
  命中 = 买入→区间收益>0 / 卖出→区间收益<0;不含交易成本;
  「观望」不可证伪 → 不计入;出场 bar 未到(未成熟)→ 剔除,等数据长出来。
纯函数零 IO:records 与收盘序列由调用方注入(端点层负责读 jsonl + 拉日线)。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

# (下界含, 上界不含, 档名)
_BUCKETS = [(0, 60, "<60"), (60, 70, "60-69"), (70, 80, "70-79"), (80, 101, "80+")]


def bucket_of(conf: float) -> Optional[str]:
    for lo, hi, name in _BUCKETS:
        if lo <= conf < hi:
            return name
    return None


def evaluate(records: List[Dict[str, Any]],
             closes_by_code: Dict[str, Sequence[Tuple[str, float]]],
             horizon: int = 5) -> List[Dict[str, Any]]:
    """成熟记录打分:返回 [{code,direction,confidence,asof,ret,hit}]。

    closes_by_code: {代码: [(YYYY-MM-DD, close), ...] 升序};缺码/缺值/未成熟/观望 → 剔除。
    """
    out: List[Dict[str, Any]] = []
    for r in records or []:
        try:
            if r.get("kind") != "decide":
                continue
            d = r.get("direction")
            if d not in ("买入", "卖出"):
                continue
            conf = r.get("confidence")
            if not isinstance(conf, (int, float)):
                continue
            code = str(r.get("code", "")).upper()
            asof = str(r.get("asof", ""))[:10]
            series = closes_by_code.get(code)
            if not series or not asof:
                continue
            idx = next((i for i, (dt, _) in enumerate(series) if dt >= asof), None)
            if idx is None or idx + horizon >= len(series):
                continue   # asof 晚于全序列 / 出场 bar 未到 → 未成熟
            entry, exitp = float(series[idx][1]), float(series[idx + horizon][1])
            if not entry:
                continue
            ret = exitp / entry - 1.0
            hit = (ret > 0) if d == "买入" else (ret < 0)
            out.append({"code": code, "direction": d, "confidence": float(conf),
                        "asof": asof, "ret": ret, "hit": hit})
        except Exception:  # noqa: BLE001 — 单条坏记录跳过,不挡整体
            continue
    return out


def calibration_table(evaluated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按置信档聚合:[{bucket, n, hits, hit_rate}](空档 n=0 hit_rate=None)。"""
    table = []
    for lo, hi, name in _BUCKETS:
        rows = [e for e in (evaluated or []) if lo <= e["confidence"] < hi]
        hits = sum(1 for e in rows if e["hit"])
        table.append({"bucket": name, "n": len(rows), "hits": hits,
                      "hit_rate": (hits / len(rows)) if rows else None})
    return table
