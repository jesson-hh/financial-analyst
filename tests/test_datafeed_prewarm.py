# -*- coding: utf-8 -*-
"""datafeed.prewarm 单测(全离线):启动预热的幂等/禁用/覆盖面/吞错契约。"""
import guanlan_v2.datafeed.prewarm as pw


def _run_inline(monkeypatch):
    """把线程启动改为同步执行,便于断言。"""
    # The release test environment disables background prewarm globally.  Tests
    # that exercise the enabled path must opt in explicitly instead of depending
    # on the caller's ambient environment.
    monkeypatch.setenv("GUANLAN_DATAFEED_PREWARM", "1")
    calls = []

    class _T:
        def __init__(self, target=None, daemon=None, name=None):
            self._t = target

        def start(self):
            self._t()

    monkeypatch.setattr(pw.threading, "Thread", _T)
    monkeypatch.setattr(pw.time, "sleep", lambda s: None)
    return calls


def test_prewarm_touches_all_faces_and_is_idempotent(monkeypatch):
    calls = _run_inline(monkeypatch)
    monkeypatch.setattr(pw, "_STARTED", [False])
    import guanlan_v2.datafeed.market_tape as mt
    import guanlan_v2.datafeed.overseas as ov
    import guanlan_v2.datafeed.newsradar as nr
    import guanlan_v2.fundflow.pulse as ff
    monkeypatch.setattr(mt, "read_tape", lambda *a, **k: calls.append("tape") or {})
    monkeypatch.setattr(ov, "read_overseas", lambda *a, **k: calls.append("overseas") or {})
    monkeypatch.setattr(nr, "read_radar_swr", lambda *a, **k: calls.append("radar") or {})
    monkeypatch.setattr(ff, "read_live", lambda kind, *a, **k: calls.append(f"ff:{kind}") or {})

    assert pw.start_prewarm(delay_s=0) is True
    assert calls == ["tape", "overseas", "ff:industry", "ff:concept", "radar"]
    # 幂等:第二次不再跑
    assert pw.start_prewarm(delay_s=0) is False
    assert len(calls) == 5


def test_prewarm_disabled_by_env(monkeypatch):
    monkeypatch.setattr(pw, "_STARTED", [False])
    monkeypatch.setenv("GUANLAN_DATAFEED_PREWARM", "0")
    assert pw.start_prewarm() is False
    assert pw._STARTED[0] is False   # 禁用不占幂等票,开关翻回后仍可预热


def test_prewarm_swallows_face_errors(monkeypatch):
    """单面预热炸了绝不拖垮其余面(各面自己的 note/stale 显形)。"""
    calls = _run_inline(monkeypatch)
    monkeypatch.setattr(pw, "_STARTED", [False])
    import guanlan_v2.datafeed.market_tape as mt
    import guanlan_v2.datafeed.overseas as ov
    import guanlan_v2.datafeed.newsradar as nr
    import guanlan_v2.fundflow.pulse as ff

    def _boom(*a, **k):
        raise RuntimeError("face down")

    monkeypatch.setattr(mt, "read_tape", _boom)
    monkeypatch.setattr(ov, "read_overseas", lambda *a, **k: calls.append("overseas") or {})
    monkeypatch.setattr(nr, "read_radar_swr", lambda *a, **k: calls.append("radar") or {})
    monkeypatch.setattr(ff, "read_live", lambda kind, *a, **k: calls.append(f"ff:{kind}") or {})
    assert pw.start_prewarm(delay_s=0) is True
    assert calls == ["overseas", "ff:industry", "ff:concept", "radar"]
