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
