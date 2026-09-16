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

import hashlib
import json
import os
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from guanlan_v2.factorlib.store import LIBRARY_FAMILY, LibraryFactorStore
from guanlan_v2.factorlib.qlib_to_zoo import UnsupportedFactor, qlib_to_zoo
from guanlan_v2.factorlib.specs import (
    FactorSpecError, factor_spec_digest, pin_factor_spec_dependencies,
    verify_factor_spec_dependencies,
)


# 安全名:只允许字母/数字/下划线/连字符;其余(含 / \ . : 等路径穿越字符)折成 _。
_SAFE_NAME = re.compile(r"[^0-9A-Za-z_\-]+")


def _safe_filename(name: str) -> str:
    """Map a logical factor name to one deterministic, collision-safe file."""
    logical = (name or "").strip()
    digest = hashlib.sha256(logical.encode("utf-8")).hexdigest()[:12]
    slug = _SAFE_NAME.sub("_", logical).strip("_")[:80]
    # Preserve legacy ASCII filenames.  For names with no ASCII slug (for
    # example Chinese-only names), the deterministic digest is essential: two
    # processes must target the same destination so link publication can
    # arbitrate instead of creating two random files for one logical name.
    return slug or f"factor_{digest}"


class ValidateIn(BaseModel):
    expr: str = ""
    is_qlib: bool = False     # True → 先过 qlib_to_zoo 译写再校验
    factor_spec: Optional[dict] = None


class SaveIn(BaseModel):
    name: str
    expr: str = ""            # structured factor 可由 factor_spec.base_features[0] 提供
    family: str = "library_mined"   # 默认带 library 前缀 → 进 /factorlib/registered
    description: str = ""
    source: str = ""
    is_qlib: bool = False
    status: str = ""                # P2:空=正式;"draft"=研究回路产物待人审(不上选股货架)
    meta: dict = Field(default_factory=dict)   # 展示用快照(_label/universe/ic…);store 忽略未知键
    factor_spec: Optional[dict] = None  # 结构化因子权威定义；expr 仅作兼容展示


_VALID_SAVE_STATUS = {"", "draft"}
_FACTORLIB_WRITE_LOCK = threading.RLock()
_RESERVED_PROVENANCE_KEYS = frozenset({
    "workspace_id", "workspace_asset_id", "workspace_version",
})
_WORKSPACE_PUBLISH_SOURCE = "research_workspace_publish"


