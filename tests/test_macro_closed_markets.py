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
        # 低量活跃市场:量排第 4/5,旧码的 alive[:3] 会把它们整个丢弃——而锚定词恰在其中
        {"id": "1808549", "question": "China x Taiwan military clash before 2027?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.12", "0.88"]',
         "closed": False, "active": True, "volume24hr": 800.0, "endDate": "2026-12-31T00:00:00Z"},
        {"id": "1808550", "question": "US recession declared in 2026?",
         "outcomes": '["Yes", "No"]', "outcomePrices": '["0.30", "0.70"]',
         "closed": False, "active": True, "volume24hr": 500.0, "endDate": "2026-12-31T00:00:00Z"},
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
    assert sorted(vols, reverse=True) == [22207.6, 15913.8, 11537.4, 800.0, 500.0]


def test_anchor_pool_is_all_alive_markets_not_just_displayed_top():
    """锚定搜索池 = 该主题拉到的全部活跃市场,不受每 event 展示切片限制。

    真机:geopolitics 的 military clash 锚定市场成交量排不进 event 前 3,
    旧码(fetch 时 alive[:3])根本没把它们拉进来 → 锚点 0 命中 → 主题温度恒 "—"。
    """
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    ids = {r["id"] for r in rows}
    assert ids == {"pm_1808546", "pm_1808547", "pm_1808548", "pm_1808549", "pm_1808550"}
    assert rows[0]["id"] == "pm_1808548"  # 仍按量降序,October 居首
    assert not any(r["id"] in ("pm_1808544", "pm_1808545") for r in rows)  # 死市场仍剔


def test_low_volume_anchor_market_still_hits():
    """量排第 4 的锚定市场必须参与温度合成(旧码 alive[:3] 里它已消失)。"""
    anchors = [{"match": "military clash", "direction": -1, "weight": 1.0}]
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    rows = sorted(rows, key=lambda m: m.get("volume") or 0, reverse=True)
    temp, hits, _ids = mp._theme_temp(rows, anchors)
    assert hits == 1, "低量锚定市场未进池 → 主题温度会恒为 —"
    # military clash prob=0.12,dir=-1 → 50+50*(-1)*(0.12-0.5) = 69.0
    assert abs(temp - 69.0) < 0.1


def test_display_still_capped_while_anchor_pool_is_full(tmp_path):
    """展示仍按 display_top_n 切片(池全量 ≠ 页面全列)。"""
    snap = tmp_path / "s.jsonl"
    cfg = {"themes": [{"id": "t1", "label": "T1", "polymarket_tags": ["inflation"],
                       "kalshi_series": [], "anchors": [{"match": "military clash",
                                                         "direction": -1, "weight": 1.0}]}],
           "display_top_n": 2}
    import guanlan_v2.macro.pulse as _mp
    orig = ms.load_themes
    ms.load_themes = lambda: cfg
    try:
        out = _mp.build_pulse(refresh=True, snapshot_path=snap,
                              astock_fn=lambda: {"available": False, "temp": None, "notes": []},
                              translate_fn=lambda qs: ({}, ""), http=FakeHttp())
    finally:
        ms.load_themes = orig
    theme = out["themes"][0]
    assert theme["anchor_hits"] == 1, "锚点应在全量池命中(military clash 量排第 4)"
    assert abs(theme["temp"] - 69.0) < 0.1
    ids = [m["id"] for m in theme["markets"]]
    # 展示 = 量前 2 + 锚定命中市场(量排第 4,本不入前 2)
    assert ids == ["pm_1808548", "pm_1808546", "pm_1808549"]
    anchors_shown = [m for m in theme["markets"] if m.get("is_anchor")]
    assert [m["id"] for m in anchors_shown] == ["pm_1808549"], "合成温度的市场必须在页面可见可核"


def test_unmatched_anchors_are_surfaced_not_silent(tmp_path):
    """声明了锚点却一个都没命中 → notes 必须告警。

    真机:themes.yaml 给 geopolitics 写了 "military clash"/"nuclear",而该 tag 下 81 个
    市场无一含这些词,温度静默变 "—",看不出是「无数据」还是「配置写错了」。
    """
    snap = tmp_path / "s.jsonl"
    cfg = {"themes": [{"id": "geo", "label": "地缘", "polymarket_tags": ["inflation"],
                       "kalshi_series": [],
                       "anchors": [{"match": "词库里根本没有的措辞", "direction": -1, "weight": 1.0}]}],
           "display_top_n": 3}
    import guanlan_v2.macro.pulse as _mp
    orig = ms.load_themes
    ms.load_themes = lambda: cfg
    try:
        out = _mp.build_pulse(refresh=True, snapshot_path=snap,
                              astock_fn=lambda: {"available": False, "temp": None, "notes": []},
                              translate_fn=lambda qs: ({}, ""), http=FakeHttp())
    finally:
        ms.load_themes = orig
    assert out["themes"][0]["temp"] is None
    assert any("锚定" in n and "geo" in n for n in out["notes"]), \
        f"锚点 0 命中未告警,配置写错会被伪装成「无数据」;notes={out['notes']}"


