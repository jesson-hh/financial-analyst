# -*- coding: utf-8 -*-
"""events.ingest 确定性零 LLM 入库 adapter 单测(全 tmp db,feed 全桩,零网络零 LLM)。

分层:Task 2 = 两 feed 逐字段映射 / 单事务不半写 / 幂等 / available_at 落盘时刻;
Task 3 = SimHash 转载去重 + 72h 桶 line_id;Task 5 = 调度 tick 三门 + 守护四连。
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from guanlan_v2.events import ingest, store


def _db(tmp_path):
    conn = store.connect(path=tmp_path / "events" / "events.db")
    store.init_db(conn)
    return conn


def _all(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY rowid")]


# ───────────────────────── Task 2: 逐字段映射 ─────────────────────────

def test_ingest_kuaixun_field_mapping(tmp_path):
    conn = _db(tmp_path)
    rows = [{"time": "07-18 09:31", "title": "央行降准", "summary": "释放长期资金",
             "codes": ["SH600000", "SZ000001"]}]
    now = datetime(2026, 7, 18, 9, 35, 0)
    out = ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows, now=now)
    assert out["inserted"] == 1
    r = _all(conn)[0]
    assert r["event_type"] == "kuaixun"
    assert r["ingested_from"] == "kuaixun"
    assert r["title"] == "央行降准" and r["summary"] == "释放长期资金"
    assert r["event_time"] == "2026-07-18 09:31"          # M1:年份已补齐
    assert r["available_at"] == "2026-07-18T09:35:00"
    import json
    assert json.loads(r["tickers"]) == ["SH600000", "SZ000001"]


def test_ingest_newsradar_field_mapping(tmp_path):
    conn = _db(tmp_path)
    ts = int(datetime(2026, 7, 18, 1, 0, 0, tzinfo=timezone.utc).timestamp())
    items = [{"title": "OpenAI ships", "url": "https://x.com/a", "summary": "big news",
              "source": "OpenAI Blog", "sector": "AI", "ts": ts}]
    now = datetime(2026, 7, 18, 12, 0, 0)
    out = ingest.ingest_newsradar(conn, read=lambda **kw: {"items": items}, now=now)
    assert out["inserted"] == 1
    r = _all(conn)[0]
    assert r["event_type"] == "newsradar:AI"
    assert r["ingested_from"] == "newsradar"
    assert r["source_ref"] == "https://x.com/a"
    assert r["source"] == "OpenAI Blog"
    import json
    assert json.loads(r["tickers"]) == []                       # newsradar 行无 ticker
    assert r["event_time"] == datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    assert r["available_at"] == "2026-07-18T12:00:00"


def test_ingest_newsradar_no_sector_falls_back(tmp_path):
    conn = _db(tmp_path)
    items = [{"title": "T", "url": "", "summary": "", "source": "", "sector": "", "ts": 0}]
    ingest.ingest_newsradar(conn, read=lambda **kw: {"items": items},
                            now=datetime(2026, 7, 18, 12, 0, 0))
    assert _all(conn)[0]["event_type"] == "newsradar"           # 无 sector → 裸 newsradar


# ───────────────────────── Task 2: 单事务不半写 ─────────────────────────

def test_ingest_source_error_propagates_no_write(tmp_path):
    conn = _db(tmp_path)

    def boom(limit=200):
        raise RuntimeError("feed down")

    with pytest.raises(RuntimeError):
        ingest.ingest_kuaixun(conn, fetch=boom, now=datetime(2026, 7, 18, 9, 0, 0))
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_ingest_single_transaction_rolls_back_on_mid_batch_error(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    rows = [{"time": f"07-18 09:0{i}", "title": f"t{i}", "summary": "s", "codes": []}
            for i in range(5)]
    calls = {"n": 0}
    real = store.insert_event

    def flaky(c, row):
        calls["n"] += 1
        if calls["n"] == 3:                     # 第 3 行构造炸
            raise RuntimeError("boom row 3")
        return real(c, row)

    monkeypatch.setattr(ingest.store, "insert_event", flaky)
    with pytest.raises(RuntimeError):
        ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows,
                              now=datetime(2026, 7, 18, 9, 0, 0))
    # 前两行也必须回滚 —— 单事务不半写
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


# ───────────────────────── Task 2: 幂等 / 空返 / 确定性 id ─────────────────────────

def test_ingest_idempotent_reingest(tmp_path):
    conn = _db(tmp_path)
    rows = [{"time": "07-18 09:31", "title": "央行降准", "summary": "x", "codes": []}]
    now = datetime(2026, 7, 18, 9, 35, 0)
    out1 = ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows, now=now)
    out2 = ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows, now=now)
    assert out1["inserted"] == 1 and out2["inserted"] == 0      # 重入不重复
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_ingest_kuaixun_empty_zero_rows_zero_error(tmp_path):
    conn = _db(tmp_path)
    out = ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [],
                                now=datetime(2026, 7, 18, 9, 0, 0))
    assert out["inserted"] == 0 and out["fetched"] == 0
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_event_id_deterministic(tmp_path):
    # M1 后不变量收敛为:同年语境(含 12月/1月 边界)内 available_at 不影响 id。
    row = {"time": "07-18 09:31", "title": "央行降准", "summary": "x", "codes": []}
    a = ingest._map_kuaixun_row(row, "2026-07-18T09:35:00")["event_id"]
    b = ingest._map_kuaixun_row(row, "2026-08-01T00:00:00")["event_id"]  # 同年异批
    assert a == b and len(a) >= 16
    dec = {"time": "12-31 23:58", "title": "跨年快讯", "summary": "x", "codes": []}
    c = ingest._map_kuaixun_row(dec, "2026-12-31T23:59:00")["event_id"]
    d = ingest._map_kuaixun_row(dec, "2027-01-01T00:05:00")["event_id"]  # 边界:仍上一年
    assert c == d


def test_available_at_default_now_is_walltime(tmp_path):
    conn = _db(tmp_path)
    before = datetime.now().replace(microsecond=0)
    rows = [{"time": "07-18 09:31", "title": "T", "summary": "x", "codes": []}]
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows)     # now=None → 真墙钟
    avail = _all(conn)[0]["available_at"]
    assert datetime.fromisoformat(avail) >= before               # 落盘时刻 ≥ 调用前


# ───────────────────────── Task 3: SimHash 转载去重 + 72h 桶 ─────────────────────────
# 转载(转发)= 同一篇被多家原样转发,标题+摘要近乎一致(至多尾附来源标注/空白)。
# 字符 bigram SimHash 对此类近重复海明 ≤3;对实质改写/不同事件海明远大于 3。

_TITLE = "央行今日宣布全面降准零点五个百分点"
_SUMMARY = "此次降准将释放长期资金约一万亿元人民币旨在支持实体经济发展降低企业融资成本保持流动性合理充裕引导金融机构加大对小微企业的信贷支持力度"
_SUMMARY_REPRINT = _SUMMARY + "(来源:新华社)"                 # 转载尾附来源 → 海明 3
_TITLE_B = "某科技公司发布全新人工智能芯片"
_SUMMARY_B = "性能大幅提升引发半导体行业高度关注产业链上下游多家厂商积极布局相关订单需求旺盛业绩预期向好"


def test_simhash_pure_function_properties():
    a = ingest._simhash(_TITLE + " " + _SUMMARY)
    ar = ingest._simhash(_TITLE + " " + _SUMMARY_REPRINT)
    b = ingest._simhash(_TITLE_B + " " + _SUMMARY_B)
    assert isinstance(a, int) and 0 <= a < (1 << 64)
    assert ingest._simhash(_TITLE + " " + _SUMMARY) == a     # 确定性(纯 stdlib,进程间稳定)
    assert ingest._hamming(a, ar) <= 3                       # 转载近重复海明近
    assert ingest._hamming(a, b) > 3                         # 不相干海明远


def test_simhash_near_duplicate_shares_line_evidence_kept(tmp_path):
    conn = _db(tmp_path)
    rows = [
        {"time": "07-18 09:00", "title": _TITLE, "summary": _SUMMARY, "codes": []},
        {"time": "07-18 09:05", "title": _TITLE, "summary": _SUMMARY_REPRINT, "codes": []},
    ]
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows,
                          now=datetime(2026, 7, 18, 9, 10, 0))
    got = _all(conn)
    assert len(got) == 2                                 # 转载仍入库,证据不丢
    assert got[0]["line_id"] == got[1]["line_id"]        # 共享 line_id


def test_simhash_unrelated_distinct_lines(tmp_path):
    conn = _db(tmp_path)
    rows = [
        {"time": "07-18 09:00", "title": _TITLE, "summary": _SUMMARY, "codes": []},
        {"time": "07-18 09:05", "title": _TITLE_B, "summary": _SUMMARY_B, "codes": []},
    ]
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows,
                          now=datetime(2026, 7, 18, 9, 10, 0))
    got = _all(conn)
    assert got[0]["line_id"] != got[1]["line_id"]


def test_simhash_72h_boundary_distinct_bucket(tmp_path):
    conn = _db(tmp_path)
    # 同文本(simhash 同)但 event_time 不同 → event_id 不同 → 两行都入库;
    # 两次入库落盘时刻相隔 73h > 72h → 跨桶 → 不同 line。
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [
        {"time": "07-18 09:00", "title": _TITLE, "summary": _SUMMARY, "codes": []}],
        now=datetime(2026, 7, 18, 9, 0, 0))
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [
        {"time": "07-21 10:00", "title": _TITLE, "summary": _SUMMARY, "codes": []}],
        now=datetime(2026, 7, 21, 10, 0, 0))          # +73h
    got = _all(conn)
    assert len(got) == 2
    assert got[0]["line_id"] != got[1]["line_id"]


def test_simhash_within_72h_same_bucket(tmp_path):
    conn = _db(tmp_path)
    # 同文本、不同 event_time(两行)、落盘相隔 48h < 72h → 同桶 → 共享 line。
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [
        {"time": "07-18 09:00", "title": _TITLE, "summary": _SUMMARY, "codes": []}],
        now=datetime(2026, 7, 18, 9, 0, 0))
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [
        {"time": "07-20 09:00", "title": _TITLE, "summary": _SUMMARY, "codes": []}],
        now=datetime(2026, 7, 20, 9, 0, 0))          # +48h
    got = _all(conn)
    assert len(got) == 2 and got[0]["line_id"] == got[1]["line_id"]


def test_simhash_ticker_scoped_lines(tmp_path):
    conn = _db(tmp_path)
    # 同事件类型、近重复文本,但个股码不相交 → 不同票桶 → 不同 line(转载去重按 ticker×type×72h)。
    rows = [
        {"time": "07-18 09:00", "title": _TITLE, "summary": _SUMMARY, "codes": ["SH600000"]},
        {"time": "07-18 09:05", "title": _TITLE, "summary": _SUMMARY_REPRINT, "codes": ["SZ000001"]},
    ]
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: rows,
                          now=datetime(2026, 7, 18, 9, 10, 0))
    got = _all(conn)
    assert got[0]["line_id"] != got[1]["line_id"]


# ───────────────────────── Task 5: env 闸调度 tick(三门)─────────────────────────

@pytest.fixture
def _reset_tick():
    ingest._TICK_STATE.update(last_ingest_ts=None, eod_date=None)
    yield
    ingest._TICK_STATE.update(last_ingest_ts=None, eod_date=None)


def test_tick_env_gate_off_default(_reset_tick, monkeypatch):
    monkeypatch.delenv("GUANLAN_EVENTS_INGEST", raising=False)  # 默认关
    called = {"n": 0}
    ok = ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 10, 0, 0),
                                  do_ingest=lambda now: called.__setitem__("n", called["n"] + 1))
    assert ok is False and called["n"] == 0                     # 门①关 → 零入库


def test_tick_env_on_first_call_ingests(_reset_tick, monkeypatch):
    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "1")
    called = {"n": 0}
    ok = ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 10, 0, 0),
                                  do_ingest=lambda now: called.__setitem__("n", called["n"] + 1))
    assert ok is True and called["n"] == 1


def test_tick_30min_rhythm_dedup(_reset_tick, monkeypatch):
    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "1")
    n = {"c": 0}
    stub = lambda now: n.__setitem__("c", n["c"] + 1)
    assert ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 10, 0, 0), do_ingest=stub) is True
    # 距上次 <30min → 跳过(盘中窗,非 EOD)
    assert ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 10, 20, 0), do_ingest=stub) is False
    # ≥30min → 再入库
    assert ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 10, 31, 0), do_ingest=stub) is True
    assert n["c"] == 2


def test_tick_eod_fallback(_reset_tick, monkeypatch):
    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "1")
    n = {"c": 0}
    stub = lambda now: n.__setitem__("c", n["c"] + 1)
    # 15:20 一拍(盘中)
    assert ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 15, 20, 0), do_ingest=stub) is True
    # 15:35 距上次仅 15min(节奏未到)但进入 EOD 窗且今日未兜底 → 强制入库
    assert ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 15, 35, 0), do_ingest=stub) is True
    # 15:40 EOD 已兜底且节奏未到 → 不重复
    assert ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 15, 40, 0), do_ingest=stub) is False
    assert n["c"] == 2


def test_tick_swallows_ingest_error(_reset_tick, monkeypatch):
    monkeypatch.setenv("GUANLAN_EVENTS_INGEST", "1")

    def boom(now):
        raise RuntimeError("ingest down")

    ok = ingest.maybe_ingest_tick(now=datetime(2026, 7, 18, 10, 0, 0), do_ingest=boom)
    assert ok is False                                          # 自吞异常绝不拖垮宿主
    assert ingest._TICK_STATE["last_ingest_ts"] is None         # 失败不占节奏,下拍可重试


# ───────────────────────── Task 5: 守护四连 ─────────────────────────

_EVENTS_DIR = Path(ingest.__file__).parent


def _events_sources():
    return {p.name: p.read_text(encoding="utf-8") for p in _EVENTS_DIR.glob("*.py")}


def _imported_modules(src: str) -> set:
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


def test_guard_events_never_import_orchestration():
    """D10:guanlan_v2/events/* 零 import orchestration(AST 扫描,含函数内 lazy import)。"""
    for name, src in _events_sources().items():
        for m in _imported_modules(src):
            assert "orchestration" not in m, f"{name} 违规 import {m}(D10 零 orchestration)"


def test_guard_ingest_zero_llm_seam():
    """入库路径零 LLM 接缝:events 源不 import 任何 LLM 客户端/引擎 LLM 模块(结构性钉死)。"""
    llm_markers = ("openai", "anthropic", "deepseek", "kimi", "llm", "reasoner", "news_pulse")
    for name, src in _events_sources().items():
        for m in _imported_modules(src):
            low = m.lower()
            assert not any(mk in low for mk in llm_markers), \
                f"{name} 违规 import LLM 模块 {m}(v1 零 LLM)"


def test_guard_search_requires_asof_regression(tmp_path):
    """无 asof 检索必炸(回归钉死;静默 now = 前视温床)。"""
    conn = store.connect(path=tmp_path / "events.db")
    store.init_db(conn)
    with pytest.raises((ValueError, TypeError)):
        store.search_events("x", asof=None, conn=conn)
    with pytest.raises((ValueError, TypeError)):
        store.read_events(conn, asof=None)
    conn.close()


def test_guard_db_default_under_var_and_injectable(tmp_path, monkeypatch):
    """全测 tmp 注入,真 var/ 零触碰:默认路径在 repo var/events,参数/env 皆可改到 tmp。"""
    d = store.db_path()
    assert d.parent.parent.name == "var" and d.parent.name == "events"
    assert store.db_path(path=tmp_path / "x.db") == tmp_path / "x.db"      # 参数注入
    monkeypatch.setenv("GUANLAN_EVENTS_DB", str(tmp_path / "y.db"))
    assert store.db_path() == tmp_path / "y.db"                            # env 注入


# ───────────────── M1:kuaixun 无年份 time 的年份补齐(防跨年撞 id)─────────────────

def test_stamp_year_mmdd_gets_year_from_available_at():
    assert ingest._stamp_year("07-18 09:31", "2026-07-18T15:30:00") == "2026-07-18 09:31"


def test_stamp_year_new_year_boundary_takes_previous_year():
    # 12-31 的快讯在 01-01 才入库:年份必须取上一年,不许标成未来。
    assert ingest._stamp_year("12-31 23:58", "2027-01-01T00:05:00") == "2026-12-31 23:58"


def test_stamp_year_already_yeared_and_odd_shapes_untouched():
    assert ingest._stamp_year("2026-07-18 09:31", "2026-07-18T15:30:00") == "2026-07-18 09:31"
    assert ingest._stamp_year("", "2026-07-18T15:30:00") == ""
    assert ingest._stamp_year("昨日盘后", "2026-07-18T15:30:00") == "昨日盘后"


def test_cross_year_same_title_same_minute_distinct_event_ids(tmp_path):
    # M1 场景:同标题同分钟、相隔一年 → 补年后 event_id 必须不同,两行都入库。
    conn = _db(tmp_path)
    row = {"time": "07-18 09:31", "title": "央行降准", "summary": "x", "codes": []}
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [dict(row)],
                          now=datetime(2026, 7, 18, 15, 30, 0))
    ingest.ingest_kuaixun(conn, fetch=lambda limit=200: [dict(row)],
                          now=datetime(2027, 7, 18, 15, 30, 0))
    times = sorted(r["event_time"] for r in _all(conn))
    assert len(times) == 2 and times == ["2026-07-18 09:31", "2027-07-18 09:31"]
