# -*- coding: utf-8 -*-
"""datafeed.health 数据健康总闸单测(桩各 item 源,全离线)。"""
import json

import guanlan_v2.datafeed.health as h


def test_age_helpers():
    from datetime import date, timedelta
    d3 = (date.today() - timedelta(days=3)).isoformat()
    assert h._age_days(d3) == 3
    assert h._age_days(None) is None and h._age_days("不是日期") is None
    assert h._age_hours(None) is None


def test_dl_all_inactive_is_stale(tmp_path, monkeypatch):
    prov = {"date": "2026-07-01", "active": False,
            "sources": [{"model_id": "fincast", "active": False, "stale_days": 6},
                        {"model_id": "gat", "active": False, "stale_days": 8}]}
    p = tmp_path / "v4_dl_provenance.json"
    p.write_text(json.dumps(prov), encoding="utf-8")
    monkeypatch.setattr(h, "_read_json", lambda path: prov)
    out = h._item_dl()
    assert out["status"] == "stale" and out["n_active"] == 0 and "全断供" in out["note"]


def test_dl_active_fresh(monkeypatch):
    # prov date 用动态今日:原硬编 2026-07-06 随真实日期流逝腐烂成 stale(2026-07-11 基线已红)
    prov = {"date": _today_iso(), "active": True,
            "sources": [{"model_id": "fincast", "active": True, "stale_days": 0},
                        {"model_id": "lstm", "active": True, "stale_days": 1}]}
    monkeypatch.setattr(h, "_read_json", lambda path: prov)
    out = h._item_dl()
    assert out["status"] == "fresh" and out["n_active"] == 2


def test_dl_active_but_stale(monkeypatch):
    prov = {"sources": [{"model_id": "lstm", "active": True, "stale_days": 9}]}
    monkeypatch.setattr(h, "_read_json", lambda path: prov)
    assert h._item_dl()["status"] == "stale"


def test_dl_provenance_date_stale_overrides_frozen_zero(monkeypatch):
    """评审 Important(真机坐实):regen 停摆时 per-source stale_days 冻结在 0,但 provenance
    整体 date 已 6 天前 → 必判 stale,绝不因冻结的 0 误报 fresh。"""
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=6)).isoformat()
    prov = {"date": old, "active": True,
            "sources": [{"model_id": "fincast", "active": True, "stale_days": 0},
                        {"model_id": "lstm", "active": True, "stale_days": 0}]}
    monkeypatch.setattr(h, "_read_json", lambda path: prov)
    out = h._item_dl()
    assert out["status"] == "stale" and out["prov_age_days"] == 6 and "停摆" in out["note"]


def test_dl_active_stale_days_none_is_suspicious(monkeypatch):
    prov = {"date": _today_iso(), "sources": [{"model_id": "x", "active": True, "stale_days": None}]}
    monkeypatch.setattr(h, "_read_json", lambda path: prov)
    assert h._item_dl()["status"] == "stale"       # 活跃源未记 stale_days → 保守判陈旧


def test_dl_stale_note_mentions_producer_hook(monkeypatch):
    """T3:断供/陈旧 note 必写「regen 不自愈,需 DL 生产器」,GUANLAN_DL_DAILY 状态诚实反映
    env(开=已挂/关=未挂,绝不冒充已挂)。"""
    prov = {"date": "2020-01-01", "active": True,
            "sources": [{"model_id": "x", "active": True, "stale_days": 9}]}
    monkeypatch.setattr(h, "_read_json", lambda path: prov)
    monkeypatch.setenv("GUANLAN_DL_DAILY", "1")
    out = h._item_dl()
    assert out["status"] == "stale" and "regen 不自愈" in out["note"]
    assert "GUANLAN_DL_DAILY 已挂" in out["note"]
    monkeypatch.delenv("GUANLAN_DL_DAILY", raising=False)
    assert "GUANLAN_DL_DAILY 未挂" in h._item_dl()["note"]


def test_age_busdays_weekend_gap(monkeypatch):
    """交易日窗核心修:周五产物到下周一,自然日 3 但工作日 1 → 不因周末误报龄期。"""
    from datetime import date

    class _FD(date):
        @classmethod
        def today(cls):
            return cls(2026, 7, 13)                # 2026-07-13 = 周一

    monkeypatch.setattr(h, "_date", _FD)
    assert h._age_busdays("2026-07-10") == 1       # 上周五 → 1 工作日
    assert h._age_days("2026-07-10") == 3          # 自然日口径对照:3
    assert h._age_busdays(None) is None and h._age_busdays("不是日期") is None