@contextmanager
def _factorlib_write_guard(store: LibraryFactorStore):
    """Serialize the global factor namespace across threads and processes."""
    with _FACTORLIB_WRITE_LOCK:
        store.mined_dir.mkdir(parents=True, exist_ok=True)
        lock_path = store.mined_dir / ".guanlan-factorlib.lock"
        with lock_path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _save_factor_unlocked(body: SaveIn, store: LibraryFactorStore) -> dict:
    """/factorlib/save 内核(模块级;P2 研究回路直调复用)。返回 dict,端点包 JSONResponse。

    流程与诚实失败契约同原闭包(见 /save docstring);新增 status 校验与落盘
    (空=正式,draft=待人审——draft 仍注册进 zoo 可按名复验,只是不上选股货架)。
    """
    # 1) 因子名非空
    raw = (body.expr or "").strip()
    nm = (body.name or "").strip()
    if not nm:
        return {"ok": False, "reason": "因子名不能为空"}
    # 1.5) 结构化规范是权威定义。禁止与 expr 分叉，也不允许把结构化
    #     base_feature 当 Qlib 串部分翻译后留下两套含义。
    try:
        factor_spec = (pin_factor_spec_dependencies(
            body.factor_spec, fallback_expr=raw, factor_name=nm,
        ) if body.factor_spec is not None else None)
    except FactorSpecError as exc:
        return {"ok": False, "reason": f"factor_spec 非法: {exc}"}
    if factor_spec is not None:
        if body.is_qlib:
            return {"ok": False, "reason": "结构化 factor_spec 不支持 is_qlib；请先保存 zoo-DSL base_feature"}
        raw = factor_spec["base_features"][0]
    if not raw:
        return {"ok": False, "reason": "空表达式"}
    # 2.5) P2:status 校验(空=正式;draft=待人审)
    status = (body.status or "").strip()
    if status not in _VALID_SAVE_STATUS:
        return {"ok": False, "reason": f"status 非法: {status}(允许空或 draft)"}

    # 3) 译写(Qlib 形 → zoo 形;复用 /factorlib/validate 范式)
    try:
        zexpr = qlib_to_zoo(raw) if body.is_qlib else raw
    except UnsupportedFactor as u:
        return {"ok": False, "reason": f"untranslatable: {u}"}

    # 4) 校验/编译。structured 走 store 的统一编译器，注册、启动重载、
    #    /validate 与此处使用完全相同的执行定义。
    try:
        zexpr, compute, factor_spec = store.compile_entry({
            "name": nm, "expr": zexpr, "factor_spec": factor_spec,
        })
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "zoo_expr": zexpr,
                "reason": f"{type(exc).__name__}: {exc}"}

    # 5) 重名检查(名字层把关:store.register 是 replace 语义不撞 registry,
    #    但文件层同名会覆盖 → 此处拒绝覆盖,重名诚实失败)
    try:
        existing = {f["name"] for f in store.list_factors(validate=False)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"读库失败: {type(exc).__name__}: {exc}"}
    if nm in existing:
        return {"ok": False, "reason": f"因子名已存在: {nm}"}

    # 6) 落盘(写 mined/<安全名>.json;list[单 dict],对齐 _sample_mined.json + base/*.json)
    try:
        fn = _safe_filename(nm)
        # 二次确认无路径穿越:文件名必须等于自身的 basename
        if Path(fn).name != fn:
            return {"ok": False, "reason": f"非法文件名: {fn}"}
        fp = store.mined_dir / f"{fn}.json"
        if fp.exists():
            return {"ok": False, "reason": f"文件已存在: {fp.name}"}
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
        if factor_spec is not None:
            rec["factor_spec"] = factor_spec
            rec["factor_spec_digest"] = factor_spec_digest(factor_spec)
        if status:
            rec["status"] = status   # P2:draft 落盘(promote 转正时摘除)
        rec["saved_at"] = datetime.now().isoformat(timespec="seconds")
        rec["id"] = uuid.uuid4().hex
        fp.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([rec], ensure_ascii=False, indent=2)
        tmp = fp.with_name(f".{fp.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with tmp.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            # A hard-link publishes the fully flushed temp file only if the
            # destination does not already exist.  Unlike replace(), this is
            # atomic and cannot overwrite a factor written by another process.
            os.link(tmp, fp)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # final file, if linked, is already complete and durable
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "zoo_expr": zexpr,
                "reason": f"落盘失败: {type(exc).__name__}: {exc}"}

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
        tags = ["guanlan_factorlib", "mined"]
        if factor_spec is not None:
            tags.extend(("factor_spec:v1", f"spec:{factor_spec_digest(factor_spec)}"))
        register(AlphaSpec(
            name=nm, family=fam,
            description=body.description or "",
            formula_text=zexpr,
            compute=compute,
            tags=tuple(tags),
        ))
        registered = True
    except Exception as exc:  # noqa: BLE001
        # 注册失败不回滚文件(已诚实落盘);标注 registered=False + 原因即可
        reg_reason = f"{type(exc).__name__}: {exc}"

    # 8) 成功返回(落盘成功即 ok:True;registered 标注运行时注册是否生效)
    resp = {"ok": True, "name": nm, "expr": zexpr, "family": fam,
            "file": fp.name, "registered": registered}
    if factor_spec is not None:
        resp.update({"structured": True, "factor_spec": factor_spec,
                     "factor_spec_digest": factor_spec_digest(factor_spec)})
    if reg_reason:
        resp["reason"] = reg_reason
    return resp


