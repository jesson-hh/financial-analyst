import sys, pathlib
_ENGINE = pathlib.Path(__file__).resolve().parents[1] / "engine"
if str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

from financial_analyst.data import f10_corpus as fc


def test_num_handles_units_and_missing():
    assert fc._num("134.0947亿") == 13409470000.0
    assert fc._num("177.86万") == 1778600.0
    assert fc._num("2.7954") == 2.7954
    assert fc._num("83.59%") == 83.59
    assert fc._num("-") is None
    assert fc._num("") is None
    assert fc._num(None) is None


def test_cells_splits_fullwidth_bar():
    line = "｜总股本(股)            ｜    134.0947亿｜    127.9413亿｜"
    assert fc._cells(line) == ["总股本(股)", "134.0947亿", "127.9413亿"]


def test_find_date():
    assert fc._find_date("｜   2026-05-29   ｜公告｜") == "2026-05-29"
    assert fc._find_date("无日期") is None


def test_visible_date_reporting_lag():
    assert fc._visible_date("2026-03-31") == "2026-04-30"   # Q1
    assert fc._visible_date("2025-06-30") == "2025-08-31"   # H1
    assert fc._visible_date("2025-09-30") == "2025-10-31"   # Q3
    assert fc._visible_date("2025-12-31") == "2026-04-30"   # annual


def _fixt(cat):
    import glob
    p = pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10" / "sz000630"
    hit = glob.glob(str(p / f"{cat}_*.txt"))[0]
    return pathlib.Path(hit).read_text(encoding="utf-8")


def test_parse_valuation_live_latest_period():
    v = fc._parse_valuation(_fixt("最新提示"), asof=None)
    assert v["report_period"] == "2026-03-31"
    assert v["total_shares"] == 13409470000.0
    assert v["bvps"] == 2.7954
    assert v["roe"] == 3.59
    assert v["revenue"] == 646.993e8
    assert v["revenue_yoy"] == 83.59
    assert v["net_profit"] == 13.3845e8


def test_parse_valuation_pit_picks_visible_quarter():
    # asof 2026-04-15:Q1(可见 2026-04-30)与 2025 年报(可见 2026-04-30)都不可见
    # -> 退到 2025-09-30(可见 2025-10-31 <= asof)
    v = fc._parse_valuation(_fixt("最新提示"), asof="2026-04-15")
    assert v["report_period"] == "2025-09-30"
    assert v["bvps"] == 2.6938


def test_parse_valuation_asof_too_early_returns_none():
    v = fc._parse_valuation(_fixt("最新提示"), asof="2024-01-01")
    assert v is None


def test_parse_events_live_sorted_desc():
    evs = fc._parse_events(_fixt("公司大事"), category="公司大事", asof=None)
    assert evs[0]["date"] == "2026-05-29"
    assert "权益分派实施" in evs[0]["title"]
    assert evs[0]["category"] == "公司大事"
    assert len(evs) == 4


def test_parse_events_pit_drops_future():
    evs = fc._parse_events(_fixt("公司大事"), category="公司大事", asof="2026-05-15")
    dates = [e["date"] for e in evs]
    assert dates == ["2026-05-14", "2026-05-09"]   # 05-29/05-21 被裁


def test_parse_broker_extracts_target_price():
    b = fc._parse_broker(_fixt("研究报告"), asof=None)
    r = [x for x in b["ratings"] if x["org"] == "国泰海通"][0]
    assert r["date"] == "2026-03-31"
    assert r["rating"] == "增持"
    assert r["report_price"] == 5.81
    assert r["target_price"] == 6.80
    assert len(b["ratings"]) == 4


def test_parse_broker_pit_filters_by_date():
    b = fc._parse_broker(_fixt("研究报告"), asof="2026-01-01")
    assert all(x["date"] <= "2026-01-01" for x in b["ratings"])
    assert any(x["org"] == "国信证券" and x["date"] == "2025-08-19" for x in b["ratings"])


def test_parse_lhb_margin_rows():
    m = fc._parse_lhb(_fixt("龙虎榜单"), asof=None)["margin"]
    assert m[0]["date"] == "2026-05-29"
    assert m[0]["margin_balance"] == 23.292e8        # 融资余额
    assert m[0]["margin_buy"] == 5.188e8             # 融资买入额
    assert len(m) == 3


def test_parse_lhb_pit():
    m = fc._parse_lhb(_fixt("龙虎榜单"), asof="2026-05-25")["margin"]
    assert [r["date"] for r in m] == ["2026-05-22"]


def test_parse_holders():
    h = fc._parse_holders(_fixt("股东研究"), asof=None)
    assert h["report_date"] == "2026-03-31"
    assert h["a_share_holders"] == 866667.0          # 86.6667万 户
    assert "铜陵有色金属集团" in h["controlling_holder"]
    assert "安徽省国有资产监督管理委员会" in h["actual_controller"]
    assert h["top_holders"][0]["name"].startswith("铜陵有色金属集团")
    assert h["top_holders"][0]["pct"] == 34.51
    assert h["top_holders"][1]["name"].startswith("香港中央结算")
    # 折行续行(无持股数)被跳过 -> 只取 3 个真实行
    assert len(h["top_holders"]) == 3


