"""Thin Console tool contract for deterministic workspace quant charts."""
from __future__ import annotations

import base64
import inspect
import json
from contextlib import contextmanager
from pathlib import Path

from guanlan_v2.console import workspace_tools as wt
from guanlan_v2.console.media import MediaStore
from guanlan_v2.console.store import ConsoleStore


# Structurally valid, CRC-checked 1x1 PNG.  The wrapper test must exercise the
# real private-media publication boundary without touching repository ``var``.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@contextmanager
def _bound(store: ConsoleStore, sid: str):
    token = wt.CTX_WORKSPACE.set(wt.bind_workspace_context(store, sid))
    try:
        yield
    finally:
        wt.CTX_WORKSPACE.reset(token)


def _stock_space(store: ConsoleStore):
    workspace = store.workspace_store.create({
        "name": "太极实业",
        "kind": "stock",
        "code": "600667",
        "agent_mode": "research",
    })
    sid = store.create_session("量化位置", workspace_id=workspace["id"])["id"]
    return workspace, sid


def test_quant_chart_tool_is_registered_without_user_controlled_scope():
    from guanlan_v2.console import tools as ct

    spec = next(t for t in ct.WW_TOOL_TABLE if t["name"] == "ww_workspace_quant_chart")
    properties = spec["input_schema"]["properties"]

    assert set(properties) == {"benchmark", "asof"}
    assert {"code", "root", "path"}.isdisjoint(properties)
    assert {"code", "root", "path"}.isdisjoint(
        inspect.signature(spec["impl"]).parameters,
    )
    assert properties["benchmark"]["enum"] == ["csi300"]
    assert spec["confirm"] is False

    allowed = wt.allowed_tools_for_workspace(
        ct.CONSOLE_ALLOWED,
        {"id": "rw_0123456789ab", "agent_mode": "research"},
    )
    assert "ww_workspace_quant_chart" in allowed
    assert "ww_workspace_run" not in allowed


def test_system_prompt_routes_the_request_to_one_deterministic_chart_call():
    from guanlan_v2.console.api import _SYSTEM_PROMPT

    assert "优先且只需一次调用 ww_workspace_quant_chart" in _SYSTEM_PROMPT
    assert "不要临时编写Python重复计算" in _SYSTEM_PROMPT
    assert "current_ready" in _SYSTEM_PROMPT
    assert "unavailable" in _SYSTEM_PROMPT
    assert "ETF空间暂不调用该工具" in _SYSTEM_PROMPT


def test_bound_stock_quant_chart_builds_once_and_returns_media_image(
        tmp_path, monkeypatch):
    store = ConsoleStore(root=tmp_path / "console")
    workspace, sid = _stock_space(store)
    calls = []

    def fake_build(subject, code, *, output_dir, benchmark, windows, reference_date):
        output_dir = Path(output_dir)
        calls.append({
            "subject": subject,
            "code": code,
            "output_dir": output_dir,
            "benchmark": benchmark,
            "windows": windows,
            "reference_date": reference_date,
        })
        output_dir.joinpath("position.png").write_bytes(_PNG)
        result = {
            "current_ready": True,
            "position_price": 22.95,
            "position_asof": "2026-08-14",
            "analysis_asof": "2026-08-14",
            "benchmark_asof": "2026-08-14",
            "common_cutoff": "2026-08-14",
            "position_levels": {
                "supports": [{"price": 22.61}, {"price": 21.96}],
                "resistances": [{"price": 23.00}, {"price": 23.94}],
            },
            "regressions": [
                {"window": 20, "available": True, "n": 20,
                 "corr": 0.21, "alpha_daily": 0.001, "beta": 1.1, "r2": 0.04},
                {"window": 60, "available": True, "n": 60,
                 "corr": 0.42, "alpha_daily": 0.002, "beta": 1.2, "r2": 0.18},
                {"window": 120, "available": True, "n": 120,
                 "corr": 0.51, "alpha_daily": 0.003, "beta": 1.3, "r2": 0.26},
            ],
            "freshness": {"warnings": []},
            "artifacts": {"png": "position.png", "json": "position.json"},
        }
        output_dir.joinpath("position.json").write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8",
        )
        return result

    import guanlan_v2.console.quant_chart as quant_chart
    monkeypatch.setattr(quant_chart, "build_workspace_quant_chart", fake_build)

    with _bound(store, sid):
        response = wt.workspace_quant_chart_impl(asof="2026-08-14")

    assert response["ok"] is True
    assert len(calls) == 1
    call = calls[0]
    assert call["subject"] == "太极实业"
    assert call["code"] == "SH600667"
    assert call["benchmark"] == "csi300"
    assert call["windows"] == (20, 60, 120)
    assert call["reference_date"] == "2026-08-14"
    expected_root = tmp_path / "console" / "workspace_files" / workspace["id"]
    call["output_dir"].relative_to(expected_root)
    assert call["output_dir"].parent == expected_root / "output"

    artifact = response["artifact"]
    assert artifact["kind"] == "media_image"
    assert artifact["channel"] == "conversation"
    assert artifact["ref"] == artifact["payload"]["media_id"]
    assert artifact["payload"]["workspace_id"] == workspace["id"]
    assert artifact["payload"]["mime"] == "image/png"
    assert artifact["payload"]["width"] == artifact["payload"]["height"] == 1
    for window in (20, 60, 120):
        assert f"{window}日 n={window}" in response["content"]
    assert "相关性" in response["content"]
    assert "Alpha(日)" in response["content"]
    assert "Beta" in response["content"]

    metadata, image = MediaStore(store).load(artifact["payload"]["media_id"])
    assert image == _PNG
    assert metadata["workspace_id"] == workspace["id"]


def test_general_workspace_rejects_quant_chart_without_creating_output(tmp_path):
    store = ConsoleStore(root=tmp_path / "console")
    sid = store.create_session("通用研究", workspace_id="rw_general")["id"]

    with _bound(store, sid):
        response = wt.workspace_quant_chart_impl()

    assert response["ok"] is False
    assert response["artifact"] is None
    assert "股票研究空间" in response["content"]
    assert not (tmp_path / "console" / "workspace_files" / "rw_general" / "output").exists()
