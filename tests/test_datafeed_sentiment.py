# -*- coding: utf-8 -*-
"""datafeed.sentiment 统一情绪 store 单测(tmp_path 隔离 _ROOT,全离线)。"""
import pytest

import guanlan_v2.datafeed.sentiment as sm


@pytest.fixture(autouse=True)
def _tmp_root(monkeypatch, tmp_path):
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")
    yield


def test_judgment_roundtrip_and_none_vs_absent():
    day = "2026-07-07"
    sm.write_judgments(day, {"600519": {"tag": "利好", "read": "白酒回暖"},
                             "000630": None}, as_of="2026-07-07T09:30", source="rescore")
    js = sm.read_judgments(["600519", "000630", "300750"], day)
    assert js["600519"]["tag"] == "利好" and js["600519"]["score"] == 1.0
    assert js["600519"]["read"] == "白酒回暖"
    assert js["000630"] is None                          # 判过无新闻 → None
    assert "000630" in js and "300750" not in js         # None≠缺席:000630 在 dict,300750 从未判
    assert sm.read_judgment("600519", day)["tag"] == "利好"
    assert sm.read_judgment("300750", day) is None


def test_latest_wins_same_code():
    day = "2026-07-07"
    sm.write_judgments(day, {"600519": {"tag": "中性", "read": "早盘"}}, source="rescore")
    sm.write_judgments(day, {"600519": {"tag": "利空", "read": "午后出利空"}}, source="news_search")
    assert sm.read_judgment("600519", day)["tag"] == "利空"   # 后写覆盖前写


def test_month_rotation_isolates_days():
    sm.write_judgments("2026-07-31", {"600519": {"tag": "利好", "read": "七月"}}, source="x")
    sm.write_judgments("2026-08-01", {"600519": {"tag": "利空", "read": "八月"}}, source="x")
    assert sm._file("judgments", "2026-07-31").name == "judgments-202607.jsonl"
    assert sm._file("judgments", "2026-08-01").name == "judgments-202608.jsonl"
    assert sm.read_judgment("600519", "2026-07-31")["tag"] == "利好"   # 跨月文件互不串
    assert sm.read_judgment("600519", "2026-08-01")["tag"] == "利空"


def test_market_latest_wins_and_empty():
    day = "2026-07-07"
    assert sm.latest_market(day)["market_read"] is None                # 无 → 全 None
    sm.write_market(day, "情绪偏暖", "利好", "2026-07-07T09:30", "rescore")
    sm.write_market(day, "午后转弱", "中性", "2026-07-07T13:30", "news_search")
    mk = sm.latest_market(day)
    assert mk["market_read"] == "午后转弱" and mk["market_tilt"] == "中性"  # latest wins
    assert sm.write_market(day, None, None, None, "x") is False         # 空不写


def test_dashless_date_normalized():
    """评审 Minor:dashless 20260707 归一到 ISO,与存储行同形,不再假报未判。"""
    sm.write_judgments("2026-07-07", {"600519": {"tag": "利好", "read": "r"}}, source="x")
    assert sm.read_judgment("600519", "20260707")["tag"] == "利好"      # 无连字符也命中
    assert sm.read_summary("600519", "20260707")["date"] == "2026-07-07"


def test_read_summary_one_stop():
    day = "2026-07-07"
    sm.write_market(day, "偏暖", "利好", "2026-07-07T09:30", "rescore")
    sm.write_judgments(day, {"600519": {"tag": "利好", "read": "r"}}, source="rescore")
    s = sm.read_summary("600519", day)
    assert s["market"]["market_read"] == "偏暖" and s["judgment"]["tag"] == "利好" and s["judged"] is True
    s2 = sm.read_summary("300750", day)
    assert s2["judged"] is False and s2["judgment"] is None             # 从未判
    s3 = sm.read_summary("", day)
    assert s3["judgment"] is None and s3["market"]["market_tilt"] == "利好"  # 无 code 只给大盘


