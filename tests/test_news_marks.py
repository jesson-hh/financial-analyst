import json, sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import pandas as pd
from financial_analyst.backtest.pit_reader import PitReader
from guanlan_v2.seats import news_marks as nm


class _FakeLoader:
    """最小 day_loader:给 PitReader 一份日历 + data_end 探针。"""
    def __init__(self, days):
        self._days = [pd.Timestamp(d) for d in days]
    def _load_calendar(self, freq):
        return self._days
    def fetch_quote(self, code, s, e, f):
        return pd.DataFrame({"trade_date": [self._days[-1]], "close": [10.0]})


def _mk_store(tmp, day, rows):
    d = tmp / day
    d.mkdir(parents=True, exist_ok=True)
    for kind in ("news", "events", "policy"):
        lines = [json.dumps(r, ensure_ascii=False) for r in rows.get(kind, [])]
        (d / f"{kind}.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _reader(tmp, days):
    return PitReader(store_root=tmp, day_loader=_FakeLoader(days))


def test_pit_drops_future_and_after_boundary(tmp_path):
    days = ["2026-05-25", "2026-05-26", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-27", {"news": [
        {"ts": "2026-05-27T09:00:00", "date": "2026-05-27", "session": "open", "code": None, "title": "早间宏观", "body": "x"},
        {"ts": "2026-05-27T16:30:00", "date": "2026-05-27", "session": "post", "code": None, "title": "盘后消息", "body": "x"},
    ]})
    _mk_store(tmp_path, "2026-05-28", {"news": [
        {"ts": "2026-05-28T09:00:00", "date": "2026-05-28", "session": "open", "code": None, "title": "未来新闻", "body": "x"},
    ]})
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_reader(tmp_path, days))
    titles = [it["title"] for it in out["items"]]
    assert "早间宏观" in titles
    assert "盘后消息" not in titles
    assert "未来新闻" not in titles
    assert all(it["ts"][:10] <= "2026-05-27" for it in out["items"])


def test_events_filtered_by_ann_date(tmp_path):
    days = ["2026-05-26", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-27", {"events": [
        {"ann_date": "2026-05-26", "code": "SZ000630", "type": "block_trade", "summary": "大宗交易", "fields": {"visible_ts": "2026-05-26T23:59:59"}},
        {"ann_date": "2026-05-28", "code": "SZ000630", "type": "x", "summary": "未来事件", "fields": {}},
    ]})
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_reader(tmp_path, days))
    titles = [it["title"] for it in out["items"]]
    assert "大宗交易" in titles and "未来事件" not in titles
    assert [it for it in out["items"] if it["title"] == "大宗交易"][0]["level"] == "event"


def test_code_filter_keeps_stock_and_macro_drops_others(tmp_path):
    days = ["2026-05-26", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-27", {"news": [
        {"ts": "2026-05-27T10:00:00", "date": "2026-05-27", "session": "am", "code": "SZ000630", "title": "本票消息", "body": "x"},
        {"ts": "2026-05-27T10:01:00", "date": "2026-05-27", "session": "am", "code": None, "title": "宏观加息", "body": "x"},
        {"ts": "2026-05-27T10:02:00", "date": "2026-05-27", "session": "am", "code": "SH600519", "title": "茅台消息", "body": "x"},
    ]})
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_reader(tmp_path, days))
    titles = [it["title"] for it in out["items"]]
    assert "本票消息" in titles and "宏观加息" in titles and "茅台消息" not in titles
    lv = {it["title"]: it["level"] for it in out["items"]}
    assert lv["本票消息"] == "stock" and lv["宏观加息"] == "macro"


def test_coverage_floor_partial(tmp_path):
    days = ["2026-05-18", "2026-05-27"]
    _mk_store(tmp_path, "2026-05-18", {})
    (tmp_path / "_meta.json").write_text(json.dumps({
        "news_coverage_floor": "2026-05-20", "cal_start": "2026-03-13", "cal_end": "2026-07-01"}), encoding="utf-8")
    out = nm.assemble_news_marks("SZ000630", "2026-05-18", "pit", 250, reader=_reader(tmp_path, days))
    assert out["coverage"]["partial"] is True and out["coverage"]["note"]


def test_honest_empty_on_missing_asof(tmp_path):
    out = nm.assemble_news_marks("SZ000630", "", "pit", 250, reader=_reader(tmp_path, ["2026-05-27"]))
    assert out["ok"] is True and out["items"] == []


def test_meta_read_never_crashes_honest_degrade():
    class _StubReader:
        # 没有 _root 属性;get_visible_info 返回一个空 VisibleInfo 样式对象
        def get_visible_info(self, *a, **k):
            class _VI:
                news = []; events = []; policy = []
            return _VI()
    out = nm.assemble_news_marks("SZ000630", "2026-05-27", "pit", 250, reader=_StubReader())
    assert out["ok"] is True and out["items"] == []
    assert "coverage" in out and out["provenance"]["source"] == "pit_store"


