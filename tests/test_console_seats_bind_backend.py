"""Server-owned ww_seats_bind control plane and UI mirror contract."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from guanlan_v2.console import tools as ct
from guanlan_v2.seats import watch_assignments as wa


def _install_control(monkeypatch, *, artifacts=None, assignments=None):
    """Truthful in-memory model of archive + authoritative assignment APIs."""
    state = {
        "artifacts": {row["id"]: dict(row) for row in (artifacts or [])},
        "assignments": {str(row["code"]): dict(row) for row in (assignments or [])},
        "calls": [],
    }

    def fake_get(path, timeout=30):
        state["calls"].append(("GET", path, None))
        if path == "/archive/list":
            return {"ok": True, "items": list(state["artifacts"].values())}
        if path == "/seats/watch/assignments":
            return {"ok": True, "assignments": list(state["assignments"].values())}
        raise AssertionError(path)

    def fake_post(path, payload, timeout=120):
        state["calls"].append(("POST", path, payload))
        if path != "/archive/put":
            raise AssertionError(path)
        row = dict(payload["artifact"])
        state["artifacts"][row["id"]] = row
        return {"ok": True, "id": row["id"]}

    def fake_put(path, payload, timeout=120):
        state["calls"].append(("PUT", path, payload))
        code = path.rsplit("/", 1)[-1]
        current = state["assignments"].get(code) or {}
        assert payload["expected_revision"] == int(current.get("revision") or 0)
        strategy = state["artifacts"][payload["strategy_id"]]
        row = {
            "code": code,
            "enabled": bool(payload["enabled"]),
            "strategy_id": payload["strategy_id"],
            "strategy_name": strategy["name"],
            "evidence_tf": payload["evidence_tf"],
            "requested_evidence_tf": payload["evidence_tf"],
            "effective_evidence_tf": payload["evidence_tf"],
            "decision_cadence": payload["decision_cadence"],
            "thinking_mode": payload["thinking_mode"],
            "revision": int(current.get("revision") or 0) + 1,
            "source": "explicit",
            "status": "active",
            "runnable": True,
        }
        state["assignments"][code] = row
        return {"ok": True, "assignment": row}

    monkeypatch.setattr(ct, "_self_get", fake_get)
    monkeypatch.setattr(ct, "_self_post", fake_post)
    monkeypatch.setattr(ct, "_self_put", fake_put)
    monkeypatch.setattr(ct, "_seat_bind_assignment_store_ready", lambda: True)
    return state


def test_seats_bind_keeps_confirmation_gate_and_declares_real_endpoints():
    spec = next(row for row in ct.WW_TOOL_TABLE if row["name"] == "ww_seats_bind")
    assert spec["confirm"] is True
    assert spec["impl"] is ct.seats_bind_impl
    assert set(spec["reachable"]) == {
        "/archive/list",
        "/archive/put",
        "/seats/watch/assignments",
        "/seats/watch/assignments/{code}",
    }


def test_seats_bind_persists_strategy_before_authoritative_assignment(monkeypatch):
    state = _install_control(monkeypatch)

    result = ct.seats_bind_impl(
        code="SZ000630", name="铜陵有色", creed="盯铜价异动", template="momentum",
    )

    assert result["ok"] is True
    payload = result["artifact"]["payload"]
    assert payload["backendManaged"] is True
    assert payload["controlPlane"] == "watch_assignment_v1"
    assert payload["strategyId"] == "strat_watch_000630_ww"
    assert payload["strategy"]["bind"] == ["000630"]
    assert payload["assignment"]["strategy_id"] == payload["strategyId"]
    assert payload["assignment"]["runnable"] is True
    methods = [(method, path) for method, path, _body in state["calls"]]
    assert methods.index(("POST", "/archive/put")) < methods.index(
        ("PUT", "/seats/watch/assignments/000630")
    )
    assert not any(path == "/seats/watch/assignments/migrate" for _method, path in methods)
    assert "未宣称总闸当前已开启" in result["content"]


def test_seats_bind_repeat_reuses_one_strategy_and_one_assignment(monkeypatch):
    state = _install_control(monkeypatch)

    first = ct.seats_bind_impl("SH600519", name="贵州茅台")
    state["calls"].clear()
    second = ct.seats_bind_impl("SH600519", name="贵州茅台")

    assert first["ok"] is True and second["ok"] is True
    assert first["artifact"]["payload"]["strategyId"] \
        == second["artifact"]["payload"]["strategyId"] \
        == "strat_watch_600519_ww"
    assert list(state["artifacts"]) == ["strat_watch_600519_ww"]
    assert list(state["assignments"]) == ["600519"]
    assert not any(method == "POST" and path == "/archive/put"
                   for method, path, _body in state["calls"])


def test_seats_bind_rejects_concurrent_assignment_change(monkeypatch):
    strategy = {
        "id": "strat_watch_600519_ww", "type": "strategy", "name": "茅台 · 盯盘",
        "template": "momentum", "bind": ["600519"], "refs": [],
        "clock": {"execTF": "day", "decisionFreq": "60min"},
        "creed": "量价齐升", "w": 0, "pa": False,
    }
    initial = {
        "code": "600519", "enabled": True, "strategy_id": strategy["id"],
        "evidence_tf": "day", "decision_cadence": "60min", "thinking_mode": "fast",
        "revision": 7, "source": "explicit", "status": "active", "runnable": True,
    }
    changed = {**initial, "revision": 8, "thinking_mode": "deep"}
    reads = {"assignments": 0}
    puts = []

    def fake_get(path, timeout=30):
        if path == "/archive/list":
            return {"ok": True, "items": [strategy]}
        if path == "/seats/watch/assignments":
            reads["assignments"] += 1
            row = initial if reads["assignments"] == 1 else changed
            return {"ok": True, "assignments": [row]}
        raise AssertionError(path)

    monkeypatch.setattr(ct, "_self_get", fake_get)
    monkeypatch.setattr(ct, "_self_post", lambda *args, **kwargs: pytest.fail("不应改策略"))
    monkeypatch.setattr(ct, "_self_put", lambda *args, **kwargs: puts.append(args))
    monkeypatch.setattr(ct, "_seat_bind_assignment_store_ready", lambda: True)

    result = ct.seats_bind_impl("SH600519")

    assert result["ok"] is False and result["artifact"] is None
    assert "配置在绑定过程中已变化" in result["content"]
    assert puts == []


def test_seats_bind_archive_failure_never_touches_assignment(monkeypatch):
    puts = []
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: (
        {"ok": True, "items": []} if path == "/archive/list"
        else {"ok": True, "assignments": []}
    ))
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120: {
        "ok": False, "reason": "disk full",
    })
    monkeypatch.setattr(ct, "_self_put", lambda *args, **kwargs: puts.append(args))
    monkeypatch.setattr(ct, "_seat_bind_assignment_store_ready", lambda: True)

    result = ct.seats_bind_impl("SH600519")

    assert result["ok"] is False and result["artifact"] is None
    assert "盯盘配置未启用" in result["content"] and "disk full" in result["content"]
    assert puts == []


def test_seats_bind_assignment_failure_reports_partial_strategy(monkeypatch):
    state = _install_control(monkeypatch)
    monkeypatch.setattr(ct, "_self_put", lambda *args, **kwargs: {
        "ok": False, "reason": "revision_conflict", "detail": "revision 冲突",
    })

    result = ct.seats_bind_impl("SH600519")

    assert result["ok"] is False and result["artifact"] is None
    assert "策略已持久化" in result["content"] and "revision 冲突" in result["content"]
    assert "strat_watch_600519_ww" in state["artifacts"]
    assert state["assignments"] == {}


def test_assignment_store_cas_allows_only_one_concurrent_writer(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    strategy_id = "strat_watch_600519_ww"
    (archive_dir / f"{strategy_id}.json").write_text(json.dumps({
        "id": strategy_id, "type": "strategy", "name": "茅台 · 盯盘",
        "bind": ["600519"], "clock": {"execTF": "day", "decisionFreq": "60min"},
    }, ensure_ascii=False), encoding="utf-8")
    assignment_path = tmp_path / "seats_watch_assignments.json"
    assignment_path.write_text(json.dumps({
        "schema_version": 1, "migrated_at": "2026-08-30T00:00:00", "assignments": {},
    }), encoding="utf-8")
    payload = {
        "enabled": True, "strategy_id": strategy_id, "evidence_tf": "day",
        "decision_cadence": "60min", "thinking_mode": "fast", "expected_revision": 0,
    }

    def write_once():
        try:
            return wa.upsert_assignment(
                "600519", payload, path=assignment_path, archive_dir=archive_dir,
            )["revision"]
        except wa.RevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: write_once(), range(2)))

    assert sorted(outcomes, key=str) == [1, "conflict"]
    stored = json.loads(assignment_path.read_text(encoding="utf-8"))["assignments"]
    assert list(stored) == ["600519"] and stored["600519"]["revision"] == 1


def test_backend_managed_ui_mirrors_exact_strategy_and_keeps_receipts():
    source = (Path(__file__).resolve().parents[1] / "ui" / "console" /
              "console-app.jsx").read_text(encoding="utf-8")
    managed = source.split("if (p.backendManaged === true) {", 1)[1].split(
        "\n    if (code &&", 1,
    )[0]
    assert "p.strategy" in managed
    assert "GL.put({ ...strategy" in managed
    assert "Date.now" not in managed and "Math.random" not in managed
    assert "WW_SEAT_BIND_RECEIPT_PREFIX" in source
    assert "applySeatBindEvent(state.sid, ev)" in source
