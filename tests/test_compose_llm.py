"""SP-D.2 多因子合成 LLM: 输入顾问 + 结果研判。"""
from __future__ import annotations

import json
import pytest

import financial_analyst.factors.zoo  # noqa: F401  (注册 alpha families)
from financial_analyst.factors.compose.advisor import (
    ComposeRecipe, compose_advisor, interpret_compose)


def _canned(payload):
    """返回一个 complete_fn, 它忽略 messages 总是返回 json.dumps(payload)。"""
    return lambda messages: json.dumps(payload, ensure_ascii=False)


def test_compose_advisor_ok():
    fn = _canned({"members": ["rank(-delta(close,5))", "rank(delta(close,20))"],
                  "method": "linear", "train_frac": 0.65, "rationale": "反转+动量互补"})
    rec = compose_advisor("低回撤动量反转", complete_fn=fn)
    assert isinstance(rec, ComposeRecipe)
    assert rec.status == "ok"
    assert len(rec.members) == 2
    assert rec.method == "linear"
    assert rec.train_frac == pytest.approx(0.65)
    assert "互补" in rec.rationale


def test_compose_advisor_clamps_and_defaults():
    fn = _canned({"members": ["rank(close)", "rank(volume)"],
                  "method": "bogus", "train_frac": 0.99, "rationale": "x"})
    rec = compose_advisor("随便", complete_fn=fn)
    assert rec.status == "ok"
    assert rec.method == "lgbm"            # 非法方法 → 默认 lgbm
    assert rec.train_frac == pytest.approx(0.8)   # clip 到 0.8


def test_compose_advisor_repairs_bad_member():
    calls = {"n": 0}
    def fn(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"members": ["rank(nonexistent_field)", "rank(close)"],
                               "method": "equal", "train_frac": 0.6, "rationale": "first"})
        return json.dumps({"members": ["rank(-delta(close,5))", "rank(close)"],
                           "method": "equal", "train_frac": 0.6, "rationale": "fixed"})
    rec = compose_advisor("目标", complete_fn=fn)
    assert rec.status == "ok" and calls["n"] == 2   # 第一次烂成员→repair→第二次成功
    assert rec.rationale == "fixed"


def test_compose_advisor_bad_output():
    rec = compose_advisor("目标", complete_fn=_canned({"members": ["rank(close)"], "method": "equal"}))
    assert rec.status == "bad_output"   # 成员 <2, 两次都不行


def test_compose_advisor_llm_error():
    def boom(messages):
        raise RuntimeError("llm down")
    rec = compose_advisor("目标", complete_fn=boom)
    assert rec.status == "llm_error" and "llm down" in rec.error


class _FakeIC:
    rank_ic_mean = 0.03
class _FakePf:
    sharpe = 0.9
    ann_return = 0.2
    max_drawdown = -0.1
class _FakeComposite:
    ic = _FakeIC()
    portfolio = _FakePf()
class _FakeMember:
    def __init__(self, name, ric, sh):
        self.name, self.rank_ic, self.sharpe = name, ric, sh
class _FakeResult:
    status = "ok"
    method = "lgbm"
    weights = {"rank(close)": 0.7, "rank(volume)": 0.3}
    member_oos = [_FakeMember("rank(close)", 0.02, 0.6), _FakeMember("rank(volume)", 0.01, 0.4)]
    composite = _FakeComposite()
    n_train_dates = 20
    n_test_dates = 12
    verdict = "综合分 Sharpe 0.9 vs 最优成员 0.6 → 增益"


def test_interpret_compose_ok():
    res = interpret_compose(_FakeResult(), complete_fn=lambda m: "权重集中在 rank(close), 注意过拟合; 建议加正交化。")
    assert "过拟合" in res


def test_interpret_compose_failure_returns_empty():
    res = interpret_compose(_FakeResult(), complete_fn=lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    assert res == ""


def test_interpret_compose_non_ok_returns_empty():
    class _Bad:
        status = "load_error"
    assert interpret_compose(_Bad(), complete_fn=lambda m: "should not be called") == ""
