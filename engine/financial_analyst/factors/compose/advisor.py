"""多因子合成 LLM 层 (SP-D.2) — 输入顾问 (NL→配方) + 结果研判。

compose_advisor: 自然语言目标 → ComposeRecipe (成员表达式 + 方法 + 理由), 校验 + repair。
interpret_compose: ComposeResult → 质性研判串。两者经可注入 complete_fn (测试), 默认走
buddy LLMClient (asyncio.run, 须无事件循环线程)。永不抛。纯 compose_factors 引擎不碰 LLM。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, validate_expr, compile_factor
from financial_analyst.factors.zoo.panel import PanelData

CompleteFn = Callable[[List[dict]], str]
_METHODS = ("equal", "ic_weighted", "linear", "lgbm")


@dataclass
class ComposeRecipe:
    goal: str
    members: List[str] = field(default_factory=list)
    method: str = "lgbm"
    train_frac: float = 0.6
    rationale: str = ""
    status: str = "ok"        # ok / bad_output / llm_error
    error: str = ""


def _complete_json(messages: List[dict]) -> str:
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    resp = asyncio.run(client.chat(messages, response_format={"type": "json_object"}, temperature=0.2))
    return resp["choices"][0]["message"]["content"]


def _complete_text(messages: List[dict]) -> str:
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    resp = asyncio.run(client.chat(messages, temperature=0.3))
    return resp["choices"][0]["message"]["content"]


def _tiny_panel() -> PanelData:
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.lognormal(0.0, 0.02, len(idx)), index=idx)
    close = rets.groupby(level="code").cumprod() * 50 + 10
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close, "volume": pd.Series(1e6, index=idx)})
    return PanelData(df)


def _member_error(expr: str, panel) -> Optional[str]:
    """成员表达式错误信息 (None=通过): validate + compile + 小面板 dry-run。"""
    try:
        validate_expr(expr)
        out = compile_factor(expr)(panel)
    except Exception as e:
        return f"{expr!r}: {type(e).__name__}: {e}"
    if not isinstance(out, pd.Series):
        return f"{expr!r}: 求值非 Series ({type(out).__name__})"
    return None


_ADVISOR_SYSTEM = (
    "你是量化多因子组合研究员。把用户的目标拆成 **>=2 个互补的截面因子表达式**, "
    "只用下列字段+算子 (Python 语法):\n" + FACTOR_VOCAB + "\n"
    "再选合成方法: equal(等权,少而稳)/ic_weighted(按IC加权)/linear(线性回归去冗余)/lgbm(非线性,成员多时); "
    "成员可能同源时优先 linear/lgbm。给 train_frac (0.5~0.7) 和简短理由。"
    "不要用 Python 内置函数, 只用算子表里的 (abs_/max_pair/min_pair 等)。\n"
    '只输出 JSON: {"members": ["expr1","expr2"], "method": "lgbm", "train_frac": 0.6, "rationale": "..."}'
)
_ADVISOR_FEWSHOT = [
    {"role": "user", "content": "低回撤的动量+反转组合"},
    {"role": "assistant", "content": json.dumps({
        "members": ["rank(delta(close,20))", "rank(-delta(close,5))", "rank(-stddev(returns,20))"],
        "method": "lgbm", "train_frac": 0.6,
        "rationale": "中期动量+短期反转互补, 加低波动降回撤; 成员可能同源, lgbm 非线性去冗余。"},
        ensure_ascii=False)},
]


def _advise_messages(goal: str, repair: Optional[str] = None) -> List[dict]:
    msgs = [{"role": "system", "content": _ADVISOR_SYSTEM}] + _ADVISOR_FEWSHOT
    user = goal if not repair else f"{goal}\n\n上次配方有问题, 修正后重出: {repair}"
    return msgs + [{"role": "user", "content": user}]


def compose_advisor(goal: str, complete_fn: Optional[CompleteFn] = None) -> ComposeRecipe:
    """自然语言目标 → ComposeRecipe。校验成员 + 单轮 repair, 永不抛。"""
    goal = (goal or "").strip()
    if not goal:
        return ComposeRecipe(goal="", status="bad_output", error="缺少目标 (goal)")
    complete = complete_fn or _complete_json
    panel = _tiny_panel()
    repair: Optional[str] = None
    rec = ComposeRecipe(goal=goal)
    for _attempt in range(2):
        try:
            content = complete(_advise_messages(goal, repair))
        except Exception as e:
            return ComposeRecipe(goal=goal, status="llm_error",
                                 error=f"LLM 调用失败: {type(e).__name__}: {e}")
        try:
            obj = json.loads(content)
        except Exception as e:
            repair = f"输出非合法 JSON: {e}"
            rec.error = "LLM 输出无法解析为 JSON"
            continue
        members = [str(m).strip() for m in (obj.get("members") or []) if str(m).strip()]
        method = (obj.get("method") or "lgbm").strip()
        method = method if method in _METHODS else "lgbm"
        try:
            tf = float(obj.get("train_frac", 0.6))
        except (TypeError, ValueError):
            tf = 0.6
        tf = min(0.8, max(0.5, tf))
        rationale = (obj.get("rationale") or "").strip()
        if len(members) < 2:
            repair = "成员不足 2 个, 必须 >=2"
            rec.error = "成员不足 2 个"
            continue
        errs = [e for e in (_member_error(m, panel) for m in members) if e]
        if errs:
            repair = "; ".join(errs[:3])
            rec.error = "成员表达式有误: " + repair
            continue
        return ComposeRecipe(goal=goal, members=members, method=method,
                             train_frac=tf, rationale=rationale, status="ok")
    rec.status = "bad_output"
    return rec


def interpret_compose(result, complete_fn: Optional[CompleteFn] = None) -> str:
    """ComposeResult → 质性研判串。result 非 ok 或 LLM 失败 → 返回 "" (调用方回落机械 verdict)。"""
    if result is None or getattr(result, "status", "") != "ok":
        return ""
    complete = complete_fn or _complete_text
    comp = getattr(result, "composite", None)
    ic = getattr(comp, "ic", None)
    pf = getattr(comp, "portfolio", None)
    facts = {
        "method": result.method,
        "weights": result.weights,
        "members_oos": [{"name": m.name, "rank_ic": m.rank_ic, "sharpe": m.sharpe}
                        for m in result.member_oos],
        "composite_rank_ic": getattr(ic, "rank_ic_mean", None),
        "composite_sharpe": getattr(pf, "sharpe", None),
        "composite_ann": getattr(pf, "ann_return", None),
        "composite_mdd": getattr(pf, "max_drawdown", None),
        "n_train_dates": result.n_train_dates,
        "n_test_dates": result.n_test_dates,
        "verdict": result.verdict,
    }
    sys = ("你是量化组合研究员。基于给定 OOS 事实写 3-5 句中文研判: "
           "①综合分 vs 最优成员增益是否显著 ②权重是否过度集中(过拟合风险) "
           "③成员是否同源冗余 ④下一步迭代建议(换/加/正交化哪个)。只说数据支持的, 不编造数字。")
    msgs = [{"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)}]
    try:
        return (complete(msgs) or "").strip()
    except Exception:
        return ""
