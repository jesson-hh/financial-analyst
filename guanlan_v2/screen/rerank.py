# -*- coding: utf-8 -*-
"""P6′ 行业判断上下文重排层:上下文包 → LLM 一次整批 → top-N 内自由重排(带理由)。

红线:数据榜与正式 picks 零变化(本模块只产 rerank 块,落档由 rescore 编排);
LLM 失败/校验失败 → {"ok": False, "reason": ...} 显形,绝不部分采用、绝不编序。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

_LESSON_PAT = re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \((行业·[^)]*)\) (.+)$")
_STANCES = ("顺风", "逆风", "中性")

_SYSTEM = (
    "你是A股行业研究员,基于行业材料对候选票的现有量化排名做行业视角重排。"
    "规则:只能重排给定候选票,绝不新增/删除/重复;每票给 stance(顺风/逆风/中性)"
    "与一句具体 reason(引用链环/新闻/大盘/教训中的事实);材料不支持判断时保持原名次附近并给中性。"
    '只输出 JSON:{"order":[{"code":"...","stance":"...","reason":"..."}...],"overall":"一句总览"};'
    "order 按新排名从第1名开始,必须包含全部候选票各一次。")


# ── 桥(独立小函数便于 monkeypatch,仓例 rescore.py §桥)──────────────────

def _board_summary() -> Dict[str, Any]:
    """链环景气全景摘要;board 坏 → {ok:False}(上游诚实失败传导)。"""
    from guanlan_v2.industry.aggregate import build_board
    b = build_board()
    if not b.get("ok"):
        return {"ok": False, "reason": b.get("reason"), "segments": [], "snapshot": {}}
    segs = []
    for s in (b.get("segments") or []):
        if not isinstance(s, dict) or s.get("adjacent"):
            continue
        segs.append({"name": s.get("display_name") or s.get("name"),
                     "research": (s.get("research") or {}).get("score"),
                     "therm": s.get("therm"), "quadrant": s.get("quadrant")})
    corpus = dict(((b.get("freshness") or {}).get("corpus")) or {})
    return {"ok": True, "segments": segs,
            "snapshot": {"latest_publish_ts": corpus.get("latest_publish_ts"),
                         "n_docs": corpus.get("n_docs")}}


def _call_llm(system: str, user: str) -> Dict[str, Any]:
    """daemon 线程内同步跑异步 _call_llm_json(仓内已验模式,rescore._call_news 同款)。"""
    import asyncio
    from guanlan_v2.screen.llm import _call_llm_json
    return asyncio.run(_call_llm_json(system, user, timeout=120.0, temperature=0.2))


# ── 纯函数 ───────────────────────────────────────────────────────────────

def read_industry_lessons(board_segs: Set[str], k: int = 5) -> Tuple[List[str], List[str]]:
    """按今天盘面行业相关性召回帷幄「行业·」keyed 教训。

    命中 = 某盘面 seg 与教训 key(去『行业·』前缀、非空)双向子串相含(容 seg⊂key 与 key⊂seg)。
    返回 (lessons, matched_segs);board_segs 空/无记忆/不可读/无命中 → ([], []) 诚实降级不回填。
    lessons 保持既有格式 "(行业·XXX) 正文";matched_segs = 被保留的 k 条命中到的盘面 seg 去重升序。"""
    segs = {s.strip() for s in (board_segs or set()) if s and s.strip()}
    if not segs:
        return [], []
    try:
        from guanlan_v2.console.tools import _MEMORY_PATH
        lines = _MEMORY_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 — 无/不可读记忆诚实降级,绝不挡重排
        return [], []
    hits: List[Tuple[str, str]] = []          # (key_去前缀, 格式化行)
    for ln in lines:
        m = _LESSON_PAT.match(ln.strip())
        if not m:
            continue
        key = m.group(1).removeprefix("行业·").strip()
        if not key:                            # 空 key(如「行业·」)跳过,防空串全命中
            continue
        if any((s in key) or (key in s) for s in segs):
            hits.append((key, f"({m.group(1)}) {m.group(2)}"))
    kept = hits[-max(0, int(k)):] if k else []
    lessons = [line for _, line in kept]
    matched = sorted({s for key, _ in kept for s in segs if (s in key) or (key in s)})
    return lessons, matched


def build_context_pack(ranked_rows: List[dict], board: Dict[str, Any],
                       market: Optional[Dict[str, Any]],
                       lessons: List[str]) -> Dict[str, Any]:
    """行业材料上下文包;不含任何因子明细(行业判断只用行业材料,边界干净)。"""
    tickets = []
    for r in ranked_rows:
        ch, nw = r.get("chain"), r.get("news")
        tickets.append({
            "code": r.get("code"), "rank": r.get("rank"), "v4pct": r.get("v4pct"),
            "chain": ({"seg_name": ch.get("seg_name"), "chain": ch.get("chain"),
                       "quadrant": ch.get("quadrant"), "research": ch.get("research"),
                       "therm": ch.get("therm")} if isinstance(ch, dict) else "不在链上"),
            "news": ({"tag": nw.get("tag"), "read": nw.get("read")}
                     if isinstance(nw, dict) else "无新闻")})
    return {"tickets": tickets, "board": board,
            "market": {"market_read": (market or {}).get("market_read"),
                       "market_tilt": (market or {}).get("market_tilt")},
            "lessons": list(lessons or [])}


def build_prompt(pack: Dict[str, Any]) -> Tuple[str, str]:
    user = ("行业材料(JSON):\n" + json.dumps(pack, ensure_ascii=False)
            + "\n\n请输出重排 JSON。")
    return _SYSTEM, user


def validate_order(codes_in: List[str], order: List[dict]) -> Tuple[bool, str]:
    """硬校验:票集合逐一相等/无重复/stance 合法/reason 非空;违者整体拒。"""
    if not isinstance(order, list) or not order:
        return False, "order 缺失或为空"
    codes_out = [str((o or {}).get("code") or "") for o in order]
    if len(codes_out) != len(set(codes_out)):
        return False, "order 含重复票"
    want = {str(c) for c in codes_in}
    got = set(codes_out)
    if got != want:
        return False, f"票集合不等: 缺{sorted(want - got)[:3]} 多{sorted(got - want)[:3]}"
    for o in order:
        if str((o or {}).get("stance") or "") not in _STANCES:
            return False, f"stance 非法: {o.get('code')}={o.get('stance')}"
        if not str((o or {}).get("reason") or "").strip():
            return False, f"reason 为空: {o.get('code')}"
    return True, ""


def run_rerank(rows: List[dict], market: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """重排主体(rescore rows 就绪后在同一 daemon 线程调用)。任何失败 → ok:false 显形。"""
    t0 = time.time()
    try:
        board = _board_summary()
        if not board.get("ok"):
            return {"ok": False, "reason": f"产业链板不可用: {board.get('reason')}"}
        ranked = [dict(r, rank=i + 1) for i, r in enumerate(rows)]
        board_segs = {r["chain"]["seg_name"] for r in ranked
                      if isinstance(r.get("chain"), dict) and r["chain"].get("seg_name")}
        lessons, matched_segs = read_industry_lessons(board_segs, k=5)
        pack = build_context_pack(ranked, board, market, lessons)
        system, user = build_prompt(pack)
        resp = _call_llm(system, user)
        if not resp.get("ok"):
            return {"ok": False, "reason": f"LLM 失败: {resp.get('reason')}"}
        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
        order = data.get("order")
        ok, why = validate_order([r["code"] for r in ranked], order or [])
        if not ok:
            return {"ok": False, "reason": f"rerank_failed: {why}"}
        pos = {str(o["code"]): i + 1 for i, o in enumerate(order)}
        meta = {str(o["code"]): o for o in order}
        out_rows = [{"code": r["code"], "rank_before": r["rank"],
                     "rank_after": pos[str(r["code"])],
                     "stance": meta[str(r["code"])]["stance"],
                     "reason": str(meta[str(r["code"])]["reason"]).strip()[:160]}
                    for r in ranked]
        return {"ok": True, "model": resp.get("model"),
                "overall": str(data.get("overall") or "")[:200],
                "lessons_injected": len(lessons),
                "matched_segs": sorted(matched_segs),
                "board_snapshot": dict(board.get("snapshot") or {}),
                "elapsed_sec": round(time.time() - t0, 1), "rows": out_rows}
    except Exception as exc:  # noqa: BLE001 — 重排层任何异常绝不炸 rescore run
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