def _save_factor(body: SaveIn, store: LibraryFactorStore) -> dict:
    """Serialize global factor writes across HTTP, research and workspace paths.

    ``_save_factor_unlocked`` performs a read-before-write duplicate check.  A
    single process-wide lock makes that check and the following file creation
    one operation, so two research spaces cannot publish the same global name
    concurrently and overwrite one another.
    """
    if (body.source or "").strip() == _WORKSPACE_PUBLISH_SOURCE \
            or any(key in (body.meta or {}) for key in _RESERVED_PROVENANCE_KEYS):
        return {"ok": False, "reason": "workspace provenance 为内部发布保留字段"}
    try:
        with _factorlib_write_guard(store):
            return _save_factor_unlocked(body, store)
    except Exception as exc:  # keep the HTTP-200 honest-failure contract
        return {"ok": False, "reason": f"因子库写锁失败: {type(exc).__name__}: {exc}"}


def save_factor_idempotent(body: SaveIn, store: LibraryFactorStore, *,
                           workspace_id: str, workspace_asset_id: str,
                           workspace_version: int) -> dict:
    """Create a global factor once, or recover the same workspace publish.

    The provenance tuple is the idempotency key.  This is intentionally inside
    the same lock as the global name duplicate check so concurrent spaces can
    neither overwrite a name nor produce two files for one asset version.
    """
    try:
        with _factorlib_write_guard(store):
            try:
                requested_spec = (pin_factor_spec_dependencies(
                    body.factor_spec, fallback_expr=str(body.expr or "").strip(),
                    factor_name=str(body.name or "").strip(),
                ) if body.factor_spec is not None else None)
            except FactorSpecError as exc:
                return {"ok": False, "reason": f"factor_spec 非法: {exc}"}
            requested_expr = (requested_spec["base_features"][0] if requested_spec is not None
                              else str(body.expr or "").strip())
            for entry in store.load_all():
                meta = entry.get("meta") or {}
                if not isinstance(meta, dict):
                    continue
                try:
                    recorded_version = int(meta.get("workspace_version") or 0)
                except (TypeError, ValueError):
                    recorded_version = 0
                same_origin = (
                    entry.get("source") == _WORKSPACE_PUBLISH_SOURCE
                    and meta.get("workspace_id") == workspace_id
                    and meta.get("workspace_asset_id") == workspace_asset_id
                    and recorded_version == int(workspace_version)
                )
                if not same_origin:
                    continue
                if str(entry.get("expr") or "").strip() != requested_expr:
                    return {"ok": False, "reason": "同一空间资产版本的全局记录表达式不一致"}
                try:
                    stored_spec = (verify_factor_spec_dependencies(
                        entry.get("factor_spec"),
                        fallback_expr=str(entry.get("expr") or "").strip(),
                        factor_name=str(entry.get("name") or "").strip(),
                    ) if entry.get("factor_spec") is not None else None)
                except FactorSpecError as exc:
                    return {"ok": False, "reason": f"已存 factor_spec 损坏: {exc}"}
                if stored_spec != requested_spec:
                    return {"ok": False, "reason": "同一空间资产版本的 factor_spec 不一致"}
                return {
                    "ok": True, "name": entry.get("name"), "expr": entry.get("expr"),
                    "family": entry.get("family"), "file": entry.get("_file"),
                    "registered": False, "recovered": True,
                    **({"structured": True, "factor_spec": stored_spec,
                        "factor_spec_digest": factor_spec_digest(stored_spec)}
                       if stored_spec is not None else {}),
                }
            if (body.source or "").strip() != _WORKSPACE_PUBLISH_SOURCE:
                return {"ok": False, "reason": "内部空间发布来源标记缺失"}
            return _save_factor_unlocked(body, store)
    except Exception as exc:
        return {"ok": False, "reason": f"因子库写锁失败: {type(exc).__name__}: {exc}"}


class FactorPromoteIn(BaseModel):
    """``POST /factorlib/promote`` 入参:draft 因子人审转正(摘 status)。"""

    name: str = ""


