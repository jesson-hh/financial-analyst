# -*- coding: utf-8 -*-
"""因子库 REST(guanlan 自有,挂到薄壳 app 上)—— ``/factorlib/*``。

随 cards / seats 先例的工厂式:``build_factorlib_router()`` 返回 ``/factorlib`` 路由组。
库因子是 guanlan 自有的「迁移基础因子 + 自挖落点」(base/ + mined/,引擎 zoo-DSL 表达式),
经引擎 primitive(``factors/zoo/expr.py`` 的 ``validate_expr``/``compile_factor``)校验、并在
启动时注册进引擎运行期 zoo registry(见 ``register_library_factors``),故也会出现在引擎
``/factor/list`` 的 ``registered`` 里。本路由组额外暴露 guanlan 自有端点:

- ``GET  /factorlib/list``        库内因子清单(name/expr/family/source/origin/valid)
- ``GET  /factorlib/registered``  当前 zoo registry 里属库因子(library*)的已注册项
- ``POST /factorlib/validate``    校验任意表达式(zoo 或 Qlib 形)→ {ok, zoo_expr, reason?}

数据只经包内 JSON + 引擎 primitive;求值时引擎 panel 才经 ``get_data_paths`` 碰数据。
诚实失败:异常 → ``ok:False`` + reason,HTTP 200(前端降级,不抛 500)。
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from guanlan_v2.factorlib.store import LIBRARY_FAMILY, LibraryFactorStore
from guanlan_v2.factorlib.qlib_to_zoo import UnsupportedFactor, qlib_to_zoo


# 安全名:只允许字母/数字/下划线/连字符;其余(含 / \ . : 等路径穿越字符)折成 _。
_SAFE_NAME = re.compile(r"[^0-9A-Za-z_\-]+")


def _safe_filename(name: str) -> str:
    """把因子名 slug 成安全文件名(防路径穿越)。全非法字符 → 回退随机 uuid。"""
    s = _SAFE_NAME.sub("_", (name or "").strip()).strip("_")
    return s or ("factor_" + uuid.uuid4().hex[:8])


class ValidateIn(BaseModel):
    expr: str
    is_qlib: bool = False     # True → 先过 qlib_to_zoo 译写再校验


class SaveIn(BaseModel):
    name: str
    expr: str
    family: str = "library_mined"   # 默认带 library 前缀 → 进 /factorlib/registered
    description: str = ""
    source: str = ""
    is_qlib: bool = False
    meta: dict = Field(default_factory=dict)   # 展示用快照(_label/universe/ic…);store 忽略未知键


def build_factorlib_router(store: Optional[LibraryFactorStore] = None) -> APIRouter:
    store = store or LibraryFactorStore()
    router = APIRouter(prefix="/factorlib", tags=["factorlib"])

    @router.get("/list")
    def factorlib_list(validate: bool = True):
        """库内因子清单。

        返回 ``{ok, count, factors:[{name, expr, family, source, origin, description,
        qlib_src?, valid?, reason?}]}``。``validate=true``(默认)对每条经引擎
        ``validate_expr``+``compile_factor`` 标 ``valid``(不注册、不碰数据)。
        """
        try:
            factors = store.list_factors(validate=validate)
            return JSONResponse({"ok": True, "count": len(factors), "factors": factors})
        except Exception as exc:  # noqa: BLE001  —— 诚实失败,不退假数据
            return JSONResponse({"ok": False, "count": 0, "factors": [],
                                 "reason": f"{type(exc).__name__}: {exc}"})

    @router.get("/registered")
    def factorlib_registered():
        """当前引擎 zoo registry 里属本库的已注册项(family 以 ``library`` 开头)。

        借引擎 ``list_alphas`` 读运行期 registry;证明库因子已真正注册(也会出现在
        引擎 ``/factor/list`` 的 registered)。返回 ``{ok, count, registered:[{name,family,formula}]}``。
        """
        try:
            import financial_analyst.factors.zoo  # noqa: F401  触发内置注册
            from financial_analyst.factors.zoo.registry import list_alphas
            rows = [{"name": s.name, "family": s.family, "formula": s.formula_text}
                    for s in list_alphas(None)
                    if str(s.family).startswith(LIBRARY_FAMILY)]
            return JSONResponse({"ok": True, "count": len(rows), "registered": rows})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "count": 0, "registered": [],
                                 "reason": f"{type(exc).__name__}: {exc}"})

    @router.post("/validate")
    def factorlib_validate(body: ValidateIn):
        """校验一条表达式(借引擎 primitive)。

        ``is_qlib=true`` → 先 ``qlib_to_zoo`` 译写。返回 ``{ok, zoo_expr, reason?}``;
        译/编不动 → ``ok:False`` + reason(HTTP 200)。
        """
        raw = (body.expr or "").strip()
        if not raw:
            return JSONResponse({"ok": False, "zoo_expr": "", "reason": "空表达式"})
        try:
            zoo_expr = qlib_to_zoo(raw) if body.is_qlib else raw
        except UnsupportedFactor as u:
            return JSONResponse({"ok": False, "zoo_expr": "", "reason": f"untranslatable: {u}"})
        try:
            from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
            validate_expr(zoo_expr)
            compile_factor(zoo_expr)
            return JSONResponse({"ok": True, "zoo_expr": zoo_expr})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "zoo_expr": zoo_expr,
                                 "reason": f"{type(exc).__name__}: {exc}"})

    @router.post("/save")
    def factorlib_save(body: SaveIn):
        """把一条好因子(表达式)存入因子库 mined/ 并运行时注册进引擎 zoo。

        流程:名字/表达式非空 → (is_qlib 则)``qlib_to_zoo`` 译写 → 引擎
        ``validate_expr``+``compile_factor`` 校验(非法 → 诚实失败)→ 名字唯一性把关
        (重名拒绝覆盖)→ 落盘 ``mined/<安全名>.json``(对齐现有 schema:list[单 dict])
        → 复刻 store.register_all 内核单条注册(``unregister``→``register``,replace 语义)
        使其立即出现在 ``/factorlib/list``、``/factor/list``、可被 ``/factor/report`` 求值。

        诚实失败:任何异常 → ``{ok:False, reason}`` HTTP 200,绝不抛 500。
        """
        # 1) 表达式非空
        raw = (body.expr or "").strip()
        if not raw:
            return JSONResponse({"ok": False, "reason": "空表达式"})
        # 2) 因子名非空
        nm = (body.name or "").strip()
        if not nm:
            return JSONResponse({"ok": False, "reason": "因子名不能为空"})

        # 3) 译写(Qlib 形 → zoo 形;复用 /factorlib/validate 范式)
        try:
            zexpr = qlib_to_zoo(raw) if body.is_qlib else raw
        except UnsupportedFactor as u:
            return JSONResponse({"ok": False, "reason": f"untranslatable: {u}"})

        # 4) 校验(与 /factorlib/validate、store.register_all 同源两行;非法 expr 诚实失败)
        try:
            from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
            validate_expr(zexpr)
            compute = compile_factor(zexpr)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "zoo_expr": zexpr,
                                 "reason": f"{type(exc).__name__}: {exc}"})

        # 5) 重名检查(名字层把关:store.register 是 replace 语义不撞 registry,
        #    但文件层同名会覆盖 → 此处拒绝覆盖,重名诚实失败)
        try:
            existing = {f["name"] for f in store.list_factors(validate=False)}
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "reason": f"读库失败: {type(exc).__name__}: {exc}"})
        if nm in existing:
            return JSONResponse({"ok": False, "reason": f"因子名已存在: {nm}"})

        # 6) 落盘(写 mined/<安全名>.json;list[单 dict],对齐 _sample_mined.json + base/*.json)
        try:
            fn = _safe_filename(nm)
            # 二次确认无路径穿越:文件名必须等于自身的 basename
            if Path(fn).name != fn:
                return JSONResponse({"ok": False, "reason": f"非法文件名: {fn}"})
            fp = store.mined_dir / f"{fn}.json"
            if fp.exists():
                return JSONResponse({"ok": False, "reason": f"文件已存在: {fp.name}"})
            fam = body.family or "library_mined"
            rec = {
                "name": nm,
                "family": fam,
                "expr": zexpr,
                "description": body.description or "",
                "source": body.source or "workflow",
            }
            if body.is_qlib:
                rec["qlib_src"] = raw           # 原始 Qlib 串仅作台账/展示
            if body.meta:
                rec["meta"] = body.meta         # 展示用快照;store.list_factors 只读固定键,忽略未知键
            rec["saved_at"] = datetime.now().isoformat(timespec="seconds")
            rec["id"] = uuid.uuid4().hex
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(json.dumps([rec], ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "zoo_expr": zexpr,
                                 "reason": f"落盘失败: {type(exc).__name__}: {exc}"})

        # 7) 运行时注册(写后立即生效;复刻 store.register_all 内核单条三步,复用步骤4已编译的 compute)
        registered = False
        reg_reason = ""
        try:
            try:
                import financial_analyst.factors.zoo  # noqa: F401  触发内置族注册(失败不致命)
            except Exception:  # noqa: BLE001
                pass
            from financial_analyst.factors.zoo.registry import AlphaSpec, register, unregister
            unregister(nm)                     # replace 语义:避开 frozen-collision
            register(AlphaSpec(
                name=nm, family=fam,
                description=body.description or "",
                formula_text=zexpr,
                compute=compute,
                tags=("guanlan_factorlib", "mined"),
            ))
            registered = True
        except Exception as exc:  # noqa: BLE001
            # 注册失败不回滚文件(已诚实落盘);标注 registered=False + 原因即可
            reg_reason = f"{type(exc).__name__}: {exc}"

        # 8) 成功返回(落盘成功即 ok:True;registered 标注运行时注册是否生效)
        resp = {"ok": True, "name": nm, "expr": zexpr, "family": fam,
                "file": fp.name, "registered": registered}
        if reg_reason:
            resp["reason"] = reg_reason
        return JSONResponse(resp)

    return router
