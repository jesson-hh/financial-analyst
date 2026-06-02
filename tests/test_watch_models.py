from financial_analyst.watch.models import WatchItem, WatchContext, WatchRec


def test_watchitem_optional_stop():
    it = WatchItem(code="SH600519")
    assert it.stop_loss is None and it.avg_cost is None
    it2 = WatchItem(code="SZ002594", avg_cost=80.0, stop_loss=72.0)
    assert it2.stop_loss == 72.0


def test_watchrec_schema_and_jsonable():
    r = WatchRec(code="SH600519", action="hold", target_price=1800.0,
                 stop_loss=1650.0, reason="放量突破确认", confidence=0.6,
                 trigger_kind="breakout_high", ts="2026-06-02 10:05:00")
    d = r.to_dict()
    assert d["action"] == "hold" and d["trigger_kind"] == "breakout_high"
    assert set(["code", "action", "target_price", "stop_loss", "reason", "confidence", "trigger_kind", "ts"]).issubset(d)


def test_watchrec_action_validation():
    import pytest
    with pytest.raises(ValueError):
        WatchRec(code="X", action="moon", reason="", trigger_kind="x", ts="t")
