# -*- coding: utf-8 -*-
"""因子库 store —— 读 ``base/*.json`` + ``mined/*.json``,校验→编译→注册进引擎 zoo registry。

每条因子是一个 dict::

    {name, family, expr, description, source, qlib_src?}

- ``expr`` 是**引擎 zoo-DSL**(已在 JSON 里译写好,见 README 台账);若某条 ``expr`` 仍是
  Qlib 形(含 ``$`` 或 ``Ref(``…),store 兜底再过 ``qlib_to_zoo`` 译一次(幂等)。
- 注册走引擎**运行期 registry**(进程级全局 dict,``factors/zoo/registry.py``):
  ``unregister(name)`` → ``register(AlphaSpec(compute=compile_factor(expr)))``
  —— 与引擎 ``UserFactorStore.register_one`` 同范式(replace 语义,避免 frozen-collision raise)。
  **不改 engine/ 任何文件**,只在运行期调用其公共 API。
- 校验经引擎 primitive ``validate_expr`` + ``compile_factor``(``factors/zoo/expr.py``);
  译/编不动的逐条记台账、跳过(诚实失败,不崩、不写假因子)。

数据只经包内 JSON;不读 stock_data、不碰 ``get_data_paths``(求值时引擎 panel 才碰数据)。
被 ``guanlan_v2.factorlib.{__init__, api}`` 调用。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from guanlan_v2.factorlib.qlib_to_zoo import UnsupportedFactor, qlib_to_zoo

logger = logging.getLogger(__name__)

_PKG_DIR = Path(__file__).resolve().parent
_BASE_DIR = _PKG_DIR / "base"
_MINED_DIR = _PKG_DIR / "mined"

# 库因子统一 family slug(出现在 /factor/list 的 registered[].family)。
LIBRARY_FAMILY = "library"

# 看着像 Qlib 形(需兜底再译)的判据:含 $ 字段 或 大写驼峰函数 Ref/Std/Mean...
_QLIB_HINTS = ("$", "Ref(", "Std(", "Mean(", "Sum(", "Corr(", "Abs(", "If(", "Slope(")


def _looks_qlib(expr: str) -> bool:
    return any(h in (expr or "") for h in _QLIB_HINTS)


def _read_json_dir(d: Path) -> List[dict]:
    """读一个目录下所有 ``*.json``(每文件可为 list[dict] 或单 dict)。坏文件 warn 跳过。"""
    out: List[dict] = []
    if not d.is_dir():
        return out
    for fp in sorted(d.glob("*.json")):
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("factorlib: 跳过损坏 JSON %s: %s", fp.name, e)
            continue
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            logger.warning("factorlib: %s 顶层既非 list 也非 dict,跳过", fp.name)
            continue
        for entry in data:
            if isinstance(entry, dict) and entry.get("name") and entry.get("expr"):
                e = dict(entry)
                e.setdefault("_file", fp.name)
                out.append(e)
            else:
                logger.warning("factorlib: %s 内一条缺 name/expr,跳过", fp.name)
    return out


class LibraryFactorStore:
    """guanlan 自有因子库:读 base/ + mined/ 的 zoo-DSL 因子,注册进引擎 zoo registry。

    Parameters
    ----------
    base_dir / mined_dir : 可注入(测试传 tmp);缺省走包内 ``factorlib/base`` `/mined`。
    """

    def __init__(self, base_dir: Optional[Path] = None, mined_dir: Optional[Path] = None):
        self.base_dir = Path(base_dir) if base_dir is not None else _BASE_DIR
        self.mined_dir = Path(mined_dir) if mined_dir is not None else _MINED_DIR

    # ---- 读 ----------------------------------------------------------------
    def load_all(self) -> List[dict]:
        """合并 base/ + mined/ 的全部库因子条目(原样,未编译)。base 在前。

        每条补 ``origin`` = ``base`` | ``mined``,便于 /factorlib/list 标注来源层。
        """
        base = _read_json_dir(self.base_dir)
        for e in base:
            e.setdefault("origin", "base")
        mined = _read_json_dir(self.mined_dir)
        for e in mined:
            e.setdefault("origin", "mined")
        return base + mined

    def _zoo_expr(self, entry: dict) -> str:
        """取条目可编译的 zoo 表达式:已是 zoo 形则原样;像 Qlib 形则兜底译一次。"""
        expr = str(entry.get("expr", "")).strip()
        if expr and _looks_qlib(expr):
            expr = qlib_to_zoo(expr)        # 可能抛 UnsupportedFactor → 调用方记台账
        return expr

    # ---- 注册(进引擎运行期 zoo registry)----------------------------------
    def register_all(self) -> Dict[str, Any]:
        """把库因子逐条 校验→编译→注册进引擎 zoo registry。

        Returns
        -------
        dict: ``{registered:int, skipped:int, total:int, ledger:[{name,origin,status,reason?}]}``
              幂等:内部 ``unregister`` 后 ``register``;单条失败只记台账、不影响其余、不抛。
        """
        # 触发引擎内置三族注册(alpha101/gtja191/qlib158),确保 registry 已就绪。
        # 失败不致命:库因子仍可注册;只是 list 里少了内置族(由 /factor/list 各自处理)。
        try:
            import financial_analyst.factors.zoo  # noqa: F401
        except Exception as e:  # noqa: BLE001
            logger.warning("factorlib: import zoo 触发内置注册失败(继续注册库因子): %s", e)

        try:
            from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
            from financial_analyst.factors.zoo.registry import AlphaSpec, register, unregister
        except Exception as e:  # noqa: BLE001
            logger.error("factorlib: 引擎 registry/expr 不可导入,放弃注册: %s", e)
            return {"registered": 0, "skipped": 0, "total": 0,
                    "ledger": [], "error": f"{type(e).__name__}: {e}"}

        ledger: List[dict] = []
        registered = skipped = 0
        for entry in self.load_all():
            name = entry["name"]
            origin = entry.get("origin", "")
            family = entry.get("family") or LIBRARY_FAMILY
            try:
                zexpr = self._zoo_expr(entry)
                validate_expr(zexpr)
                compute = compile_factor(zexpr)
                unregister(name)            # replace: 重编译的 compute 是新 fn,避免 frozen-collision
                register(AlphaSpec(
                    name=name, family=family,
                    description=entry.get("description", ""),
                    formula_text=zexpr,
                    compute=compute,
                    tags=("guanlan_factorlib", origin) if origin else ("guanlan_factorlib",),
                ))
                registered += 1
                ledger.append({"name": name, "origin": origin, "status": "registered"})
            except UnsupportedFactor as u:
                skipped += 1
                ledger.append({"name": name, "origin": origin, "status": "skipped",
                               "reason": f"untranslatable: {u}"})
                logger.warning("factorlib: 因子 %r 无法译写,跳过: %s", name, u)
            except Exception as e:  # noqa: BLE001
                skipped += 1
                ledger.append({"name": name, "origin": origin, "status": "skipped",
                               "reason": f"{type(e).__name__}: {e}"})
                logger.warning("factorlib: 因子 %r 编译/注册失败,跳过: %s", name, e)
        return {"registered": registered, "skipped": skipped,
                "total": registered + skipped, "ledger": ledger}

    # ---- 列(供 /factorlib/list)-------------------------------------------
    def list_factors(self, validate: bool = True) -> List[dict]:
        """库因子清单(name/expr/family/source/origin + 可选 valid/reason)。

        ``validate=True`` 时对每条跑一次 ``validate_expr``+``compile_factor`` 标 ``valid``
        (不注册、不碰数据),让前端能看出哪些可用、哪些只是台账记录。
        """
        v_expr = v_compile = None
        if validate:
            try:
                from financial_analyst.factors.zoo.expr import compile_factor as v_compile
                from financial_analyst.factors.zoo.expr import validate_expr as v_expr
            except Exception:  # noqa: BLE001
                v_expr = v_compile = None     # 引擎不可导入 → 跳过校验,仍出清单

        out: List[dict] = []
        for entry in self.load_all():
            row = {
                "name": entry["name"],
                "expr": str(entry.get("expr", "")),
                "family": entry.get("family") or LIBRARY_FAMILY,
                "source": entry.get("source", ""),
                "origin": entry.get("origin", ""),
                "description": entry.get("description", ""),
            }
            if entry.get("status"):
                row["status"] = str(entry.get("status"))    # P2:draft 显形(空/缺省不带键)
            # P2-E:验证快照里的 RankIC 下发(workflow「存入因子库」落的 meta.ic;base 库无 meta → 缺席)
            # —— 此前 meta 整体被吞,落子料库 syncArchive 只能给 ic:''(「validated」只剩可编译语义)。
            try:
                _ic = (entry.get("meta") or {}).get("ic")
                _icv = (_ic.get("rank_ic_mean", _ic.get("ic_mean")) if isinstance(_ic, dict)
                        else (_ic if isinstance(_ic, (int, float)) else None))
                if _icv is not None:
                    row["ic"] = round(float(_icv), 4)
            except Exception:  # noqa: BLE001 — 快照缺损不挡清单
                pass
            if entry.get("qlib_src"):
                row["qlib_src"] = entry["qlib_src"]
            if validate and v_expr is not None:
                try:
                    zexpr = self._zoo_expr(entry)
                    v_expr(zexpr)
                    v_compile(zexpr)
                    row["valid"] = True
                except Exception as e:  # noqa: BLE001
                    row["valid"] = False
                    row["reason"] = f"{type(e).__name__}: {e}"
            out.append(row)
        return out
