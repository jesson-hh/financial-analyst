# -*- coding: utf-8 -*-
"""corpus 种子包契约(2026-07-03 语料层重建后):theme 白名单/水位含当日/回填窗/剔重/诚实降级。"""
import pandas as pd

_AI_THEMES = ["compute_chain", "semiconductor_equipment_chain", "ai_application_chain"]


def _mk_seed(tmp_path):
    txt = tmp_path / "text" / "a.txt"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("EML 缺口 25-30%" + "x" * 30000, encoding="utf-8")
    rows = [
        {"doc_id": "d1", "report_kind": "industry_research", "title": "光通信行业深度", "institution": "某券商",
         "publish_ts": "2026-06-30 00:00:00", "text_path": str(txt), "stock_codes": "[]",
         "text_status": "parsed", "text_chars": 30015, "matched_themes": ["compute_chain"]},
        {"doc_id": "d2", "report_kind": "company_research", "title": "源杰科技点评", "institution": "某券商",
         "publish_ts": "2026-06-29 00:00:00", "text_path": str(txt), "stock_codes": "['SH688498']",
         "text_status": "parsed", "text_chars": 30015, "matched_themes": ["compute_chain"]},
        {"doc_id": "d3", "report_kind": "company_research", "title": "某锂电公司点评", "institution": "某券商",
         "publish_ts": "2026-06-28 00:00:00", "text_path": str(txt), "stock_codes": "['SZ300750']",
         "text_status": "parsed", "text_chars": 30015, "matched_themes": ["lithium_battery_chain"]},
        {"doc_id": "d4", "report_kind": "company_research", "title": "AI办公跟踪", "institution": "某券商",
         "publish_ts": "2026-06-27 00:00:00", "text_path": str(txt), "stock_codes": "['SH688111']",
         "text_status": "parsed", "text_chars": 30015, "matched_themes": ["ai_application_chain"]},
        {"doc_id": "d5", "report_kind": "industry_research", "title": "扫描版", "institution": "某券商",
         "publish_ts": "2026-06-26 00:00:00", "text_path": str(txt), "stock_codes": "[]",
         "text_status": "parse_failed", "text_chars": 0, "matched_themes": ["compute_chain"]},
        {"doc_id": "d0", "report_kind": "industry_research", "title": "水位前旧文", "institution": "某券商",
         "publish_ts": "2026-01-01 00:00:00", "text_path": str(txt), "stock_codes": "[]",
         "text_status": "parsed", "text_chars": 30015, "matched_themes": ["compute_chain"]},
    ]
    p = tmp_path / "seed.parquet"
    pd.DataFrame(rows).to_parquet(p)
    return p, txt


def test_scan_theme_filter_and_watermark(tmp_path, monkeypatch):
    p, _ = _mk_seed(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes=set(), keywords=[], themes=_AI_THEMES)
    assert r["ok"] is True
    ids = [d["doc_id"] for d in r["docs"]]
    # d1/d2/d4 AI主题收;d3 锂电主题剔;d5 parse_failed 跳;d0 在水位前
    assert ids == ["d4", "d2", "d1"]          # publish_ts 升序
    assert r["skipped_unparsed"] == 1
    # 契约键:institution→org, report_kind→doc_type, 日期截10位
    d = r["docs"][0]
    assert d["org"] == "某券商" and d["doc_type"] == "company_research" and d["publish_ts"] == "2026-06-27"


def test_scan_watermark_boundary_inclusive(tmp_path, monkeypatch):
    # publish_ts == watermark 的文必须被扫到(同日晚回填的研报不能永久漏扫)
    p, _ = _mk_seed(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-27", pool_codes=set(), keywords=[], themes=_AI_THEMES)
    assert [d["doc_id"] for d in r["docs"]] == ["d4", "d2", "d1"]   # d4 == 水位,应在列


def test_scan_no_watermark_uses_backfill_window(tmp_path, monkeypatch):
    # 首跑无水位:只回填 backfill_days 内(防全量3年烧钱)
    txt = tmp_path / "t.txt"
    txt.write_text("x" * 100, encoding="utf-8")
    now = pd.Timestamp.now()
    rows = [
        {"doc_id": "new", "report_kind": "industry_research", "title": "近文", "institution": "某券商",
         "publish_ts": str((now - pd.Timedelta(days=3)).date()), "text_path": str(txt), "stock_codes": "[]",
         "text_status": "parsed", "text_chars": 100, "matched_themes": ["compute_chain"]},
        {"doc_id": "old", "report_kind": "industry_research", "title": "远文", "institution": "某券商",
         "publish_ts": str((now - pd.Timedelta(days=200)).date()), "text_path": str(txt), "stock_codes": "[]",
         "text_status": "parsed", "text_chars": 100, "matched_themes": ["compute_chain"]},
    ]
    p = tmp_path / "seed.parquet"
    pd.DataFrame(rows).to_parquet(p)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark=None, pool_codes=set(), keywords=[], themes=_AI_THEMES, backfill_days=35)
    assert [d["doc_id"] for d in r["docs"]] == ["new"]


def test_scan_excludes_already_extracted(tmp_path, monkeypatch):
    p, _ = _mk_seed(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes=set(), keywords=[], themes=_AI_THEMES,
                      exclude_doc_ids={"d4", "d2"})
    assert [d["doc_id"] for d in r["docs"]] == ["d1"]


def test_scan_exclude_applies_before_limit(tmp_path, monkeypatch):
    p, _ = _mk_seed(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes=set(), keywords=[], themes=_AI_THEMES,
                      exclude_doc_ids={"d4"}, limit=1)
    assert [d["doc_id"] for d in r["docs"]] == ["d2"]     # limit 名额不被已抽取文占掉


def test_read_doc_text_truncates(tmp_path, monkeypatch):
    _, txt = _mk_seed(tmp_path)
    from guanlan_v2.industry.corpus import read_doc_text
    out = read_doc_text(str(txt), max_chars=20000)
    assert len(out) <= 20000 + 50 and "EML 缺口 25-30%" in out and "…[中略]…" in out


def test_missing_seed_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "nope.parquet"))
    from guanlan_v2.industry.corpus import scan_new_docs, corpus_freshness
    r = scan_new_docs(None, set(), [])
    assert r["ok"] is False and r["reason"]
    f = corpus_freshness()
    assert f["ok"] is False and f["reason"]


def test_missing_schema_columns_honest(tmp_path, monkeypatch):
    # 缺 text_status/text_chars 等列——测试防静默清空(T3 终审教训)
    p = tmp_path / "seed.parquet"
    pd.DataFrame([{"doc_id": "d1", "publish_ts": "2026-06-30"}]).to_parquet(p)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(None, set(), [])
    assert r["ok"] is False and "缺列" in r["reason"]


def test_freshness_theme_scoped(tmp_path, monkeypatch):
    p, _ = _mk_seed(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(p))
    from guanlan_v2.industry.corpus import corpus_freshness
    f = corpus_freshness(themes=_AI_THEMES)
    assert f["ok"] is True
    assert f["n_docs"] == 4          # d1/d2/d4/d0 parsed 且 AI 主题(d3 锂电剔,d5 未解析)
    assert f["n_industry"] == 2      # d1/d0
    assert f["latest_publish_ts"] == "2026-06-30"
