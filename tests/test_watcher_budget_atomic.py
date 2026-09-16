from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from guanlan_v2.seats import watcher


NOW = datetime(2026, 8, 14, 10, 0)  # Friday, inside the A-share session


def test_tick_never_overwrites_a_concurrent_budget_charge(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "watch.json")
    watcher.save_state({"enabled": True, "daily_budget": 20, "counts": {}})
    monkeypatch.setattr(watcher, "watching_codes", lambda: [{"code": "300750"}])
    monkeypatch.setattr(
        watcher, "_build_payload",
        lambda row, now, quote: {"code": row["code"], "source": "watcher"})

    def decide(_payload):
        # Models a write that happens while the long-running tick is in decide.
        watcher.note_external_llm_use(3, now=NOW)
        return {"ok": True}

    out = watcher.tick(
        now=NOW,
        decide_fn=decide,
        quote_fn=lambda _code: {"fresh": True},
        decisions_tail_fn=lambda _code: None,
    )
    assert out["judged"] == ["300750"]
    # One fast reservation plus the concurrent three-unit charge all survive.
    assert watcher.load_state()["counts"]["2026-08-14"] == 4


def test_fast_budget_reservations_are_atomic_across_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "watch.json")
    watcher.save_state({"enabled": True, "daily_budget": 5, "counts": {}})
    with ThreadPoolExecutor(max_workers=20) as pool:
        admitted = list(pool.map(
            lambda _index: watcher._try_reserve_fast_judgment(NOW), range(20)))
    assert sum(admitted) == 5
    assert watcher.load_state()["counts"]["2026-08-14"] == 5


def test_failed_fast_decision_returns_only_its_own_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "watch.json")
    watcher.save_state({"enabled": True, "daily_budget": 20, "counts": {}})
    monkeypatch.setattr(watcher, "watching_codes", lambda: [{"code": "300750"}])
    monkeypatch.setattr(
        watcher, "_build_payload",
        lambda row, now, quote: {"code": row["code"], "source": "watcher"})

    def decide(_payload):
        watcher.note_external_llm_use(3, now=NOW)
        return {"ok": False, "reason": "model_failed"}

    out = watcher.tick(
        now=NOW,
        decide_fn=decide,
        quote_fn=lambda _code: {"fresh": True},
        decisions_tail_fn=lambda _code: None,
    )
    assert out["skipped"]["300750"] == "model_failed"
    # The unused one-unit fast reservation is returned; the other charge stays.
    assert watcher.load_state()["counts"]["2026-08-14"] == 3
