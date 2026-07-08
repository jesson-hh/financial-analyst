# -*- coding: utf-8 -*-
"""datafeed.kuaixun 统一快讯门户单测(全离线,桩引擎 fetch)。

门户=观澜侧东财 7×24 快讯唯一入口(T2 收敛):背靠引擎 opencli fetch_kuaixun,
返回规范行 {time(16位),title,summary,codes(qlib列表)},与旧 news_pulse.fetch_kuaixun
逐字段一致(选股页C节/rescore/store/news_marks 契约零改)。
"""
import pytest

import guanlan_v2.datafeed.kuaixun as kx


def test_fetch_delegates_and_normalizes(monkeypatch):
    """门户委托引擎 fetch 并收敛成规范 4 键:time 截 16、title/summary strip、codes 原样。"""
    monkeypatch.setattr(kx, "_engine_fetch", lambda limit: [
        {"time": "2026-07-08 20:36:05", "title": " 央行降准 ", "summary": " 释放流动性 ",
         "codes": ["SH600030", "SZ300750"]}])
    out = kx.fetch_kuaixun(limit=10)
    assert out == [{"time": "2026-07-08 20:36", "title": "央行降准",
                    "summary": "释放流动性", "codes": ["SH600030", "SZ300750"]}]


def test_normalize_defends_shape_drift(monkeypatch):
    """防引擎 shape 漂移:codes 非 list/None 兜成 list、缺字段兜空、非 dict 行剔除。"""
    monkeypatch.setattr(kx, "_engine_fetch", lambda limit: [
        {"time": "2026-07-08 20:36", "title": "宏观快讯", "summary": "x", "codes": None},
        {"title": "缺 time", "codes": "SH600000"},          # codes 标量 → 兜成单元素 list
        "不是 dict 的脏行",                                    # 非 dict → 剔除
    ])
    out = kx.fetch_kuaixun()
    assert len(out) == 2                                     # 脏行被过滤
    assert out[0]["codes"] == []                             # None → []
    assert out[1]["time"] == "" and out[1]["summary"] == ""  # 缺字段兜空串
    assert out[1]["codes"] == ["SH600000"]                   # 标量 → ["SH600000"]


def test_empty_source_returns_empty(monkeypatch):
    """源本次空返 → [](上层据此走『快讯源返回空』分支,绝不编造)。"""
    monkeypatch.setattr(kx, "_engine_fetch", lambda limit: [])
    assert kx.fetch_kuaixun() == []
    monkeypatch.setattr(kx, "_engine_fetch", lambda limit: None)
    assert kx.fetch_kuaixun() == []


def test_engine_error_propagates(monkeypatch):
    """源抛错(网络/子进程失败)→ 门户向上传播,让上层区分『拉取失败』vs『返回空』,不吞。"""
    def boom(limit):
        raise RuntimeError("opencli down")
    monkeypatch.setattr(kx, "_engine_fetch", boom)
    with pytest.raises(RuntimeError):
        kx.fetch_kuaixun()


def test_limit_forwarded_to_engine(monkeypatch):
    """limit 透传给引擎 fetch(缓存键按 limit,门户不擅改)。"""
    seen = {}

    def _cap(limit):
        seen["limit"] = limit
        return []
    monkeypatch.setattr(kx, "_engine_fetch", _cap)
    kx.fetch_kuaixun(limit=200)
    assert seen["limit"] == 200
