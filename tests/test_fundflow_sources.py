def _fake_live(ok_rows):
    def _fn(source, code="", date="", limit=20):
        return {"ok": bool(ok_rows), "status": "ok" if ok_rows else "error",
                "items": [{"raw": r} for r in ok_rows], "n": len(ok_rows), "note": ""}
    return _fn


def test_fetch_sector_returns_rows():
    from guanlan_v2.fundflow import sources
    rows = [{"code": "BK1", "name": "算力概念", "main_net": 9.45e9,
             "super_net": 6e9, "large_net": 3.45e9, "mid_net": -1e8, "small_net": -4e9,
             "change_pct": 2.1, "up_count": 30, "down_count": 5}]
    out = sources.fetch_sector("concept", live_fn=_fake_live(rows))
    assert out["ok"] is True and out["rows"][0]["name"] == "算力概念"


def test_fetch_sector_degrades_on_empty():
    from guanlan_v2.fundflow import sources
    out = sources.fetch_sector("industry", live_fn=_fake_live([]))
    assert out["ok"] is False and out["note"]


def test_fetch_market_returns_single_row():
    """大盘五档独立源:单行 {date, 五档, src_host}(单位:元)。"""
    from guanlan_v2.fundflow import sources
    row = {"date": "2026-07-10", "main_net": -3.9791e10, "super_net": -2.9097e10,
           "large_net": -1.0694e10, "mid_net": 6.426e9, "small_net": 3.3366e10,
           "src_host": "push2delay.eastmoney.com"}
    out = sources.fetch_market(live_fn=_fake_live([row]))
    assert out["ok"] is True
    assert out["row"]["main_net"] == -3.9791e10
    assert out["row"]["date"] == "2026-07-10"
    assert out["row"]["src_host"] == "push2delay.eastmoney.com"


def test_fetch_market_degrades_on_empty():
    """源挂 → ok=False + 空 row + note;绝不由板块加总兜底给错数。"""
    from guanlan_v2.fundflow import sources
    out = sources.fetch_market(live_fn=_fake_live([]))
    assert out["ok"] is False and out["row"] == {} and out["note"]
