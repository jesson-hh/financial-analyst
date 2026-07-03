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


def _seed_row(i, ts, txt):
    """种子包 schema(2026-07-03 语料层重建):institution/report_kind/text_status/matched_themes。"""
    return {"doc_id": f"d{i}", "report_kind": "industry_research", "title": f"标题D{i}",
            "institution": "x", "publish_ts": ts, "text_path": str(txt), "stock_codes": "[]",
            "text_status": "parsed", "text_chars": 2, "matched_themes": ["compute_chain"]}


def _dates(n):
    """动态近日日期(升序),避开首跑回填窗(35天)时效炸弹。"""
    now = pd.Timestamp.now()
    return [str((now - pd.Timedelta(days=n - i)).date()) for i in range(1, n + 1)]


def _mk_corpus(tmp_path, n=2):
    txt = tmp_path / "t.txt"
    txt.write_text("正文", encoding="utf-8")
    ds = _dates(n)
    rows = [_seed_row(i, ds[i - 1], txt) for i in range(1, n + 1)]
    pd.DataFrame(rows).to_parquet(tmp_path / "seed.parquet")
    return ds


def _wait_done(mod, timeout=10):
    for _ in range(int(timeout * 20)):
        if not mod.ingest_state()["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("ingest 未在时限内结束")


def test_ingest_ok_advances_watermark(tmp_path, monkeypatch):
    ds = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "seed.parquet"))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    r = ingest.start_ingest(client=_OkClient())
    assert r["ok"] and r["accepted"]
    _wait_done(ingest)
    st = store.load_state()
    assert st["watermark"] == ds[-1] and st["totals"]["docs"] == 2
    assert len(store.load_extractions()) == 2
    assert st["failed_docs"] == []


def test_ingest_rerun_same_day_backfill_no_double_count(tmp_path, monkeypatch):
    # 首跑后水位=d2 日;同日晚回填 d3(publish_ts == 水位)→ 二跑只抽 d3,
    # 已抽取的 d2 不重复(totals.docs 不双计,无重复 LLM 花费)
    ds = _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "seed.parquet"))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    ingest.start_ingest(client=_OkClient())
    _wait_done(ingest)
    assert store.load_state()["totals"]["docs"] == 2

    txt = tmp_path / "t.txt"
    rows = [_seed_row(1, ds[0], txt), _seed_row(2, ds[1], txt), _seed_row(3, ds[1], txt)]
    pd.DataFrame(rows).to_parquet(tmp_path / "seed.parquet")
    ingest.start_ingest(client=_OkClient())
    _wait_done(ingest)
    st = store.load_state()
    assert st["totals"]["docs"] == 3                       # 2+1,不双计
    assert sorted(r["doc_id"] for r in store.load_extractions()) == ["d1", "d2", "d3"]
    assert st["watermark"] == ds[1]


def test_ingest_partial_failure_keeps_watermark(tmp_path, monkeypatch):
    _mk_corpus(tmp_path)
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "seed.parquet"))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    ingest.start_ingest(client=_FailOnD2Client())
    _wait_done(ingest)
    st = store.load_state()
    assert st["watermark"] is None                      # 有失败,水位不动
    assert [f["doc_id"] for f in st["failed_docs"]] == ["d2"]
    assert len(store.load_extractions()) == 1           # d1 成功已落库


def test_worker_crash_surfaces_in_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "seed.parquet"))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    import guanlan_v2.industry.framework as fwmod

    def _boom():
        raise RuntimeError("boom-framework")

    monkeypatch.setattr(fwmod, "load_framework", _boom)
    ingest.start_ingest()
    import time
    for _ in range(100):
        if not ingest.ingest_state()["running"]:
            break
        time.sleep(0.05)
    st = store.load_state()
    assert st["failed_docs"] and "worker 崩溃" in st["failed_docs"][0]["reason"]
    assert st["last_ingest_at"]


def test_deep_backfill_ignores_watermark_and_never_regresses_it(tmp_path, monkeypatch):
    # d_old 在默认35天窗外;常规跑只抽 d_new;深回填(backfill_days=90)补抽 d_old,
    # 且水位不因老批回退(防回退护栏)
    txt = tmp_path / "t.txt"
    txt.write_text("正文", encoding="utf-8")
    now = pd.Timestamp.now()
    d_old, d_new = str((now - pd.Timedelta(days=60)).date()), str((now - pd.Timedelta(days=1)).date())
    rows = [_seed_row("old", d_old, txt), _seed_row("new", d_new, txt)]
    pd.DataFrame(rows).to_parquet(tmp_path / "seed.parquet")
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "seed.parquet"))
    monkeypatch.setenv("GL_INDUSTRY_STORE", str(tmp_path / "store"))
    from guanlan_v2.industry import ingest, store
    ingest.start_ingest(client=_OkClient())
    _wait_done(ingest)
    st = store.load_state()
    assert st["totals"]["docs"] == 1 and st["watermark"] == d_new     # 只抽 d_new
    ingest.start_ingest(client=_OkClient(), backfill_days=90)
    _wait_done(ingest)
    st = store.load_state()
    assert st["totals"]["docs"] == 2                                   # 深回填补抽 d_old
    assert sorted(r["doc_id"] for r in store.load_extractions()) == ["dnew", "dold"]
    assert st["watermark"] == d_new                                    # 水位不回退到 d_old


def test_ingest_single_flight(tmp_path, monkeypatch):
    _mk_corpus(tmp_path, n=1)
    monkeypatch.setenv("GL_CHAIN_SEED", str(tmp_path / "seed.parquet"))
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
