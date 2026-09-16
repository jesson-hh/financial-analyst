from types import SimpleNamespace

import pandas as pd
import pytest

from guanlan_v2.console import codex_query_tools as cq


class _Panel:
    def n_codes(self):
        return 2

    def n_dates(self):
        return 42


def _patch_panel(monkeypatch):
    from financial_analyst.factors.zoo.panel import PanelData

    seen = {}

    def _from_loader(loader, codes, start, end, **kwargs):
        seen.update({
            "loader": loader, "codes": list(codes), "start": start,
            "end": end, **kwargs,
        })
        return _Panel()

    monkeypatch.setattr(PanelData, "from_loader", staticmethod(_from_loader))
    return seen


def test_alpha_bench_query_is_in_process_and_never_saves(monkeypatch):
    from financial_analyst.factors.zoo import bench_runner

    monkeypatch.setattr(
        cq, "_trusted_universe_codes", lambda name: ["SH600000", "SZ000001"],
    )
    monkeypatch.setattr(
        cq, "_read_only_loaders", lambda: ("qlib", "csi300", None),
    )
    monkeypatch.setattr(cq, "codex_alpha_bench_names", lambda: ("alpha001",))
    panel_seen = _patch_panel(monkeypatch)
    bench_seen = {}

    def _run_bench(panel, *, prev_bench=None, **kwargs):
        bench_seen.update({"panel": panel, "prev_bench": prev_bench, **kwargs})
        return pd.DataFrame([{
            "name": "mom", "family": "momentum", "rank_ic": 0.052,
            "rank_ir": 0.81, "hit_rate": 0.57, "n_dates": 40,
            "state": "ok", "status": "ok",
        }])

    monkeypatch.setattr(bench_runner, "run_bench", _run_bench)
    out = cq.codex_alpha_bench_query_impl(
        universe="sample30", since="2026-01-01", until="2026-03-01", top=3,
    )
    assert out["ok"] is True and out["artifact"] is None
    assert out["raw"]["persisted"] is False
    assert bench_seen["prev_bench"] is None
    assert bench_seen["names"] == ["alpha001"]
    assert panel_seen["codes"] == ["SH600000", "SZ000001"]
    assert panel_seen["benchmark_loader"] == "csi300"
    assert "未保存 bench CSV" in out["content"]

    with pytest.raises(ValueError, match="不允许保存"):
        cq.codex_alpha_bench_query_impl(save=True)


def test_factor_report_query_never_archives(monkeypatch):
    from financial_analyst.factors.eval import report as report_module

    monkeypatch.setattr(
        cq, "_trusted_universe_codes", lambda name: ["SH600000", "SZ000001"],
    )
    monkeypatch.setattr(
        cq, "_read_only_loaders", lambda: ("qlib", "csi300", "industry"),
    )
    panel_seen = _patch_panel(monkeypatch)
    monkeypatch.setattr(cq, "_trusted_alpha_spec", lambda name: SimpleNamespace(
        name=name, compute=lambda panel: panel, family="alpha101",
    ))
    report_seen = {}

    def _build_report(panel, compute, config, label, family):
        report_seen.update({
            "panel": panel, "config": config, "label": label, "family": family,
        })
        return SimpleNamespace(
            status="ok", error=None, warnings=[],
            meta=SimpleNamespace(
                factor=label, n_codes=2, freq=config.freq,
                start=config.start, end=config.end, n_dates=24, fwd_days=21,
            ),
            ic=SimpleNamespace(
                ic_mean=0.04, icir=0.7, ic_tstat=2.1, ic_win_rate=0.58,
                rank_ic_mean=0.05, rank_icir=0.8,
            ),
            quantile=SimpleNamespace(monotonicity=0.9, long_short_spread=0.12),
            portfolio=SimpleNamespace(
                ann_return=0.15, sharpe=1.2, max_drawdown=-0.08,
                turnover=0.3, win_rate=0.56,
            ),
            characteristics=SimpleNamespace(
                coverage=0.95, autocorr_1=0.4, half_life=5,
            ),
        )

    monkeypatch.setattr(report_module, "build_report", _build_report)
    out = cq.codex_factor_report_query_impl(
        "book_to_price", universe="sample30", freq="month",
        start="2025-01-01", end="2026-01-01",
    )
    assert out["ok"] is True and out["artifact"] is None
    assert out["raw"]["persisted"] is False
    assert panel_seen["codes"] == ["SH600000", "SZ000001"]
    assert panel_seen["industry_loader"] == "industry"
    assert report_seen["label"] == "book_to_price"
    assert "未写 research archive" in out["content"]

    with pytest.raises(ValueError, match="不允许归档"):
        cq.codex_factor_report_query_impl("book_to_price", archive=True)
    with pytest.raises(ValueError, match="不允许归档"):
        cq.codex_factor_report_query_impl("book_to_price", note="write me")


def test_query_tools_reject_non_named_universes_before_loading(monkeypatch):
    monkeypatch.setattr(
        cq, "_read_only_loaders",
        lambda: pytest.fail("loader must not be opened for a rejected universe"),
    )
    with pytest.raises(ValueError, match="不允许股票池"):
        cq.codex_alpha_bench_query_impl(universe="../../secret.txt")
    with pytest.raises(ValueError, match="不允许股票池"):
        cq.codex_factor_report_query_impl("mom", universe="C:\\secret.txt")


def test_factor_report_rejects_expression_write_primitive_before_panel(monkeypatch, tmp_path):
    target = tmp_path / "must_not_exist.csv"
    monkeypatch.setattr(
        cq, "_trusted_alpha_spec",
        lambda name: (_ for _ in ()).throw(ValueError("只允许内置已注册 Alpha 名称")),
    )
    monkeypatch.setattr(
        cq, "_trusted_universe_codes",
        lambda name: pytest.fail("universe must not be loaded for rejected expression"),
    )
    with pytest.raises(ValueError, match="只允许内置"):
        cq.codex_factor_report_query_impl(f"close.to_csv('{target.as_posix()}')")
    assert not target.exists()


def test_trusted_alpha_list_excludes_user_registry(monkeypatch):
    trusted_compute = lambda panel: panel
    trusted_compute.__module__ = "financial_analyst.factors.zoo.alpha101.alphas"
    user_compute = lambda panel: panel
    user_compute.__module__ = "user_saved_factor"
    specs = [
        SimpleNamespace(name="alpha001", family="alpha101", compute=trusted_compute),
        SimpleNamespace(name="evil", family="user", compute=user_compute),
        SimpleNamespace(name="spoof", family="alpha101", compute=user_compute),
    ]
    from financial_analyst.factors.zoo import registry
    monkeypatch.setattr(registry, "list_alphas", lambda: specs)
    assert cq.codex_trusted_alpha_names() == ("alpha001",)