def test_parse_holders_pit_too_early():
    assert fc._parse_holders(_fixt("股东研究"), asof="2024-01-01") is None


def test_parse_main_capital():
    m = fc._parse_main_capital(_fixt("主力追踪"), asof=None)
    assert m["report_period"] == "2025-12-31"          # 跳过 2026-03-31 未完
    assert m["inst_count"] == 587
    assert m["inst_holding_pct"] == 13.29
    assert m["fund_holding_pct"] == 13.29
    assert m["holder_count_trend"][0]["count"] == 269241.0   # 26.9241万户(最新期)


def test_parse_main_capital_pit():
    m = fc._parse_main_capital(_fixt("主力追踪"), asof="2025-11-01")
    assert m["report_period"] <= "2025-09-30"          # 12-31 可见日 2026-04-30 > asof 被裁


def test_parse_main_capital_incomplete_latest_period_aligns():
    # 最新期标 "X|未完"(半角竖线)会被 _cells 误切成两格→列错位。
    # 须跳到上一完整期(2025-12-31),且 机构数量/持仓比例/基金比例 同期对齐。
    text = "\n".join([
        "【1.机构持股汇总】",
        "｜报告日期    ｜    2026-03-31｜    2025-12-31｜    2025-09-30｜",
        "｜机构数量(家) ｜     1(更新中)｜           593｜           132｜",
        "｜累计持仓比例 ｜    0.01%|未完｜        54.97%｜        49.18%｜",
        "｜基金持仓比例 ｜    0.01%|未完｜        13.29%｜         9.44%｜",
    ])
    m = fc._parse_main_capital(text, asof=None)
    assert m["report_period"] == "2025-12-31"
    assert m["inst_count"] == 593
    assert m["inst_holding_pct"] == 54.97       # 同为 2025-12-31 列,不串到别期
    assert m["fund_holding_pct"] == 13.29


def test_parse_lhb_abnormal():
    a = fc._parse_lhb(_fixt("龙虎榜单"), asof=None)["abnormal"]
    assert a[0]["date"] and a[0]["amplitude_pct"] == 12.30
    hit = [x for x in a if x["date"] == "2026-02-03"][0]
    assert hit["amplitude_pct"] == 15.55
    assert hit["amount"] == 102.3364e8        # 亿→元
    assert hit["volume"] == 14.2239e8         # 亿股→股


def test_parse_lhb_abnormal_pit():
    a = fc._parse_lhb(_fixt("龙虎榜单"), asof="2026-02-10")["abnormal"]
    assert all(x["date"] <= "2026-02-10" for x in a)
    assert any(x["date"] == "2026-02-03" for x in a)


def test_parse_lhb_moneyflow():
    mf = fc._parse_lhb(_fixt("龙虎榜单"), asof=None)["moneyflow"]
    assert mf[0]["date"] == "2026-05-29"
    assert mf[0]["main_net"] == 4.2355e8
    assert mf[0]["main_pct"] == 8.08
    assert len(mf) == 2


def test_parse_lhb_block_trades_honest_empty():
    # 真文件无 §4 大宗交易数据段 -> 诚实空,绝不伪造
    lhb = fc._parse_lhb(_fixt("龙虎榜单"), asof=None)
    assert lhb["block_trades"] == []
    assert any("§4" in n or "大宗交易" in n for n in lhb.get("notes", []))


def _fixt_root():
    return str(pathlib.Path(__file__).resolve().parents[0] / "fixtures" / "f10")


def test_load_facts_live_assembles_all():
    f = fc.load_facts("SZ000630", asof=None, root=_fixt_root())
    d = f.to_dict()
    assert d["valuation"]["total_shares"] == 13409470000.0
    assert d["events"][0]["date"] == "2026-05-29"
    assert any(r["target_price"] == 6.80 for r in d["broker"]["ratings"])
    assert d["lhb"]["margin"][0]["date"] == "2026-05-29"
    assert d["snapshot_date"]
    assert d["asof"] is None
    assert d["honest_note"] == ""
    assert d["provenance"]


def test_load_facts_missing_stock_is_honest_empty():
    f = fc.load_facts("SZ999999", asof=None, root=_fixt_root())
    d = f.to_dict()
    assert d["valuation"] is None and d["events"] == []
    assert "无" in d["honest_note"]


def test_load_facts_code_normalization_and_lowercase_dir():
    assert fc.load_facts("000630", asof=None, root=_fixt_root()).to_dict()["events"]
    assert fc.load_facts("sz000630", asof=None, root=_fixt_root()).to_dict()["events"]