def _today_iso():
    from datetime import date
    return date.today().isoformat()


def test_dl_missing(monkeypatch):
    monkeypatch.setattr(h, "_read_json", lambda path: None)
    assert h._item_dl()["status"] == "missing"


def test_pit_store_fresh_and_stale(monkeypatch):
    from datetime import date, timedelta
    fresh = {"cal_end": date.today().isoformat(), "news_date_max": "x", "n_trade_days": 76}
    monkeypatch.setattr(h, "_paths", lambda: type("P", (), {"pit_store_root": "/x"})())
    monkeypatch.setattr(h, "_read_json", lambda path: fresh)
    assert h._item_pit_store()["status"] == "fresh"
    old = {"cal_end": (date.today() - timedelta(days=10)).isoformat()}
    monkeypatch.setattr(h, "_read_json", lambda path: old)
    assert h._item_pit_store()["status"] == "stale"


def test_regen_enabled_status(monkeypatch):
    import guanlan_v2.screen.api as sa
    monkeypatch.setattr(sa, "_REGEN_SCHED", {"enabled": True, "last_auto_ts": "2026-07-07T18:00"})
    assert h._item_regen()["status"] == "fresh"
    monkeypatch.setattr(sa, "_REGEN_SCHED", {"enabled": False, "last_auto_ts": None})
    out = h._item_regen()
    assert out["status"] == "unknown" and "未启" in out["note"]


def test_collect_overall_takes_worst(monkeypatch):
    monkeypatch.setattr(h, "_ITEMS", {
        "a": lambda: {"status": "fresh"},
        "b": lambda: {"status": "stale", "note": "旧了"},
        "c": lambda: {"status": "missing", "note": "没了"},
    })
    out = h.collect_data_health()
    assert out["ok"] is True and out["overall"]["status"] == "missing"   # 取最差
    assert out["overall"]["stale"] == ["b"] and out["overall"]["missing"] == ["c"]


def test_regen_ops_item_not_dragging_overall(monkeypatch):
    """评审 Minor:regen 调度是运维开关,关着(unknown)不该把全 fresh 的数据面 overall 拉成 unknown。"""
    monkeypatch.setattr(h, "_ITEMS", {
        "v4_ranking": lambda: {"status": "fresh"},
        "dl_ensemble": lambda: {"status": "fresh"},
        "regen_scheduler": lambda: {"status": "unknown", "enabled": False},
    })
    out = h.collect_data_health()
    assert out["overall"]["status"] == "fresh"      # regen unknown 不参与 overall
    assert out["items"]["regen_scheduler"]["status"] == "unknown"   # 但仍在 items 显形


def test_collect_item_exception_degrades_not_crash(monkeypatch):
    def boom():
        raise RuntimeError("炸")
    monkeypatch.setattr(h, "_ITEMS", {"a": lambda: {"status": "fresh"}, "b": boom})
    out = h.collect_data_health()
    assert out["items"]["b"]["status"] == "missing" and "炸" in out["items"]["b"]["note"]
    assert out["ok"] is True                                            # 单项炸不拖垮整体


def test_stock_basic_missing_when_no_paths(monkeypatch):
    monkeypatch.setattr(h, "_paths", lambda: None)
    assert h._item_stock_basic()["status"] == "missing"
    assert h._item_tencent_cache()["status"] == "missing"
    assert h._item_pit_store()["status"] == "missing"


def test_market_tape_health_fresh_stale_missing(monkeypatch, tmp_path):
    import guanlan_v2.datafeed.market_tape as mt
    p = tmp_path / "market_tape.json"
    monkeypatch.setattr(mt, "_CACHE_PATH", p)
    assert h._item_market_tape()["status"] == "missing"        # 缺文件
    now = h.datetime.now().isoformat(timespec="seconds")
    p.write_text('{"pulled_at": "%s"}' % now, encoding="utf-8")
    assert h._item_market_tape()["status"] == "fresh"          # 新鲜
    p.write_text('{"pulled_at": "2020-01-01T00:00:00"}', encoding="utf-8")
    assert h._item_market_tape()["status"] == "stale"          # 陈旧


