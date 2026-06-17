"""观澜自有「报告库」(reports)后端 —— 把工作流 run 结果存盘 + 浏览/重看/删除。

端点(挂在无 prefix 的 router 上,由 server.py include):
  - ``POST /report/save``      存一份 run 结果 → ``store/<id>.json``,返回 {ok,id,name,ts}
  - ``GET  /report/list``      列出全部(仅摘要,不含 result 大对象)
  - ``GET  /report/get/{rid}`` 取单份完整记录(含 result,供前端载回抽屉重看)
  - ``POST /report/delete``    删一份

落点 = ``guanlan_v2/reports/store/*.json``(**仓内自有报告 JSON**,非 engine、不拷 stocks 数据)。
id 经正则白名单,杜绝路径穿越。诚实失败 ok:False + reason(HTTP 200),绝不抛 500。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# 报告落盘目录(仓内,随模块走)。
_STORE = Path(__file__).resolve().parent / "store"
# id 白名单:仅字母数字/下划线/连字符,长度 1-64 → 拼路径前必过,防 ../ 穿越。
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _ensure_store() -> Path:
    _STORE.mkdir(parents=True, exist_ok=True)
    return _STORE


class ReportSaveIn(BaseModel):
    """``POST /report/save`` 入参:前端把抽屉里那个 result 对象(整份)+ 元信息 POST 来。"""

    name: Optional[str] = None          # 报告名(留空 → 用 label / 时间)
    universe: Optional[str] = None
    label: Optional[str] = None
    method: Optional[str] = None        # report2 / backtest_vector / portfolio_build / tsic …
    workflow_name: Optional[str] = None  # 产出该报告的工作流名(重看时回填顶栏)
    graph: Optional[Dict[str, Any]] = None  # 产出快照 {nodes, edges}(重看时铺回画布 → "打开报告即回到工作流")
    kpi: Dict[str, Any] = Field(default_factory=dict)     # 关键指标快照(选填,列表页直接显示)
    result: Dict[str, Any] = Field(default_factory=dict)  # 完整 run 结果(抽屉那个对象,重看用)


class ReportDeleteIn(BaseModel):
    id: str = ""


def _summary(rec: Dict[str, Any]) -> Dict[str, Any]:
    """列表项摘要(剔除 result 大对象,控体积)。"""
    g = rec.get("graph") or {}
    return {
        "id": rec.get("id"),
        "name": rec.get("name"),
        "ts": rec.get("ts"),
        "universe": rec.get("universe"),
        "label": rec.get("label"),
        "method": rec.get("method"),
        "workflow_name": rec.get("workflow_name"),
        "has_graph": bool(isinstance(g.get("nodes"), list) and g.get("nodes")),
        "kpi": rec.get("kpi") or {},
    }


def build_reports_router() -> APIRouter:
    """工厂式构造无 prefix 的 reports 路由组(对齐 cards/seats/factorlib/workflow/screen)。"""
    router = APIRouter()

    @router.post("/report/save")
    def report_save(body: ReportSaveIn):
        """存一份报告 → store/<id>.json。id = UTC 时间戳 + 6 位随机,天然有序且唯一。"""
        try:
            store = _ensure_store()
            rid = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
            ts = datetime.now().isoformat(timespec="seconds")
            res = body.result if isinstance(body.result, dict) else {}
            name = (body.name or "").strip() or (body.label or "").strip() or ("报告 " + ts)
            # 产出快照:仅当带 nodes 列表才存,否则置 None(防脏数据撑大文件)。
            g = body.graph if (isinstance(body.graph, dict)
                               and isinstance(body.graph.get("nodes"), list)) else None
            rec = {
                "id": rid,
                "name": name,
                "ts": ts,
                "universe": body.universe or res.get("_universe"),
                "label": body.label or res.get("_label"),
                "method": body.method or res.get("method"),
                "workflow_name": body.workflow_name or res.get("_wfName"),
                "graph": g,
                "kpi": body.kpi or {},
                "result": res,
            }
            (store / f"{rid}.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
            return JSONResponse({"ok": True, "id": rid, "name": name, "ts": ts})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"save_error: {type(exc).__name__}: {exc}"})

    @router.get("/report/list")
    def report_list():
        """列出全部报告(摘要,按时间倒序)。坏文件跳过,不阻断。"""
        try:
            store = _ensure_store()
            out: List[Dict[str, Any]] = []
            for p in store.glob("*.json"):
                try:
                    out.append(_summary(json.loads(p.read_text(encoding="utf-8"))))
                except Exception:  # noqa: BLE001  —— 单份坏文件不拖垮列表
                    continue
            out.sort(key=lambda r: (r.get("ts") or ""), reverse=True)
            return JSONResponse({"ok": True, "reports": out, "n": len(out)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"list_error: {type(exc).__name__}: {exc}", "reports": []})

    @router.get("/report/get/{rid}")
    def report_get(rid: str):
        """取单份完整记录(含 result,供前端载回抽屉重看)。"""
        if not _ID_RE.match(rid or ""):
            return JSONResponse({"ok": False, "reason": "bad_id"})
        try:
            p = _ensure_store() / f"{rid}.json"
            if not p.exists():
                return JSONResponse({"ok": False, "reason": "not_found"})
            rec = json.loads(p.read_text(encoding="utf-8"))
            rec["ok"] = True
            return JSONResponse(rec)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"get_error: {type(exc).__name__}: {exc}"})

    @router.post("/report/delete")
    def report_delete(body: ReportDeleteIn):
        """删一份报告(id 过白名单;不存在也返回 ok,幂等)。"""
        rid = (body.id or "").strip()
        if not _ID_RE.match(rid):
            return JSONResponse({"ok": False, "reason": "bad_id"})
        try:
            p = _ensure_store() / f"{rid}.json"
            if p.exists():
                p.unlink()
            return JSONResponse({"ok": True, "id": rid})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"delete_error: {type(exc).__name__}: {exc}"})

    return router
