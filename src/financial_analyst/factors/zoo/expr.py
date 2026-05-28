"""因子表达式编译 — 把白名单 DSL 字符串编译成 PanelData->Series 的 compute 函数。

从 buddy/tools.py 抽出, 供因子评测引擎与 buddy 的 factor_test/alpha_compare 共用。
受限 eval (无 builtins), 字段+算子白名单见 FACTOR_VOCAB。
"""
from __future__ import annotations

FACTOR_VOCAB = (
    "字段: close open high low volume vwap amount returns industry | "
    "算子: rank ts_rank delta delay ts_mean ts_sum ts_max ts_min ts_argmax ts_argmin "
    "stddev correlation(x,y,n) covariance decay_linear sma wma signedpower(x,p) "
    "log sign abs power(x,p) scale indneutralize(x,industry) max_pair min_pair filter_where | "
    "运算: + - * / ** 比较 ()"
)

_FORBIDDEN = ("__", "import", "lambda")


def validate_expr(expr: str) -> None:
    """Raise ValueError if expr is empty or contains a forbidden token."""
    if not expr or not str(expr).strip():
        raise ValueError("空因子表达式")
    if any(tok in expr for tok in _FORBIDDEN):
        raise ValueError("表达式含非法 token (__ / import / lambda)")


def compile_factor(expr: str):
    """Build a PanelData->Series compute function from a whitelisted expression.

    Does NOT validate — call validate_expr() first if the source is untrusted.
    """
    from financial_analyst.factors.zoo import operators as _ops

    def compute(p):
        ns = {
            "close": p.close, "open": p.open, "high": p.high, "low": p.low,
            "volume": p.volume, "vwap": p.vwap, "amount": p.amount,
            "returns": p.returns, "industry": p.industry,
            "rank": _ops.rank, "scale": _ops.scale, "ts_sum": _ops.ts_sum,
            "ts_mean": _ops.ts_mean, "stddev": _ops.stddev, "ts_max": _ops.ts_max,
            "ts_min": _ops.ts_min, "ts_argmax": _ops.ts_argmax, "ts_argmin": _ops.ts_argmin,
            "ts_rank": _ops.ts_rank, "delta": _ops.delta, "delay": _ops.delay,
            "correlation": _ops.correlation, "covariance": _ops.covariance,
            "decay_linear": _ops.decay_linear, "sma": _ops.sma, "wma": _ops.wma,
            "signedpower": _ops.signedpower, "log": _ops.log, "sign": _ops.sign,
            "abs": _ops.abs_, "abs_": _ops.abs_, "product": _ops.product,
            "power": _ops.power, "indneutralize": _ops.indneutralize,
            "max_pair": _ops.max_pair, "min_pair": _ops.min_pair,
            "filter_where": _ops.filter_where,
        }
        return eval(expr, {"__builtins__": {}}, ns)  # restricted namespace
    return compute
