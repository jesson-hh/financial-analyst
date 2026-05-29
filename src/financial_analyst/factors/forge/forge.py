"""炼因子: 自然语言想法 → 截面因子表达式 (LLM + expr DSL), 含校验/编译/dry-run/repair。"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, validate_expr, compile_factor
from financial_analyst.factors.zoo.panel import PanelData

logger = logging.getLogger(__name__)

CompleteFn = Callable[[List[dict]], str]  # messages -> assistant message content


@dataclass
class ForgeResult:
    idea: str
    expr: str = ""
    parsed: List[dict] = field(default_factory=list)
    name: str = ""
    rationale: str = ""
    compile_ok: bool = False
    error: str = ""
    out_of_vocab: bool = False


_SYSTEM = (
    "你是量化因子工程师。把用户的自然语言想法转成 **一个截面因子表达式**, "
    "只能用下列字段+算子 (Python 语法):\n" + FACTOR_VOCAB + "\n"
    "表达式对每个 (日期,股票) 返回一个打分, **高分=更看好** (反转类记得加负号)。\n"
    "估值(pe_ttm/pb/ps_ttm)、股息(dv_ttm)、规模(total_mv/circ_mv)、换手(turnover_rate) 已支持。"
    "若想法需要表中没有的字段 (财报字段如 ROE/净利润/负债率, 需财报数据; 或'连续/金叉/突破'这类事件条件), "
    "把 out_of_vocab 设 true 并在 rationale 里说明缺什么, expr 留空。\n"
    "不要用 Python 内置函数 (abs/round/sum/min/max 等), 只用上面算子表里的算子 (如 abs_, max_pair, min_pair)。\n"
    '只输出 JSON: {"expr": "...", "parsed": [{"k":"触发","v":"..."}], '
    '"name": "usr_xxx", "rationale": "...", "out_of_vocab": false}'
)
_FEWSHOT = [
    {"role": "user", "content": "5日反转"},
    {"role": "assistant", "content": json.dumps({"expr": "rank(-delta(close,5))",
        "parsed": [{"k": "方向", "v": "近5日跌得多→反弹, 负delta"}], "name": "usr_rev5",
        "rationale": "5日动量取负做反转打分", "out_of_vocab": False}, ensure_ascii=False)},
    {"role": "user", "content": "放量上涨"},
    {"role": "assistant", "content": json.dumps({"expr": "rank(delta(close,1)) * rank(volume / ts_mean(volume,20))",
        "parsed": [{"k": "价", "v": "当日上涨"}, {"k": "量", "v": "量比20日均"}], "name": "usr_volup",
        "rationale": "涨幅×相对放量", "out_of_vocab": False}, ensure_ascii=False)},
    {"role": "user", "content": "高股息"},
    {"role": "assistant", "content": json.dumps({"expr": "rank(dv_ttm)",
        "parsed": [{"k": "方向", "v": "股息率高→看好"}], "name": "usr_divyield",
        "rationale": "股息率排序", "out_of_vocab": False}, ensure_ascii=False)},
]


def _build_messages(idea: str, repair_error: Optional[str] = None) -> List[dict]:
    user_content = idea
    if repair_error:
        user_content = (
            f"{idea}\n\n(上一版表达式有问题: {repair_error}。"
            f"请只用允许的字段+算子, 重出 JSON。)"
        )
    return [{"role": "system", "content": _SYSTEM}] + _FEWSHOT + [{"role": "user", "content": user_content}]


def _default_complete(messages: List[dict]) -> str:
    """Production LLM call. MUST be called from a synchronous context (no running
    event loop) — buddy tools run on a worker thread via asyncio.to_thread, so
    asyncio.run() is safe there; calling from an already-async context raises RuntimeError."""
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    resp = asyncio.run(client.chat(messages, response_format={"type": "json_object"}, temperature=0.2))
    return resp["choices"][0]["message"]["content"]


def _tiny_panel() -> PanelData:
    # recreated each call; forge is latency-insensitive vs the LLM round-trip
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.lognormal(0.0, 0.02, len(idx)), index=idx)
    close = rets.groupby(level="code").cumprod() * 50 + 10
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close, "volume": pd.Series(1e6, index=idx)})
    return PanelData(df)


def forge_factor(idea: str, complete_fn: Optional[CompleteFn] = None) -> ForgeResult:
    """Turn a natural-language idea into a validated cross-sectional factor expression.

    LLM reached via ``complete_fn(messages) -> content_str`` (injected for tests; defaults
    to the buddy LLMClient). Up to 2 attempts, feeding a compile/parse error back on the
    2nd (repair). Never raises — failures land in ForgeResult.error."""
    idea = (idea or "").strip()
    if not idea:
        return ForgeResult(idea="", error="缺少想法 (idea)")
    complete = complete_fn or _default_complete
    res = ForgeResult(idea=idea)
    repair_error: Optional[str] = None

    for _attempt in range(2):
        res.parsed, res.name, res.rationale, res.out_of_vocab = [], "", "", False
        try:
            content = complete(_build_messages(idea, repair_error))
        except Exception as e:
            return ForgeResult(idea=idea, error=f"LLM 调用失败: {type(e).__name__}: {e}")
        try:
            obj = json.loads(content)
        except Exception as e:
            repair_error = f"输出非合法 JSON: {e}"
            res.error = "LLM 输出无法解析为 JSON"
            continue

        res.parsed = obj.get("parsed") or []
        res.name = (obj.get("name") or "").strip()
        res.rationale = (obj.get("rationale") or "").strip()
        res.out_of_vocab = bool(obj.get("out_of_vocab", False))
        if res.out_of_vocab:
            res.compile_ok = False
            res.error = res.rationale or "想法需要当前价量 DSL 没有的字段/事件条件 (基本面→SP-B.1b, 事件→SP-B.2)"
            return res

        expr = (obj.get("expr") or "").strip()
        if not expr:
            repair_error = "expr 为空"
            res.error = "未生成表达式"
            continue
        try:
            # dry-run only checks compile + returns a Series; all-NaN output (e.g. window > 12d) is accepted as compile_ok
            validate_expr(expr)
            fn = compile_factor(expr)
            out = fn(_tiny_panel())
            if not isinstance(out, pd.Series):
                raise TypeError(f"表达式返回 {type(out).__name__}, 应为 pd.Series")
            res.expr, res.compile_ok, res.error = expr, True, ""
            return res
        except Exception as e:
            repair_error = f"{type(e).__name__}: {e}"
            res.expr = expr
            res.error = f"表达式无法编译/运行: {e}"
            continue

    return res  # compile_ok=False after 2 attempts
