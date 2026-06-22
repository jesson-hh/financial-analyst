# -*- coding: utf-8 -*-
"""选股页因子目录 —— 复用 workflow 的 `_FACTOR_CATALOG`(中文名·预定向 zoo-DSL·分族)。

替掉原 api.py 里硬编的 5 因子 FACTOR_DEFS:
- 只收**连续截面**因子(动量反转/估值/波动率/流动性/技术/规模/财务质量/成长/情绪);
  事件触发类(反弹/消息面,离散 0/1)不适合截面 z-复合,**不进**选股目录(workflow 事件研究节点用)。
- **大盘因子**(共振/跟随族,表达式含 idx_ret)收进来:个股 vs 沪深300 的相关/β/R²/跟随,
  求值前由 `_panel_enrich` 注入 idx_ret 列(复用 workflow `_inject_market_refs`,真指数 399300.SZ)。
  含 ref_ret(龙头)/indmean(行业列)的暂不收(选股页无龙头选择器/行业列)。
- 原 3 个幽灵因子(北向已停披/PEAD/消息面 expr=None,可勾选但无效=摆设)**直接除名**;
  旧配置里的这些 id 后端自然忽略、前端静态表兜底显示,不崩。
- 每条: ``{short, family, expr, dir, ic, desc}``;**dir 恒 +1**(目录表达式已预定向,
  高分=偏好),唯 legacy ``fa_distrib`` 保留 -1 旧语义。``ic`` 一律 None ——
  **实测 IC** 由 regen 顺算的 ``vendor/artifacts/factor_ic.parquet`` 在 /screen/factors 合并下发,
  不再用静态装饰数(审计 #4)。

id 规则:``c_`` + md5(中文名)[:6](目录重排不漂移);legacy 两因子保留旧 id(老配置兼容)。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict

# 选股目录收录的族(连续截面因子);事件类(反弹/消息面)排除。
_KEEP_FAMILIES = {
    "动量反转", "估值", "财务质量", "成长", "波动率", "流动性",
    "技术", "规模", "情绪", "共振", "跟随", "资金面",
}
# 这些参照列选股页注入不了(无龙头选择器/面板无 industry 列),含之即跳过。
_UNAVAILABLE_REFS = ("ref_ret", "indmean(")

# legacy 两因子(保留旧 id,老保存配置/前端静态表兼容)。
_LEGACY: Dict[str, Dict[str, Any]] = {
    "fa_reversal": {"short": "缩量反转", "family": "动量反转", "expr": "-delta(close,5)",
                    "dir": 1, "ic": None, "desc": "近5日价格变化的反向(超跌反弹弹性)"},
    "fa_distrib":  {"short": "退潮风控", "family": "技术", "expr": "close/ts_max(close,60)",
                    "dir": -1, "ic": None, "desc": "贴近60日高位降权(高位退潮风控,惩罚层)"},
}


def _fid(name: str) -> str:
    return "c_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]


def _build() -> Dict[str, Dict[str, Any]]:
    """workflow._FACTOR_CATALOG + factorlib → 选股目录 dict。任一来源不可用即诚实降级跳过。"""
    out: Dict[str, Dict[str, Any]] = dict(_LEGACY)
    try:
        from guanlan_v2.workflow.api import _FACTOR_CATALOG
    except Exception:  # noqa: BLE001
        _FACTOR_CATALOG = []
    for name, expr, fam, _direction, desc in _FACTOR_CATALOG:
        if fam not in _KEEP_FAMILIES:
            continue
        if any(r in expr for r in _UNAVAILABLE_REFS):
            continue
        fid = _fid(name)
        if fid in out:
            continue
        out[fid] = {"short": name, "family": fam, "expr": expr,
                    "dir": 1, "ic": None, "desc": desc}
    # —— factorlib 并入(互通审计 P1⑥):工作流「存入因子库」/base 库 → 选股可选 ——
    # 同一 zoo-DSL(store._zoo_expr 对 Qlib 形兜底译写);id 直接用 factorlib name(lib_*,
    # 与落子料库/GL 总线同名互认);编译不过/含选股页注不进的参照列 → 跳过(诚实缺席)。
    # ic 恒 None:实测 RankIC 由下次 regen 的 factor_ic 顺算(其迭代本目录,自动覆盖新因子)。
    try:
        from financial_analyst.factors.zoo.expr import compile_factor, validate_expr
        from guanlan_v2.factorlib.store import LibraryFactorStore
        _st = LibraryFactorStore()
        for entry in _st.load_all():
            fid = str(entry.get("name") or "").strip()
            if not fid or fid in out:
                continue
            try:
                expr = _st._zoo_expr(entry)
                if not expr or any(r in expr for r in _UNAVAILABLE_REFS):
                    continue
                validate_expr(expr)
                compile_factor(expr)
            except Exception:  # noqa: BLE001 — 单条坏因子不拖累目录
                continue
            out[fid] = {"short": fid[4:] if fid.startswith("lib_") else fid, "family": "因子库",
                        "expr": expr, "dir": 1, "ic": None,
                        "desc": (str(entry.get("description") or "").strip()
                                 or str(entry.get("source") or ""))[:90]}
    except Exception:  # noqa: BLE001 — factorlib 整体不可用 → 目录退回 catalog-only
        pass
    return out


# 模块级单例:api.py / llm.py 直接 `from guanlan_v2.screen.catalog import FACTOR_DEFS`,
# 引用形态(dict[id]→{expr,dir,ic,short,...})与原硬编 FACTOR_DEFS 完全同形,旧代码零改。
FACTOR_DEFS: Dict[str, Dict[str, Any]] = _build()


def refresh_factor_defs() -> int:
    """重建目录并**原地**更新 FACTOR_DEFS(消费方持有 dict 引用,绝不换对象)。
    挂在 /screen/factors 入口 → 工作流刚「存入因子库」的因子,选股页一刷新即可选(P1⑥)。"""
    new = _build()
    FACTOR_DEFS.clear()
    FACTOR_DEFS.update(new)
    return len(FACTOR_DEFS)


# 族展示顺序(前端因子库分组用,/screen/factors 下发)。
FAMILY_ORDER = ["动量反转", "技术", "估值", "财务质量", "成长", "波动率",
                "流动性", "资金面", "情绪", "规模", "共振", "跟随", "因子库"]