def test_write_failure_silent(monkeypatch, tmp_path):
    # OSError 写失败不抛(缓存写失败不挡上层)
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(sm.Path, "mkdir", boom)
    assert sm._append(tmp_path / "x.jsonl", {"a": 1}) is False


# ── ww_sentiment 工具面 + news_search 写透 ────────────────────────────────


def test_ww_sentiment_registered_and_reads_store(monkeypatch, tmp_path):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")
    entry = next(t for t in ct.WW_TOOL_TABLE if t["name"] == "ww_sentiment")
    assert "ww_sentiment" in ct.CONSOLE_ALLOWED and entry["confirm"] is False
    day = "2026-07-07"
    sm.write_market(day, "偏暖", "利好", "09:30", "rescore")
    # 生产者(rescore/news_search)存的是 qlib 前缀形 SZ000630;ww_sentiment 同形查
    sm.write_judgments(day, {"SZ000630": {"tag": "利空", "read": "减持"}}, source="news_search")
    out = ct.sentiment_impl(code="SZ000630", date=day)     # 零 LLM 读回
    assert out["ok"] and "利空" in out["content"] and "偏暖" in out["content"]
    # 未判票诚实提示
    out2 = ct.sentiment_impl(code="300750", date=day)
    assert "未判读" in out2["content"] and "ww_news_search" in out2["content"]
    assert ct.sentiment_impl(code="bad!")["ok"] is False    # 非法代码拒
    # 判过无新闻(judged=True 且 judgment=None):措辞须与"未判读"区分(评审 Minor)
    sm.write_judgments(day, {"SZ600000": None}, source="rescore")
    out3 = ct.sentiment_impl(code="SZ600000", date=day)
    assert "已判" in out3["content"] and "无相关新闻" in out3["content"]
    assert "未判读" not in out3["content"]                  # 绝不导向重判


def test_ww_sentiment_wrap_carries_full_content(monkeypatch, tmp_path):
    """交付层守护:ww_sentiment content 经真 _wrap 全量可见,不被 json[:400] 截断。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")
    day = "2026-07-07"
    sm.write_market(day, "大盘消息面读数够长" * 60, "利好", "09:30", "rescore")
    sm.write_judgments(day, {"SZ000630": {"tag": "利空", "read": "个股解读够长" * 60}}, source="x")
    tr = ct._wrap(ct.sentiment_impl)(code="SZ000630", date=day)
    assert not tr.is_error and len(tr.content) > 400 and "利空" in tr.content


def test_news_search_write_through(monkeypatch, tmp_path):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")
    # 桩 news_sentiment:LLM 成功返本票判读 + 大盘
    monkeypatch.setattr(ct, "_run_news_sentiment", lambda codes, limit: {
        "ok": True, "as_of": "10:00", "market_read": "情绪修复", "market_tilt": "利好",
        "market": [], "by_code": {}, "sentiment": {"SZ000630": {"tag": "利好", "read": "中标"}}})
    r = ct.news_search_impl(code="SZ000630", scope="stock")
    assert r["ok"]
    from datetime import date as _d
    day = _d.today().isoformat()
    # 写透:rescore/ww_sentiment 现在能读到 news_search 的判读与大盘(共享 store)
    assert sm.read_judgment("SZ000630", day) == {"tag": "利好", "read": "中标", "score": 1.0}
    assert sm.latest_market(day)["market_tilt"] == "利好"


def test_news_search_llm_fail_no_write(monkeypatch, tmp_path):
    """LLM 失败(ok=False)→ news_search 早退,绝不写 store(可重试语义,不污染当日缓存)。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(sm, "_ROOT", tmp_path / "sentiment")
    monkeypatch.setattr(ct, "_run_news_sentiment",
                        lambda codes, limit: {"ok": False, "reason": "限频"})
    r = ct.news_search_impl(code="SZ000630", scope="stock")
    assert r["ok"] is False
    from datetime import date as _d
    day = _d.today().isoformat()
    assert sm.read_judgments(["SZ000630"], day) == {}      # 失败不写 → 从未判(可重试)
    assert sm.latest_market(day)["market_read"] is None
