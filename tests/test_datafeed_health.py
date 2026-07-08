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
    prov = {"date": "2026-07-06", "active": True,
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