def test_market_tape_in_items_and_overall(monkeypatch, tmp_path):
    import guanlan_v2.datafeed.market_tape as mt
    monkeypatch.setattr(mt, "_CACHE_PATH", tmp_path / "absent.json")
    out = h.collect_data_health()
    assert "market_tape" in out["items"]                       # 纳入 items
    assert "market_tape" not in h._OPS_ITEMS                   # 数据项(参与 overall)


def test_newsradar_health_fresh_stale_missing(monkeypatch, tmp_path):
    """收口:资讯雷达快照(var/live/newsradar.json)纳入总闸——缺=missing,
    pulled_at ≤24h=fresh,超=stale(无人访问资讯页/服务停摆显形)。"""
    import guanlan_v2.datafeed.newsradar as nr
    p = tmp_path / "newsradar.json"
    monkeypatch.setattr(nr, "_CACHE_PATH", p)
    assert h._item_newsradar()["status"] == "missing"
    now = h.datetime.now().isoformat(timespec="seconds")
    p.write_text('{"pulled_at": "%s", "n_sources": 108, "items": []}' % now, encoding="utf-8")
    out = h._item_newsradar()
    assert out["status"] == "fresh" and out["n_sources"] == 108
    p.write_text('{"pulled_at": "2020-01-01T00:00:00"}', encoding="utf-8")
    assert h._item_newsradar()["status"] == "stale"


def test_macro_pulse_health_fresh_stale_missing(monkeypatch, tmp_path):
    """收口:宏观温度计快照(var/macro_pulse/snapshots.jsonl 尾行 ts)纳入总闸——
    缺=missing,≤30h=fresh(复盘官日跑节拍),超=stale;脏尾行跳过取最近合法行。"""
    import guanlan_v2.macro.pulse as mp
    p = tmp_path / "snapshots.jsonl"
    monkeypatch.setattr(mp, "_SNAP_DEFAULT", p)
    assert h._item_macro_pulse()["status"] == "missing"
    now = h.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    p.write_text('{"ts": "2020-01-01T00:00:00"}\n{"ts": "%s"}\n' % now, encoding="utf-8")
    assert h._item_macro_pulse()["status"] == "fresh"          # 取最后一行,不被首行旧记录带偏
    p.write_text('{"ts": "2020-01-01T00:00:00"}\n不是json\n', encoding="utf-8")
    assert h._item_macro_pulse()["status"] == "stale"          # 脏尾行跳过,退最近合法行


def test_overseas_health_mem_cache_states(monkeypatch):
    """收口:海外看板纯内存 SWR——冷=unknown(非故障,按需预热);缓存在场=fresh 带龄期;
    全部缺价=stale(上游不可达显形)。运维项不拖 overall。"""
    import guanlan_v2.datafeed.overseas as ov
    monkeypatch.setitem(ov._MEM_CACHE, "data", None)
    out = h._item_overseas()
    assert out["status"] == "unknown" and "预热" in out["note"]
    now_ts = int(h.datetime.now().timestamp())
    monkeypatch.setitem(ov._MEM_CACHE, "data",
                        {"pulled_ts": now_ts, "pulled_at": "x", "n": 2,
                         "rows": [{"price": 1.0}, {"price": None}]})
    assert h._item_overseas()["status"] == "fresh"
    monkeypatch.setitem(ov._MEM_CACHE, "data",
                        {"pulled_ts": now_ts, "pulled_at": "x", "n": 2,
                         "rows": [{"price": None}, {"price": None}]})
    out = h._item_overseas()
    assert out["status"] == "stale" and "缺价" in out["note"]


def test_kuaixun_health_gateway_availability(monkeypatch):
    """收口:快讯门户现拉型无快照——仅校验采集器可导入(可用=fresh 带诚实 note,
    不可导入=missing);绝不冒充内容新鲜度。"""
    out = h._item_kuaixun()
    assert out["status"] == "fresh" and "不代表内容新鲜" in out["note"]

    def _boom(_name):
        raise ImportError("采集器缺席")
    monkeypatch.setattr(h, "_import_module", _boom)
    assert h._item_kuaixun()["status"] == "missing"


