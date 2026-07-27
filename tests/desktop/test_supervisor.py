# -*- coding: utf-8 -*-
"""supervisor 的全部行为都在这里定死。这个模块不 import pywebview,故可无头测试。"""
from __future__ import annotations

from guanlan_v2.desktop import supervisor as sv


# ── probe ──────────────────────────────────────────────────────────────
def test_probe_true_on_200():
    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert sv.probe(opener=lambda url, timeout: _Resp()) is True


def test_probe_false_on_exception():
    def _boom(url, timeout):
        raise OSError("connection refused")
    assert sv.probe(opener=_boom) is False


def test_probe_false_on_non_200():
    class _Resp:
        status = 503
        def __enter__(self): return self
        def __exit__(self, *a): return False
    assert sv.probe(opener=lambda url, timeout: _Resp()) is False


def test_probe_targets_9999_health_endpoint():
    seen = {}
    class _Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def _opener(url, timeout):
        seen["url"] = url
        return _Resp()
    sv.probe(opener=_opener)
    assert seen["url"] == "http://127.0.0.1:9999/workflow/list"


# ── GUANLAN_PORT 剔除:2026-07-19~07-26 停机 7.5 天的根因 ──────────────
def test_watchdog_env_strips_guanlan_port():
    env = sv.watchdog_env({"PATH": "x", "GUANLAN_PORT": "9998", "KEEP": "1"})
    assert "GUANLAN_PORT" not in env
    assert env["PATH"] == "x" and env["KEEP"] == "1"


def test_watchdog_env_does_not_mutate_caller_mapping():
    base = {"GUANLAN_PORT": "9998"}
    sv.watchdog_env(base)
    assert base == {"GUANLAN_PORT": "9998"}


def test_spawn_passes_scrubbed_env_to_popen():
    captured = {}
    class _Proc:
        pid = 4242
    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["kw"] = kw
        return _Proc()
    r = sv.spawn_watchdog(popen=_popen, base_env={"GUANLAN_PORT": "9998", "PATH": "x"})
    assert r.ok is True and r.pid == 4242
    assert "GUANLAN_PORT" not in captured["kw"]["env"]


def test_spawn_command_line_launches_the_watchdog_script():
    class _Proc:
        pid = 1
    captured = {}
    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        return _Proc()
    sv.spawn_watchdog(popen=_popen, base_env={})
    joined = " ".join(captured["cmd"])
    assert "check_9999.ps1" in joined
    assert "conhost.exe" in joined and "--headless" in joined


def test_spawn_is_detached():
    class _Proc:
        pid = 1
    captured = {}
    def _popen(cmd, **kw):
        captured["kw"] = kw
        return _Proc()
    sv.spawn_watchdog(popen=_popen, base_env={})
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP —— 与 server.py 的复活循环同口径
    assert captured["kw"]["creationflags"] == 0x00000008 | 0x00000200


def test_spawn_reports_failure_instead_of_raising():
    def _popen(cmd, **kw):
        raise OSError("nope")
    r = sv.spawn_watchdog(popen=_popen, base_env={})
    assert r.ok is False and r.pid is None and "nope" in r.detail


# ── ensure_running ─────────────────────────────────────────────────────
def test_ensure_healthy_on_first_probe_never_spawns():
    def _spawner(**kw):
        raise AssertionError("must not spawn when already healthy")
    out = sv.ensure_running(prober=lambda **kw: True, spawner=_spawner, sleep=lambda s: None)
    assert out.state == "healthy" and out.spawned is False


def test_ensure_spawns_then_succeeds():
    results = iter([False, False, True])
    spawns = []
    out = sv.ensure_running(
        prober=lambda **kw: next(results),
        spawner=lambda **kw: (spawns.append(1), sv.SpawnResult(True, 7, "ok"))[1],
        sleep=lambda s: None,
    )
    assert out.state == "healthy" and out.spawned is True
    assert len(spawns) == 1, "只允许派生一次"


def test_ensure_times_out_and_says_so():
    ticks = iter([0.0, 30.0, 60.0, 95.0])
    out = sv.ensure_running(
        prober=lambda **kw: False,
        spawner=lambda **kw: sv.SpawnResult(True, 7, "ok"),
        sleep=lambda s: None,
        clock=lambda: next(ticks),
        deadline_seconds=90.0,
    )
    assert out.state == "timeout" and out.spawned is True


def test_ensure_reports_spawn_failure_without_waiting():
    out = sv.ensure_running(
        prober=lambda **kw: False,
        spawner=lambda **kw: sv.SpawnResult(False, None, "WMI/Popen boom"),
        sleep=lambda s: None,
    )
    assert out.state == "spawn_failed" and "boom" in out.detail


def test_ensure_emits_progress():
    seen = []
    results = iter([False, True])
    sv.ensure_running(
        prober=lambda **kw: next(results),
        spawner=lambda **kw: sv.SpawnResult(True, 7, "ok"),
        sleep=lambda s: None,
        on_progress=seen.append,
    )
    assert seen, "启动期必须有进度回调,否则引导页只能干等"


# ── 污染警告 ───────────────────────────────────────────────────────────
def test_port_contamination_flags_a_non_9999_value(monkeypatch):
    monkeypatch.setenv("GUANLAN_PORT", "9998")
    assert "9998" in (sv.port_contamination() or "")


def test_port_contamination_silent_when_absent(monkeypatch):
    monkeypatch.delenv("GUANLAN_PORT", raising=False)
    assert sv.port_contamination() is None
