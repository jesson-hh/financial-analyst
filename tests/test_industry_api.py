# tests/test_industry_api.py
# -*- coding: utf-8 -*-
import json

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _app():
    from guanlan_v2.industry import build_industry_router
    app = FastAPI()
    app.include_router(build_industry_router())
    return TestClient(app)


def _seed_store(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path / "nope"))  # 语料断供→徽章显形
    from guanlan_v2.industry import store
    now = pd.Timestamp.now()
    store.append_extraction({
        "doc_id": "dX", "title": "光芯片深度", "org": "某券商",
        "publish_ts": str((now - pd.Timedelta(days=2)).date()), "doc_type": "industry_research",
        "extracted_at": now.isoformat(timespec="seconds"), "model": "deepseek-chat",
        "segments": [{"segment_id": "C2", "stance": "多", "strength": 3, "quote": "EML缺口", "quote_dropped": False}],
        "catalysts": [{"type": "涨价", "desc": "EML涨价", "date_hint": None}],
        "edges": [{"edge_id": "T4", "verdict": "支持", "evidence": "缺口"}],
        "narratives": [{"narrative_id": "N4", "stance": "多"}],
        "global_updates": [], "stocks": [{"code": "SH688498", "stance": "多", "logic": "量产"}],
    })


def test_board_shape_and_honesty(tmp_path, monkeypatch):
    _seed_store(tmp_path, monkeypatch)
    # 语料断供必须是构造出来的,不能依赖真机恰好缺语料(2026-07-03 种子包已就位)
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "no-seed.parquet"))
    c = _app()
    r = c.get("/industry/board", params={"refresh": 1}).json()
    assert r["ok"] is True
    assert len(r["drivers"]) == 7 and len(r["narratives"]) == 8 and len(r["edges"]) == 15
    segs = {s["id"]: s for s in r["segments"]}
    assert len(segs) == 30 and segs["G3"]["adjacent"] is True
    c2 = segs["C2"]
    assert c2["research"]["n30"] == 1 and c2["research"]["bull"] == 1 and c2["research"]["score"] > 0
    assert c2["quadrant"] in ("hh", "hl", "lh", "ll")
    edge = {e["id"]: e for e in r["edges"]}["T4"]
    assert edge["verdict_counts"]["support"] == 1
    assert r["freshness"]["corpus"]["ok"] is False        # 语料断供诚实显形
    assert r["freshness"]["extracted_total"] == 0          # state.totals 未走 ingest,不冒充


def test_segment_and_doc_detail(tmp_path, monkeypatch):
    _seed_store(tmp_path, monkeypatch)
    c = _app()
    seg = c.get("/industry/segment/C2").json()
    assert seg["ok"] and seg["segment"]["id"] == "C2"
    assert seg["opinions"][0]["doc_id"] == "dX" and seg["opinions"][0]["quote"] == "EML缺口"
    doc = c.get("/industry/doc/dX").json()
    assert doc["ok"] and doc["extraction"]["title"] == "光芯片深度"
    miss = c.get("/industry/doc/nope").json()
    assert miss["ok"] is False and miss["reason"]
    bad = c.get("/industry/segment/ZZ9").json()
    assert bad["ok"] is False


def test_ingest_endpoints(tmp_path, monkeypatch):
    _seed_store(tmp_path, monkeypatch)
    # 语料断供必须显式构造:不设 GL_CHAIN_SEED 时真种子包在场,client=None 会走真 kimi
    # → worker 泄漏拖 _running=True 毒害后续 ingest 测试 + 每次全量烧真钱(2026-07-03 实证)
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "no-seed.parquet"))
    c = _app()
    st = c.get("/industry/ingest_state").json()
    assert "watermark" in st and "running" in st
    r = c.post("/industry/ingest", content=json.dumps({"limit": 1}),
               headers={"Content-Type": "application/json"}).json()
    assert r["ok"] is True and "accepted" in r
    # 等 worker 结束(语料断供,应快速落 failed_docs 而非崩);结束是硬断言,不许泄漏
    import time
    from guanlan_v2.industry import ingest as ing
    done = False
    for _ in range(200):
        if not ing.ingest_state()["running"]:
            done = True
            break
        time.sleep(0.05)
    assert done, "ingest worker 泄漏:10s 未收尾(_running 拖 True 会毒害后续测试)"
    st2 = ing.ingest_state()
    assert st2["failed_docs"] and "语料库不可读" in str(st2["failed_docs"][0].get("reason"))