def test_live_uses_provider_headlines():
    class _Stub:
        def headlines(self, code):
            return ["实时利好一则", "实时利空一则"]
    out = nm.assemble_news_marks("SZ000630", mode="live", provider=_Stub())
    titles = [it["title"] for it in out["items"]]
    assert out["mode"] == "live" and titles == ["实时利好一则", "实时利空一则"]
    assert all(it["ts"] == "" for it in out["items"])


def test_live_provider_failure_is_empty():
    class _Boom:
        def headlines(self, code):
            raise RuntimeError("net down")
    out = nm.assemble_news_marks("SZ000630", mode="live", provider=_Boom())
    assert out["ok"] is True and out["items"] == []


def test_live_three_way_merge_and_dedupe(tmp_path):
    import pandas as pd
    pq = tmp_path / "news_events.parquet"
    pd.DataFrame([
        {"publish_ts": "2026-07-04 09:00:00", "title": "公告:拟增持", "content": "x",
         "source": "eastmoney_announcement", "stock_codes": "SZ000630", "is_policy": False},
        {"publish_ts": "2026-07-04 08:00:00", "title": "政策:降准", "content": "y",
         "source": "gov_policy", "stock_codes": "", "is_policy": True},
    ]).to_parquet(pq)
    sn = lambda code, limit=20: [
        {"time": "2026-07-04 10:00", "title": "个股新闻A", "summary": "a", "source": "东方财富"},
        {"time": "2026-07-04 09:30", "title": "重复标题", "summary": "b", "source": "东方财富"},
    ]
    kx = lambda limit=200: [
        {"time": "2026-07-04 10:05", "title": "重复标题", "summary": "", "codes": ["SZ000630"]},
        {"time": "2026-07-04 10:03", "title": "宏观快讯Z", "summary": "", "codes": []},
    ]
    out = nm.assemble_news_marks("000630", mode="live", stock_news_fn=sn, kuaixun_fn=kx,
                                 parquet_path=pq, limit=10)
    titles = [it["title"] for it in out["items"]]
    assert titles.count("重复标题") == 1                      # ①②同标题去重
    lv = {it["title"]: it["level"] for it in out["items"]}
    assert lv["个股新闻A"] == "stock" and lv["公告:拟增持"] == "event"
    assert lv["政策:降准"] == "policy" and lv["宏观快讯Z"] == "macro"
    assert out["freshness"]["rich_available"] is True
    assert out["freshness"]["rich_asof"] == "2026-07-04T09:00"
    ts = [it["ts"] for it in out["items"]]
    assert ts == sorted(ts, reverse=True)                     # 展示按 ts 降序


def test_live_limit_prioritizes_stock_items():
    sn = lambda code, limit=20: [{"time": f"2026-07-04 09:{i:02d}", "title": f"股{i}",
                                  "summary": "", "source": ""} for i in range(5)]
    kx = lambda limit=200: [{"time": f"2026-07-04 10:{i:02d}", "title": f"宏{i}",
                             "summary": "", "codes": []} for i in range(5)]
    out = nm.assemble_news_marks("SZ000630", mode="live", stock_news_fn=sn, kuaixun_fn=kx,
                                 parquet_path="Z:/__none__.parquet", limit=6)
    lv = [it["level"] for it in out["items"]]
    assert len(out["items"]) == 6 and lv.count("stock") == 5 and lv.count("macro") == 1


def test_live_single_source_failure_degrades():
    def sn(code, limit=20):
        raise RuntimeError("akshare down")
    kx = lambda limit=200: [{"time": "2026-07-04 10:00", "title": "快讯B", "summary": "",
                             "codes": ["SZ000630"]}]
    out = nm.assemble_news_marks("SZ000630", mode="live", stock_news_fn=sn, kuaixun_fn=kx,
                                 parquet_path="Z:/__none__.parquet", limit=5)
    assert out["ok"] is True and [it["title"] for it in out["items"]] == ["快讯B"]
    assert "个股新闻" in out["coverage"]["note"]


def test_live_parquet_absent_rich_unavailable():
    out = nm.assemble_news_marks("SZ000630", mode="live",
                                 stock_news_fn=lambda code, limit=20: [],
                                 kuaixun_fn=lambda limit=200: [],
                                 parquet_path="Z:/__none__.parquet", limit=5)
    assert out["ok"] is True and out["items"] == []
    assert out["freshness"]["rich_available"] is False and out["freshness"]["pulled_at"]
