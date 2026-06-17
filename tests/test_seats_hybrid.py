import guanlan_v2.seats.api as api


def test_prev_trading_day_skips_weekend_via_calendar():
    import pandas as pd
    # 交易日历:周三06-03/周四06-04/周五06-05/下周一06-08/周二06-09(周末06-06/07无交易)
    idx = pd.to_datetime(["2026-06-03", "2026-06-04", "2026-06-05", "2026-06-08", "2026-06-09"])
    # 周一06-08盘中 → 上一交易日=上周五06-05(显式跳周末),不是日历的周日06-07
    assert api._prev_trading_day("2026-06-08", idx) == "2026-06-05"
    assert api._prev_trading_day("2026-06-09", idx) == "2026-06-08"
    # 16字符盘中时间戳也按10字符日处理
    assert api._prev_trading_day("2026-06-08 10:30", idx) == "2026-06-05"


def test_prev_trading_day_fallback_calendar_minus1(monkeypatch):
    # 无任何交易日历(idx 空 + 引擎日历不可用)→ 退保守日历减1天(只会更早、不泄漏)
    monkeypatch.setattr(api, "_trading_calendar", lambda: None)
    assert api._prev_trading_day("2026-06-08", None) == "2026-06-07"
    assert api._prev_trading_day("2026-06-08", []) == "2026-06-07"


def test_llm_score_mapping():
    assert api._llm_score("买入", 85) == 0.85
    assert api._llm_score("卖出", 70) == -0.70
    assert api._llm_score("观望", 60) == 0.0
    assert api._llm_score("买入", None) == 0.0


def test_combine_factor_score_clip_equal_dir():
    feats = [{"z": 5.0, "dir": 1}, {"z": 0.5, "dir": -1}]   # clip(+5)=+1、(-1*0.5)=-0.5 → 均值0.25
    assert abs(api._combine_factor_score(feats) - 0.25) < 1e-9


def test_combine_factor_score_none_when_empty():
    assert api._combine_factor_score([]) is None
    assert api._combine_factor_score([{"z": None, "dir": 1}]) is None


def test_hybrid_direction_w0_passthrough_no_deadband():
    assert api._hybrid_direction("买入", 0.10, factor_score=0.9, w=0.0) == ("买入", 0.10)


def test_hybrid_direction_none_factor_passthrough():
    assert api._hybrid_direction("卖出", -0.7, factor_score=None, w=0.5) == ("卖出", -0.7)


def test_hybrid_direction_w_mix_and_deadband():
    d, b = api._hybrid_direction("买入", 0.10, factor_score=0.9, w=0.5)
    assert d == "买入" and abs(b - 0.5) < 1e-9
    d2, _ = api._hybrid_direction("买入", 0.10, factor_score=-0.9, w=0.8)   # bias=-0.70 → 翻成卖
    assert d2 == "卖出"
    d3, _ = api._hybrid_direction("买入", 0.10, factor_score=-0.10, w=0.5)  # bias=0.0 死区 → 观望
    assert d3 == "观望"


def test_rf_vintage_line_carries_z_dir_score(monkeypatch):
    monkeypatch.setattr(api, "_factor_id_index", lambda: {"by_expr": {}, "by_name": {"动量20": "mom_20"}})
    monkeypatch.setattr(api, "tsic_vintage_asof",
                        lambda code, fid, date, **k: {"ic": 0.1, "n": 40, "dir": 1, "asof": date})
    monkeypatch.setattr(api, "cs_vintage_asof", lambda *a, **k: None)
    monkeypatch.setattr(api, "factor_z_asof",
                        lambda code, fid, date, **k: {"z": 2.0, "fval": 1.0, "n": 40, "asof": date})
    _line, vint = api._rf_vintage_line([{"name": "动量20"}], "SH605358", "2026-03-01", "day")
    assert vint[0]["z"] == 2.0 and vint[0]["dir"] == 1
    assert vint[0]["score"] == 1.0   # clip(1*2.0)=1.0
