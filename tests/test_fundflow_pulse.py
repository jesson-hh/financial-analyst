import json
from datetime import datetime
from pathlib import Path


def _sector_fn(concept_rows, industry_rows):
    def _fn(kind, live_fn=None):
        rows = industry_rows if str(kind).startswith("ind") else concept_rows
        return {"ok": bool(rows), "rows": rows, "note": "" if rows else "empty"}
    return _fn


def _rows(*specs):
    # spec = (name, main, super, large, mid, small, chg, up, down)
    return [{"code": f"BK{i}", "name": n, "main_net": m, "super_net": su, "large_net": la,
             "mid_net": mi, "small_net": sm, "change_pct": c, "up_count": u, "down_count": d}
            for i, (n, m, su, la, mi, sm, c, u, d) in enumerate(specs)]


_MARKET_ROW = {"date": "2026-07-10", "main_net": -3.9791e10, "super_net": -2.9097e10,
               "large_net": -1.0694e10, "mid_net": 6.426e9, "small_net": 3.3366e10,
               "src_host": "push2delay.eastmoney.com"}


def _market_fn(row=None, ok=True):
    def _fn():
        if not ok:
            return {"ok": False, "row": {}, "note": "大盘资金源不可达"}
        return {"ok": True, "row": dict(row if row is not None else _MARKET_ROW), "note": ""}
    return _fn


