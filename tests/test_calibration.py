"""置信度校准(0612演习修复#3)单元测试。

口径:基准=asof 当日(或其后首根)收盘进、horizon 根后收盘出,方向命中,不含成本;
观望不可证伪不计入;未成熟(出场 bar 不存在)剔除。
"""
from guanlan_v2.seats.calibration import bucket_of, calibration_table, evaluate

# 合成日线:RISE 每日 +1,FALL 每日 -1(10 根,2026-06-01 起的工作日序)
DATES = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05",
         "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12"]
RISE = [(d, 100.0 + i) for i, d in enumerate(DATES)]
FALL = [(d, 100.0 - i) for i, d in enumerate(DATES)]
CLOSES = {"SH600001": RISE, "SH600002": FALL}


def _rec(code, direction, conf, asof, kind="decide"):
    return {"kind": kind, "code": code, "direction": direction,
            "confidence": conf, "asof": asof}


def test_buy_on_rising_is_hit():
    ev = evaluate([_rec("SH600001", "买入", 85, "2026-06-01")], CLOSES, horizon=5)
    assert len(ev) == 1 and ev[0]["hit"] is True and ev[0]["ret"] > 0


def test_buy_on_falling_is_miss():
    ev = evaluate([_rec("SH600002", "买入", 85, "2026-06-01")], CLOSES, horizon=5)
    assert len(ev) == 1 and ev[0]["hit"] is False and ev[0]["ret"] < 0


def test_sell_on_falling_is_hit():
    ev = evaluate([_rec("SH600002", "卖出", 70, "2026-06-01")], CLOSES, horizon=5)
    assert len(ev) == 1 and ev[0]["hit"] is True


def test_watch_excluded():
    assert evaluate([_rec("SH600001", "观望", 60, "2026-06-01")], CLOSES) == []


def test_immature_excluded():
    # asof=06-10,horizon=5 → 出场 bar 超出序列 → 未成熟剔除
    assert evaluate([_rec("SH600001", "买入", 85, "2026-06-10")], CLOSES, horizon=5) == []


def test_missing_code_or_conf_excluded():
    assert evaluate([_rec("SH999999", "买入", 85, "2026-06-01")], CLOSES) == []
    assert evaluate([_rec("SH600001", "买入", None, "2026-06-01")], CLOSES) == []


def test_asof_on_non_trading_day_uses_next_bar():
    # 06-06/06-07 是周末:asof=06-06 → 基准取 06-08 那根
    ev = evaluate([_rec("SH600001", "买入", 80, "2026-06-06")], CLOSES, horizon=3)
    assert len(ev) == 1
    assert abs(ev[0]["ret"] - (108.0 / 105.0 - 1)) < 1e-9   # 06-08 收105 → +3根=06-11 收108


def test_non_decide_kind_excluded():
    assert evaluate([_rec("SH600001", "买入", 85, "2026-06-01", kind="order")], CLOSES) == []


def test_bucket_boundaries():
    assert bucket_of(59) == "<60" and bucket_of(60) == "60-69"
    assert bucket_of(79) == "70-79" and bucket_of(80) == "80+" and bucket_of(100) == "80+"


def test_calibration_table_aggregates():
    ev = evaluate([
        _rec("SH600001", "买入", 85, "2026-06-01"),   # hit
        _rec("SH600002", "买入", 88, "2026-06-01"),   # miss
        _rec("SH600002", "卖出", 65, "2026-06-01"),   # hit
    ], CLOSES, horizon=5)
    table = calibration_table(ev)
    by = {b["bucket"]: b for b in table}
    assert by["80+"]["n"] == 2 and by["80+"]["hits"] == 1 and abs(by["80+"]["hit_rate"] - 0.5) < 1e-9
    assert by["60-69"]["n"] == 1 and abs(by["60-69"]["hit_rate"] - 1.0) < 1e-9
    assert by["<60"]["n"] == 0 and by["<60"]["hit_rate"] is None


def test_empty_inputs_safe():
    assert evaluate([], {}) == []
    table = calibration_table([])
    assert len(table) == 4 and all(b["n"] == 0 for b in table)
