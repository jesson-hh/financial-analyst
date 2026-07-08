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


def test_build_live_aggregates_market_and_breadth(tmp_path):
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5),
                    ("存储芯片", -1.52e10, -9e9, -6.2e9, 1e8, 1.42e10, -3.4, 4, 40))
    industry = _rows(("半导体", -1.34e10, -8e9, -5.4e9, 1e8, 1.3e10, -1.2, 10, 30),
                     ("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    now = datetime(2026, 7, 8, 10, 57, 0)
    out = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn(concept, industry), now=now)
    assert out["ok"] and out["kind"] == "concept" and out["trading"] is True
    # 大盘分解 = 行业档加总
    assert round(out["market"]["main_net"]) == round(-1.34e10 + 2.66e8)
    assert round(out["market"]["super_net"]) == round(-8e9 + 1e8)
    # 全A 涨跌 = 行业档 up/down 加总;行业涨跌数=行业板块涨跌计数;概念涨跌数=概念板块计数
    assert out["breadth"]["allA"] == {"up": 30, "down": 38}
    assert out["breadth"]["industry"] == {"up": 1, "down": 1}   # 银行涨、半导体跌
    assert out["breadth"]["concept"] == {"up": 1, "down": 1}    # 算力涨、存储跌
    # boards = 当前档(concept),按 main_net 降序,带 rank
    assert out["boards"][0]["name"] == "算力概念" and out["boards"][0]["rank"] == 1
    # 落点:当日快照文件出现,含 concept + industry 两行
    snap = Path(tmp_path) / "20260708.jsonl"
    lines = [json.loads(l) for l in snap.read_text(encoding="utf-8").splitlines() if l.strip()]
    kinds = {l["kind"] for l in lines}
    assert kinds == {"concept", "industry"}


def test_build_live_no_sink_when_not_trading_and_not_refresh(tmp_path):
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5))
    industry = _rows(("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    now = datetime(2026, 7, 8, 20, 0, 0)   # 收盘后
    out = pulse.build_live("concept", refresh=False, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn(concept, industry), now=now)
    assert out["trading"] is False
    assert not (Path(tmp_path) / "20260708.jsonl").exists()   # 非交易且非 refresh 不落点


def test_build_live_degrades_when_sector_empty(tmp_path):
    from guanlan_v2.fundflow import pulse
    out = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn([], []), now=datetime(2026, 7, 8, 10, 0, 0))
    assert out["ok"] is False and out["notes"]
