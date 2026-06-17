import pandas as pd
from guanlan_v2.seats.narrative import surface_narratives, build_pool, regime_asof

_POOL = [
    {"id": "n1", "as_of": "2026-05-01", "codes": ["605358"], "industry": "半导体", "kind": "研报", "title": "立昂微深度", "insight": "硅片景气"},
    {"id": "n2", "as_of": "2026-06-09", "codes": ["605358"], "industry": "半导体", "kind": "新闻", "title": "立昂微涨停", "insight": "放量封板"},
    {"id": "n3", "as_of": "2026-06-30", "codes": ["605358"], "industry": "半导体", "kind": "新闻", "title": "未来新闻", "insight": "不该出现"},
    {"id": "n4", "as_of": "2026-06-08", "codes": ["300750"], "industry": "电池", "kind": "新闻", "title": "别的票", "insight": "不相关"},
]
_WIN = {"研报": 60, "新闻": 10, "复盘": 30}


def test_pit_never_future():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=10, windows=_WIN)
    assert "n3" not in [c["id"] for c in out], "未来卡泄漏 = PIT 破"


def test_window_by_kind():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=10, windows=_WIN)
    ids = [c["id"] for c in out]
    assert "n2" in ids and "n1" in ids


def test_news_window_expires():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-25", k=10, windows=_WIN)
    ids = [c["id"] for c in out]
    assert "n2" not in ids and "n1" in ids


def test_relevance_code_or_industry():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=10, windows=_WIN)
    assert "n4" not in [c["id"] for c in out]


def test_topk_recency():
    out = surface_narratives(_POOL, "605358", "半导体", "2026-06-10", k=1, windows=_WIN)
    assert len(out) == 1 and out[0]["id"] == "n2"


def test_empty_honest():
    out = surface_narratives(_POOL, "999999", "无此行业", "2026-06-10", k=10, windows=_WIN)
    assert out == []


def test_build_pool_normalizes_and_drops_undated():
    archive = [
        {"id": "a1", "type": "card", "tier": "narrative", "as_of": "2026-06-01",
         "codes": ["605358"], "industry": "半导体", "kind": "复盘", "title": "T", "insight": "I"},
        {"id": "a2", "type": "card", "tier": "narrative", "title": "无日期", "insight": "x"},
        {"id": "a3", "type": "card", "tier": "quant", "as_of": "2026-06-01", "title": "量化卡"},
    ]
    reports = [{"id": "r1", "as_of": "2026-05-20", "codes": ["605358"], "industry": "半导体",
                "title": "立昂微深度", "insight": "硅片", "path": "out/x.md"}]
    pool = build_pool(archive, reports)
    ids = {c["id"] for c in pool}
    assert ids == {"a1", "r1"}
    assert all(c.get("as_of") for c in pool)
    assert next(c for c in pool if c["id"] == "r1")["kind"] == "研报"


def _bdf():
    idx = pd.to_datetime(["2026-06-05", "2026-06-08", "2026-06-09", "2026-06-10"])
    return pd.DataFrame({"breadth": [0.40, 0.55, 0.62, 0.58]}, index=idx)


def test_regime_pit_picks_le_date():
    s = regime_asof("2026-06-09", _bdf())
    assert s is not None and "2026-06-09" in s


def test_regime_never_future():
    s = regime_asof("2026-06-08", _bdf())
    assert "2026-06-09" not in s and "2026-06-10" not in s


def test_regime_empty_before_data():
    assert regime_asof("2026-01-01", _bdf()) is None


def test_build_pool_drops_non_card_types():
    archive = [
        {"id": "e1", "type": "event", "tier": "narrative", "as_of": "2026-06-01", "title": "事件"},
        {"id": "c1", "type": "card", "tier": "narrative", "as_of": "2026-06-01", "title": "真叙事卡"},
    ]
    pool = build_pool(archive, [])
    assert [c["id"] for c in pool] == ["c1"]   # 非 card 类型不入叙事池


# ════════════════ P1 Task4:decide 接「按日 PIT 浮出 + regime 补 + 落盘审计」集成测试 ════════════════
# 子进程钉 engine(同 test_seats_runs 先例);注入合成叙事池保证确定性(不碰真 archive/parquet)。
import json as _json   # noqa: E402
import sys as _sys     # noqa: E402
from pathlib import Path as _Path   # noqa: E402

_REPO = _Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in _sys.modules:
    _sys.path.insert(0, str(_ENGINE))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from guanlan_v2.seats import api as seats_api  # noqa: E402


class _FakeLLMClient:
    """decide 内 `from financial_analyst.llm.client import LLMClient` 的替身:固定成功 JSON 结论。"""
    provider = "deepseek"
    model = "deepseek-chat"

    @classmethod
    def for_agent(cls, name):
        return cls()

    def with_overrides(self, **kw):
        return self

    async def chat(self, messages, **kw):
        return {"choices": [{"message": {
            "content": '{"direction":"买入","confidence":70,"rationale":"桩","key_evidence":["e1"]}',
            "reasoning_content": ""}}]}


