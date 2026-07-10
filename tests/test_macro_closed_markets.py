# -*- coding: utf-8 -*-
"""真机抓的三缺陷回归锁:已结算子市场混入 / volume 冒充 event 量 / 死市场污染锚定温度。

2026-07-06 真机:Polymarket event "Fed rate hike by...?" 的 event.closed=False,
但子市场 April/June 2026 已 closed=True(会议已过,结算 Yes=0)。这些死市场:
  ① 原市场页面根本不展示,我们展示 0% 属幽灵行;
  ② 其 volume24hr 为空 → 旧码回落 ev.volume24hr,两条不同市场显示同一个量(伪造归属);
  ③ 因此排到成交量榜首,锚定匹配吃到 prob=0 的死市场 → fed 温度伪造成 75.0。
"""
from guanlan_v2.macro import pulse as mp
from guanlan_v2.macro import sources as ms

_EVENT = {
    "title": "Fed rate hike by...?", "slug": "fed-rate-hike-by",
    "closed": False, "volume24hr": 49658.83,
    "markets": [
        # 已结算死市场(会议已过):closed=True,无自身量
        {"id": "1808544", "question": "Fed Rate Hike by April 2026 Meeting?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0", "1"]',
         "closed": True, "active": True, "volume24hr": None, "endDate": "2026-12-09T00:00:00Z"},
        {"id": "1808545", "question": "Fed Rate Hike by June 2026 Meeting?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0", "1"]',
         "closed": True, "active": True, "volume24hr": None, "endDate": "2026-12-09T00:00:00Z"},
        # 活跃市场
        {"id": "1808546", "question": "Fed Rate Hike by July 2026 Meeting?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.153", "0.847"]',
         "closed": False, "active": True, "volume24hr": 15913.8, "endDate": "2026-12-09T00:00:00Z"},
        {"id": "1808547", "question": "Fed Rate Hike by September 2026 Meeting?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.435", "0.565"]',
         "closed": False, "active": True, "volume24hr": 11537.4, "endDate": "2026-12-09T00:00:00Z"},
        {"id": "1808548", "question": "Fed Rate Hike by October 2026 Meeting?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.465", "0.535"]',
         "closed": False, "active": True, "volume24hr": 22207.6, "endDate": "2026-12-09T00:00:00Z"},
    ],
}


class _Resp:
    def __init__(self, payload):
        self._p = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._p


class FakeHttp:
    def get(self, url, params=None, timeout=None):
        if "gamma" in url:
            return _Resp([_EVENT])
        return _Resp({"markets": []})


def test_closed_submarkets_excluded():
    """已结算子市场(closed=True)不得进入行:原市场页不展示它们,我们也不展示。"""
    rows, notes = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    ids = {r["id"] for r in rows}
    assert "pm_1808544" not in ids and "pm_1808545" not in ids, "死市场混入 → 页面出现幽灵 0% 行"
    assert not any(r["prob"] == 0.0 for r in rows)


def test_volume_never_borrows_event_total():
    """market 无自身 24h 量时不得冒充 event 总量(否则同 event 各行显示同一个量)。"""
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    vols = [r["volume"] for r in rows]
    assert 49658.83 not in vols, "market volume 冒用 event.volume24hr"
    assert sorted(vols, reverse=True) == [22207.6, 15913.8, 11537.4]


def test_active_markets_not_crowded_out_by_dead_ones():
    """每 event 取前 N 应在过滤+按量排序之后,活跃市场不被死市场挤出。"""
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    assert len(rows) == 3
    assert rows[0]["id"] == "pm_1808548"  # October,量最大


def test_dead_market_no_longer_forges_theme_temp():
    """锚定温度必须由活跃市场合成:死市场 prob=0 曾把 fed 温度顶到 75.0。"""
    cfg_anchors = [{"match": "fed rate hike", "direction": -1, "weight": 1.0}]
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    rows = sorted(rows, key=lambda m: m.get("volume") or 0, reverse=True)
    temp, hits = mp._theme_temp(rows, cfg_anchors)
    assert hits == 1
    assert temp != 75.0, "温度仍由死市场 prob=0 合成"
    # October prob=0.465,dir=-1 → 50+50*(-1)*(0.465-0.5) = 51.75 ≈ 51.8
    assert abs(temp - 51.8) < 0.05
