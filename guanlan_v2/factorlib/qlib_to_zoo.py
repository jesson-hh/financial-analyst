# -*- coding: utf-8 -*-
"""Qlib 表达式 DSL → 引擎 zoo DSL 确定性译写器。

stocks 的因子挖掘产物(``G:/stocks/results/factor_mining/*.txt``)是 **Qlib 语法**
(``$close`` / ``Ref`` / ``Std`` / ``Mean`` / ``Corr`` / ``Sum`` / ``If`` / ``Abs`` /
``Log`` / ``Sign`` / ``Slope`` …),引擎 zoo(``factors/zoo/expr.py`` 的受限 eval
命名空间,见 ``FACTOR_VOCAB``)是**另一套**(``close`` / ``delay`` / ``stddev`` /
``ts_mean`` / ``correlation`` / ``ts_sum`` / ``filter_where`` / ``abs_`` / ``log`` /
``sign`` …)。二者不兼容,故迁移 = 把 Qlib 串**译写**成 zoo 串,再 ``compile_factor``。

本模块只做**确定性、可逆向核对**的翻译(字段去 ``$``、函数名整词替换);
碰到 zoo 没有安全对应物的算子(三目 ``If`` / ``Slope`` / ``EMA`` / 截面 ``Quantile`` …)
**诚实抛 ``UnsupportedFactor``** —— 调用方(store.py)捕获后记台账并跳过,绝不猜译。

无副作用、不读数据、不 import 引擎;纯字符串变换。被 ``guanlan_v2.factorlib.store``
调用。译写结果仍须经引擎 ``validate_expr`` + ``compile_factor`` 校验(store 负责)。
"""
from __future__ import annotations

import re


class UnsupportedFactor(ValueError):
    """该 Qlib 因子含无法安全译写的成分(三目 If / Slope / EMA / 未知算子)。

    调用方应捕获它、记入迁移台账、跳过该因子(诚实失败,不写错译)。
    """


# 字段: ``$close`` → ``close``、``$turnover_rate`` → ``turnover_rate`` (仅去 ``$``)。
# 引擎 panel 字段齐备(expr.py ns):close/open/high/low/volume/vwap/amount/returns/
# industry + pe_ttm/pb/ps_ttm/dv_ttm/total_mv/circ_mv/turnover_rate。
_FIELD = re.compile(r"\$([a-zA-Z_]\w*)")

# 函数名映射(Qlib → zoo)。仅收录**参数语义一致、可直接换名**的算子。
#   Qlib Ref(x,n)=x_{t-n} ↔ zoo delay(x,n);Std/Mean/Sum/Corr/Cov/Max/Min/Delta 同序;
#   Abs/Log/Sign/Power 逐元素。Qlib ``Rank`` 多为**时序** rank ↔ zoo ts_rank
#   (截面 rank 需人工甄别,见 _REJECT 不含 Rank → 默认按时序译,台账标注)。
_FUNC = {
    "Ref": "delay",         # Ref(x,n) → delay(x,n)   x 的 n 期前值
    "Std": "stddev",        # Std(x,n) → stddev(x,n)
    "Mean": "ts_mean",      # Mean(x,n) → ts_mean(x,n)
    "Sum": "ts_sum",        # Sum(x,n) → ts_sum(x,n)
    "Corr": "correlation",  # Corr(x,y,n) → correlation(x,y,n)
    "Cov": "covariance",
    "Max": "ts_max",        # 注: Qlib Max(x,n) 为时序滚动最大 → ts_max
    "Min": "ts_min",
    "Delta": "delta",
    "Abs": "abs_",
    "Log": "log",
    "Sign": "sign",
    "Power": "power",
    "WMA": "wma",
    "Rank": "ts_rank",
}

# zoo 无安全对应物、必须拒绝(诚实失败)的算子前缀。
#   If(c,a,b): 三目条件,zoo 无;不猜译(filter_where 是单臂掩码,语义不等价)。
#   Slope/EMA/Quantile/Med/Rsquare/IdxMax/IdxMin: zoo expr 白名单未暴露(operators 里
#   regbeta/rsqr 存在但 expr.py ns 未注入,故 eval 用不到 → 拒绝)。
_REJECT = (
    "If(", "Slope(", "EMA(", "Quantile(", "Med(",
    "Rsquare(", "IdxMax(", "IdxMin(", "WMA2(",
)


def qlib_to_zoo(expr: str) -> str:
    """把一条 Qlib-DSL 因子表达式译写成引擎 zoo-DSL 表达式。

    Parameters
    ----------
    expr : Qlib 表达式串(可含 ``|name`` 后缀前请先剥离;本函数只吃纯表达式)。

    Returns
    -------
    译写后的 zoo 表达式串(尚未校验;调用方须再过 ``validate_expr``+``compile_factor``)。

    Raises
    ------
    UnsupportedFactor
        含 ``_REJECT`` 中任一无法安全译写的算子。
    ValueError
        表达式为空。
    """
    if not expr or not str(expr).strip():
        raise ValueError("空 Qlib 表达式")
    e = str(expr).strip()

    for tok in _REJECT:
        if tok in e:
            raise UnsupportedFactor(f"含未译写算子 {tok!r}: {expr}")

    # 1) 字段去 $: $close → close
    e = _FIELD.sub(r"\1", e)

    # 2) 函数名整词替换(\b 词边界 + 紧跟 '(' ,避免把 Mean 误伤成 ts_Mean 之类)。
    #    注意先替换长名再短名无关紧要,因为都要求紧跟 '(' 且按整词边界。
    for q, z in _FUNC.items():
        e = re.sub(rf"\b{q}\(", f"{z}(", e)

    return e


def split_qlib_line(line: str) -> tuple[str, str]:
    """拆 stocks 因子文件的一行 ``<expr>|<name>`` → (expr, name)。

    无 ``|`` 时 name 为空串;首尾空白剥离。注释/空行由调用方过滤。
    """
    expr, _, name = str(line).partition("|")
    return expr.strip(), name.strip()
