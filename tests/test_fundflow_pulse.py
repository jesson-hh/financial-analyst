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


def test_build_live_delta_intraday_across_two_calls(tmp_path):
    from guanlan_v2.fundflow import pulse
    industry = _rows(("银行", 2.66e8, 1e8, 1.66e8, 0.0, -2e8, 0.3, 20, 8))
    concept1 = _rows(("算力概念", 5e9, 3e9, 2e9, 0, 0, 2.1, 30, 5))
    concept2 = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, 0, 0, 2.1, 30, 5))
    out1 = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                            sector_fn=_sector_fn(concept1, industry), now=datetime(2026, 7, 8, 10, 0, 0))
    assert out1["boards"][0]["delta_intraday"] is None            # 首快照无基线
    out2 = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                            sector_fn=_sector_fn(concept2, industry), now=datetime(2026, 7, 8, 10, 6, 0))
    assert round(out2["boards"][0]["delta_intraday"]) == round(9.45e9 - 5e9)


def test_build_live_other_tier_degrade_continues(tmp_path):
    import json
    from pathlib import Path
    from guanlan_v2.fundflow import pulse
    concept = _rows(("算力概念", 9.45e9, 6e9, 3.45e9, -1e8, -4e9, 2.1, 30, 5))
    out = pulse.build_live("concept", refresh=True, snapshot_dir=str(tmp_path),
                           sector_fn=_sector_fn(concept, []), now=datetime(2026, 7, 8, 10, 0, 0))
    assert out["ok"] is True and out["market"] == {}
    assert out["breadth"]["allA"] == {"up": None, "down": None}
    assert out["breadth"]["industry"] == {"up": None, "down": None}
    assert out["breadth"]["concept"] == {"up": 1, "down": 0}
    assert out["notes"]                                            # 降级 note 显形
    lines = [json.loads(l) for l in (Path(tmp_path) / "20260708.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert {l["kind"] for l in lines} == {"concept"}              # 只落有数据的档


def test_load_history_top_in_out_and_gap(tmp_path):
    from guanlan_v2.fundflow import pulse
    snap = Path(tmp_path) / "20260708.jsonl"
    def line(ts, kind, boards):
        return json.dumps({"ts": ts, "kind": kind,
                           "market": {"main_net": sum(b[1] for b in boards)},
                           "boards": [{"name": n, "main_net": v} for n, v in boards]},
                          ensure_ascii=False)
    snap.write_text("\n".join([
        line("2026-07-08T09:33:00", "concept", [("算力概念", 1e9), ("存储芯片", -1e9)]),
        line("2026-07-08T09:36:00", "concept", [("算力概念", 3e9)]),                    # 存储缺该 tick
        line("2026-07-08T09:36:00", "industry", [("银行", 5e8)]),                        # 别档,忽略
        line("2026-07-08T09:39:00", "concept", [("算力概念", 9.45e9), ("存储芯片", -1.52e10)]),
    ]) + "\n", encoding="utf-8")
    out = pulse.load_history("concept", date="20260708", snapshot_dir=str(tmp_path))
    assert out["ticks"] == ["2026-07-08T09:33:00", "2026-07-08T09:36:00", "2026-07-08T09:39:00"]
    names = {b["name"]: b["series"] for b in out["boards"]}
    assert names["算力概念"] == [1e9, 3e9, 9.45e9]
    assert names["存储芯片"] == [-1e9, None, -1.52e10]      # 中间 tick 缺 → None(断线)
    assert out["market_series"]["main_net"][0] == 0.0        # 1e9 + (-1e9)


def test_load_history_missing_day_returns_empty(tmp_path):
    from guanlan_v2.fundflow import pulse
    out = pulse.load_history("concept", date="20260708", snapshot_dir=str(tmp_path))
    assert out == {"date": "20260708", "kind": "concept", "ticks": [],
                   "boards": [], "market_series": {"main_net": []}}


def test_load_history_dedup_and_split_many_boards(tmp_path):
    import json
    from pathlib import Path
    from guanlan_v2.fundflow import pulse
    boards = [{"name": f"板块{i:02d}", "main_net": (9 - i) * 1e9} for i in range(18)]  # +9e9…-8e9
    snap = Path(tmp_path) / "20260708.jsonl"
    snap.write_text(json.dumps({"ts": "2026-07-08T10:00:00", "kind": "concept",
                                "market": {"main_net": 0.0}, "boards": boards},
                               ensure_ascii=False) + "\n", encoding="utf-8")
    out = pulse.load_history("concept", date="20260708", snapshot_dir=str(tmp_path), top_each=8)
    names = [b["name"] for b in out["boards"]]
    assert len(names) == 16                                   # 8 净流入 + 8 净流出
    assert len(set(names)) == 16                              # 无重复(dedup 生效)
    assert names[:8] == [f"板块{i:02d}" for i in range(8)]     # 净流入前8(main_net 最高,板块00..07)
    assert names[8:] == [f"板块{i:02d}" for i in range(10, 18)]  # 净流出前8(main_net 最低,板块10..17)


# ── SWR 秒回层(read_live)——收口「反复刷新反复打东财」──────────────────────────
def _mk_build(calls, ok=True):
    """伪 build_live:记录调用 + 按 now 打 pulled_at;ok=False 模拟源空返。"""
    def _b(kind, refresh=False, snapshot_dir=None, sector_fn=None, now=None):
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