def test_probe_worker_item_shapes(monkeypatch):
    """T2 评审回归:worker 通道自检必须显形(否则提速可静默蒸发数周)——
    禁用/缺文件/冷却三态各有诚实文案,归运维项不拖 overall。"""
    import guanlan_v2.datafeed.live_client as lc

    assert "probe_worker" in h._ITEMS and "probe_worker" in h._OPS_ITEMS
    cases = [
        (dict(enabled=False, script_exists=True, alive=False, state="cold",
              spawn_fails=0, cooldown_until=0.0, last_error="", last_ok_ts=0.0),
         "unknown", "禁用"),
        (dict(enabled=True, script_exists=False, alive=False, state="cold",
              spawn_fails=0, cooldown_until=0.0, last_error="", last_ok_ts=0.0),
         "missing", "缺席"),
        (dict(enabled=True, script_exists=True, alive=False, state="cooldown",
              spawn_fails=3, cooldown_until=9e12, last_error="ImportError: boom",
              last_ok_ts=0.0),
         "error", "boom"),
        (dict(enabled=True, script_exists=True, alive=True, state="ready",
              spawn_fails=0, cooldown_until=0.0, last_error="", last_ok_ts=1.0),
         "fresh", ""),
    ]
    for st, want_status, want_sub in cases:
        monkeypatch.setattr(lc, "worker_status", lambda st=st: dict(st))
        item = h._ITEMS["probe_worker"]()
        assert item["status"] == want_status, (st, item)
        assert want_sub in (item.get("note") or "")


def test_new_faces_registered_and_classified():
    """四新项都在 _ITEMS;macro_pulse 是数据项(复盘官日跑锚定,stale=真故障);
    newsradar(纯请求驱动、无定时生产者,无人访问是常态非故障)与 overseas/kuaixun
    归运维项不拖 overall——评审确认:否则周末无人开资讯页 overall 恒 stale,
    噪声经复盘官日调 ww_data_health 直进晨报,掩盖 DL 断供等真故障。"""
    assert {"newsradar", "macro_pulse", "overseas", "kuaixun"} <= set(h._ITEMS)
    assert {"newsradar", "overseas", "kuaixun"} <= h._OPS_ITEMS
    assert "macro_pulse" not in h._OPS_ITEMS


def test_ww_data_health_registered_and_formats(monkeypatch):
    import guanlan_v2.console.tools as ct
    entry = next(t for t in ct.WW_TOOL_TABLE if t["name"] == "ww_data_health")
    assert "ww_data_health" in ct.CONSOLE_ALLOWED and entry["confirm"] is False
    monkeypatch.setattr("guanlan_v2.datafeed.health.collect_data_health", lambda: {
        "ok": True, "generated_at": "2026-07-07T18:00:00",
        "overall": {"status": "stale", "stale": ["dl_ensemble"], "missing": []},
        "items": {"v4_ranking": {"status": "fresh", "date": "2026-07-06", "stale_days": 1},
                  "dl_ensemble": {"status": "stale", "n_active": 0, "note": "DL 全断供(退纯 LGB)"}}})
    out = ct.data_health_impl()
    assert out["ok"] and "stale" in out["content"] and "dl_ensemble" in out["content"]
    assert "DL 全断供" in out["content"] and "v4_ranking" in out["content"]


def test_ww_data_health_wrap_carries_full_content(monkeypatch):
    """交付层守护(同 news_live/live_text 历史 Critical):6 项健康经真 _wrap 后逐项可见,
    绝不因 content 超 400 字被 json[:400] 静默截断。"""
    import guanlan_v2.console.tools as ct
    items = {f"item_{i}": {"status": "stale", "date": "2026-07-01", "note": "陈旧说明文字够长" * 3}
             for i in range(6)}
    monkeypatch.setattr("guanlan_v2.datafeed.health.collect_data_health", lambda: {
        "ok": True, "generated_at": "2026-07-07T18:00:00",
        "overall": {"status": "stale", "stale": list(items), "missing": []}, "items": items})
    tr = ct._wrap(ct.data_health_impl)()
    assert not tr.is_error and len(tr.content) > 400
    assert all(f"item_{i}" in tr.content for i in range(6))     # 每项都在,无截断
