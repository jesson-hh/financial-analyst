# -*- coding: utf-8 -*-
"""手动增量批处理编排:扫描→抽取→落库→水位。

单飞 = 进程内互斥(threading.Lock + running 标志;9999 单进程,毋需跨进程锁);
后台 daemon 线程内 asyncio.run + Semaphore(3);全部成功才推水位。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

_run_lock = threading.Lock()
_running = False
_progress = {"done": 0, "total": 0}


def _extract_keywords(fw: dict) -> list:
    kws: list = []
    for s in fw["segments"]:
        kws.extend(s.get("keywords", []))
    return kws


async def _run_batch(docs: list, fw: dict, client) -> dict:
    from . import corpus, llmx, store
    sem = asyncio.Semaphore(3)
    totals = {"n_ok": 0, "n_fail": 0, "prompt_tokens": 0, "completion_tokens": 0, "failed": []}

    async def _one(doc: dict):
        async with sem:
            try:
                text = await asyncio.to_thread(corpus.read_doc_text, doc["text_path"])
            except Exception as exc:  # noqa: BLE001
                totals["n_fail"] += 1
                totals["failed"].append({"doc_id": doc["doc_id"], "reason": f"读文失败: {exc}"})
                _progress["done"] += 1
                return
            r = await llmx.extract_one(doc, text, fw, client=client)
            if r.get("ok"):
                await asyncio.to_thread(store.append_extraction, r["extraction"])
                totals["n_ok"] += 1
                totals["prompt_tokens"] += r.get("prompt_tokens", 0)
                totals["completion_tokens"] += r.get("completion_tokens", 0)
            else:
                totals["n_fail"] += 1
                totals["failed"].append({"doc_id": doc["doc_id"], "reason": r.get("reason")})
            _progress["done"] += 1

    await asyncio.gather(*(_one(d) for d in docs))
    return totals


def _worker(limit: Optional[int], client) -> None:
    global _running, _progress
    from . import corpus, store
    from .framework import all_pool_codes, load_framework
    try:
        import pandas as pd
        fw = load_framework()
        st = store.load_state()
        ccfg = (fw.get("meta") or {}).get("corpus") or {}
        scan = corpus.scan_new_docs(st.get("watermark"), all_pool_codes(fw), _extract_keywords(fw),
                                    limit=limit, exclude_doc_ids=store.load_extracted_doc_ids(),
                                    seed=ccfg.get("seed"), themes=ccfg.get("themes"))
        if not scan["ok"]:
            st["failed_docs"] = [{"doc_id": None, "reason": scan["reason"]}]
            st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
            store.save_state(st)
            return
        docs = scan["docs"]
        _progress = {"done": 0, "total": len(docs)}
        if not docs:
            st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
            store.save_state(st)
            return
        totals = asyncio.run(_run_batch(docs, fw, client))
        st = store.load_state()
        st["failed_docs"] = totals["failed"]
        st["totals"]["docs"] += totals["n_ok"]
        st["totals"]["prompt_tokens"] += totals["prompt_tokens"]
        st["totals"]["completion_tokens"] += totals["completion_tokens"]
        if totals["n_fail"] == 0 and docs:
            st["watermark"] = max(d["publish_ts"] for d in docs)
        st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
        store.save_state(st)
    except Exception as exc:  # noqa: BLE001 — worker 意外崩溃也必须显形(诚实红线)
        try:
            from . import store as _store
            import pandas as _pd
            st = _store.load_state()
            st["failed_docs"] = [{"doc_id": None, "reason": f"worker 崩溃: {exc}"}]
            st["last_ingest_at"] = _pd.Timestamp.now().isoformat(timespec="seconds")
            _store.save_state(st)
        except Exception:  # noqa: BLE001 — 连状态都写不进时只能放弃,绝不抛出杀线程语义
            pass
    finally:
        _running = False


def start_ingest(limit: Optional[int] = None, client=None) -> dict:
    global _running
    with _run_lock:
        if _running:
            return {"ok": True, "accepted": False, "running": True, "reason": "已有批处理在跑(单飞)"}
        _running = True
    t = threading.Thread(target=_worker, args=(limit, client), daemon=True, name="industry-ingest")
    t.start()
    return {"ok": True, "accepted": True, "running": True, "reason": None}


def ingest_state() -> dict:
    from . import store
    st = store.load_state()
    st["running"] = _running
    st["progress"] = dict(_progress)
    return st