class _FakeLoader:
    """日线替身:返回 None → decide 走 fac={} 空因子路径(asof=end,PIT 锚仍正确)。"""

    def fetch_quote(self, code, start, end, freq):
        return None


# 合成叙事卡:同一票(605358),三张不同 as_of,各 kind 在窗口内;一张未来卡(2026-06-30)。
_SYNTH_CARDS = [
    {"id": "s_old", "type": "card", "tier": "narrative", "as_of": "2026-05-20",
     "codes": ["605358"], "industry": "半导体", "kind": "研报", "title": "立昂微深度", "insight": "硅片景气"},
    {"id": "s_mid", "type": "card", "tier": "narrative", "as_of": "2026-06-05",
     "codes": ["605358"], "industry": "半导体", "kind": "复盘", "title": "中段复盘", "insight": "回踩MA20"},
    {"id": "s_new", "type": "card", "tier": "narrative", "as_of": "2026-06-09",
     "codes": ["605358"], "industry": "半导体", "kind": "新闻", "title": "放量涨停", "insight": "封板"},
    {"id": "s_future", "type": "card", "tier": "narrative", "as_of": "2026-06-30",
     "codes": ["605358"], "industry": "半导体", "kind": "新闻", "title": "未来卡", "insight": "不该浮出"},
]


def _patch_decide(monkeypatch, log):
    import financial_analyst.data.loader_factory as _lf
    import financial_analyst.llm.client as _llm
    monkeypatch.setattr(_lf, "get_default_loader", lambda: _FakeLoader())
    monkeypatch.setattr(_llm, "LLMClient", _FakeLLMClient)
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    # 注入合成叙事池(确定性):archive 返合成卡,out/ 返空 → 浮出只来自合成卡。
    monkeypatch.setattr(seats_api, "_load_archive_cards", lambda: list(_SYNTH_CARDS))
    monkeypatch.setattr(seats_api, "_load_out_reports", lambda: [])


def _client():
    app = FastAPI()
    app.include_router(seats_api.build_seats_router())
    return TestClient(app)