def test_build_live_uses_independent_market_source(tmp_path):
    """大盘五档必须来自独立源。

    绝不可由板块加总——东财 t:2 混排一/二/三级行业,股票重复归属(真机 up+down=16545
    >> A股约 5400;加总主力 +963.50亿 vs 独立源真值 -397.91亿,连符号都相反)。
    """
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5),
                    ("存储芯片", -1.52e10, -9e9, -6.2e9, 1e8, 1.42e10, -3.4, 4, 40))
    industry = _rows(("半导体", -1.34e10, -8e9, -5.4e9, 1e8, 1.3e10, -1.2, 10, 30),
                     ("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    now = datetime(2026, 7, 8, 10, 57, 0)
    out = pulse.build_live("concept", refresh=True, sector_fn=_sector_fn(concept, industry),
                           market_fn=_market_fn(), now=now)
    assert out["ok"] and out["kind"] == "concept" and out["trading"] is True
    # 大盘 = 独立源原样(加总会得 -1.34e10+2.66e8,截然不同)
    assert out["market"]["main_net"] == -3.9791e10
    assert out["market"]["super_net"] == -2.9097e10
    assert out["market"]["src_host"] == "push2delay.eastmoney.com"
    # 全A 涨跌:无独立源 → 诚实 None + note,绝不用板块加总冒充
    assert out["breadth"]["allA"] == {"up": None, "down": None}
    assert any("全A" in n for n in out["notes"])
    # 板块级涨跌计数(计的是板块个数,不涉股票重叠)照常出数
    assert out["breadth"]["industry"] == {"up": 1, "down": 1}   # 银行涨、半导体跌
    assert out["breadth"]["concept"] == {"up": 1, "down": 1}    # 算力涨、存储跌
    assert out["boards"][0]["name"] == "算力概念" and out["boards"][0]["rank"] == 1


def test_build_live_never_sums_overlapping_boards(tmp_path):
    """回归护栏:板块 up/down_count 之和绝不出现在 breadth.allA(板块重叠→重复计数)。"""
    from guanlan_v2.fundflow import pulse
    # 行业档含重复板块(航天装备Ⅱ/Ⅲ 同值),加总会得 up=38
    industry = _rows(("航天装备Ⅱ", 2.981e9, 2e9, 9.81e8, 0.0, -1e9, 10.36, 9, 0),
                     ("航天装备Ⅲ", 2.981e9, 2e9, 9.81e8, 0.0, -1e9, 10.36, 9, 0),
                     ("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    concept = _rows(("商业航天", 1.1086e10, 7e9, 4.086e9, -1e8, -2e9, 2.7, 40, 3))
    out = pulse.build_live("industry", refresh=True, sector_fn=_sector_fn(concept, industry),
                           market_fn=_market_fn(), now=datetime(2026, 7, 10, 10, 0, 0))
    assert out["breadth"]["allA"]["up"] is None          # 绝不是 38
    assert out["breadth"]["allA"]["down"] is None
    assert out["market"]["main_net"] == -3.9791e10       # 绝不是板块加总
    assert out["breadth"]["industry"] == {"up": 3, "down": 0}   # 板块个数计数照常


def test_build_live_degrades_when_market_source_down(tmp_path):
    """独立大盘源挂掉 → market 空 + note 显形,绝不回落到板块加总。"""
    from guanlan_v2.fundflow import pulse
    concept = _rows(("商业航天", 1.1086e10, 7e9, 4.086e9, -1e8, -2e9, 2.7, 40, 3))
    industry = _rows(("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    out = pulse.build_live("concept", refresh=True, sector_fn=_sector_fn(concept, industry),
                           market_fn=_market_fn(ok=False), now=datetime(2026, 7, 10, 10, 0, 0))
    assert out["ok"] is True          # 板块还在,不整份作废
    assert out["market"] == {}        # 不编造,不回落加总
    assert any("大盘" in n for n in out["notes"])
    assert out["boards"][0]["name"] == "商业航天"


def test_build_live_writes_no_snapshot_file(tmp_path, monkeypatch):
    """快照机制已废除:build_live 绝不再落 <YYYYMMDD>.jsonl(盘中线改由东财分钟线直出)。"""
    from guanlan_v2.fundflow import pulse
    monkeypatch.chdir(tmp_path)
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5))
    industry = _rows(("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    out = pulse.build_live("concept", refresh=True, sector_fn=_sector_fn(concept, industry),
                           market_fn=_market_fn(), now=datetime(2026, 7, 8, 10, 30, 0))
    assert out["ok"] is True
    assert not list(Path(tmp_path).rglob("*.jsonl"))
    assert "delta_intraday" not in out["boards"][0]      # 快照派生物,已随快照一起废除


def test_build_live_degrades_when_sector_empty(tmp_path):
    from guanlan_v2.fundflow import pulse
    out = pulse.build_live("concept", refresh=True, sector_fn=_sector_fn([], []), market_fn=_market_fn(),
                           now=datetime(2026, 7, 8, 10, 0, 0))
    assert out["ok"] is False and out["notes"]


def test_build_live_other_tier_degrade_continues(tmp_path):
    """另一档缺失只影响该档板块涨跌数;大盘来自独立源,照常出数。"""
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5))
    out = pulse.build_live("concept", refresh=True, sector_fn=_sector_fn(concept, []), market_fn=_market_fn(),
                           now=datetime(2026, 7, 8, 10, 0, 0))
    assert out["ok"] is True
    assert out["market"]["main_net"] == -3.9791e10        # 独立源不受行业档缺失影响
    assert out["breadth"]["allA"] == {"up": None, "down": None}
    assert out["breadth"]["industry"] == {"up": None, "down": None}   # 该档缺 → 诚实 None
    assert out["breadth"]["concept"] == {"up": 1, "down": 0}
    assert out["notes"]                                            # 降级 note 显形


# ── load_history:东财分钟线直出(不再自累快照)────────────────────────────────
def _min_fn(by_code):
    """批量腿:codes -> 每板块一行紧凑序列;by_code 里缺席的 code 视为该板块拉不到。"""
    def _fn(codes, live_fn=None):
        rows = []
        for c in codes:
            pairs = by_code.get(c)
            if not pairs:
                continue
            rows.append({"code": c, "name": "", "times": [t for t, _ in pairs],
                         "main_net": [v for _, v in pairs], "src_host": "push2.eastmoney.com"})
        if not rows:
            return {"ok": False, "rows": [], "note": "无分钟线"}
        return {"ok": True, "rows": rows, "note": ""}
    return _fn


def _mkt_min_fn(pairs, ok=True):
    def _fn(live_fn=None):
        if not ok:
            return {"ok": False, "row": {}, "note": "大盘分钟线不可达"}
        return {"ok": True, "note": "", "row": {
            "times": [t for t, _ in pairs], "main_net": [v for _, v in pairs],
            "src_host": "push2.eastmoney.com"}}
    return _fn


def test_load_history_builds_lines_from_minute_klines(tmp_path):
    """曲线来自东财当日分钟线,时间轴 HH:MM;某板块缺该分钟 → None(断线不插值)。"""
    from guanlan_v2.fundflow import pulse
    concept = _rows(("商业航天", 1.1086e10, 7e9, 4.086e9, -1e8, -2e9, 2.7, 40, 3),
                    ("科技风格", -4.5951e10, -3e10, -1.5951e10, 1e8, 4.6e10, -4.29, 5, 60))
    industry = _rows(("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    minute = _min_fn({
        "BK0": [("09:31", 1.77e8), ("09:32", 5e8), ("15:00", 1.1086e10)],   # 商业航天
        "BK1": [("09:31", -3e8), ("15:00", -4.5951e10)],                     # 科技风格(缺 09:32)
    })
    market = _mkt_min_fn([("09:31", 1.814e9), ("09:32", 1e9), ("15:00", -3.9791e10)])

    out = pulse.load_history("concept", top_each=1, sector_fn=_sector_fn(concept, industry),
                             minute_fn=minute, market_minute_fn=market,
                             now=datetime(2026, 7, 10, 15, 30, 0))
    assert out["ticks"] == ["09:31", "09:32", "15:00"]        # 时间并集,HH:MM
    series = {b["name"]: b["series"] for b in out["boards"]}
    assert series["商业航天"] == [1.77e8, 5e8, 1.1086e10]      # 末值 = 今日主力净额
    assert series["科技风格"] == [-3e8, None, -4.5951e10]      # 缺该分钟 → None,不插值
    assert out["market_series"]["main_net"] == [1.814e9, 1e9, -3.9791e10]
    assert out["date"] == "20260710"


def test_load_history_picks_both_ends_and_dedups(tmp_path):
    """选线 = 净流入前 N + 净流出前 N,按 code 去重保序。"""
    from guanlan_v2.fundflow import pulse
    concept = _rows(*[(f"板块{i:02d}", (9 - i) * 1e9, 0, 0, 0, 0, 1.0, 1, 0) for i in range(18)])
    by_code = {f"BK{i}": [("09:31", 0.0), ("15:00", (9 - i) * 1e9)] for i in range(18)}
    out = pulse.load_history("concept", top_each=3, sector_fn=_sector_fn(concept, []),
                             minute_fn=_min_fn(by_code), market_minute_fn=_mkt_min_fn([("09:31", 0.0)]),
                             now=datetime(2026, 7, 10, 15, 30, 0))
    names = [b["name"] for b in out["boards"]]
    assert len(names) == 6 and len(set(names)) == 6          # 3 进 + 3 出,无重复
    assert names[:3] == ["板块00", "板块01", "板块02"]        # 净流入前3
    assert names[3:] == ["板块15", "板块16", "板块17"]        # 净流出前3


def test_load_history_degrades_when_sector_rank_down(tmp_path):
    """选不出板块 → 空图 + note,绝不假装有线。"""
    from guanlan_v2.fundflow import pulse
    out = pulse.load_history("concept", sector_fn=_sector_fn([], []),
                             minute_fn=_min_fn({}), market_minute_fn=_mkt_min_fn([]),
                             now=datetime(2026, 7, 10, 15, 30, 0))
    assert out["ticks"] == [] and out["boards"] == []
    assert out["notes"]


def test_load_history_partial_minute_failure_keeps_rest(tmp_path):
    """个别板块分钟线拉挂 → 略去该线 + note 显形,其余照画(不整图作废)。"""
    from guanlan_v2.fundflow import pulse
    concept = _rows(("商业航天", 1.1086e10, 0, 0, 0, 0, 2.7, 40, 3),
                    ("科技风格", -4.5951e10, 0, 0, 0, 0, -4.29, 5, 60))
    out = pulse.load_history("concept", top_each=1, sector_fn=_sector_fn(concept, []),
                             minute_fn=_min_fn({"BK0": [("09:31", 1e8), ("15:00", 1.1086e10)]}),
                             market_minute_fn=_mkt_min_fn([("09:31", 0.0), ("15:00", -3.9791e10)]),
                             now=datetime(2026, 7, 10, 15, 30, 0))
    names = [b["name"] for b in out["boards"]]
    assert names == ["商业航天"]                              # 科技风格分钟线缺 → 略去
    assert any("科技风格" in n for n in out["notes"])          # 但显形,不静默


def test_load_history_market_minute_down_still_draws_boards(tmp_path):
    """大盘分钟线挂 → 板块线照画,market_series 为空 + note。"""
    from guanlan_v2.fundflow import pulse
    concept = _rows(("商业航天", 1.1086e10, 0, 0, 0, 0, 2.7, 40, 3))
    out = pulse.load_history("concept", top_each=1, sector_fn=_sector_fn(concept, []),
                             minute_fn=_min_fn({"BK0": [("09:31", 1e8), ("15:00", 1.1086e10)]}),
                             market_minute_fn=_mkt_min_fn([], ok=False),
                             now=datetime(2026, 7, 10, 15, 30, 0))
    assert [b["name"] for b in out["boards"]] == ["商业航天"]
    assert out["market_series"]["main_net"] == [None, None]   # 无大盘线,诚实空
    assert any("大盘" in n for n in out["notes"])


# ── SWR 秒回层(read_live)——收口「反复刷新反复打东财」──────────────────────────
def _mk_build(calls, ok=True):
    """伪 build_live:记录调用 + 按 now 打 pulled_at;ok=False 模拟源空返。"""
    def _b(kind, refresh=False, sector_fn=None, market_fn=None, now=None, **kw):
        k = "industry" if str(kind).startswith("ind") else "concept"
        calls.append((k, refresh))
        stamp = (now or datetime(2026, 7, 8, 10, 0, 0)).strftime("%Y-%m-%dT%H:%M:%S")
        return {"ok": ok, "kind": k, "pulled_at": stamp, "trading": True,
                "market": {"main_net": 1.0}, "breadth": {}, "boards": [],
                "notes": [] if ok else ["源本次空返"]}
    return _b


def test_read_live_caches_within_ttl(tmp_path):
    from guanlan_v2.fundflow import pulse
    calls = []
    b = _mk_build(calls)
    r1 = pulse.read_live("concept", cache_dir=str(tmp_path), ttl_s=180,
                         now=datetime(2026, 7, 8, 10, 0, 0), build_fn=b)
    assert r1["ok"] and r1["freshness"]["stale"] is False and len(calls) == 1
    assert (Path(tmp_path) / "fundflow_live_concept.json").exists()          # 冷启动落缓存
    # TTL 内二次读 → 命中缓存,不再拉(核心:不反复打东财)
    r2 = pulse.read_live("concept", cache_dir=str(tmp_path), ttl_s=180,
                         now=datetime(2026, 7, 8, 10, 1, 0), build_fn=b)
    assert len(calls) == 1 and r2["freshness"]["stale"] is False


def test_read_live_refresh_bypasses_cache(tmp_path):
    from guanlan_v2.fundflow import pulse
    calls = []
    b = _mk_build(calls)
    pulse.read_live("concept", cache_dir=str(tmp_path), ttl_s=180,
                    now=datetime(2026, 7, 8, 10, 0, 0), build_fn=b)
    assert len(calls) == 1
    pulse.read_live("concept", refresh=True, cache_dir=str(tmp_path), ttl_s=180,
                    now=datetime(2026, 7, 8, 10, 0, 30), build_fn=b)
    assert len(calls) == 2 and calls[1] == ("concept", True)                 # 显式强拉透传 refresh=True


def test_read_live_stale_serves_prev_and_triggers(tmp_path, monkeypatch):
    from guanlan_v2.fundflow import pulse
    calls = []
    b = _mk_build(calls)
    pulse.read_live("concept", cache_dir=str(tmp_path), ttl_s=180,
                    now=datetime(2026, 7, 8, 10, 0, 0), build_fn=b)
    triggered = []
    monkeypatch.setattr(pulse, "_trigger_live_refresh",
                        lambda *a, **k: (triggered.append(a), True)[1])
    r = pulse.read_live("concept", cache_dir=str(tmp_path), ttl_s=180,
                        now=datetime(2026, 7, 8, 10, 5, 0), build_fn=b)   # 5min>180s → 过期
    assert r["freshness"]["stale"] is True
    assert any("缓存过期" in n for n in r["notes"])
    assert triggered and len(calls) == 1        # 秒回旧值+触发后台单飞;本次读未同步再拉


def test_refresh_live_keeps_prev_on_failed_refresh(tmp_path):
    from guanlan_v2.fundflow import pulse
    pulse._refresh_live("concept", cache_dir=str(tmp_path),
                        now=datetime(2026, 7, 8, 10, 0, 0), build_fn=_mk_build([], ok=True))
    out = pulse._refresh_live("concept", cache_dir=str(tmp_path),
                              now=datetime(2026, 7, 8, 10, 3, 0), build_fn=_mk_build([], ok=False))
    assert out["ok"] is True                                     # 失败沿用上轮(诚实降级)
    assert any("刷新失败沿用上轮" in n for n in out["notes"])
    assert out["pulled_at"] == "2026-07-08T10:00:00"             # 锚点不动=真陈旧,不伪造新鲜


def test_read_live_cold_failure_is_honest(tmp_path):
    from guanlan_v2.fundflow import pulse
    out = pulse.read_live("concept", cache_dir=str(tmp_path), ttl_s=180,
                          now=datetime(2026, 7, 8, 10, 0, 0), build_fn=_mk_build([], ok=False))
    assert out["ok"] is False and out["notes"]
    assert not (Path(tmp_path) / "fundflow_live_concept.json").exists()   # 失败不缓存


def test_read_live_forced_refresh_failure_no_false_bg_note(tmp_path):
    from guanlan_v2.fundflow import pulse
    # 先落一份缓存
    pulse._refresh_live("concept", cache_dir=str(tmp_path),
                        now=datetime(2026, 7, 8, 10, 0, 0), build_fn=_mk_build([], ok=True))
    # refresh=True 强拉失败(now 远超 ttl,缓存已陈旧)→ 沿用上轮。修:该路径未触发后台刷新,
    # 不得谎报「已触发后台刷新」(评审 minor)。
    out = pulse.read_live("concept", refresh=True, cache_dir=str(tmp_path), ttl_s=180,
                          now=datetime(2026, 7, 8, 12, 0, 0), build_fn=_mk_build([], ok=False))
    assert out["ok"] is True and any("刷新失败沿用上轮" in n for n in out["notes"])
    assert not any("已触发后台刷新" in n for n in out["notes"])


def test_trigger_live_refresh_resets_flag_on_thread_fail(tmp_path, monkeypatch):
    from guanlan_v2.fundflow import pulse
    def _boom(*a, **k):
        raise RuntimeError("thread 起不来")
    monkeypatch.setattr(pulse.threading, "Thread", _boom)
    pulse._live_inflight.pop("concept", None)
    ok = pulse._trigger_live_refresh("concept", cache_dir=str(tmp_path))
    assert ok is False and pulse._live_inflight.get("concept") is False   # 复位旗,不永久冻结
