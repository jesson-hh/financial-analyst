"""Lossless factorlib persistence and replay for structured factor specs."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.factorlib.api import (
    SaveIn, _promote_factor, _save_factor, build_factorlib_router, save_factor_idempotent,
)
from guanlan_v2.factorlib.store import LibraryFactorStore


def _store(tmp_path):
    base = tmp_path / "base"
    mined = tmp_path / "mined"
    base.mkdir(); mined.mkdir()
    return LibraryFactorStore(base_dir=base, mined_dir=mined)


def _spec(*, metadata=None):
    out = {
        "schema": "guanlan.factor_spec.v1",
        "kind": "orthogonalized",
        "base_features": ["rank(close)"],
        "transform": {
            "version": "orthogonalization-v1",
            "method": "ols",
            "industry": False,
            "size": "total_mv",
            "styles": [],
            "existing_factors": [],
            "min_obs": 8,
            "max_condition_number": 1.0e8,
        },
    }
    if metadata is not None:
        out["metadata"] = metadata
    return out


def _register_structured_runtime(name, spec):
    from financial_analyst.factors.zoo.registry import AlphaSpec, register, unregister
    from guanlan_v2.factorlib.specs import compile_factor_entry

    expr, compute, canonical = compile_factor_entry({
        "name": name, "expr": spec["base_features"][0], "factor_spec": spec,
    })
    unregister(name)
    register(AlphaSpec(
        name=name, family="library_mined", description="test",
        formula_text=expr, compute=compute,
    ))
    return canonical


def _panel():
    from financial_analyst.factors.zoo.panel import PanelData

    dates = pd.date_range("2026-01-05", periods=3, freq="D")
    codes = [f"SH600{i:03d}" for i in range(12)]
    idx = pd.MultiIndex.from_product([dates, codes], names=["datetime", "code"])
    pos = np.tile(np.arange(len(codes), dtype=float), len(dates))
    day = np.repeat(np.arange(len(dates), dtype=float), len(codes))
    close = 10.0 + pos + 0.2 * np.sin(pos + day)
    return PanelData(pd.DataFrame({
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 1000.0 + pos * 20.0,
        "total_mv": np.exp(8.0 + pos / 12.0),
    }, index=idx))


def test_structured_draft_round_trips_metadata_transform_and_promotion(tmp_path):
    store = _store(tmp_path)
    spec = _spec(metadata={"research_run": "rr_1", "note": "保留"})
    result = _save_factor(SaveIn(
        name="lib_structured_roundtrip", status="draft", factor_spec=spec,
        meta={"metrics": {"rank_ic": 0.04}, "owner": "test"},
    ), store)

    assert result["ok"] is True and result["registered"] is True
    assert result["structured"] is True and len(result["factor_spec_digest"]) == 64
    assert result["factor_spec"]["dependencies"]["version"] == "factor-dependencies-v1"
    raw = json.loads((store.mined_dir / result["file"]).read_text(encoding="utf-8"))[0]
    assert raw["expr"] == "rank(close)"  # legacy display field, not execution authority
    assert raw["factor_spec"] == result["factor_spec"]
    assert raw["factor_spec"]["metadata"]["note"] == "保留"
    assert raw["meta"]["owner"] == "test"
    assert raw["status"] == "draft"

    row = {r["name"]: r for r in store.list_factors(validate=True)}["lib_structured_roundtrip"]
    assert row["valid"] is True and row["structured"] is True
    assert row["factor_spec"] == raw["factor_spec"] and row["meta"] == raw["meta"]
    assert _promote_factor("lib_structured_roundtrip", store)["ok"] is True
    promoted = store.load_all()[0]
    assert "status" not in promoted and promoted["factor_spec"] == raw["factor_spec"]

    from financial_analyst.factors.zoo.registry import unregister
    unregister("lib_structured_roundtrip")


def test_registered_name_replays_transform_after_restart(tmp_path):
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.registry import get, unregister
    from guanlan_v2.workflow.api import _orthogonalize_from_spec

    name = "lib_structured_replay_test"
    unregister(name)
    store = _store(tmp_path)
    try:
        saved = _save_factor(SaveIn(name=name, factor_spec=_spec()), store)
        assert saved["ok"] is True and saved["registered"] is True
        panel = _panel()
        expected, _ = _orthogonalize_from_spec(
            compile_factor("rank(close)")(panel), panel, saved["factor_spec"]["transform"],
        )
        actual = get(name).compute(panel)
        np.testing.assert_allclose(actual.to_numpy(), expected.to_numpy(), equal_nan=True)
        assert "factor_spec:v1" in get(name).tags

        # Simulate process restart: remove the runtime entry and rebuild it only
        # from the JSON record.  The result must remain the transformed factor.
        unregister(name)
        ledger = store.register_all()
        assert ledger["registered"] == 1
        assert ledger["ledger"][0]["structured"] is True
        replayed = get(name).compute(panel)
        np.testing.assert_allclose(replayed.to_numpy(), expected.to_numpy(), equal_nan=True)
    finally:
        unregister(name)


def test_restart_registers_plain_named_dependencies_before_structured_consumers(tmp_path):
    from financial_analyst.factors.zoo.registry import get, unregister

    dependency = "zz_plain_dependency_for_restart"
    target = "aa_structured_consumer_for_restart"
    unregister(dependency); unregister(target)
    store = _store(tmp_path)
    try:
        plain = _save_factor(SaveIn(
            name=dependency, expr="rank(close)",
        ), store)
        assert plain["ok"] is True
        spec = _spec()
        spec["base_features"] = [dependency]
        structured = _save_factor(SaveIn(name=target, factor_spec=spec), store)
        assert structured["ok"] is True

        unregister(dependency); unregister(target)
        ledger = store.register_all()
        assert ledger["registered"] == 2 and ledger["skipped"] == 0
        assert get(target).compute(_panel()).notna().any()
    finally:
        unregister(dependency); unregister(target)


def test_workflow_materializer_replays_named_spec_at_original_preprocess_stage(tmp_path, monkeypatch):
    """Backtest/portfolio/model share _materialize_xy, so exercise the real bridge."""
    from financial_analyst.factors.eval.preprocess import winsorize, zscore
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.registry import unregister
    import financial_analyst.data.loader_factory as loader_factory
    import financial_analyst.factors.zoo.panel_cache as panel_cache
    import guanlan_v2.workflow.api as workflow_api

    name = "lib_structured_materialize_test"
    unregister(name)
    store = _store(tmp_path)
    try:
        saved = _save_factor(SaveIn(name=name, factor_spec=_spec()), store)
        assert saved["ok"] is True
        panel = _panel()
        codes = list(panel.df.index.get_level_values("code").unique())
        monkeypatch.setattr(loader_factory, "get_default_loader", lambda: object())
        monkeypatch.setattr(panel_cache, "load_panel_cached", lambda *a, **k: panel)
        monkeypatch.setattr(workflow_api, "_inject_market_refs", lambda p, *a, **k: (p, []))

        body = workflow_api.ModelTrainIn(
            features=[name], codes=codes, winsorize=True, winsorize_q=0.01,
            standardize=True,
        )
        materialized = workflow_api._materialize_xy(
            body, "custom", [name], "2026-01-05", "2026-01-07",
        )
        assert not hasattr(materialized, "body")
        _panel_out, fe_df, _label, names = materialized
        base = zscore(winsorize(compile_factor("rank(close)")(panel), 0.01))
        expected, _ = workflow_api._orthogonalize_from_spec(
            base, panel, saved["factor_spec"]["transform"],
        )
        np.testing.assert_allclose(fe_df[names[0]].to_numpy(), expected.to_numpy(), equal_nan=True)
        replay = fe_df.attrs["factor_spec_replays"][0]
        assert replay["name"] == name and replay["audit"]["applied"] is True
    finally:
        unregister(name)


def test_feature_build_preview_uses_same_structured_preprocess_order(tmp_path, monkeypatch):
    """The exploratory preview must show the exact series later consumed by ML/backtest."""
    from financial_analyst.factors.eval.preprocess import winsorize, zscore
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.registry import unregister
    import financial_analyst.data.loader_factory as loader_factory
    import financial_analyst.factors.zoo.panel_cache as panel_cache
    import guanlan_v2.workflow.api as workflow_api

    name = "lib_structured_feature_preview_test"
    unregister(name)
    store = _store(tmp_path)
    try:
        saved = _save_factor(SaveIn(name=name, factor_spec=_spec()), store)
        assert saved["ok"] is True and saved["registered"] is True
        panel = _panel()
        codes = list(panel.df.index.get_level_values("code").unique())
        monkeypatch.setattr(loader_factory, "get_default_loader", lambda: object())
        monkeypatch.setattr(panel_cache, "load_panel_cached", lambda *a, **k: panel)
        monkeypatch.setattr(workflow_api, "_inject_market_refs", lambda p, *a, **k: (p, []))

        app = FastAPI()
        app.include_router(workflow_api.build_workflow_router())
        payload = TestClient(app).post("/feature/build", json={
            "features": [name], "codes": codes,
            "start": "2026-01-05", "end": "2026-01-07",
            "fwd_days": 1, "winsorize": True, "winsorize_q": 0.01,
            "standardize": True, "preview_rows": 36,
        }).json()
        assert payload["ok"] is True
        assert payload["factor_spec_replays"][0]["name"] == name
        assert payload["fe"]["factor_spec_replays"][0]["audit"]["applied"] is True

        base = zscore(winsorize(compile_factor("rank(close)")(panel), 0.01))
        expected, _ = workflow_api._orthogonalize_from_spec(
            base, panel, saved["factor_spec"]["transform"],
        )
        preview_values = np.asarray([row[2] for row in payload["preview"]["rows"]], dtype=float)
        np.testing.assert_allclose(
            preview_values, expected.iloc[:len(preview_values)].to_numpy(), equal_nan=True,
        )
    finally:
        unregister(name)


def test_factor_spec_v1_rejects_structured_factor_as_nested_base(tmp_path):
    from financial_analyst.factors.zoo.registry import unregister

    inner = "lib_structured_nested_inner"
    outer = "lib_structured_nested_outer"
    unregister(inner); unregister(outer)
    store = _store(tmp_path)
    try:
        first = _save_factor(SaveIn(name=inner, factor_spec=_spec()), store)
        assert first["ok"] is True and first["registered"] is True
        nested = _spec()
        nested["base_features"] = [inner]
        rejected = _save_factor(SaveIn(name=outer, factor_spec=nested), store)
        assert rejected["ok"] is False
        assert "不支持以结构化因子作为 base_feature" in rejected["reason"]
        assert not (store.mined_dir / f"{outer}.json").exists()
    finally:
        unregister(inner); unregister(outer)


def test_report2_resolves_named_spec_and_surfaces_replay_audit(tmp_path, monkeypatch):
    from financial_analyst.factors.zoo.expr import compile_factor
    from financial_analyst.factors.zoo.registry import unregister
    import financial_analyst.data.loader_factory as loader_factory
    import financial_analyst.factors.eval.report as report_module
    import financial_analyst.factors.zoo.panel_cache as panel_cache
    import guanlan_v2.workflow.api as workflow_api

    name = "lib_structured_report_test"
    unregister(name)
    store = _store(tmp_path)
    try:
        saved = _save_factor(SaveIn(name=name, factor_spec=_spec()), store)
        panel = _panel()
        codes = list(panel.df.index.get_level_values("code").unique())
        monkeypatch.setattr(loader_factory, "get_default_loader", lambda: object())
        monkeypatch.setattr(panel_cache, "load_panel_cached", lambda *a, **k: panel)
        monkeypatch.setattr(workflow_api, "_inject_market_refs", lambda p, *a, **k: (p, []))
        captured = {}

        def stop_after_materialization(p, factor_fn, *args, **kwargs):
            captured["series"] = factor_fn(p)
            raise RuntimeError("test-stop-after-materialization")

        monkeypatch.setattr(report_module, "build_report", stop_after_materialization)
        response = workflow_api._factor_report2(workflow_api.FactorReport2In(
            expr_or_name=name, codes=codes, start="2026-01-05", end="2026-01-07",
        ))
        payload = json.loads(bytes(response.body).decode("utf-8"))
        assert payload["ok"] is False and "test-stop-after-materialization" in payload["reason"]
        expected, _ = workflow_api._orthogonalize_from_spec(
            compile_factor("rank(close)")(panel), panel, saved["factor_spec"]["transform"],
        )
        np.testing.assert_allclose(captured["series"].to_numpy(), expected.to_numpy(), equal_nan=True)
    finally:
        unregister(name)


def test_structured_save_fails_closed_on_lossy_or_unknown_definition(tmp_path):
    store = _store(tmp_path)
    mismatch = _save_factor(SaveIn(
        name="lib_bad_mismatch", expr="rank(volume)", factor_spec=_spec(),
    ), store)
    assert mismatch["ok"] is False and "不一致" in mismatch["reason"]

    unknown = _spec()
    unknown["transform"]["future_magic"] = True
    rejected = _save_factor(SaveIn(name="lib_bad_unknown", factor_spec=unknown), store)
    assert rejected["ok"] is False and "不支持字段" in rejected["reason"]
    assert not list(store.mined_dir.glob("*.json"))


def test_normal_expression_contract_is_unchanged(tmp_path):
    from financial_analyst.factors.zoo.registry import unregister

    store = _store(tmp_path)
    saved = _save_factor(SaveIn(
        name="lib_legacy_unchanged", expr="rank(-delta(close,5))", status="draft",
        meta={"legacy": True},
    ), store)
    assert saved["ok"] is True and "factor_spec" not in saved
    raw = store.load_all()[0]
    assert raw["expr"] == "rank(-delta(close,5))" and "factor_spec" not in raw
    row = store.list_factors(validate=True)[0]
    assert row["valid"] is True and "structured" not in row
    unregister("lib_legacy_unchanged")


def test_structured_replay_rejects_registry_dependency_replacement_and_missing(
    tmp_path, monkeypatch,
):
    from financial_analyst.factors.zoo.registry import (
        AlphaSpec, get, register, unregister,
    )
    import guanlan_v2.factorlib.store as store_module
    from guanlan_v2.factorlib.specs import FactorSpecError
    from guanlan_v2.workflow.executor import NodeError, _exec_factorlib
    import guanlan_v2.workflow.api as workflow_api

    dependency = "test_named_dependency_exact_replay"
    target = "lib_structured_dependency_exact_replay"

    def implementation_a(panel):
        return panel.close

    def implementation_b(panel):
        return panel.volume

    unregister(dependency); unregister(target)
    register(AlphaSpec(
        name=dependency, family="test", description="a",
        formula_text="close", compute=implementation_a,
    ))
    store = _store(tmp_path)
    spec = _spec()
    spec["base_features"] = [dependency]
    try:
        saved = _save_factor(SaveIn(name=target, factor_spec=spec), store)
        assert saved["ok"] is True and saved["registered"] is True
        assert get(target).compute(_panel()).notna().any()

        unregister(dependency)
        register(AlphaSpec(
            name=dependency, family="test", description="b",
            formula_text="volume", compute=implementation_b,
        ))
        with np.testing.assert_raises_regex(FactorSpecError, "定义已漂移"):
            get(target).compute(_panel())
        with np.testing.assert_raises_regex(FactorSpecError, "定义已漂移"):
            workflow_api._materialize_structured_base(get(target), _panel())

        monkeypatch.setattr(store_module, "LibraryFactorStore", lambda: store)
        with np.testing.assert_raises_regex(NodeError, "依赖校验失败"):
            _exec_factorlib({}, {
                "name": target, "expr": dependency,
                "factor_spec": saved["factor_spec"],
                "factor_spec_digest": saved["factor_spec_digest"],
            }, {})

        unregister(dependency)
        with np.testing.assert_raises_regex(FactorSpecError, "既不是已注册因子"):
            get(target).compute(_panel())
    finally:
        unregister(dependency); unregister(target)


def test_structured_replay_pins_named_existing_factor_controls(tmp_path):
    from financial_analyst.factors.zoo.registry import (
        AlphaSpec, get, register, unregister,
    )
    from guanlan_v2.factorlib.specs import FactorSpecError

    dependency = "test_existing_control_exact_replay"
    target = "lib_structured_existing_control_exact_replay"

    def control_a(panel):
        return panel.volume

    def control_b(panel):
        return panel.close

    unregister(dependency); unregister(target)
    register(AlphaSpec(
        name=dependency, family="test", description="a",
        formula_text="volume", compute=control_a,
    ))
    store = _store(tmp_path)
    spec = _spec()
    spec["transform"].update({
        "industry": False, "size": "none", "existing_factors": [dependency],
    })
    try:
        saved = _save_factor(SaveIn(name=target, factor_spec=spec), store)
        assert saved["ok"] is True
        binding = saved["factor_spec"]["dependencies"]["existing_factors"][0]
        assert binding["name"] == dependency and len(binding["definition_digest"]) == 64
        unregister(dependency)
        register(AlphaSpec(
            name=dependency, family="test", description="b",
            formula_text="close", compute=control_b,
        ))
        with np.testing.assert_raises_regex(FactorSpecError, "定义已漂移"):
            get(target).compute(_panel())
    finally:
        unregister(dependency); unregister(target)


def test_unsaved_factorlib_to_orthogonalize_returns_canonical_pinned_spec(monkeypatch):
    import guanlan_v2.factorlib.store as store_module
    from guanlan_v2.factorlib.specs import factor_spec_digest
    from guanlan_v2.workflow.executor import _exec_factorlib, _exec_orthogonalize

    class EmptyStore:
        def list_factors(self, validate=False):
            return []

    monkeypatch.setattr(store_module, "LibraryFactorStore", EmptyStore)
    source = _exec_factorlib({}, {
        "name": "legacy_plain_factor", "expr": "rank(close)",
    }, {})["out"]
    factor = _exec_orthogonalize(
        {"signal": source},
        {"industry": "否", "size": "总市值", "min_obs": 8}, {},
    )["factor"]
    spec = factor["factor_spec"]
    assert spec["dependencies"]["version"] == "factor-dependencies-v1"
    assert spec["dependencies"]["base_feature"]["kind"] == "dsl"
    assert factor["factor_spec_digest"] == factor_spec_digest(spec)


def test_backtest_preflight_canonicalizes_unsaved_one_factor_spec():
    import guanlan_v2.workflow.api as workflow_api
    from guanlan_v2.factorlib.specs import factor_spec_digest

    canonical = workflow_api._pin_explicit_orthogonalized_factor(
        ["rank(close)"],
        {"industry": False, "size": "total_mv", "min_obs": 8},
    )
    assert canonical is not None
    assert canonical["transform"]["version"] == "orthogonalization-v1"
    assert canonical["dependencies"]["base_feature"]["formula_text"] == "rank(close)"
    assert len(factor_spec_digest(canonical)) == 64


def test_server_workflow_terminal_returns_canonical_spec_and_digest(monkeypatch):
    from fastapi.responses import JSONResponse
    import guanlan_v2.workflow.executor as executor

    factor = executor._exec_orthogonalize(
        {"signal": {"expr": "rank(close)", "_label": "legacy"}},
        {"industry": "否", "size": "总市值", "min_obs": 8}, {},
    )["factor"]
    monkeypatch.setattr(
        executor, "_call_backtest",
        lambda body: JSONResponse({
            "ok": True, "status": "ok", "portfolio": {}, "warnings": [],
        }),
    )
    result = executor._exec_backtest(
        {"factor": factor}, {},
        {"universe": "csi_fast", "oos_frac": 0.0, "freq": "month"},
    )["result"]
    assert result["factor_spec"] == factor["factor_spec"]
    assert result["factor_spec_digest"] == factor["factor_spec_digest"]


def test_factorlib_http_validate_list_and_registered_expose_spec(tmp_path):
    from financial_analyst.factors.zoo.registry import unregister

    name = "lib_structured_http"
    unregister(name)
    store = _store(tmp_path)
    app = FastAPI()
    app.include_router(build_factorlib_router(store=store))
    client = TestClient(app)
    try:
        validated = client.post("/factorlib/validate", json={"factor_spec": _spec()}).json()
        assert validated["ok"] is True and validated["structured"] is True
        saved = client.post("/factorlib/save", json={
            "name": name, "factor_spec": _spec(), "status": "draft",
        }).json()
        assert saved["ok"] is True
        listed = client.get("/factorlib/list?validate=true").json()["factors"][0]
        assert listed["factor_spec"] == saved["factor_spec"] and listed["valid"] is True
        registered = {r["name"]: r for r in client.get("/factorlib/registered").json()["registered"]}
        assert registered[name]["factor_spec_digest"] == saved["factor_spec_digest"]
    finally:
        unregister(name)


def test_workspace_idempotency_compares_full_factor_spec(tmp_path):
    from financial_analyst.factors.zoo.registry import unregister

    store = _store(tmp_path)
    provenance = {
        "workspace_id": "rw_aaaaaaaaaaaa",
        "workspace_asset_id": "wa_1111111111111111",
        "workspace_version": 3,
    }
    first = save_factor_idempotent(SaveIn(
        name="lib_structured_workspace", source="research_workspace_publish",
        meta=provenance, factor_spec=_spec(),
    ), store, **provenance)
    assert first["ok"] is True and first["structured"] is True
    try:
        recovered = save_factor_idempotent(SaveIn(
            name="ignored_second_name", source="research_workspace_publish",
            meta=provenance, factor_spec=_spec(),
        ), store, **provenance)
        assert recovered["ok"] is True and recovered["recovered"] is True
        assert recovered["name"] == "lib_structured_workspace"

        changed = _spec()
        changed["transform"]["min_obs"] = 9
        mismatch = save_factor_idempotent(SaveIn(
            name="ignored_third_name", source="research_workspace_publish",
            meta=provenance, factor_spec=changed,
        ), store, **provenance)
        assert mismatch["ok"] is False and "factor_spec 不一致" in mismatch["reason"]
    finally:
        unregister("lib_structured_workspace")


def test_workflow_factorlib_node_never_prefers_stale_expr_for_structured_entry(monkeypatch):
    import guanlan_v2.factorlib.store as store_module
    from financial_analyst.factors.zoo.registry import unregister
    from guanlan_v2.factorlib.specs import factor_spec_digest, pin_factor_spec_dependencies
    from guanlan_v2.workflow.executor import _exec_factorlib

    spec = pin_factor_spec_dependencies(_spec())
    digest = factor_spec_digest(spec)
    _register_structured_runtime("lib_structured", spec)

    class FakeStore:
        def list_factors(self, validate=False):
            return [{"name": "lib_structured", "expr": "rank(close)",
                     "factor_spec": spec, "factor_spec_digest": digest}]

    monkeypatch.setattr(store_module, "LibraryFactorStore", FakeStore)
    try:
        out = _exec_factorlib({}, {
            "name": "lib_structured",
            # This value is what an old UI cache may still carry.  It must not win.
            "expr": "rank(close)",
        }, {})["out"]
        assert out["expr"] == "lib_structured"
        assert out["factor_spec"] == spec
        assert out["factor_spec_digest"] == digest
    finally:
        unregister("lib_structured")


def test_workflow_structured_factor_reference_is_exact_and_digest_pinned(monkeypatch):
    import guanlan_v2.factorlib.store as store_module
    from financial_analyst.factors.zoo.registry import unregister
    from guanlan_v2.factorlib.specs import factor_spec_digest, pin_factor_spec_dependencies
    from guanlan_v2.workflow.executor import NodeError, _exec_factorlib

    spec = pin_factor_spec_dependencies(_spec())
    digest = factor_spec_digest(spec)
    _register_structured_runtime("lib_structured_exact", spec)

    class FakeStore:
        def list_factors(self, validate=False):
            return [{"name": "lib_structured_exact", "expr": "rank(close)",
                     "factor_spec": spec, "factor_spec_digest": digest}]

    monkeypatch.setattr(store_module, "LibraryFactorStore", FakeStore)
    pinned = {
        "name": "lib_structured_exact", "expr": "rank(close)",
        "factor_spec": spec, "factor_spec_digest": digest,
    }
    try:
        out = _exec_factorlib({}, pinned, {})["out"]
        assert out["expr"] == "lib_structured_exact"

        with np.testing.assert_raises_regex(NodeError, "digest 不一致"):
            _exec_factorlib({}, {**pinned, "factor_spec_digest": "0" * 64}, {})
        with np.testing.assert_raises_regex(NodeError, "已缺失"):
            _exec_factorlib({}, {**pinned, "name": "lib_structured"}, {})
    finally:
        unregister("lib_structured_exact")


def test_promoted_structured_factor_enters_screen_catalog_by_name_not_base_expr(tmp_path, monkeypatch):
    from financial_analyst.factors.zoo.registry import unregister
    import guanlan_v2.factorlib.store as store_module
    import guanlan_v2.screen.catalog as catalog

    name = "lib_structured_screen"
    unregister(name)
    store = _store(tmp_path)
    try:
        saved = _save_factor(SaveIn(name=name, factor_spec=_spec()), store)
        assert saved["ok"] is True
        monkeypatch.setattr(store_module, "LibraryFactorStore", lambda: store)
        defs = catalog._build()
        assert defs[name]["expr"] == name
        assert defs[name]["factor_spec"] == saved["factor_spec"]
    finally:
        unregister(name)


def test_structured_draft_vintage_sweep_uses_registered_name(monkeypatch):
    import guanlan_v2.factorlib.store as store_module
    import guanlan_v2.screen.catalog as catalog
    import guanlan_v2.screen.factor_vintage as vintage

    class FakeStore:
        def list_factors(self, validate=False):
            return [{"name": "lib_structured_draft", "expr": "rank(close)",
                     "factor_spec": _spec(), "status": "draft"}]

    monkeypatch.setattr(store_module, "LibraryFactorStore", FakeStore)
    monkeypatch.setattr(catalog, "FACTOR_DEFS", {})
    items = dict(vintage._sweep_items())
    assert items["lib_structured_draft"]["expr"] == "lib_structured_draft"
