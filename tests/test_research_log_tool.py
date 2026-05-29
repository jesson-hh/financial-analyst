"""SP-E 工具集成单测: factor_report/factor_compose 的 archive 往返 + research_log。

ResearchArchive 写到 ``$FINANCIAL_ANALYST_HOME/research/runs.jsonl`` — 测试把该
env 指向 tmp_path 即可隔离 (不碰用户真实 ~/.financial-analyst)。stub loader 仿
tests/test_factor_report_tool.py / test_factor_compose_tool.py: monkeypatch
``resolve_universe_codes`` + ``get_default_loader`` 的 home 模块。

绝不调 _clear_registry_for_tests (会清空全局 alpha 注册表, 拖垮跨文件测试)。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 导入 zoo 自动注册 alpha 族 (无害; 下面的用例只用纯表达式)。
import financial_analyst.factors.zoo  # noqa: F401
from financial_analyst.buddy import tools as t
from financial_analyst.buddy.tools import (
    TOOL_REGISTRY,
    ToolResult,
    get_tool,
)

CODES = ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750", "SH600276"]


# ---------------------------------------------------------------------------
# Stub loader: per-code random-walk close ~120 business days; no fundamentals.
# ---------------------------------------------------------------------------
def _stub_loader():
    class StubLoader:
        def fetch_quote(self, code, start, end, freq="day"):
            dates = pd.date_range("2023-01-02", periods=120, freq="B")
            rng = np.random.default_rng(abs(hash(code)) % 9999)
            close = 50 * np.exp(np.cumsum(rng.standard_normal(len(dates)) * 0.02))
            df = pd.DataFrame(
                {
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": np.full(len(dates), 1e6),
                },
                index=dates,
            )
            df.index.name = "datetime"
            return df

        def fetch_daily_basic(self, code, start, end):
            return pd.DataFrame()

    return StubLoader()


def _patch_panel(monkeypatch, codes=CODES):
    """让 eval / compose 引擎用 stub 面板 (patch home 模块, 非 buddy 别名)。"""
    monkeypatch.setattr(
        "financial_analyst.data.universe.resolve_universe_codes",
        lambda u: list(codes),
    )
    monkeypatch.setattr(
        "financial_analyst.data.loader_factory.get_default_loader",
        lambda: _stub_loader(),
    )


def _isolate_archive(monkeypatch, tmp_path):
    """把研究档案根指向 tmp_path (ResearchArchive 读 $FINANCIAL_ANALYST_HOME)。"""
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))


# ---------------------------------------------------------------------------
# (a) factor_report(archive=True) → 写档 + research_log() 列出
# ---------------------------------------------------------------------------
def test_factor_report_archive_then_research_log(monkeypatch, tmp_path):
    _patch_panel(monkeypatch)
    _isolate_archive(monkeypatch, tmp_path)

    res = t._tool_factor_report(
        expr_or_name="rank(-delta(close,5))", universe="csi500", freq="week",
        archive=True, note="第一次试这个反转因子",
    )
    assert isinstance(res, ToolResult)
    assert res.is_error is False, f"unexpected error: {res.content}"
    assert "已归档" in res.content
    assert "id=r0001" in res.content

    # runs.jsonl 真的落盘到 tmp_path 下
    runs = tmp_path / "research" / "runs.jsonl"
    assert runs.exists()

    # research_log() 无参 → 列出最近运行, 含该条
    log = t._tool_research_log()
    assert log.is_error is False
    assert "r0001" in log.content
    assert "研究档案" in log.content
    # note 透出
    assert "第一次试这个反转因子" in log.content


# ---------------------------------------------------------------------------
# (b) 归档两次 → research_log(compare="r0001,r0002") 出 diff
# ---------------------------------------------------------------------------
def test_research_log_compare(monkeypatch, tmp_path):
    _patch_panel(monkeypatch)
    _isolate_archive(monkeypatch, tmp_path)

    r1 = t._tool_factor_report(expr_or_name="rank(-delta(close,5))",
                               universe="csi500", freq="week", archive=True)
    r2 = t._tool_factor_report(expr_or_name="rank(close)",
                               universe="csi500", freq="week", archive=True)
    assert "id=r0001" in r1.content
    assert "id=r0002" in r2.content

    cmp = t._tool_research_log(compare="r0001,r0002")
    assert cmp.is_error is False, f"unexpected error: {cmp.content}"
    assert "运行对比" in cmp.content
    assert "r0001" in cmp.content and "r0002" in cmp.content
    # diff 列存在 (Δ 表头 + 至少一个公共数值指标行)
    assert "Δ" in cmp.content
    assert ("ic_mean" in cmp.content) or ("sharpe" in cmp.content) or ("rank_ic" in cmp.content)


def test_research_log_compare_missing_id(monkeypatch, tmp_path):
    """缺 id 的对比 → 友好报错 (is_error), 不抛。"""
    _patch_panel(monkeypatch)
    _isolate_archive(monkeypatch, tmp_path)
    t._tool_factor_report(expr_or_name="rank(close)", universe="csi500",
                          freq="week", archive=True)
    cmp = t._tool_research_log(compare="r0001,r9999")
    assert cmp.is_error is True
    assert "r9999" in cmp.content


# ---------------------------------------------------------------------------
# (c) research_log(target=...) → 该标的历史 (时间序趋势)
# ---------------------------------------------------------------------------
def test_research_log_history(monkeypatch, tmp_path):
    _patch_panel(monkeypatch)
    _isolate_archive(monkeypatch, tmp_path)

    expr = "rank(-delta(close,5))"
    # 同一 target 跑两次 → 历史里两条
    t._tool_factor_report(expr_or_name=expr, universe="csi500", freq="week", archive=True)
    t._tool_factor_report(expr_or_name=expr, universe="csi500", freq="week", archive=True)
    # 另一个 target 也跑一次 (验证 history 只过滤该 target)
    t._tool_factor_report(expr_or_name="rank(close)", universe="csi500", freq="week", archive=True)

    hist = t._tool_research_log(target=expr)
    assert hist.is_error is False
    assert "运行历史" in hist.content
    assert expr in hist.content
    # 该 target 的两次都在 (r0001, r0002), 另一 target 的 r0003 不在
    assert "r0001" in hist.content
    assert "r0002" in hist.content
    assert "r0003" not in hist.content


def test_research_log_history_empty(monkeypatch, tmp_path):
    """无该标的归档 → 友好空提示 (非 error)。"""
    _isolate_archive(monkeypatch, tmp_path)
    res = t._tool_research_log(target="不存在的因子xyz")
    assert res.is_error is False
    assert "无该标的的归档运行" in res.content


def test_research_log_empty_archive(monkeypatch, tmp_path):
    """档案为空 (无参) → 引导提示, 不崩。"""
    _isolate_archive(monkeypatch, tmp_path)
    res = t._tool_research_log()
    assert res.is_error is False
    assert "研究档案为空" in res.content


# ---------------------------------------------------------------------------
# (d) research_log 已注册且 schema 合法
# ---------------------------------------------------------------------------
def test_research_log_registered():
    names = {tool.name for tool in TOOL_REGISTRY}
    assert "research_log" in names
    tool = get_tool("research_log")
    assert tool is not None
    assert tool.cost_hint == "fast"
    schema = tool.input_schema
    assert "target" in schema["properties"]
    assert "compare" in schema["properties"]
    # 无必填 (两个都可选)
    assert not schema.get("required")
    # 两个 provider schema 都能渲染
    assert tool.to_anthropic_schema()["name"] == "research_log"
    assert tool.to_openai_schema()["function"]["name"] == "research_log"


def test_factor_report_schema_has_archive():
    """factor_report / factor_compose 的 schema 增了 archive(boolean)/note(string)。"""
    rep = get_tool("factor_report")
    assert rep.input_schema["properties"]["archive"]["type"] == "boolean"
    assert rep.input_schema["properties"]["archive"]["default"] is False
    assert rep.input_schema["properties"]["note"]["type"] == "string"
    comp = get_tool("factor_compose")
    assert comp.input_schema["properties"]["archive"]["type"] == "boolean"
    assert comp.input_schema["properties"]["note"]["type"] == "string"


# ---------------------------------------------------------------------------
# (e) archive 失败不拖垮报告主体
# ---------------------------------------------------------------------------
def test_factor_report_archive_failure_still_returns_report(monkeypatch, tmp_path):
    _patch_panel(monkeypatch)
    _isolate_archive(monkeypatch, tmp_path)

    # 让 append 抛错 (模拟磁盘/权限失败) — patch 类方法, 工具内 import 后调用走它。
    def _boom(self, record):
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "financial_analyst.factors.research.archive.ResearchArchive.append",
        _boom,
    )

    res = t._tool_factor_report(
        expr_or_name="rank(-delta(close,5))", universe="csi500", freq="week",
        archive=True, note="should still report",
    )
    # 报告主体照常返回 (非 error), 但带归档失败提示
    assert res.is_error is False, f"report body must survive archive failure: {res.content}"
    assert "RankIC" in res.content  # 报告主体还在
    assert "归档失败" in res.content
    assert "disk full" in res.content


def test_factor_compose_archive_roundtrip(monkeypatch, tmp_path):
    """factor_compose(archive=True) 也写档 + research_log 列出 (kind=compose)。"""
    _patch_panel(monkeypatch)
    _isolate_archive(monkeypatch, tmp_path)

    res = t._tool_factor_compose(
        members=["rank(-delta(close,5))", "rank(close)"],
        method="equal", universe="csi500", freq="week", train_frac=0.6,
        archive=True, note="第一个合成",
    )
    assert res.is_error is False, f"unexpected error: {res.content}"
    assert "已归档" in res.content

    log = t._tool_research_log()
    assert "compose" in log.content
    assert "第一个合成" in log.content
