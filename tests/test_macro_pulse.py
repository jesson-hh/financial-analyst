# -*- coding: utf-8 -*-
"""macro pulse:锚定温度算术/快照沉淀+Δ24h/脏行容忍/stale 回退。全 mock 不打真 API。"""
import json
from datetime import datetime, timedelta

import pytest

from guanlan_v2.macro import pulse as mp

from test_macro_sources import FakeHttp

_NO_ASTOCK = lambda: {"available": False, "temp": None, "notes": []}  # 测试禁真 probe
_NO_ZH = lambda qs: ({}, "")                                          # 测试禁真 LLM 翻译


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
    temp, hits, _ids = mp._theme_temp(markets, anchors)
    assert hits == want_hits
    if want_temp is None:
        assert temp is None
    else:
        assert temp == pytest.approx(want_temp)


def test_theme_temp_single_hit_in_bounds():
    temp, hits, _ids = mp._theme_temp(
        [_mk("doom", 1.0)],
        [{"match": "doom", "direction": -1, "weight": 1.0}])
    assert hits == 1 and temp == 25.0  # 50+50*(-0.5),单锚极值仍在 [0,100]


# ── build_pulse:现拉+快照+Δ ─────────────────────────────────────────────────

def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def test_build_pulse_writes_snapshot_and_delta(tmp_path, monkeypatch):
    snap = tmp_path / "snapshots.jsonl"
    fixed = datetime(2026, 7, 16, 12, 0, 0)
    monkeypatch.setattr(mp, "_now", lambda: fixed)
    # 预置 25h 前的老快照:同 id 概率 0.5 → Δ = now_prob - 0.5;外加脏行须容忍
    old = {"ts": _iso(fixed - timedelta(hours=25)),
           "markets": [{"source": "polymarket", "id": "pm_517311", "prob": 0.5}],
           "temps": {}, "astock_temp": None}
    snap.write_text(json.dumps(old) + "\n" + "NOT-JSON-DIRTY-LINE\n", encoding="utf-8")

    out = mp.build_pulse(refresh=True, snapshot_path=snap, astock_fn=_NO_ASTOCK, translate_fn=_NO_ZH, http=FakeHttp())
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
    out = mp.build_pulse(refresh=False, snapshot_path=snap, astock_fn=_NO_ASTOCK, translate_fn=_NO_ZH, http=FakeHttp())
    assert out["ok"] is True and out["stale_minutes"] is None
    assert snap.exists()


def test_build_pulse_total_failure_honest_empty(tmp_path):
    snap = tmp_path / "snapshots.jsonl"

    class AllFail:
        def get(self, *a, **k):
            raise ConnectionError("total outage")

    out = mp.build_pulse(refresh=True, snapshot_path=snap, astock_fn=_NO_ASTOCK, translate_fn=_NO_ZH, http=AllFail())
    assert out["ok"] is True
    assert all(not t["markets"] for t in out["themes"])
    assert out["notes"]  # 每 tag/series 一条失败 note
    assert out["thermometer"]["global"] is None
    assert not snap.exists()  # 全空不落快照


def test_build_pulse_attaches_question_zh(tmp_path):
    """翻译层命中 → 市场行带 question_zh;未命中的行诚实无该字段。"""
    snap = tmp_path / "snapshots.jsonl"
    fake_zh = lambda qs: ({qs[0]: "美联储7月会降息吗?"}, "")
    out = mp.build_pulse(refresh=True, snapshot_path=snap, astock_fn=_NO_ASTOCK,
                         translate_fn=fake_zh, http=FakeHttp())
    ms = [m for t in out["themes"] for m in t["markets"]]
    assert any(m.get("question_zh") == "美联储7月会降息吗?" for m in ms)
    assert any("question_zh" not in m for m in ms)


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


# ── 月轮转:snapshots.jsonl 恒为当月,非本月行归档 ───────────

def _row(dt, mid, prob, temps=None):
    return {"ts": _iso(dt),
            "markets": [{"source": "polymarket", "id": mid, "prob": prob}],
            "temps": temps or {}, "astock_temp": None}


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines()]


def test_write_rotates_stale_months_into_archives(tmp_path, monkeypatch):
    fixed = datetime(2026, 7, 16, 12, 0, 0)
    monkeypatch.setattr(mp, "_now", lambda: fixed)
    snap = tmp_path / "snapshots.jsonl"
    previous = datetime(2026, 6, 10, 12, 0, 0)
    older = datetime(2026, 4, 10, 12, 0, 0)
    snap.write_text(json.dumps(_row(older, "pm_old2", 0.2)) + "\n"
                    + json.dumps(_row(previous, "pm_old", 0.3)) + "\n", encoding="utf-8")

    out = mp.build_pulse(refresh=True, snapshot_path=snap,
                         astock_fn=_NO_ASTOCK, translate_fn=_NO_ZH, http=FakeHttp())
    assert out["ok"] is True
    current = _lines(snap)
    assert len(current) == 1 and current[0]["ts"][:7] == "2026-07"
    archived = [row for month in ("202604", "202606")
                for row in _lines(tmp_path / f"snapshots-{month}.jsonl")]
    assert {row["markets"][0]["id"] for row in archived} == {"pm_old", "pm_old2"}