def test_declared_empty_anchors_do_not_warn(tmp_path):
    """显式声明 anchors: [] 是有意为之(不硬标方向),不该告警。"""
    snap = tmp_path / "s.jsonl"
    cfg = {"themes": [{"id": "crypto", "label": "加密", "polymarket_tags": ["inflation"],
                       "kalshi_series": [], "anchors": []}], "display_top_n": 3}
    import guanlan_v2.macro.pulse as _mp
    orig = ms.load_themes
    ms.load_themes = lambda: cfg
    try:
        out = _mp.build_pulse(refresh=True, snapshot_path=snap,
                              astock_fn=lambda: {"available": False, "temp": None, "notes": []},
                              translate_fn=lambda qs: ({}, ""), http=FakeHttp())
    finally:
        ms.load_themes = orig
    assert not any("锚定" in n for n in out["notes"])


def test_anchor_rejects_illiquid_market():
    """流动性门槛:成交量过低的市场不得作锚(概率无信息量)。

    真机:"US x China tariff agreement" 24h 量仅 $979、"Japan recession in 2026?" 仅 $11,
    却各自撑起一个主题的温度。真锚定市场量在 2.5 万~15 万量级。
    """
    anchors = [{"match": "china x taiwan", "direction": -1, "weight": 1.0}]
    # military clash 夹具量=800 → 门槛 5000 应拒之
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    rows = sorted(rows, key=lambda m: m.get("volume") or 0, reverse=True)
    temp, hits, ids = mp._theme_temp(rows, anchors, min_volume={"polymarket": 5000})
    assert hits == 0 and temp is None, "低流动性市场仍被用作锚"
    # 门槛放开则命中
    temp2, hits2, _ = mp._theme_temp(rows, anchors, min_volume={"polymarket": 100})
    assert hits2 == 1 and abs(temp2 - 69.0) < 0.1


def test_kalshi_exempt_from_polymarket_volume_floor():
    """Kalshi 的 volume 填的是 liquidity_dollars,与 PM 的 volume24hr 不可比,
    不得套用同一门槛(实测多为 0,会被全数误杀)。"""
    kalshi_rows = [{"source": "kalshi", "id": "k_KXFED-T3", "question": "Fed above 3%?",
                    "prob": 0.6, "volume": 0.0}]
    anchors = [{"match": "fed above", "direction": 1, "weight": 1.0}]
    temp, hits, _ = mp._theme_temp(kalshi_rows, anchors, min_volume={"polymarket": 5000, "kalshi": 0})
    assert hits == 1, "Kalshi 锚点被 polymarket 量纲的门槛误杀"


def test_all_anchors_rejected_by_liquidity_warns_distinctly(tmp_path):
    """锚点全被流动性拒 → 告警须与「措辞不匹配」区分,否则误导排查方向。"""
    snap = tmp_path / "s.jsonl"
    cfg = {"themes": [{"id": "geo", "label": "地缘", "polymarket_tags": ["inflation"],
                       "kalshi_series": [],
                       "anchors": [{"match": "china x taiwan", "direction": -1, "weight": 1.0}]}],
           "display_top_n": 3, "anchor_min_volume": {"polymarket": 5000}}
    import guanlan_v2.macro.pulse as _mp
    orig = ms.load_themes
    ms.load_themes = lambda: cfg
    try:
        out = _mp.build_pulse(refresh=True, snapshot_path=snap,
                              astock_fn=lambda: {"available": False, "temp": None, "notes": []},
                              translate_fn=lambda qs: ({}, ""), http=FakeHttp())
    finally:
        ms.load_themes = orig
    assert out["themes"][0]["temp"] is None
    assert any("流动性" in n for n in out["notes"]), f"未区分流动性拒绝;notes={out['notes']}"


def test_theme_temp_reports_hit_ids():
    """_theme_temp 须回报命中市场 id,供展示层提行。"""
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    rows = sorted(rows, key=lambda m: m.get("volume") or 0, reverse=True)
    temp, hits, ids = mp._theme_temp(rows, [{"match": "military clash", "direction": -1, "weight": 1.0}])
    assert ids == ["pm_1808549"] and hits == 1


def test_dead_market_no_longer_forges_theme_temp():
    """锚定温度必须由活跃市场合成:死市场 prob=0 曾把 fed 温度顶到 75.0。"""
    cfg_anchors = [{"match": "fed rate hike", "direction": -1, "weight": 1.0}]
    rows, _ = ms.fetch_polymarket(["inflation"], http=FakeHttp())
    rows = sorted(rows, key=lambda m: m.get("volume") or 0, reverse=True)
    temp, hits, _ids = mp._theme_temp(rows, cfg_anchors)
    assert hits == 1
    assert temp != 75.0, "温度仍由死市场 prob=0 合成"
    # October prob=0.465,dir=-1 → 50+50*(-1)*(0.465-0.5) = 51.75 ≈ 51.8
    assert abs(temp - 51.8) < 0.05