def _read_jsonl(p):
    return [_json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_decide_surfaces_narratives_per_day_pit(tmp_path, monkeypatch):
    """同一票两个不同决策日 D1<D2:晚的日子浮出更多/不同卡(逐日 PIT),且绝无未来卡。"""
    log = tmp_path / "seats_decisions.jsonl"
    _patch_decide(monkeypatch, log)
    client = _client()

    # D1=2026-05-21:只有 s_old(研报窗60)在窗内;s_mid/s_new 还未发生。
    body = {"code": "SH605358", "name": "立昂微", "industry": "半导体",
            "seat_cn": "动量席", "mode": "fast", "regime": "外部市况"}
    r1 = client.post("/seats/decide", json={**body, "date": "2026-05-21"})
    assert r1.status_code == 200 and r1.json()["ok"] is True
    # D2=2026-06-10:s_old(研报60窗内)+ s_mid(复盘30窗内)+ s_new(新闻10窗内)全浮出。
    r2 = client.post("/seats/decide", json={**body, "date": "2026-06-10"})
    assert r2.status_code == 200 and r2.json()["ok"] is True

    recs = _read_jsonl(log)
    assert len(recs) == 2
    d1_ids = set(recs[-2]["narratives_surfaced"])
    d2_ids = set(recs[-1]["narratives_surfaced"])

    # 1) 逐日不同:D2 浮出 ⊋ D1(晚的日子浮出更多)。
    assert d1_ids == {"s_old"}, f"D1 应只浮出 s_old,实际 {d1_ids}"
    assert d2_ids == {"s_old", "s_mid", "s_new"}, f"D2 应浮出三张,实际 {d2_ids}"
    assert d1_ids < d2_ids, "晚的决策日应浮出更多/不同 = 逐日 PIT"

    # 2) 无未来卡:两天的浮出 as_of 均 ≤ 各自决策日。
    by_id = {c["id"]: c["as_of"] for c in _SYNTH_CARDS}
    assert all(by_id[i] <= "2026-05-21" for i in d1_ids), "D1 浮出含未来卡 = PIT 破"
    assert all(by_id[i] <= "2026-06-10" for i in d2_ids), "D2 浮出含未来卡 = PIT 破"
    assert "s_future" not in (d1_ids | d2_ids), "未来卡(2026-06-30)绝不应浮出"

    # 3) 落盘含 regime_asof_used 布尔字段。
    assert isinstance(recs[-1]["regime_asof_used"], bool)


def test_decide_honest_empty_when_no_narratives(tmp_path, monkeypatch):
    """无关联叙事卡(别的票/别的行业)→ 浮出 [],research 落盘空,绝不退 demo。"""
    log = tmp_path / "seats_decisions.jsonl"
    _patch_decide(monkeypatch, log)
    client = _client()

    body = {"code": "SZ300750", "name": "宁德时代", "industry": "电池",
            "seat_cn": "动量席", "mode": "fast", "date": "2026-06-10"}
    r = client.post("/seats/decide", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True
    rec = _read_jsonl(log)[-1]
    assert rec["narratives_surfaced"] == [], "无关联卡应诚实空"
    assert rec["research"] == [], "research 应空(无浮出)"


def test_decide_regime_asof_used_flag(tmp_path, monkeypatch):
    """外部传 regime → regime_asof_used=True(用上了 regime,无论来源);该字段恒布尔。"""
    log = tmp_path / "seats_decisions.jsonl"
    _patch_decide(monkeypatch, log)
    client = _client()

    body = {"code": "SH605358", "name": "立昂微", "industry": "半导体",
            "seat_cn": "动量席", "mode": "fast", "date": "2026-06-10", "regime": "外部市况"}
    r = client.post("/seats/decide", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True
    rec = _read_jsonl(log)[-1]
    assert rec["regime_asof_used"] is True


# ── 合成大盘 breadth(日线 EOD 产物):覆盖 D1=06-10 及其前一交易日 06-09 ──
def _synth_breadth():
    idx = pd.to_datetime(["2026-06-05", "2026-06-08", "2026-06-09", "2026-06-10"])
    return pd.DataFrame({"breadth": [0.40, 0.55, 0.62, 0.58]}, index=idx)


def _spy_regime_date(monkeypatch):
    """包住 narrative.regime_asof 记录每次调用的 PIT 日期参数(decide 内 from ... import,
    须 patch 源模块)。返回收集列表;同时让 _load_breadth_df 返合成 breadth(不碰真 parquet)。"""
    import guanlan_v2.seats.narrative as _narr
    calls: list = []
    _real = _narr.regime_asof

    def _wrap(date, df):
        calls.append(str(date))
        return _real(date, df)

    monkeypatch.setattr(_narr, "regime_asof", _wrap)
    monkeypatch.setattr(seats_api, "_load_breadth_df", _synth_breadth)
    return calls


def test_decide_regime_pit_intraday_uses_prev_day(tmp_path, monkeypatch):
    """IMPORTANT1:同一票同一天,30min 盘中决策的 regime 日期 < 日线决策的 regime 日期
    (intraday 当日 EOD breadth 盘中不可得 → 用上一交易日,绝不看未来)。"""
    log = tmp_path / "seats_decisions.jsonl"
    _patch_decide(monkeypatch, log)
    calls = _spy_regime_date(monkeypatch)
    client = _client()

    base = {"code": "SH605358", "name": "立昂微", "industry": "半导体", "seat_cn": "动量席", "mode": "fast"}
    # 日线:决策在 D=06-10 收盘 → regime 锚 = 06-10
    r_day = client.post("/seats/decide", json={**base, "date": "2026-06-10", "freq": "day"})
    assert r_day.status_code == 200 and r_day.json()["ok"] is True
    day_date = calls[-1]
    # 30min:D=06-10 盘中 10:30 → regime 锚 = 上一交易日(06-09),当日 EOD 那时不存在
    r_min = client.post("/seats/decide", json={**base, "date": "2026-06-10 10:30", "freq": "30min"})
    assert r_min.status_code == 200 and r_min.json()["ok"] is True
    min_date = calls[-1]

    assert day_date == "2026-06-10", f"日线 regime 锚应为决策日,实际 {day_date}"
    assert min_date == "2026-06-09", f"30min regime 锚应为上一交易日,实际 {min_date}"
    assert min_date < day_date, "intraday regime 日期必须 < 日线(盘中不可看当日 EOD)"
    # 两笔都用上了 breadth → regime_asof_used=True;且落盘 regime 字符串各自选到正确那天
    recs = _read_jsonl(log)
    assert recs[-2]["regime_asof_used"] is True and recs[-1]["regime_asof_used"] is True


def test_decide_regime_from_breadth_when_no_external(tmp_path, monkeypatch):
    """无外部 regime → 后端按大盘日产物补,regime_asof_used=True;无 breadth → None/False(诚实空)。"""
    log = tmp_path / "seats_decisions.jsonl"
    _patch_decide(monkeypatch, log)
    monkeypatch.setattr(seats_api, "_load_breadth_df", _synth_breadth)
    client = _client()

    base = {"code": "SH605358", "name": "立昂微", "industry": "半导体", "seat_cn": "动量席", "mode": "fast"}
    r = client.post("/seats/decide", json={**base, "date": "2026-06-10", "freq": "day"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert _read_jsonl(log)[-1]["regime_asof_used"] is True

    # 无 breadth 产物 → regime 诚实 None → regime_asof_used=False(不退 demo)
    monkeypatch.setattr(seats_api, "_load_breadth_df", lambda: None)
    r2 = client.post("/seats/decide", json={**base, "date": "2026-06-10", "freq": "day"})
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert _read_jsonl(log)[-1]["regime_asof_used"] is False
