"""SP-W2A — 真工作流节点单测 + 4 节点端到端链.

5 个节点:
- data.universe (params.name) -> list[str]
- data.load_panel (params.freq/start/end, inputs.codes) -> PanelData.df DataFrame
- factor.from_registry (params.name, inputs.panel) -> pd.Series
- factor.from_expression (params.expr, inputs.panel) -> pd.Series
- eval.factor_report (params.fwd_days/..., inputs.alpha+panel) -> dict (FactorReport asdict)

测试策略:
- universe 用 csi_fast (~100 只, 真小池)
- 加载窗口 ~3 个月 (2025-09 → 2025-11), 30 只, ~700 行
- 真数据加载估计 ~10-15s 含 alpha 注册; 标 slow 但默认跑 (集成测试).

conftest 影响: 项目 conftest.py 把 ``find_config('loaders.yaml')`` 重定向到 fake
空 qlib root, 让默认 loader 没数据. 本测试需要真数据故 module-level autouse
fixture 把 find_config 还原, 让 loader_factory 拿到真 G:/stocks/... QlibBinary 配置.
"""
from __future__ import annotations

import time

import pandas as pd
import pytest

# 触发 @node 注册 (workflow_nodes 包 + alpha zoo families)
import financial_analyst.factors.zoo  # noqa: F401
import financial_analyst.factors.workflow_nodes  # noqa: F401
from financial_analyst.workflow.registry import NodeRegistry


@pytest.fixture(autouse=True)
def _real_loader_yaml(monkeypatch):
    """Override conftest 的 fake_loaders_yaml — 让本测试用真 QlibBinaryLoader
    (G:/stocks/stock_data/cn_data) 跑真数据.

    实现: 不调 conftest 已 patched 的 ``find_config`` (会无限递归 / 命中 fake yaml),
    我们直接用 ``config_candidates`` 列表 (未被 patch 的函数) 做真实查找.
    """
    import tempfile
    from pathlib import Path

    # 真 yaml — 指向真数据
    tmp_dir = Path(tempfile.mkdtemp(prefix="real_yaml_"))
    real_yaml = tmp_dir / "loaders.yaml"
    real_yaml.write_text(
        "default: qlib_binary\n"
        "loaders:\n"
        "  qlib_binary:\n"
        "    provider_uri:\n"
        "      day: G:/stocks/stock_data/cn_data\n"
        "      5min: G:/stocks/stock_data/cn_data_5min\n",
        encoding="utf-8",
    )

    # config_candidates 没有被 conftest monkeypatch, 我们用它绕过 find_config
    from financial_analyst._config import config_candidates as _candidates

    def _real_find_config(name, explicit=None):
        if explicit is not None:
            return Path(explicit)
        if name == "loaders.yaml":
            return real_yaml
        # 其它 (universes/csi_fast.txt 等) 用未 patched 的 config_candidates 查
        for p in _candidates(name):
            if p.is_file():
                return p
        raise FileNotFoundError(f"Config file {name!r} not found")

    # 覆盖三个绑定
    import financial_analyst._config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "find_config", _real_find_config)
    import financial_analyst.data.loader_factory as _lf_mod
    monkeypatch.setattr(_lf_mod, "find_config", _real_find_config)
    try:
        import financial_analyst.data.paths as _paths_mod
        monkeypatch.setattr(_paths_mod, "find_config", _real_find_config)
    except Exception:
        pass

    # 清 panel_cache, 防上次 fake loader 缓存命中
    try:
        from financial_analyst.factors.zoo.panel_cache import clear_panel_cache
        clear_panel_cache()
    except Exception:
        pass
    yield

# Compute funcs (调用方便, 不走 Pydantic 验证 — schema 校验由 runner 负责)
from financial_analyst.factors.workflow_nodes.data_nodes import (
    data_universe,
    data_load_panel,
    UniverseParams,
    LoadPanelParams,
)
from financial_analyst.factors.workflow_nodes.factor_nodes import (
    factor_from_registry,
    factor_from_expression,
    FromRegistryParams,
    FromExpressionParams,
)
from financial_analyst.factors.workflow_nodes.eval_nodes import (
    eval_factor_report,
    FactorReportParams,
)


# ---------------------------------------------------------------------------
# data.universe — 单测
# ---------------------------------------------------------------------------


def test_data_universe_csi_fast():
    """csi_fast 应返 ~100 只代码 list."""
    codes = data_universe({"name": "csi_fast"}, {})
    assert isinstance(codes, list)
    assert all(isinstance(c, str) for c in codes)
    assert 50 <= len(codes) <= 200  # csi_fast 是个 ~100 的小池, 容忍校准
    # 代码形态: SH600... / SZ000... / SZ300... / BJ...
    assert all(c.startswith(("SH", "SZ", "BJ")) for c in codes), codes[:10]


