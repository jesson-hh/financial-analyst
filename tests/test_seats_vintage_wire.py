# -*- coding: utf-8 -*-
"""Task 4(P2):vintage IC 接进 decide 研判端点的接线测试。

只测纯接线逻辑(catalog 反查 + tsic 优先退 cs vintage + 诚实空态),
monkeypatch 掉 _factor_id_index / tsic_vintage_asof / cs_vintage_asof,
不碰盘、不碰引擎。
"""
import guanlan_v2.seats.api as api


def test_resolve_factor_id_by_expr_then_name():
    idx = {"by_expr": {"rank(mom_20)": "mom_20"}, "by_name": {"动量20": "mom_20", "mom_20": "mom_20"}}
    assert api._resolve_factor_id({"id": "mom_20"}, idx) == "mom_20"            # 显式 id 优先
    assert api._resolve_factor_id({"expr": "rank(mom_20)"}, idx) == "mom_20"    # expr 次之
    assert api._resolve_factor_id({"name": "动量20"}, idx) == "mom_20"          # name 兜底
    assert api._resolve_factor_id({"name": "不存在"}, idx) is None


def test_rf_vintage_prefers_tsic_then_cs(monkeypatch):
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof",
                        lambda code, fid, date, **k: {"ic": 0.12, "n": 40, "dir": 1, "asof": date} if code == "SH605358" else None)
    monkeypatch.setattr(api, "cs_vintage_asof",
                        lambda fid, date, **k: {"ic": 0.05, "n": 55, "dir": 1, "asof": date})
    line, vint = api._rf_vintage_line([{"name": "动量20"}], "SH605358", "2026-03-01")
    assert "本票" in line and "IC@" in line and "0.12" in line       # tsic 优先
    assert vint[0]["kind"] == "tsic" and vint[0]["ic"] == 0.12


def test_rf_vintage_falls_to_cs_then_honest(monkeypatch):
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof", lambda *a, **k: None)
    monkeypatch.setattr(api, "cs_vintage_asof", lambda fid, date, **k: None)   # 样本不足
    line, vint = api._rf_vintage_line([{"name": "动量20"}], "SZ000001", "2026-03-01")
    assert "样本不足" in line and vint[0]["ic"] is None


def test_rf_vintage_daily_uses_decision_day(monkeypatch):
    # 日线:PIT 锚=决策日本身(EOD 已知当日 fwd 实现)
    seen = {}
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof", lambda *a, **k: None)

    def _cs(fid, date, **k):
        seen["date"] = date
        return None
    monkeypatch.setattr(api, "cs_vintage_asof", _cs)
    api._rf_vintage_line([{"name": "动量20"}], "SH605358", "2026-06-11", freq="day")
    assert seen["date"] == "2026-06-11"


def test_rf_vintage_intraday_uses_prev_trading_day(monkeypatch):
    # 30min 盘中(asof 16字符):PIT 锚回退到上一日历日(同 regime_asof 口径),
    # 防当日 EOD 未结算的 realized_date 入选 = 真·防看未来(P2 命门)。
    seen = {}
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof", lambda code, fid, date, **k: seen.__setitem__("t", date) or None)
    monkeypatch.setattr(api, "cs_vintage_asof", lambda fid, date, **k: seen.__setitem__("c", date) or None)
    api._rf_vintage_line([{"name": "动量20"}], "SH605358", "2026-06-11 10:30", freq="30min")
    assert seen["t"] == "2026-06-10" and seen["c"] == "2026-06-10"   # 不是 2026-06-11


def test_factor_id_index_rebuilds_on_catalog_change(monkeypatch):
    # FACTOR_DEFS 在 refresh_factor_defs 后原地变更 → 反查索引须按规模探针重建(防陈旧漏 resolve)
    monkeypatch.setattr(api, "FACTOR_DEFS", {"mom_20": {"short": "动量20", "expr": "rank(mom_20)"}})
    api._fid_index_cache["v"] = None
    api._fid_index_cache["n"] = -1
    idx1 = api._factor_id_index()
    assert "动量20" in idx1["by_name"] and "lib_new" not in idx1["by_name"].values()
    monkeypatch.setattr(api, "FACTOR_DEFS",
                        {"mom_20": {"short": "动量20", "expr": "rank(mom_20)"},
                         "lib_new": {"short": "新因子", "expr": "rank(roe)"}})
    idx2 = api._factor_id_index()
    assert "新因子" in idx2["by_name"] and idx2["by_name"]["新因子"] == "lib_new"
