# -*- coding: utf-8 -*-
"""macro astock:打板温度确定性算术/probe 失败降级/热源失败不拖垮主体。mock live_fn 不起子进程。"""
import pytest

from guanlan_v2.macro import astock as ma


ZT_ROWS = [
    {"code": "002396", "name": "星网锐捷", "pct": 10.01, "zt_stat": "4天3板",
     "break_times": 1, "limit_days": 1, "industry": "通信设备"},
    {"code": "002148", "name": "北纬科技", "pct": 10.07, "zt_stat": "1天1板",
     "break_times": 0, "limit_days": 1, "industry": "通信服务"},
    {"code": "600000", "name": "示例三", "pct": 10.0, "zt_stat": "",
     "break_times": 0, "limit_days": 2, "industry": "示例"},
]


def _live_ok(source, code="", date="", limit=20):
    if source == "em_zt_pool":
        return {"ok": True, "note": "", "n": len(ZT_ROWS), "rows": ZT_ROWS}
    if source == "ths_hot_reason":
        return {"ok": True, "note": "", "n": 2,
                "rows": [{"reason": "算力"}, {"reason": "机器人"}]}
    if source == "ths_hot_list":
        return {"ok": True, "note": "", "n": 1, "rows": [{"name": "热股A"}]}
    raise AssertionError(f"unexpected source {source}")


def test_astock_temp_arithmetic():
    out = ma.build_astock(live_fn=_live_ok)
    assert out["available"] is True
    assert out["zt_count"] == 3
    assert out["max_streak"] == 3          # "4天3板" → 3;无 zt_stat 行回落 limit_days=2
    assert out["break_ratio"] == pytest.approx(1 / 3, abs=1e-4)
    # 温度 = base30 + 0.35*3 + 3*3 - 30*(1/3) = 30+1.05+9-10 = 30.05 → round .1
    assert out["temp"] == pytest.approx(30.1, abs=0.01)
    assert out["top_reasons"] and out["hot_list"]
    assert out["notes"] == []


def test_astock_zt_truncation_honest_note():
    """涨停家数打满统一客户端上限(现 300;旧壳 50 饱和已修)→ 仍诚实标注『>=』口径。"""
    many = [dict(ZT_ROWS[1], code=f"{i:06d}") for i in range(ma._ZT_LIMIT)]

    def full(source, code="", date="", limit=20):
        if source == "em_zt_pool":
            return {"ok": True, "note": "", "n": ma._ZT_LIMIT, "rows": many}
        return {"ok": True, "note": "", "n": 0, "rows": []}

    out = ma.build_astock(live_fn=full)
    assert out["zt_count"] == ma._ZT_LIMIT and ma._ZT_LIMIT >= 300
    assert any("截断" in n for n in out["notes"])


def test_astock_zt_64_true_count_no_truncation():
    """真机口径回归(2026-07-06 实测 64 家):>旧上限 50 不再饱和,取真值且无截断标注。"""
    many = [dict(ZT_ROWS[1], code=f"{i:06d}") for i in range(64)]

    def full(source, code="", date="", limit=20):
        if source == "em_zt_pool":
            return {"ok": True, "note": "", "n": 64, "rows": many}
        return {"ok": True, "note": "", "n": 0, "rows": []}

    out = ma.build_astock(live_fn=full)
    assert out["zt_count"] == 64
    assert not any("截断" in n for n in out["notes"])


def test_astock_probe_dead_degrades_honest():
    def dead(source, code="", date="", limit=20):
        return {"ok": True, "note": "stocks probe 不可用(G:\\stocks 缺席)", "n": 0, "rows": []}
    out = ma.build_astock(live_fn=dead)
    assert out["available"] is False and out["temp"] is None
    assert any("em_zt_pool" in n for n in out["notes"])


def test_astock_hot_sources_fail_but_zt_survives():
    def partial(source, code="", date="", limit=20):
        if source == "em_zt_pool":
            return {"ok": True, "note": "", "n": 3, "rows": ZT_ROWS}
        return {"ok": True, "note": "probe 超时(90s)", "n": 0, "rows": []}
    out = ma.build_astock(live_fn=partial)
    assert out["available"] is True and out["temp"] is not None
    assert out["top_reasons"] == [] and out["hot_list"] == []
    assert len(out["notes"]) == 2  # 两个热源各一条


# ── 盘口快照收敛(MT-3):默认路径优先读快照,缺席/warming 回落直拉 ──────────────
def test_build_astock_reads_from_tape_when_fresh(monkeypatch):
    tape = {"warming": False, "sources": {
        "em_limit_up_pool": {"rows": [{"zt_stat": "3天3板", "break_times": 0, "limit_days": 3}]},
        "ths_hot_reason": {"rows": [{"reason": "AI 算力"}]},
        "ths_hot_list": {"rows": [{"name": "寒武纪"}]}}}
    monkeypatch.setattr(ma, "_read_tape_safe", lambda: tape)
    called = {"n": 0}
    monkeypatch.setattr(ma, "_client_live",
                        lambda **k: called.__setitem__("n", called["n"] + 1) or {"ok": True, "rows": [], "n": 0, "note": ""})
    out = ma.build_astock()                                   # 无 live_fn → 走快照
    assert out["available"] is True and out["zt_count"] == 1 and out["max_streak"] == 3
    assert called["n"] == 0                                   # 快照在→零直拉(收敛)
    assert out["top_reasons"] and out["hot_list"]


def test_build_astock_falls_back_when_tape_warming(monkeypatch):
    monkeypatch.setattr(ma, "_read_tape_safe", lambda: {"warming": True, "sources": {}})
    hits = []

    def fake_live(**kw):
        hits.append(kw.get("source"))
        rows = [{"zt_stat": "2天2板", "break_times": 0}] if kw.get("source") == "em_zt_pool" else []
        return {"ok": True, "rows": rows, "n": len(rows), "note": ""}
    monkeypatch.setattr(ma, "_client_live", fake_live)
    out = ma.build_astock()                                   # 无 live_fn → 快照 warming → 回落直拉
    assert "em_zt_pool" in hits and out["zt_count"] == 1      # 温度计不破


def test_build_astock_stale_tape_source_falls_back_not_faked(monkeypatch):
    """快照里 em_zt_pool 是上轮失败保留的陈旧行(note 带『新失败』)→ 不当今日算温度,
    回落直拉;直拉同源也失败 → 诚实 available=False + note,绝不伪造新鲜(评审 Important)。"""
    tape = {"warming": False, "sources": {
        "em_limit_up_pool": {"rows": [{"zt_stat": "9天9板", "break_times": 0}],
                             "note": "(旧)|新失败:probe 超时"}}}
    monkeypatch.setattr(ma, "_read_tape_safe", lambda: tape)
    hits = []

    def fake_live(**kw):
        hits.append(kw.get("source"))
        return {"ok": True, "rows": [], "n": 0, "note": "probe 超时(90s)"}   # 直拉同源也宕
    monkeypatch.setattr(ma, "_client_live", fake_live)
    out = ma.build_astock()
    assert "em_zt_pool" in hits                               # 陈旧源不读快照,回落直拉
    assert out["available"] is False                         # 直拉空 → 诚实不可用
    assert any("em_zt_pool" in n for n in out["notes"])      # 诚实 note,不把 9连板陈旧当今日
