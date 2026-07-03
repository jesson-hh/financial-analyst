# -*- coding: utf-8 -*-
import pandas as pd


def _mk_corpus(tmp_path):
    txt = tmp_path / "text" / "a.txt"
    txt.parent.mkdir(parents=True, exist_ok=True)
    txt.write_text("EML 缺口 25-30%" + "x" * 30000, encoding="utf-8")
    df = pd.DataFrame([
        {"doc_id": "d1", "doc_type": "industry_research", "title": "光通信行业深度", "org": "某券商",
         "publish_ts": "2026-06-30", "text_path": str(txt), "stock_codes": "", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d2", "doc_type": "company_research", "title": "源杰科技点评", "org": "某券商",
         "publish_ts": "2026-06-29", "text_path": str(txt), "stock_codes": "SH688498", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d3", "doc_type": "company_research", "title": "某白酒公司点评", "org": "某券商",
         "publish_ts": "2026-06-28", "text_path": str(txt), "stock_codes": "SH600519", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d4", "doc_type": "company_research", "title": "液冷龙头跟踪", "org": "某券商",
         "publish_ts": "2026-06-27", "text_path": str(txt), "stock_codes": "SH600000", "status": "parsed", "text_chars": 30015},
        {"doc_id": "d5", "doc_type": "industry_research", "title": "扫描版", "org": "某券商",
         "publish_ts": "2026-06-26", "text_path": str(txt), "stock_codes": "", "status": "parse_failed", "text_chars": 0},
        {"doc_id": "d0", "doc_type": "industry_research", "title": "水位前旧文", "org": "某券商",
         "publish_ts": "2026-01-01", "text_path": str(txt), "stock_codes": "", "status": "parsed", "text_chars": 30015},
    ])
    df.to_parquet(tmp_path / "documents.parquet")
    return tmp_path


def test_scan_filter_and_watermark(tmp_path, monkeypatch):
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes={"SH688498"}, keywords=["液冷"])
    assert r["ok"] is True
    ids = [d["doc_id"] for d in r["docs"]]
    # d1 行业研报全收;d2 票池码命中;d4 标题关键词命中;d3 白酒不收;d5 parse_failed 跳过;d0 在水位前
    assert ids == ["d4", "d2", "d1"]          # publish_ts 升序
    assert r["skipped_unparsed"] == 1


def test_scan_watermark_boundary_inclusive(tmp_path, monkeypatch):
    # publish_ts == watermark 的文必须被扫到(同日晚回填的研报不能永久漏扫)
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-27", pool_codes={"SH688498"}, keywords=["液冷"])
    assert r["ok"] is True
    ids = [d["doc_id"] for d in r["docs"]]
    assert ids == ["d4", "d2", "d1"]          # d4 publish_ts == 水位,应在列


def test_scan_excludes_already_extracted(tmp_path, monkeypatch):
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes={"SH688498"}, keywords=["液冷"],
                      exclude_doc_ids={"d4", "d2"})
    assert r["ok"] is True
    assert [d["doc_id"] for d in r["docs"]] == ["d1"]


def test_scan_exclude_applies_before_limit(tmp_path, monkeypatch):
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(watermark="2026-06-01", pool_codes={"SH688498"}, keywords=["液冷"],
                      exclude_doc_ids={"d4"}, limit=1)
    assert [d["doc_id"] for d in r["docs"]] == ["d2"]     # limit 名额不被已抽取文占掉


def test_read_doc_text_truncates(tmp_path, monkeypatch):
    root = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(root))
    from guanlan_v2.industry.corpus import read_doc_text
    txt = read_doc_text(str(root / "text" / "a.txt"), max_chars=20000)
    assert len(txt) <= 20000 + 50 and "EML 缺口 25-30%" in txt and "…[中略]…" in txt


def test_missing_root_honest(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path / "nope"))
    from guanlan_v2.industry.corpus import scan_new_docs, corpus_freshness
    r = scan_new_docs(None, set(), [])
    assert r["ok"] is False and r["reason"]
    f = corpus_freshness()
    assert f["ok"] is False and f["reason"]


def test_missing_schema_columns_honest(tmp_path, monkeypatch):
    # 缺 status 和 text_chars 列——测试防静默清空
    pd.DataFrame([{"doc_id": "d1", "publish_ts": "2026-06-30"}]).to_parquet(tmp_path / "documents.parquet")
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    from guanlan_v2.industry.corpus import scan_new_docs
    r = scan_new_docs(None, set(), [])
    assert r["ok"] is False and "缺列" in r["reason"]