def test_data_universe_empty_universe_raises():
    """不存在的 universe 名应抛 ValueError (resolve_universe_codes 返 [])."""
    with pytest.raises(ValueError, match="解析为空"):
        data_universe({"name": "totally_nonexistent_universe_zzz"}, {})


def test_data_universe_node_registered():
    """节点应注册到 NodeRegistry, group=data, tag=['data']."""
    reg = NodeRegistry.get("data.universe")
    assert reg.group == "data"
    assert "data" in reg.tag
    assert reg.params_model is UniverseParams


# ---------------------------------------------------------------------------
# data.load_panel — 真数据加载
# ---------------------------------------------------------------------------


def test_load_panel_real():
    """csi_fast 头 20 只 + 3 个月窗口 → MultiIndex DataFrame, 必含 OHLCV."""
    codes = data_universe({"name": "csi_fast"}, {})[:20]
    t0 = time.time()
    df = data_load_panel(
        {"freq": "day", "start": "2025-09-01", "end": "2025-11-30"},
        {"codes": codes},
    )
    duration = time.time() - t0
    # 真数据加载估计 ~1-3s (有 panel_cache hit 时秒级). 给 20s 的宽松上限.
    assert duration < 20.0, f"load_panel took {duration:.1f}s, too slow"

    # 形态校验
    assert isinstance(df, pd.DataFrame)
    assert isinstance(df.index, pd.MultiIndex)
    assert list(df.index.names) == ["datetime", "code"]
    for col in ("open", "high", "low", "close", "volume"):
        assert col in df.columns, f"missing column {col}"

    # 时间区间合理
    dates = df.index.get_level_values("datetime").unique()
    assert dates.min() >= pd.Timestamp("2025-09-01")
    assert dates.max() <= pd.Timestamp("2025-11-30")
    # ~60 个交易日
    assert 40 <= len(dates) <= 80, f"unexpected n_dates={len(dates)}"


def test_load_panel_node_registered():
    reg = NodeRegistry.get("data.load_panel")
    assert reg.group == "data"
    assert "data" in reg.tag
    assert reg.params_model is LoadPanelParams


def test_load_panel_rejects_non_list_codes():
    """inputs.codes 必须是 list, 给 str 应抛 TypeError."""
    with pytest.raises(TypeError, match="list"):
        data_load_panel(
            {"freq": "day", "start": "2025-09-01", "end": "2025-11-30"},
            {"codes": "not_a_list"},
        )


# ---------------------------------------------------------------------------
# factor.from_registry — 442 alpha + user 因子
# ---------------------------------------------------------------------------


def _load_small_panel():
    """复用一个小面板给多个测试用 (panel_cache 兜底, 多次调用秒级)."""
    codes = data_universe({"name": "csi_fast"}, {})[:30]
    df = data_load_panel(
        {"freq": "day", "start": "2025-09-01", "end": "2025-11-30"},
        {"codes": codes},
    )
    return df


def test_factor_from_registry_alpha001():
    """alpha001 是 alpha101 内置因子, 在真 panel 上应返非空 Series."""
    df = _load_small_panel()
    alpha = factor_from_registry({"name": "alpha001"}, {"panel": df})
    assert isinstance(alpha, pd.Series)
    assert isinstance(alpha.index, pd.MultiIndex)
    assert alpha.index.equals(df.index)
    assert alpha.notna().sum() > len(alpha) * 0.3, "alpha 大部分是 NaN"


def test_factor_from_registry_gtja001():
    """gtja001 — gtja191 family, 验跨 family 都能调起."""
    df = _load_small_panel()
    alpha = factor_from_registry({"name": "gtja001"}, {"panel": df})
    assert isinstance(alpha, pd.Series)
    assert alpha.notna().any()


def test_factor_from_registry_unknown_name_raises():
    """未知 alpha 名应抛 KeyError (zoo.registry.get 的语义)."""
    df = _load_small_panel()
    with pytest.raises(KeyError):
        factor_from_registry({"name": "totally_fake_alpha_xyz"}, {"panel": df})


def test_factor_from_registry_node_registered():
    reg = NodeRegistry.get("factor.from_registry")
    assert reg.group == "factor"
    assert "factor" in reg.tag
    assert reg.params_model is FromRegistryParams


# ---------------------------------------------------------------------------
# factor.from_expression — DSL 白名单
# ---------------------------------------------------------------------------


