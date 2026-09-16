# -*- coding: utf-8 -*-
"""Panel-path precedence for the mainline classifier."""
from __future__ import annotations

import asyncio

import pandas as pd

from financial_analyst.agent.mainline import mainline_classifier as mc


def _panel(path, last_date: str):
    previous = (pd.Timestamp(last_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    pd.DataFrame([
        {"datetime": previous, "industry": "通信", "status": "initiation", "ex_60d": 1.0},
        {"datetime": last_date, "industry": "通信", "status": "mainline", "ex_60d": 2.0},
    ]).to_parquet(path, index=False)
    return path


def _run(agent, **inputs):
    return asyncio.run(agent._execute({"asof_date": "2099-12-31", **inputs}))


def test_default_prefers_repo_canonical_and_reads_its_latest_snapshot(tmp_path, monkeypatch):
    canonical = _panel(tmp_path / "canonical.parquet", "2026-08-13")
    legacy = _panel(tmp_path / "legacy.parquet", "2026-06-05")
    monkeypatch.delenv("FA_MAINLINE_PANEL", raising=False)
    monkeypatch.setattr(mc, "CANONICAL_PANEL_PATH", canonical)
    monkeypatch.setattr(mc, "LEGACY_PANEL_PATH", legacy)

    output = _run(mc.MainlineClassifier(memory_root=tmp_path / "memory"))

    assert output["as_of"] == "2026-08-13"
    assert output["panel_path"] == str(canonical)
    assert output["panel_source"] == "canonical"
    assert output["panel_resolution_note"] == ""


def test_explicit_panel_beats_environment_and_runtime_input_beats_constructor(
    tmp_path, monkeypatch,
):
    env_panel = _panel(tmp_path / "env.parquet", "2026-07-01")
    constructor_panel = _panel(tmp_path / "constructor.parquet", "2026-08-01")
    runtime_panel = _panel(tmp_path / "runtime.parquet", "2026-09-01")
    monkeypatch.setenv("FA_MAINLINE_PANEL", str(env_panel))

    env_output = _run(mc.MainlineClassifier(memory_root=tmp_path / "memory-env"))
    assert env_output["panel_path"] == str(env_panel)
    assert env_output["panel_source"] == "environment"

    explicit_agent = mc.MainlineClassifier(
        memory_root=tmp_path / "memory-explicit",
        panel_path=str(constructor_panel),
    )
    constructor_output = _run(explicit_agent)
    assert constructor_output["panel_path"] == str(constructor_panel)
    assert constructor_output["panel_source"] == "explicit"

    runtime_output = _run(explicit_agent, panel_path=str(runtime_panel))
    assert runtime_output["panel_path"] == str(runtime_panel)
    assert runtime_output["panel_source"] == "explicit"
    assert runtime_output["as_of"] == "2026-09-01"


def test_missing_canonical_falls_back_to_legacy_with_visible_reason(tmp_path, monkeypatch):
    canonical = tmp_path / "missing-canonical.parquet"
    legacy = _panel(tmp_path / "legacy.parquet", "2026-06-05")
    monkeypatch.delenv("FA_MAINLINE_PANEL", raising=False)
    monkeypatch.setattr(mc, "CANONICAL_PANEL_PATH", canonical)
    monkeypatch.setattr(mc, "LEGACY_PANEL_PATH", legacy)

    output = _run(mc.MainlineClassifier(memory_root=tmp_path / "memory"))

    assert output["panel_path"] == str(legacy)
    assert output["panel_source"] == "legacy_fallback"
    assert str(canonical) in output["panel_resolution_note"]
    assert output["as_of"] == "2026-06-05"
