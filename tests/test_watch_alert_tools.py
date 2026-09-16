import guanlan_v2.console.tools as ct


def test_alert_tools_are_registered_with_the_right_gate():
    by = {row["name"]: row for row in ct.WW_TOOL_TABLE}
    assert by["ww_watch_alert_research"]["confirm"] is False
    assert by["ww_watch_alerts"]["confirm"] is False
    assert by["ww_watch_alert_manage"]["confirm"] is True
    assert {"ww_watch_alert_research", "ww_watch_alerts", "ww_watch_alert_manage"} <= ct.CONSOLE_ALLOWED


def test_llm_create_is_always_disabled_until_a_second_confirm(monkeypatch):
    captured = {}

    def fake_post(path, payload, timeout=120):
        captured.update(path=path, payload=payload)
        return {"ok": True, "rule": {"id": "wa_demo", **payload}}

    monkeypatch.setattr(ct, "_self_post", fake_post)
    out = ct.watch_alert_manage_impl(
        action="create", name="全盘跳水", kind="market_proxy_drop_window_pct",
        threshold=-1.0, window_minutes=15, down_ratio_min=0.7,
        sample_size=60, enabled=True,
    )
    assert out["ok"] is True
    assert captured["path"] == "/watch-alerts/rules"
    assert captured["payload"]["enabled"] is False
    assert captured["payload"]["creator"] == "llm_confirmed"


def test_llm_update_cannot_smuggle_enable(monkeypatch):
    monkeypatch.setattr(ct, "_self_mutate", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not call HTTP")))
    out = ct.watch_alert_manage_impl(action="update", rule_id="wa_demo", enabled=True)
    assert out["ok"] is False and "enable/disable" in out["reason"]


def test_alert_read_tool_uses_live_endpoints(monkeypatch):
    def fake_get(path, timeout=30):
        if path == "/watch-alerts/rules":
            return {"ok": True, "rules": [{"id": "wa_demo"}]}
        if path.startswith("/watch-alerts/events"):
            return {"ok": True, "events": [{"id": "we_demo"}]}
        if path == "/watch-alerts/status":
            return {"ok": True, "engine": "deterministic"}
        raise AssertionError(path)

    monkeypatch.setattr(ct, "_self_get", fake_get)
    out = ct.watch_alerts_impl(view="all", limit=3)
    assert out["ok"] is True
    assert out["rules"][0]["id"] == "wa_demo"
    assert out["events"][0]["id"] == "we_demo"
    assert out["status"]["engine"] == "deterministic"