def test_delta24h_reads_latest_archive_across_month_boundary(tmp_path, monkeypatch):
    fixed = datetime(2026, 7, 1, 12, 0, 0)
    monkeypatch.setattr(mp, "_now", lambda: fixed)
    snap = tmp_path / "snapshots.jsonl"
    archive = tmp_path / "snapshots-202606.jsonl"
    archive.write_text(json.dumps(_row(fixed - timedelta(hours=25), "pm_517311", 0.5))
                       + "\n", encoding="utf-8")
    snap.write_text(json.dumps(_row(fixed - timedelta(hours=1), "pm_other", 0.9))
                    + "\n", encoding="utf-8")

    out = mp.build_pulse(refresh=True, snapshot_path=snap,
                         astock_fn=_NO_ASTOCK, translate_fn=_NO_ZH, http=FakeHttp())
    fed = next(theme for theme in out["themes"] if theme["id"] == "fed")
    market = next(row for row in fed["markets"] if row["id"] == "pm_517311")
    assert market["delta24h"] == pytest.approx(0.63 - 0.5)
    assert len(_lines(archive)) == 1


def test_read_recent_skips_archive_when_current_has_old_row(tmp_path):
    now = datetime(2026, 7, 16, 12, 0, 0)
    snap = tmp_path / "snapshots.jsonl"
    (tmp_path / "snapshots-202606.jsonl").write_text(
        json.dumps(_row(now - timedelta(days=30), "pm_marker", 0.1)) + "\n",
        encoding="utf-8")
    snap.write_text(json.dumps(_row(now - timedelta(hours=25), "pm_a", 0.4)) + "\n"
                    + json.dumps(_row(now - timedelta(hours=1), "pm_a", 0.6)) + "\n",
                    encoding="utf-8")
    got = mp._read_recent(snap, now)
    assert {market["id"] for row in got for market in row["markets"]} == {"pm_a"}

    snap.write_text(json.dumps(_row(now - timedelta(hours=1), "pm_a", 0.6)) + "\n",
                    encoding="utf-8")
    got = mp._read_recent(snap, now)
    assert {market["id"] for row in got for market in row["markets"]} == {"pm_a", "pm_marker"}


def test_load_history_spans_archives_and_current(tmp_path):
    snap = tmp_path / "snapshots.jsonl"
    (tmp_path / "snapshots-202606.jsonl").write_text(
        json.dumps(_row(datetime(2026, 6, 20, 10, 0, 0), "pm_1", 0.30, {"fed": 30.0}))
        + "\n", encoding="utf-8")
    snap.write_text(
        json.dumps(_row(datetime(2026, 7, 5, 10, 0, 0), "pm_1", 0.55, {"fed": 55.0}))
        + "\n", encoding="utf-8")
    assert [row["prob"] for row in mp.load_history(market_id="pm_1", snapshot_path=snap)] == [0.30, 0.55]
    assert [row["temp"] for row in mp.load_history(theme="fed", snapshot_path=snap)] == [30.0, 55.0]


def test_rotation_appends_into_existing_archive_dedup_by_ts(tmp_path, monkeypatch):
    fixed = datetime(2026, 7, 16, 12, 0, 0)
    monkeypatch.setattr(mp, "_now", lambda: fixed)
    snap = tmp_path / "snapshots.jsonl"
    old1 = datetime(2026, 6, 10, 12, 0, 0)
    old2 = old1 + timedelta(hours=1)
    archive = tmp_path / "snapshots-202606.jsonl"
    archive.write_text(json.dumps(_row(old1, "pm_x", 0.2)) + "\n", encoding="utf-8")
    snap.write_text(json.dumps(_row(old1, "pm_x", 0.2)) + "\n"
                    + json.dumps(_row(old2, "pm_y", 0.3)) + "\n", encoding="utf-8")

    mp.build_pulse(refresh=True, snapshot_path=snap,
                   astock_fn=_NO_ASTOCK, translate_fn=_NO_ZH, http=FakeHttp())
    got = _lines(archive)
    assert [row["ts"] for row in got] == sorted({row["ts"] for row in got})
    assert {row["markets"][0]["id"] for row in got} == {"pm_x", "pm_y"}
