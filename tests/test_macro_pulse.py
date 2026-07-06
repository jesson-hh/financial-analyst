# -*- coding: utf-8 -*-
"""macro pulse:锚定温度算术/快照沉淀+Δ24h/脏行容忍/stale 回退。全 mock 不打真 API。"""
import json
from datetime import datetime, timedelta

import pytest

from guanlan_v2.macro import pulse as mp

from test_macro_sources import FakeHttp


# ── 温度算术(表驱动) ────────────────────────────────────────────────────────

def _mk(question, prob, mid="x"):
    return {"source": "polymarket", "id": f"pm_{mid}", "question": question,
            "prob": prob, "volume": 1.0, "close_time": "", "url": ""}


@pytest.mark.parametrize("markets,anchors,want_temp,want_hits", [
    # risk-off 锚:概率 0.9 → 50+50*(-1)*(0.4) = 30
    ([_mk("china x taiwan clash", 0.9)],
     [{"match": "china x taiwan", "direction": -1, "weight": 1.0}], 30.0, 1),
    # risk-on 锚:概率 0.9 → 70
    ([_mk("rate cuts in 2026", 0.9)],
     [{"match": "rate cuts in 2026", "direction": 1, "weight": 1.0}], 70.0, 1),
    # 双锚加权:w2 是 w1 的 3 倍
    ([_mk("aaa", 1.0, "a"), _mk("bbb", 0.0, "b")],
     [{"match": "aaa", "direction": 1, "weight": 1.0},
      {"match": "bbb", "direction": 1, "weight": 3.0}],
     50.0 + 50.0 * (0.5 - 3 * 0.5) / 4.0, 2),
    # 0 锚点 → None(温度诚实缺席)
    ([_mk("whatever", 0.5)], [], None, 0),
    # 锚未命中 → None
    ([_mk("whatever", 0.5)], [{"match": "no-hit", "direction": 1, "weight": 1}], None, 0),
])
def test_theme_temp_arithmetic(markets, anchors, want_temp, want_hits):
    temp, hits = mp._theme_temp(markets, anchors)
    assert hits == want_hits
    if want_temp is None:
        assert temp is None
    else:
        assert temp == pytest.approx(want_temp)


def test_theme_temp_single_hit_in_bounds():
    temp, hits = mp._theme_temp(
        [_mk("doom", 1.0)],
        [{"match": "doom", "direction": -1, "weight": 1.0}])
    assert hits == 1 and temp == 25.0  # 50+50*(-0.5),单锚极值仍在 [0,100]


# ── build_pulse:现拉+快照+Δ ─────────────────────────────────────────────────

def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def test_build_pulse_writes_snapshot_and_delta(tmp_path):
    snap = tmp_path / "snapshots.jsonl"
    # 预置 25h 前的老快照:同 id 概率 0.5 → Δ = now_prob - 0.5;外加脏行须容忍
    old = {"ts": _iso(datetime.now() - timedelta(hours=25)),
           "markets": [{"source": "polymarket", "id": "pm_517311", "prob": 0.5}],
           "temps": {}, "astock_temp": None}
    snap.write_text(json.dumps(old) + "\n" + "NOT-JSON-DIRTY-LINE\n", encoding="utf-8")

    out = mp.build_pulse(refresh=True, snapshot_path=snap, http=FakeHttp())
    assert out["ok"] is True and out["stale_minutes"] is None
    fed = next(t for t in out["themes"] if t["id"] == "fed")
    m = next(x for x in fed["markets"] if x["id"] == "pm_517311")
    assert m["prob"] == 0.63 and m["delta24h"] == pytest.approx(0.13)
    # 无历史的市场 Δ 诚实 None
    others = [x for x in fed["markets"] if x["id"] != "pm_517311"]
    assert all(x["delta24h"] is None for x in others)
    # 快照追加了一行(老行+脏行+新行)
    lines = snap.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    newest = json.loads(lines[-1])
    assert {"ts", "markets", "temps", "astock_temp"} <= set(newest)
    # 总温度 = 有锚主题等权均值,且在 [0,100]
    g = out["thermometer"]["global"]
    assert g is None or 0.0 <= g <= 100.0


def test_build_pulse_nonrefresh_returns_stale_snapshot(tmp_path):
    snap = tmp_path / "snapshots.jsonl"
    old = {"ts": _iso(datetime.now() - timedelta(hours=2)),
           "markets": [{"source": "polymarket", "id": "pm_1", "prob": 0.4}],
           "temps": {"fed": 44.0}, "astock_temp": 50.0}
    snap.write_text(json.dumps(old) + "\n", encoding="utf-8")

    class Boom:
        def get(self, *a, **k):
            raise AssertionError("非 refresh 不得打网络")

    out = mp.build_pulse(refresh=False, snapshot_path=snap, http=Boom())
    assert out["ok"] is True
    assert out["stale_minutes"] == pytest.approx(120, abs=5)
    assert out["thermometer"]["global"] == 44.0
    assert out["thermometer"]["astock"] == 50.0


def test_build_pulse_no_snapshot_forces_fetch(tmp_path):
    snap = tmp_path / "snapshots.jsonl"
    out = mp.build_pulse(refresh=False, snapshot_path=snap, http=FakeHttp())
    assert out["ok"] is True and out["stale_minutes"] is None
    assert snap.exists()


def test_build_pulse_total_failure_honest_empty(tmp_path):
    snap = tmp_path / "snapshots.jsonl"

    class AllFail:
        def get(self, *a, **k):
            raise ConnectionError("total outage")

    out = mp.build_pulse(refresh=True, snapshot_path=snap, http=AllFail())
    assert out["ok"] is True
    assert all(not t["markets"] for t in out["themes"])
    assert out["notes"]  # 每 tag/series 一条失败 note
    assert out["thermometer"]["global"] is None
    assert not snap.exists()  # 全空不落快照


def test_load_history_filters_by_market(tmp_path):
    snap = tmp_path / "snapshots.jsonl"
    rows = [
        {"ts": "2026-07-04T10:00:00",
         "markets": [{"source": "polymarket", "id": "pm_1", "prob": 0.40}],
         "temps": {"fed": 40.0}, "astock_temp": None},
        {"ts": "2026-07-05T10:00:00",
         "markets": [{"source": "polymarket", "id": "pm_1", "prob": 0.55}],
         "temps": {"fed": 55.0}, "astock_temp": None},
    ]
    snap.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    hist = mp.load_history(market_id="pm_1", snapshot_path=snap)
    assert [h["prob"] for h in hist] == [0.40, 0.55]
    themed = mp.load_history(theme="fed", snapshot_path=snap)
    assert [h["temp"] for h in themed] == [40.0, 55.0]
