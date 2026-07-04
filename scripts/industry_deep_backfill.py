# -*- coding: utf-8 -*-
"""AI投研深回填 —— 独立于 9999 的常驻批处理进程。

为什么独立:9999 受看门狗代际管理(5min一代·健康失败强杀),10小时级长批
住在 server 进程里迟早随进程死(2026-07-03 真机实证:server 硬死批随葬)。
本脚本同一套 corpus/llmx/store 模块、同一个 store jsonl(单写者:跑本脚本时
不要再点页面「處理新研報」),server 只读聚合,进程互不牵连。

断点续跑:每块 scan 都用 store.load_extracted_doc_ids() 剔重——进程死了重启,
自动从缺的抽起,绝不重复花钱。

单一 asyncio loop 跑全程(chunk 循环在 async main 里):AsyncOpenAI/httpx 客户端
绑定首个 loop,跨多个 asyncio.run 会炸 "Event loop is closed"(帷幄 P2 老坑)。

用法(engine venv):
    G:/financial-analyst/.venv/Scripts/python.exe scripts/industry_deep_backfill.py \
        --backfill-days 1200 --concurrency 8 --chunk 40
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "engine"))   # fork 引擎优先,压过 pinned 安装


def _load_secrets() -> None:
    p = _REPO / "var" / "secrets.env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() and v.strip() and not os.environ.get(k.strip()):
            os.environ[k.strip()] = v.strip()


async def _main(backfill_days: int, concurrency: int, chunk: int, fw_id: str = "ai_chain") -> int:
    from guanlan_v2.industry import corpus, ingest, store
    from guanlan_v2.industry.framework import all_pool_codes, load_framework
    from financial_analyst.llm.client import LLMClient

    os.environ["GL_INGEST_CONCURRENCY"] = str(concurrency)   # ingest._run_batch 读它
    fw = load_framework(fw=fw_id)
    ccfg = (fw.get("meta") or {}).get("corpus") or {}
    client = LLMClient.for_agent("industry_extract", config_path=_REPO / "config" / "llm.yaml")
    print(f"[deep-backfill] fw={fw_id} provider={client.provider} model={client.model} "
          f"days={backfill_days} conc={concurrency} chunk={chunk}", flush=True)

    grand = {"ok": 0, "fail": 0, "pt": 0, "ct": 0}
    perm_fail: set = set()   # 本次运行内失败的 doc_id:不再重试,防 content_filter 类永败篇死循环
    t0 = time.time()
    while True:
        done_ids = store.load_extracted_doc_ids(fw_id)
        scan = corpus.scan_new_docs(None, all_pool_codes(fw), [], limit=chunk,
                                    exclude_doc_ids=done_ids | perm_fail,
                                    seed=ccfg.get("seed"), themes=ccfg.get("themes"),
                                    backfill_days=backfill_days)
        if not scan["ok"]:
            print(f"[deep-backfill] SCAN-FAIL: {scan['reason']}", flush=True)
            return 2
        docs = scan["docs"]
        if not docs:
            break
        totals = await ingest._run_batch(docs, fw, client)   # noqa: SLF001 — 同仓复用编排
        grand["ok"] += totals["n_ok"]; grand["fail"] += totals["n_fail"]
        grand["pt"] += totals["prompt_tokens"]; grand["ct"] += totals["completion_tokens"]
        n_conn = 0
        for f in totals["failed"]:
            reason = str(f.get("reason") or "")
            # 瞬时/可恢复:断网、超时、429(限速或余额耗尽——充值后可续,被拒请求不计费)
            transient = ("APIConnectionError" in reason) or ("TimeoutError" in reason) \
                or ("RateLimitError" in reason) or ("429" in reason)
            if transient:
                n_conn += 1        # 不进永败集,下一轮自动重试
            elif f.get("doc_id"):
                perm_fail.add(str(f["doc_id"]))   # content_filter 等真永败才跳过
            print(f"[deep-backfill]   FAIL {f.get('doc_id')}: {reason[:100]}", flush=True)
        # 熔断:整块全是可恢复失败=断网或配额耗尽(2026-07-03 两次实证:Clash fake-ip 窗口
        # 294篇误标永败;余额烧干 429 quota)——歇 180s 待命重试,充值/网络恢复后自动续跑
        if totals["n_ok"] == 0 and docs and n_conn == len(docs):
            print("[deep-backfill] SUSPENDED: 整块可恢复失败(断网/配额),歇180s重试(不标永败)", flush=True)
            await asyncio.sleep(180)
        el = time.time() - t0
        rate = grand["ok"] / el * 60 if el > 0 else 0
        print(f"[deep-backfill] +{totals['n_ok']}/-{totals['n_fail']} | 累计 ok={grand['ok']} "
              f"fail={grand['fail']} | {rate:.1f}篇/分 | tokens {grand['pt']}/{grand['ct']}", flush=True)

    # 收官:把 token/篇数记进 server 可见的 state totals(单写者约定下低撞险)
    st = store.load_state(fw_id)
    st["totals"]["docs"] += grand["ok"]
    st["totals"]["prompt_tokens"] += grand["pt"]
    st["totals"]["completion_tokens"] += grand["ct"]
    import pandas as pd
    st["last_ingest_at"] = pd.Timestamp.now().isoformat(timespec="seconds")
    store.save_state(st, fw_id)
    print(f"[deep-backfill] DONE ok={grand['ok']} fail={grand['fail']} "
          f"tokens={grand['pt']}/{grand['ct']} elapsed={(time.time()-t0)/3600:.1f}h", flush=True)
    return 0 if grand["fail"] == 0 else 1


if __name__ == "__main__":
    _load_secrets()
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=1200)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--fw", type=str, default="ai_chain", help="框架 id(ai_chain/robot_chain)")
    a = ap.parse_args()
    raise SystemExit(asyncio.run(_main(a.backfill_days, a.concurrency, a.chunk, a.fw)))
