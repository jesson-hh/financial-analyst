# -*- coding: utf-8 -*-
import json


def test_append_and_load(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    store.append_extraction({"doc_id": "d1", "publish_ts": "2026-06-30", "segments": []})
    store.append_extraction({"doc_id": "d2", "publish_ts": "2026-05-01", "segments": []})
    allrecs = store.load_extractions()
    assert [r["doc_id"] for r in allrecs] == ["d1", "d2"]
    recent = store.load_extractions(window_days=30, now="2026-07-02")
    assert [r["doc_id"] for r in recent] == ["d1"]


def test_load_extracted_doc_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    assert store.load_extracted_doc_ids() == set()
    store.append_extraction({"doc_id": "d1", "publish_ts": "2026-06-30"})
    store.append_extraction({"doc_id": "d2", "publish_ts": "2026-06-30"})
    with open(tmp_path / "extractions.jsonl", "a", encoding="utf-8") as f:
        f.write("{broken json\n")                          # 坏行跳过
    store.append_extraction({"doc_id": "d1", "publish_ts": "2026-06-30"})
    assert store.load_extracted_doc_ids() == {"d1", "d2"}


def test_state_roundtrip_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    st = store.load_state()
    assert st["watermark"] is None and st["totals"]["docs"] == 0
    st["watermark"] = "2026-07-01"
    st["totals"]["docs"] = 3
    store.save_state(st)
    again = store.load_state()
    assert again["watermark"] == "2026-07-01" and again["totals"]["docs"] == 3
    assert not list(tmp_path.glob("*.tmp"))
    json.loads((tmp_path / "ingest_state.json").read_text(encoding="utf-8"))


def test_corrupt_jsonl_line_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path))
    from guanlan_v2.industry import store
    store.append_extraction({"doc_id": "ok1", "publish_ts": "2026-06-30"})
    with open(tmp_path / "extractions.jsonl", "a", encoding="utf-8") as f:
        f.write("{broken json\n")
    store.append_extraction({"doc_id": "ok2", "publish_ts": "2026-06-30"})
    assert [r["doc_id"] for r in store.load_extractions()] == ["ok1", "ok2"]