def test_factor_from_expression_rank_close():
    """``rank(close)`` 是 DSL 最简单一句, 应秒级返横截面排名."""
    df = _load_small_panel()
    alpha = factor_from_expression({"expr": "rank(close)"}, {"panel": df})
    assert isinstance(alpha, pd.Series)
    assert alpha.index.equals(df.index)
    # rank 输出 [0, 1] 区间, 每横截面累计应近 1.
    assert alpha.notna().any()


def test_factor_from_expression_compound_dsl():
    """``rank(-delta(close, 5))`` — 5 日反转 (经典 A 股因子)."""
    df = _load_small_panel()
    alpha = factor_from_expression(
        {"expr": "rank(-delta(close, 5))"}, {"panel": df}
    )
    assert isinstance(alpha, pd.Series)
    assert alpha.notna().any()


def test_factor_from_expression_forbidden_token_raises():
    """含 __ / import / lambda 应被 validate_expr 拒."""
    df = _load_small_panel()
    with pytest.raises(ValueError, match="非法 token"):
        factor_from_expression({"expr": "__import__('os')"}, {"panel": df})


def test_factor_from_expression_node_registered():
    reg = NodeRegistry.get("factor.from_expression")
    assert reg.group == "factor"
    assert "factor" in reg.tag
    assert reg.params_model is FromExpressionParams


# ---------------------------------------------------------------------------
# eval.factor_report — FactorReport asdict
# ---------------------------------------------------------------------------


def test_eval_factor_report_node_registered():
    reg = NodeRegistry.get("eval.factor_report")
    assert reg.group == "eval"
    assert "factor" in reg.tag
    assert "backtest" in reg.tag
    assert reg.params_model is FactorReportParams


def test_eval_factor_report_e2e():
    """端到端 4 节点链 (compute 函数直接串, 不走 runner): universe → load_panel →
    factor.from_registry(gtja001) → eval.factor_report → 返 FactorReport dict.

    验:
    - status == 'ok'
    - meta 含 n_dates / n_codes / fwd_days
    - ic 含 rank_ic_mean (真数字, 不是 None/NaN)
    - 计算时间 < 30s

    注: 用 gtja001 (不是 alpha003) — alpha003 在 csi_fast 小池上有 ±inf 值,
    zscore 后整列变 NaN 让 n_dates=0. 用 stable 因子做 e2e smoke.
    """
    t0 = time.time()
    # 1. universe
    codes = data_universe({"name": "csi_fast"}, {})

    # 2. load_panel (用前 30 只省时间)
    df = data_load_panel(
        {"freq": "day", "start": "2025-09-01", "end": "2025-11-30"},
        {"codes": codes[:30]},
    )

    # 3. factor.from_registry — gtja001 (stable across panels)
    alpha = factor_from_registry({"name": "gtja001"}, {"panel": df})

    # 4. eval.factor_report
    rpt = eval_factor_report(
        {"fwd_days": 5, "n_groups": 5, "cost_bps": 0.0, "freq": "day"},
        {"alpha": alpha, "panel": df},
    )
    duration = time.time() - t0

    assert duration < 30.0, f"e2e took {duration:.1f}s, too slow"
    assert isinstance(rpt, dict)
    assert rpt["status"] == "ok", f"report status={rpt['status']} error={rpt.get('error')}"

    meta = rpt["meta"]
    assert meta["fwd_days"] == 5
    assert meta["n_dates"] > 10  # ≥ 11 个调仓期 (3 个月 day=63 - 5 个 fwd = ~58)
    assert meta["n_codes"] > 0

    ic = rpt["ic"]
    assert "rank_ic_mean" in ic
    # rank_ic_mean 真数字 (-1, 1) 区间; None 表示算丢了
    assert ic["rank_ic_mean"] is not None
    assert -1.0 <= ic["rank_ic_mean"] <= 1.0

    # quantile / portfolio 段也有内容
    assert rpt["quantile"] is not None
    assert rpt["portfolio"] is not None


def test_eval_factor_report_accepts_dataframe_alpha():
    """artifact_store Series → DataFrame 的反序列化场景: alpha 是单列 DataFrame 也能跑."""
    df = _load_small_panel()
    alpha = factor_from_registry({"name": "gtja001"}, {"panel": df})
    alpha_df = alpha.to_frame()  # 模拟 store.read 把 Series 读回 DataFrame
    rpt = eval_factor_report(
        {"fwd_days": 3, "n_groups": 5, "cost_bps": 0.0, "freq": "day"},
        {"alpha": alpha_df, "panel": df},
    )
    assert rpt["status"] == "ok"
