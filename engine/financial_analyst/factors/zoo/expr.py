"""因子表达式编译 — 把白名单 DSL 字符串编译成 PanelData->Series 的 compute 函数。

从 buddy/tools.py 抽出, 供因子评测引擎与 buddy 的 factor_test/alpha_compare 共用。
受限 eval (无 builtins), 字段+算子白名单见 FACTOR_VOCAB。
"""
from __future__ import annotations

import ast as _ast
from difflib import get_close_matches as _close_matches

FACTOR_VOCAB = (
    "字段(价量): close open high low volume vwap amount returns industry | "
    "字段(基本面,day频): pe_ttm pb ps_ttm dv_ttm(股息率%) total_mv circ_mv(总/流通市值,万元) turnover_rate(换手%) | "
    "字段(技术,day频·精算·缺则NaN): rsi_14(Wilder RSI) macd_signal(MACD信号线) amihud_20(Amihud非流动性) mom_20 mom_60 mom_120(动量) | "
    "字段(财务,季频·公告日PIT对齐): roe(净资产收益率) roa(总资产收益率) net_margin(净利率) "
    "rev_yoy(营收同比) np_yoy(净利同比) debt_ratio(资产负债率) eps(每股收益,元) "
    "net_income revenue total_equity cfo(净利/营收/净资产/经营现金流,元,原始量) | "
    "字段(资金面,day频·EOD PIT·缺则NaN): main_net_amount main_net_pct "
    "super_large_net_amount super_large_net_pct large_net_amount large_net_pct "
    "medium_net_amount medium_net_pct small_net_amount small_net_pct "
    "(主力/超大/大/中/小单净流入额与净占比) | "
    "字段(参照,壳注入,选配): idx_ret=对标宽基指数日收益(如沪深300) ref_ret=龙头股日收益 | "
    "算子: rank ts_rank delta delay ts_mean ts_sum ts_max ts_min ts_argmax ts_argmin "
    "stddev correlation(x,y,n) covariance regbeta(y,x,n)=滚动β(cov/var,共振/跟随弹性) "
    "regresi(y,x,n)=回归残差(剔除y对x的暴露) rsqr(y,x,n)=拟合优度R²(共振强度) sequence(x,n)=时间序号(配regbeta算趋势斜率) "
    "decay_linear sma wma signedpower(x,p) "
    "log sign abs power(x,p) scale indneutralize(x,industry) csmean(x)=截面/篮子均值(→共振) "
    "indmean(x,industry)=所在行业均值(→行业共振) max_pair min_pair filter_where cross(x,y) | "
    "运算: + - * / ** 比较 ()"
)

_FORBIDDEN = ("__", "import", "lambda")

# 白名单名字(算子 + 字段),供 validate_expr 做 AST 名字校验 + 友好报错。
# 与 compile_factor 的 ns 同源 —— 新增算子/字段时务必同步这里(否则会把合法名字误判为未知)。
_OP_NAMES = frozenset({
    "rank", "scale", "indneutralize", "csmean", "indmean", "ts_sum", "ts_mean",
    "stddev", "ts_max", "ts_min", "ts_argmax", "ts_argmin", "ts_rank", "delta",
    "delay", "correlation", "covariance", "regbeta", "regresi", "rsqr", "sequence",
    "decay_linear", "sma", "wma", "signedpower", "log", "sign", "abs", "abs_",
    "product", "power", "max_pair", "min_pair", "filter_where", "cross",
})
_FIELD_NAMES = frozenset({
    "close", "open", "high", "low", "volume", "vwap", "amount", "returns", "industry",
    "pe_ttm", "pb", "ps_ttm", "dv_ttm", "total_mv", "circ_mv", "turnover_rate",
    "rsi_14", "macd_signal", "amihud_20", "mom_20", "mom_60", "mom_120",
    "roe", "roa", "net_margin", "rev_yoy", "np_yoy", "debt_ratio", "eps",
    "net_income", "revenue", "total_equity", "cfo",
    "main_net_amount", "main_net_pct", "super_large_net_amount", "super_large_net_pct",
    "large_net_amount", "large_net_pct", "medium_net_amount", "medium_net_pct",
    "small_net_amount", "small_net_pct",  # 资金面(东财五档·day频·EOD PIT)
    "idx_ret", "ref_ret", "benchmark_close",  # 壳注入(选配)
})
_KNOWN_NAMES = _OP_NAMES | _FIELD_NAMES


