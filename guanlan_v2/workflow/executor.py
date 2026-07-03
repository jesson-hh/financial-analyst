# -*- coding: utf-8 -*-
"""后端图执行器(P4):画布 graph JSON 服务端整图执行,直调模块级计算函数。

镜像前端 ui/factor/workflow.jsx runGraph(topoOrder:715/_universeForNode:747/_universeOf:246/
NODE_EXEC:311),计算不走 HTTP——全部直调 workflow/api.py 模块级函数(P2 三道菜直调的推广),
与画布/帷幄同一批函数口径逐位一致。纯同步:只在 daemon 线程(研究回路)或 FastAPI 线程池
(/workflow/run)里跑,绝不进事件循环协程(仓级红线)。

诚实合约:节点失败记 node_errors 继续跑(下游缺输入自然失败显形);无主终端 → ok:False;
metrics 抽不出 → None + warning,绝不编数。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional

_TERMINAL_PRIORITY = ("backtest", "analysis", "iccalc")   # 主终端优先级(过门指标来源)
_DIAG_TYPES = {"tsic", "event", "relstat", "risk", "garch", "attrib", "tvbeta"}  # 照跑存档不过门


def _resp_json(resp: Any) -> Dict[str, Any]:
    """JSONResponse → dict(镜像 research/loop.py:_resp_json)。"""
    if isinstance(resp, dict):
        return resp
    try:
        return json.loads(bytes(resp.body).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"响应解析失败: {e}"}


def _universe_of(params: Dict[str, Any]) -> str:
    """source 节点 params → universe(镜像 workflow.jsx:_universeOf:246)。"""
    u = str((params or {}).get("universe") or "").strip()
    if u and u != "自动":
        return u
    code = str((params or {}).get("code") or "").lower()
    scope = (params or {}).get("scope") or ""
    if "500" in code:
        return "csi500"
    if "800" in code:
        return "csi800"
    if "300" in code:
        return "csi300_active"
    if scope == "全市场":
        return "all"
    return "csi_fast"


def topo_order(nodes: List[dict], edges: List[dict]) -> List[str]:
    """Kahn 拓扑,入度0/同层按 x 决序;有环兜底按 x 追加(镜像 workflow.jsx:715-738)。"""
    ids = [n["id"] for n in nodes]
    xmap = {n["id"]: n.get("x", 0) for n in nodes}
    indeg = {i: 0 for i in ids}
    adj: Dict[str, List[str]] = {i: [] for i in ids}
    for e in edges:
        f, t = e["from"][0], e["to"][0]
        if f in indeg and t in indeg:
            indeg[t] += 1
            adj[f].append(t)
    q = sorted([i for i in ids if indeg[i] == 0], key=lambda i: xmap[i])
    order: List[str] = []
    while q:
        cur = q.pop(0)
        order.append(cur)
        nxt = []
        for t in adj[cur]:
            indeg[t] -= 1
            if indeg[t] == 0:
                nxt.append(t)
        q.extend(sorted(nxt, key=lambda i: xmap[i]))
    if len(order) < len(ids):                                  # 环兜底
        order.extend(sorted([i for i in ids if i not in set(order)], key=lambda i: xmap[i]))
    return order


def universe_for_node(nid: str, nodes: List[dict], edges: List[dict]) -> Dict[str, Any]:
    """沿入边 BFS 回溯最近上游 source(镜像 workflow.jsx:_universeForNode:747-765)。"""
    by_id = {n["id"]: n for n in nodes}
    seen = {nid}
    frontier = [nid]
    while frontier:
        nxt: List[str] = []
        for cur in frontier:
            for e in edges:
                if e["to"][0] == cur and e["from"][0] not in seen:
                    up = by_id.get(e["from"][0])
                    if up and up.get("type") == "source":
                        p = up.get("params") or {}
                        raw = str(p.get("codes") or "").replace("，", ",").replace("、", ",") \
                            .replace(";", ",")
                        codes = [c.strip() for c in raw.split(",") if c.strip()]
                        return {"universe": _universe_of(p),
                                "start": str(p.get("start") or "").strip(),
                                "end": str(p.get("end") or "").strip(),
                                "codes": codes,
                                "benchmark": str(p.get("benchmark") or "").strip(),
                                "leader": str(p.get("leader") or "").strip(),
                                "oos_frac": float(p.get("oos_frac") or 0) or 0.0,
                                "wf_refit": (p.get("wf_refit") == "是"), "wired": True}
                    seen.add(e["from"][0])
                    nxt.append(e["from"][0])
        frontier = nxt
    return {"universe": None, "start": "", "end": "", "codes": [], "benchmark": "",
            "leader": "", "oos_frac": 0.0, "wf_refit": False, "wired": False}


def graph_signature(graph: Dict[str, Any]) -> str:
    """规范化图签名(停滞守卫比较键):type+params+edges,不含 x/y(挪位置≠改图)。"""
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    cn = sorted([{"id": str(n.get("id")), "type": str(n.get("type")),
                  "params": (n.get("params") or {})} for n in nodes if isinstance(n, dict)],
                key=lambda d: d["id"])
    ce = sorted([json.dumps(e, sort_keys=True, ensure_ascii=False)
                 for e in edges if isinstance(e, dict)])
    blob = json.dumps({"nodes": cn, "edges": ce}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def metrics_of_terminal(payload: Dict[str, Any]) -> Dict[str, Any]:
    """终端载荷 → 六键指标(泛化 research/loop.py:_metrics_of;composite dict 先展开)。"""
    r = payload.get("composite") if isinstance(payload.get("composite"), dict) else payload
    hic = r.get("headline_ic") if isinstance(r.get("headline_ic"), dict) else {}
    ic = r.get("ic") if isinstance(r.get("ic"), dict) else {}
    met = r.get("metrics") if isinstance(r.get("metrics"), dict) else {}
    pf = r.get("portfolio") if isinstance(r.get("portfolio"), dict) else {}
    oos = r.get("oos") if isinstance(r.get("oos"), dict) else {}
    rank_ic = hic.get("rank_ic")
    if rank_ic is None:
        rank_ic = ic.get("rank_ic_mean")
    if rank_ic is None:
        rank_ic = met.get("rank_ic")
    return {"rank_ic": rank_ic, "sharpe": pf.get("sharpe"), "ann_return": pf.get("ann_return"),
            "oos_verdict": oos.get("verdict"), "n_dates": r.get("n_dates"),
            "factor": r.get("_label") or r.get("factor")}
