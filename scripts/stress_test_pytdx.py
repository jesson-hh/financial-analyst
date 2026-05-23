"""scripts/stress_test_pytdx.py — pytdx 主站全市场 5500 只压力测试.

验证 P0 实现在生产规模下的稳定性. 不动真实数据 (sandbox FA_DATA_DIR).

测量:
  - 总壁钟时间
  - 失败率 + 失败类型分布
  - 是否触发主站断连
  - 是否触发限速
  - p50 / p95 / p99 单只延迟
  - 退市股识别准确率

输出: out/stress_test_pytdx.json
"""
from __future__ import annotations

import os
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

sys.path.insert(0, "G:/financial-analyst/src")

from financial_analyst.data.bin_writer import load_instruments, load_calendar
from financial_analyst.data.updaters.pytdx_pool import PytdxClient
from financial_analyst.data.updaters.pytdx_kline import update_daily


SANDBOX = "G:/financial-analyst/test_data/cn_data_stress"
SOURCE_INST = "G:/stocks/stock_data/cn_data"


def main():
    print("=" * 70)
    print(f" pytdx 全市场 stress test — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 70)

    # 1. 从源拿 universe
    src_inst = load_instruments(SOURCE_INST, market="all")
    codes = sorted(src_inst.keys())
    print(f"\n  universe: {len(codes)} 只 (from {SOURCE_INST}/instruments/all.txt)")
    if not codes:
        print("✗ 源 instruments 是空, 中止")
        return

    # 2. 清 sandbox
    sandbox = Path(SANDBOX)
    if sandbox.exists():
        import shutil
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    print(f"  sandbox: {sandbox} (clean)")

    # 3. 连 pytdx
    client = PytdxClient()
    client._connect()    # 主动连一下, 拿 host 信息
    print(f"  pytdx host: {client.host}")

    # 4. 跑 update_daily
    print(f"\n  开始拉日线 (n_bars=30, 增量场景)...")
    t0 = time.time()
    per_stock = []
    failures: Counter = Counter()
    last_host = client.host

    for i, code in enumerate(codes, 1):
        t_start = time.time()
        host_at_start = client.host
        try:
            n = update_daily(str(sandbox), client, code, n_bars=30)
            dt = (time.time() - t_start) * 1000
            kind = "OK" if n > 0 else "EMPTY"
            per_stock.append({"code": code, "kind": kind, "n_bars": n,
                              "latency_ms": round(dt, 0)})
            if n == 0:
                failures["EMPTY (退市/未上市)"] += 1
        except Exception as e:
            dt = (time.time() - t_start) * 1000
            kind = type(e).__name__
            failures[kind] += 1
            per_stock.append({"code": code, "kind": kind, "n_bars": 0,
                              "latency_ms": round(dt, 0),
                              "err": str(e)[:120]})
        # 监控主站切换
        if client.host != host_at_start and client.host != "(disconnected)":
            print(f"  [{i:>4}/{len(codes)}] 主站切换: {host_at_start} → {client.host}")

        if i % 500 == 0:
            ok = sum(1 for p in per_stock if p["kind"] == "OK")
            print(f"  [{i:>4}/{len(codes)}]  ok={ok}  failures={dict(failures)}  "
                  f"elapsed={time.time() - t0:.0f}s  host={client.host}")

    total_t = time.time() - t0
    client.close()

    # 5. 统计
    ok = sum(1 for p in per_stock if p["kind"] == "OK")
    empty = sum(1 for p in per_stock if p["kind"] == "EMPTY")
    fail = len(per_stock) - ok - empty
    latencies = [p["latency_ms"] for p in per_stock if p["kind"] == "OK"]

    summary = {
        "ts": datetime.now().isoformat(),
        "universe_size": len(codes),
        "ok": ok,
        "empty": empty,
        "failed": fail,
        "ok_ratio": round(ok / len(codes), 4),
        "wall_time_s": round(total_t, 1),
        "stocks_per_sec": round(len(codes) / total_t, 1),
        "p50_ms": round(statistics.median(latencies), 0) if latencies else None,
        "p95_ms": round(statistics.quantiles(latencies, n=20)[18], 0) if len(latencies) >= 20 else None,
        "p99_ms": round(statistics.quantiles(latencies, n=100)[98], 0) if len(latencies) >= 100 else None,
        "failure_types": dict(failures),
        "final_host": client.host if hasattr(client, "host") else "(closed)",
    }

    print("\n" + "=" * 70)
    print(" 总结")
    print("=" * 70)
    print(f"  Universe:      {summary['universe_size']}")
    print(f"  ✓ OK:          {summary['ok']} ({summary['ok_ratio']:.1%})")
    print(f"  ⏭ EMPTY:       {summary['empty']} (退市/未上市等正常情况)")
    print(f"  ✗ FAILED:      {summary['failed']}")
    print(f"  失败类型:      {summary['failure_types']}")
    print(f"  壁钟时间:      {summary['wall_time_s']}s ({summary['stocks_per_sec']:.1f} 只/秒)")
    print(f"  延迟 P50/P95/P99: {summary['p50_ms']}/{summary['p95_ms']}/{summary['p99_ms']} ms")

    # 6. 落盘
    out = Path("G:/financial-analyst/out/stress_test_pytdx.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "per_stock": per_stock[:200]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  结果落盘: {out}")
    print(f"  (per_stock 只存前 200 个样本避免文件过大)")


if __name__ == "__main__":
    main()