def _name_hint(unknown: str, pool) -> str:
    """对未知名字给近似建议(difflib);无近似则空串。"""
    near = _close_matches(str(unknown), list(pool), n=3, cutoff=0.6)
    return f"(是不是想用 {' / '.join(near)}?)" if near else ""


def validate_expr(expr: str) -> None:
    """校验 zoo-DSL 表达式:空 / 非法 token / 语法错误 / 未知字段算子 → 友好 ValueError
    (在加载面板前快速失败)。未知名字会列出近似建议;参照字段 idx_ret/ref_ret 需数据源设对标指数/龙头。"""
    if not expr or not str(expr).strip():
        raise ValueError("空因子表达式")
    if any(tok in expr for tok in _FORBIDDEN):
        raise ValueError("表达式含非法 token (__ / import / lambda)")
    try:
        tree = _ast.parse(str(expr), mode="eval")
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误:{e.msg}(检查括号/逗号/运算符)") from None
    names = {n.id for n in _ast.walk(tree) if isinstance(n, _ast.Name)}
    unknown = sorted(n for n in names if n not in _KNOWN_NAMES)
    if unknown:
        tips = "、".join(f"'{u}'{_name_hint(u, _KNOWN_NAMES)}" for u in unknown)
        raise ValueError(
            f"未知字段/算子:{tips}。可用 — 价量:close/open/high/low/volume/turnover_rate;"
            "技术:rsi_14/macd_signal/amihud_20/mom_20/mom_60/mom_120;"
            "财务:roe/roa/net_margin/rev_yoy/np_yoy/debt_ratio/eps;"
            "参照(需数据源):idx_ret/ref_ret;算子见公式面板。"
        )


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
            "pe_ttm": p.pe_ttm, "pb": p.pb, "ps_ttm": p.ps_ttm, "dv_ttm": p.dv_ttm,
            "total_mv": p.total_mv, "circ_mv": p.circ_mv, "turnover_rate": p.turnover_rate,
            "roe": p.roe, "roa": p.roa, "net_margin": p.net_margin,
            "rev_yoy": p.rev_yoy, "np_yoy": p.np_yoy, "debt_ratio": p.debt_ratio,
            "eps": p.eps, "net_income": p.net_income, "revenue": p.revenue,
            "total_equity": p.total_equity, "cfo": p.cfo,
            "rank": _ops.rank, "scale": _ops.scale, "ts_sum": _ops.ts_sum,
            "ts_mean": _ops.ts_mean, "stddev": _ops.stddev, "ts_max": _ops.ts_max,
            "ts_min": _ops.ts_min, "ts_argmax": _ops.ts_argmax, "ts_argmin": _ops.ts_argmin,
            "ts_rank": _ops.ts_rank, "delta": _ops.delta, "delay": _ops.delay,
            "correlation": _ops.correlation, "covariance": _ops.covariance,
            "regbeta": _ops.regbeta, "regresi": _ops.regresi, "rsqr": _ops.rsqr,
            "sequence": _ops.sequence,
            "decay_linear": _ops.decay_linear, "sma": _ops.sma, "wma": _ops.wma,
            "signedpower": _ops.signedpower, "log": _ops.log, "sign": _ops.sign,
            "abs": _ops.abs_, "abs_": _ops.abs_, "product": _ops.product,
            "power": _ops.power, "indneutralize": _ops.indneutralize, "csmean": _ops.csmean,
            "indmean": _ops.indmean,
            "max_pair": _ops.max_pair, "min_pair": _ops.min_pair,
            "filter_where": _ops.filter_where, "cross": _ops.cross,
        }
        # 把面板携带的额外列也暴露成字段(壳层可注入 idx_ret/ref_ret/benchmark_close 等
        # 外部参照序列 → 公式可写 correlation(returns, idx_ret, 20) 做"个股 vs 大盘/龙头"共振)。
        # setdefault 保证绝不覆盖上面的标准字段/算子;标准 OHLCV 列本已在 ns,故对既有调用为 no-op。
        for _c in p.df.columns:
            ns.setdefault(str(_c), p.df[_c])
        try:
            return eval(expr, {"__builtins__": {}}, ns)  # restricted namespace
        except NameError as e:   # 兜底:未知名字给友好建议(用真实 ns,含动态注入字段如 idx_ret)
            nm = getattr(e, "name", None)
            if not nm:
                _s = str(e)
                nm = _s.split("'")[1] if "'" in _s else _s
            raise ValueError(
                f"未知字段/算子 '{nm}'{_name_hint(nm, ns.keys())}。"
                "注:idx_ret 需数据源设对标指数、ref_ret 需设龙头代码。"
            ) from None
    return compute
