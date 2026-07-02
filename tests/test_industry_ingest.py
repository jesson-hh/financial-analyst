# -*- coding: utf-8 -*-
import json
import time

import pandas as pd


class _FakeClient:
    """镜像真 LLMClient 契约(见 test_industry_llmx.py::_FakeClient):chat() 返回
    dict {"choices":[{"message":{"content":...}}]};model/tokens 是 client 实例属性。"""

    def __init__(self, model="deepseek-chat", prompt_tokens=10, completion_tokens=5):
        self.model = model
        self.total_prompt_tokens = prompt_tokens
        self.total_completion_tokens = completion_tokens

    async def chat(self, messages, **kw):
        content = json.dumps(
            {"segments": [{"segment_id": "C2", "stance": "多", "strength": 1, "quote": None}]},
            ensure_ascii=False)
        return {"choices": [{"message": {"content": content}}]}


_OkClient = _FakeClient


class _FailOnD2Client(_FakeClient):
    async def chat(self, messages, **kw):
        joined = "".join(m["content"] for m in messages)
        if "标题D2" in joined:
            raise RuntimeError("boom-d2")
        return await super().chat(messages, **kw)


def _mk_corpus(tmp_path, n=2):
    txt = tmp_path / "t.txt"
    txt.write_text("正文", encoding="utf-8")
    rows = []
    for i in range(1, n + 1):
        rows.append({"doc_id": f"d{i}", "doc_type": "industry_research", "title": f"标题D{i}", "org": "x",
                     "publish_ts": f"2026-06-2{i}", "text_path": str(txt), "stock_codes": "",
                     "status": "parsed", "text_chars": 2})
    pd.DataFrame(rows).to_parquet(tmp_path / "documents.parquet")


def _wait_done(mod, timeout=10):
    for _ in range(int(timeout * 20)):
        if not mod.ingest_state()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("ingest 未在时限内结束")


def test_ingest_ok_advances_watermark(tmp_path, monkeypatch):
    _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    r = ingest.start_ingest(client=_OkClient())
    assert r["ok"] and r["accepted"]
    _wait_done(ingest)
    st = store.load_state()
    assert st["watermark"] == "2026-06-22" and st["totals"]["docs"] == 2
    assert len(store.load_extractions()) == 2
    assert st["failed_docs"] == []


def test_ingest_partial_failure_keeps_watermark(tmp_path, monkeypatch):
    _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    ingest.start_ingest(client=_FailOnD2Client())
    _wait_done(ingest)
    st = store.load_state()
    assert st["watermark"] is None                      # 有失败,水位不动
    assert [f["doc_id"] for f in st["failed_docs"]] == ["d2"]
    assert len(store.load_extractions()) == 1           # d1 成功已落库


def test_ingest_single_flight(tmp_path, monkeypatch):
    _mk_corpus(tmp_path, n=1)
    monkeypatch.setenv("GL_TEXT_SOURCE_ROOT", str(tmp_path))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest

    class _SlowClient(_OkClient):
        async def chat(self, messages, **kw):
            import asyncio
            await asyncio.sleep(0.4)
            return await super().chat(messages, **kw)

    r1 = ingest.start_ingest(client=_SlowClient())
    r2 = ingest.start_ingest(client=_SlowClient())
    assert r1["accepted"] is True
    assert r2["accepted"] is False and r2["running"] is True
    _wait_done(ingest)