def _promote_factor_unlocked(name: str, store: LibraryFactorStore) -> dict:
    """人审转正:在 mined/ 各 JSON 里找 name 条目,摘掉 status(draft→正式)。幂等;
    找不到 → not_found 诚实失败。转正后下次 /screen/factors 入口热刷新即上货架。"""
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "reason": "因子名不能为空"}
    try:
        for fp in sorted(store.mined_dir.glob("*.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 — 坏文件跳过(与 store._read_json_dir 同口径)
                continue
            entries = data if isinstance(data, list) else [data]
            hit = False
            for e in entries:
                if isinstance(e, dict) and e.get("name") == nm:
                    e.pop("status", None)
                    hit = True
            if hit:
                tmp = fp.with_name(f".{fp.name}.{uuid.uuid4().hex[:8]}.tmp")
                try:
                    with tmp.open("x", encoding="utf-8") as handle:
                        handle.write(json.dumps(entries, ensure_ascii=False, indent=2))
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(tmp, fp)
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
                return {"ok": True, "name": nm, "file": fp.name}
        return {"ok": False, "reason": f"not_found: {nm}"}
    except Exception as exc:  # noqa: BLE001 — 诚实失败 HTTP 200,不抛 500
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _promote_factor(name: str, store: LibraryFactorStore) -> dict:
    """Atomically promote under the same namespace lock as every save path."""
    try:
        with _factorlib_write_guard(store):
            return _promote_factor_unlocked(name, store)
    except Exception as exc:  # lock/open failures remain an honest API result
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


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
            # P4:draft 附前向 vintage(有值才附=诚实空态;失败静默不挡清单)
            try:
                from datetime import date as _d
                from guanlan_v2.screen.factor_vintage import cs_vintage_asof
                for f in factors:
                    if f.get("status") == "draft":
                        v = cs_vintage_asof(str(f.get("name")), _d.today().isoformat())
                        if v:
                            f["vintage"] = {"ic": v.get("ic"), "n": v.get("n"),
                                            "asof": v.get("asof")}
            except Exception:  # noqa: BLE001
                pass
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
            stored = {str(row.get("name")): row for row in store.list_factors(validate=False)}
            rows = []
            for s in list_alphas(None):
                if not str(s.family).startswith(LIBRARY_FAMILY):
                    continue
                row = {"name": s.name, "family": s.family, "formula": s.formula_text}
                saved = stored.get(str(s.name)) or {}
                if isinstance(saved.get("factor_spec"), dict):
                    row.update({"structured": True, "factor_spec": saved["factor_spec"],
                                "factor_spec_digest": saved.get("factor_spec_digest")})
                rows.append(row)
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
        try:
            factor_spec = (pin_factor_spec_dependencies(
                body.factor_spec, fallback_expr=raw,
            ) if body.factor_spec is not None else None)
        except FactorSpecError as exc:
            return JSONResponse({"ok": False, "zoo_expr": raw,
                                 "reason": f"factor_spec 非法: {exc}"})
        if factor_spec is not None:
            if body.is_qlib:
                return JSONResponse({"ok": False, "zoo_expr": raw,
                                     "reason": "结构化 factor_spec 不支持 is_qlib"})
            raw = factor_spec["base_features"][0]
        if not raw:
            return JSONResponse({"ok": False, "zoo_expr": "", "reason": "空表达式"})
        try:
            zoo_expr = qlib_to_zoo(raw) if body.is_qlib else raw
        except UnsupportedFactor as u:
            return JSONResponse({"ok": False, "zoo_expr": "", "reason": f"untranslatable: {u}"})
        try:
            _expr, _compute, canonical = store.compile_entry({
                "name": "", "expr": zoo_expr, "factor_spec": factor_spec,
            })
            payload = {"ok": True, "zoo_expr": _expr}
            if canonical is not None:
                payload.update({"structured": True, "factor_spec": canonical,
                                "factor_spec_digest": factor_spec_digest(canonical)})
            return JSONResponse(payload)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "zoo_expr": zoo_expr,
                                 "reason": f"{type(exc).__name__}: {exc}"})

    @router.post("/save")
    def factorlib_save(body: SaveIn):
        """把一条好因子(表达式)存入因子库 mined/ 并运行时注册进引擎 zoo。

        内核在模块级 _save_factor(P2 研究回路直调复用);本闭包只包 JSONResponse。
        """
        return JSONResponse(_save_factor(body, store))

    @router.post("/promote")
    def factorlib_promote(body: FactorPromoteIn):
        """draft 因子人审转正(P2):摘 status → 下次选股目录刷新即上货架。"""
        return JSONResponse(_promote_factor(body.name, store))

    return router
