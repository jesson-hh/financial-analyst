"""probe_direct_data.py — 直连数据接口稳定性测试.

目的: 验证 pytdx 主站 + 腾讯实时接口能否替代 Tushare Pro 做日常数据更新.

测 6 项:
  1. pytdx 主站连通率 (104 个公开站, 抽前 N 个)
  2. pytdx 日线拉取速度 + 完整性 (50 只代表股 × 30 天)
  3. pytdx 5min 拉取速度 + 完整性 (20 只 × 240 根 ~ 5 天)
  4. 腾讯实时接口 PE/PB/MV/换手率 字段覆盖率
  5. 数据准确性: pytdx 当日 close vs Qlib bin (Tushare 灌的) close 差异
  6. pytdx 高频压测 (单 host 连续 100 次拉, 看断连率)

输出: out/probe_direct_data.json + 控制台报告.
用法: G:/financial-analyst/.venv/Scripts/python.exe scripts/probe_direct_data.py
"""
from __future__ import annotations

import os
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import json
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# UTF-8 console
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from pytdx.config.hosts import hq_hosts
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams


# ──────────────── 测试样本 (50 只覆盖大/中/小盘 + 边缘) ────────────────
SAMPLES = [
    # mega cap (>5000亿)
    ("SH600519", "贵州茅台"), ("SH601318", "中国平安"), ("SH601398", "工商银行"),
    ("SZ300750", "宁德时代"), ("SH600036", "招商银行"), ("SH601288", "农业银行"),
    ("SH600028", "中国石化"), ("SH601628", "中国人寿"), ("SH601988", "中国银行"),
    ("SH601857", "中国石油"),
    # large (1000-5000亿)
    ("SZ000858", "五粮液"), ("SH600887", "伊利股份"), ("SH601888", "中国中免"),
    ("SZ000333", "美的集团"), ("SH601012", "隆基绿能"), ("SZ002594", "比亚迪"),
    ("SH600276", "恒瑞医药"), ("SH600030", "中信证券"), ("SH600900", "长江电力"),
    ("SZ000725", "京东方A"),
    # mid (300-1000亿)
    ("SH600009", "上海机场"), ("SH600438", "通威股份"), ("SH600837", "海通证券"),
    ("SH601728", "中国电信"), ("SZ000063", "中兴通讯"), ("SZ002415", "海康威视"),
    ("SH601633", "长城汽车"), ("SZ000651", "格力电器"), ("SH600406", "国电南瑞"),
    ("SH601229", "上海银行"),
    # small (100-300亿)
    ("SH605358", "立昂微"), ("SH600666", "奥瑞德"), ("SH600809", "山西汾酒"),
    ("SZ000538", "云南白药"), ("SH688256", "寒武纪-U"), ("SH603259", "药明康德"),
    ("SH600089", "特变电工"), ("SH601801", "皖新传媒"), ("SZ002230", "科大讯飞"),
    ("SZ300059", "东方财富"),
    # other mid (avoid micro/ST in first pass — pytdx may skip those)
    ("SZ002475", "立讯精密"), ("SH600196", "复星医药"), ("SH600188", "兖矿能源"),
    ("SZ000895", "双汇发展"), ("SH600884", "杉杉股份"), ("SH601066", "中信建投"),
    ("SH600585", "海螺水泥"), ("SH601238", "广汽集团"), ("SZ000596", "古井贡酒"),
    ("SH600585", "海螺水泥"),
]
# 去重保持顺序
_seen = set()
SAMPLES = [s for s in SAMPLES if s[0] not in _seen and not _seen.add(s[0])]

HOSTS_TO_TEST = 20    # 抽前 20 个主站
DAILY_DAYS = 30
FIVEMIN_BARS = 240    # 5 天 × 48 根
BURST_N = 100         # 高频压测次数


def _qlib_to_tdx(code: str) -> tuple[int, str]:
    code = code.upper()
    if code.startswith("SH"): return 1, code[2:]
    if code.startswith("SZ"): return 0, code[2:]
    if code.startswith("BJ"): return 2, code[2:]
    raise ValueError(code)


# ────────────────────── Test 1: 主站连通率 ──────────────────────
def test_host_connectivity() -> list[tuple]:
    print("\n" + "=" * 70)
    print(" Test 1: pytdx 主站连通率")
    print("=" * 70)
    results = []
    for i, (name, host, port) in enumerate(hq_hosts[:HOSTS_TO_TEST]):
        api = TdxHq_API(heartbeat=False, auto_retry=False)
        t = time.time()
        try:
            ok = api.connect(host, int(port), time_out=3)
            dt = (time.time() - t) * 1000
            mark = "✓" if ok else "✗"
            results.append({"host": host, "port": port, "ok": bool(ok),
                            "latency_ms": round(dt, 0)})
            print(f"  {mark} {host:20s}:{port:<5}  {dt:5.0f}ms")
        except Exception as e:
            dt = (time.time() - t) * 1000
            results.append({"host": host, "port": port, "ok": False,
                            "latency_ms": round(dt, 0), "err": type(e).__name__})
            print(f"  ✗ {host:20s}:{port:<5}  {dt:5.0f}ms  [{type(e).__name__}]")
        finally:
            try: api.disconnect()
            except Exception: pass

    ok = [r for r in results if r["ok"]]
    ratio = len(ok) / len(results) if results else 0
    avg = statistics.mean([r["latency_ms"] for r in ok]) if ok else 0
    print(f"\n  通率: {len(ok)}/{len(results)} = {ratio:.0%}")
    print(f"  可用主站平均连接耗时: {avg:.0f}ms")
    return results, ok


# ────────────────────── Test 2: 日线拉取 ──────────────────────
def test_daily_fetch(working_hosts) -> dict:
    print("\n" + "=" * 70)
    print(f" Test 2: pytdx 日线拉取 ({len(SAMPLES)} 只 × {DAILY_DAYS} 天)")
    print("=" * 70)
    host = working_hosts[0]
    api = TdxHq_API(heartbeat=False)
    api.connect(host["host"], int(host["port"]), time_out=5)

    results = []
    t0 = time.time()
    for code, name in SAMPLES:
        mkt, c = _qlib_to_tdx(code)
        t = time.time()
        try:
            bars = api.get_security_bars(TDXParams.KLINE_TYPE_DAILY, mkt, c, 0, DAILY_DAYS)
            dt = (time.time() - t) * 1000
            n = len(bars) if bars else 0
            ok = n >= DAILY_DAYS * 0.7   # 容忍 30% 缺 (新股 / 停牌)
            last = bars[-1] if bars else None
            results.append({
                "code": code, "name": name, "ok": ok, "n_bars": n,
                "latency_ms": round(dt, 0),
                "last_date": last["datetime"][:10] if last else None,
                "last_close": last["close"] if last else None,
            })
            mark = "✓" if ok else ("⚠" if n > 0 else "✗")
            extra = f"close={last['close']:.2f} @{last['datetime'][:10]}" if last else "no bars"
            print(f"  {mark} {code} {name:10s} n={n:>2}  {dt:5.0f}ms  {extra}")
        except Exception as e:
            dt = (time.time() - t) * 1000
            results.append({"code": code, "name": name, "ok": False,
                            "n_bars": 0, "latency_ms": round(dt, 0),
                            "err": f"{type(e).__name__}: {str(e)[:80]}"})
            print(f"  ✗ {code} {name:10s} {dt:5.0f}ms  [{type(e).__name__}]")

    total = time.time() - t0
    api.disconnect()

    ok = [r for r in results if r["ok"]]
    latencies = [r["latency_ms"] for r in results]
    summary = {
        "ok": len(ok), "total": len(results),
        "ratio": round(len(ok) / len(results), 3) if results else 0,
        "wall_time_s": round(total, 1),
        "per_stock_ms": round(total / len(SAMPLES) * 1000, 0),
        "p50_ms": round(statistics.median(latencies), 0),
        "p95_ms": round(statistics.quantiles(latencies, n=20)[18], 0) if len(latencies) >= 20 else None,
    }
    print(f"\n  成功 {summary['ok']}/{summary['total']} = {summary['ratio']:.0%}")
    print(f"  总耗时 {summary['wall_time_s']}s | 单只均 {summary['per_stock_ms']:.0f}ms")
    print(f"  P50/P95 延迟: {summary['p50_ms']:.0f}ms / {summary['p95_ms']:.0f}ms")
    return {"summary": summary, "per_stock": results}


# ────────────────────── Test 3: 5min 拉取 ──────────────────────
def test_5min_fetch(working_hosts) -> dict:
    n_samples = 20
    print("\n" + "=" * 70)
    print(f" Test 3: pytdx 5min ({n_samples} 只 × {FIVEMIN_BARS} 根 ~ 5 天)")
    print("=" * 70)
    host = working_hosts[0]
    api = TdxHq_API(heartbeat=False)
    api.connect(host["host"], int(host["port"]), time_out=5)

    results = []
    t0 = time.time()
    for code, name in SAMPLES[:n_samples]:
        mkt, c = _qlib_to_tdx(code)
        t = time.time()
        try:
            bars = api.get_security_bars(TDXParams.KLINE_TYPE_5MIN, mkt, c, 0, FIVEMIN_BARS)
            dt = (time.time() - t) * 1000
            n = len(bars) if bars else 0
            ok = n >= FIVEMIN_BARS * 0.7
            results.append({"code": code, "ok": ok, "n_bars": n,
                            "latency_ms": round(dt, 0),
                            "last_dt": bars[-1]["datetime"] if bars else None})
            mark = "✓" if ok else ("⚠" if n > 0 else "✗")
            print(f"  {mark} {code} {name:10s} n={n:>3} {dt:5.0f}ms")
        except Exception as e:
            dt = (time.time() - t) * 1000
            results.append({"code": code, "ok": False, "n_bars": 0,
                            "latency_ms": round(dt, 0), "err": f"{type(e).__name__}"})
            print(f"  ✗ {code} {name:10s} {dt:5.0f}ms  [{type(e).__name__}]")

    total = time.time() - t0
    api.disconnect()

    ok = [r for r in results if r["ok"]]
    latencies = [r["latency_ms"] for r in results]
    summary = {
        "ok": len(ok), "total": len(results),
        "ratio": round(len(ok) / len(results), 3) if results else 0,
        "wall_time_s": round(total, 1),
        "per_stock_ms": round(total / len(results) * 1000, 0) if results else 0,
        "p50_ms": round(statistics.median(latencies), 0) if latencies else 0,
    }
    print(f"\n  成功 {summary['ok']}/{summary['total']} = {summary['ratio']:.0%}")
    print(f"  总耗时 {summary['wall_time_s']}s | 单只均 {summary['per_stock_ms']:.0f}ms")
    return {"summary": summary, "per_stock": results}


# ────────────────────── Test 4: 腾讯字段覆盖 ──────────────────────
def test_tencent_coverage() -> dict:
    print("\n" + "=" * 70)
    print(f" Test 4: 腾讯实时接口字段覆盖率 ({len(SAMPLES)} 只一次拉)")
    print("=" * 70)
    sys.path.insert(0, "G:/financial-analyst/src")
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector

    collector = TencentQuoteCollector()
    codes = [c for c, _ in SAMPLES]
    t = time.time()
    quotes = collector.fetch(codes, timeout=10.0)
    dt = (time.time() - t) * 1000
    print(f"  一次拉 {len(codes)} 只耗时 {dt:.0f}ms, 解析得 {len(quotes)} 只")

    fields_to_check = ["price", "pe", "pb", "total_mv", "circ_mv", "turnover_rate", "vol_ratio"]
    coverage = defaultdict(int)
    per_stock = []
    for code, _name in SAMPLES:
        q = quotes.get(code)
        row = {"code": code, "found": q is not None}
        if q:
            for f in fields_to_check:
                v = q.get(f)
                if v is not None:
                    coverage[f] += 1
                row[f] = v
        per_stock.append(row)
        if q:
            print(f"  ✓ {code} price={q.get('price'):8.2f} pe={str(q.get('pe')):>7s} "
                  f"pb={str(q.get('pb')):>6s} mv={str(q.get('total_mv')):>8s}亿 "
                  f"换手={str(q.get('turnover_rate')):>5s}%")
        else:
            print(f"  ✗ {code} no quote returned")

    print("\n  字段覆盖率:")
    for f in fields_to_check:
        n = coverage[f]
        mark = "✓" if n == len(SAMPLES) else "⚠"
        print(f"    {mark} {f:15s} {n}/{len(SAMPLES)} = {n/len(SAMPLES):.0%}")

    summary = {
        "total_codes": len(codes),
        "quotes_returned": len(quotes),
        "fetch_ms": round(dt, 0),
        "field_coverage": {f: coverage[f] for f in fields_to_check},
        "tushare_daily_basic_fields_missing": ["ps_ttm", "dv_ttm"],  # Tencent 没这两个
    }
    return {"summary": summary, "per_stock": per_stock}


# ────────────────────── Test 5: 数据准确性 vs Qlib bin ──────────────────────
def test_accuracy_vs_qlib(working_hosts) -> dict:
    print("\n" + "=" * 70)
    print(" Test 5: pytdx 数据准确性 vs Qlib bin (Tushare 灌的)")
    print("=" * 70)
    try:
        import qlib
        qlib.init(provider_uri="G:/stocks/stock_data/cn_data", region="cn")
        from qlib.data import D
    except Exception as e:
        print(f"  ✗ Qlib init 失败: {e}")
        return {"summary": {"qlib_available": False, "error": str(e)}}

    host = working_hosts[0]
    api = TdxHq_API(heartbeat=False)
    api.connect(host["host"], int(host["port"]), time_out=5)

    n_check = 20
    diffs = []
    print(f"  对 {n_check} 只取最近一根日线对比 close 差异\n")
    for code, name in SAMPLES[:n_check]:
        mkt, c = _qlib_to_tdx(code)
        try:
            bars = api.get_security_bars(TDXParams.KLINE_TYPE_DAILY, mkt, c, 0, 5)
            if not bars:
                continue
            last = bars[-1]
            pytdx_close = float(last["close"])
            pytdx_date = last["datetime"][:10]
        except Exception as e:
            print(f"  ✗ {code} pytdx err: {e}")
            continue
        try:
            df = D.features([code], ["$close"], start_time="2026-04-01",
                            end_time="2026-05-30", freq="day")
            if df.empty:
                print(f"  ⚠ {code} Qlib bin empty (该股可能没灌)")
                continue
            qlib_close = float(df.iloc[-1].values[0])
            qlib_date = df.index[-1][1].strftime("%Y-%m-%d")
        except Exception as e:
            print(f"  ✗ {code} Qlib err: {e}")
            continue

        diff_pct = abs(pytdx_close - qlib_close) / qlib_close * 100
        diffs.append({"code": code, "pytdx_date": pytdx_date, "qlib_date": qlib_date,
                      "pytdx_close": pytdx_close, "qlib_close": qlib_close,
                      "diff_pct": round(diff_pct, 4)})
        mark = "✓" if diff_pct < 0.1 else ("⚠" if diff_pct < 1 else "✗")
        date_match = "✓" if pytdx_date == qlib_date else f"⚠({pytdx_date} vs {qlib_date})"
        print(f"  {mark} {code} {name:10s} "
              f"pytdx={pytdx_close:8.2f}@{pytdx_date} "
              f"qlib={qlib_close:8.2f}@{qlib_date} "
              f"diff={diff_pct:.3f}%  date={date_match}")

    api.disconnect()
    if diffs:
        diffs_pct = [d["diff_pct"] for d in diffs]
        summary = {
            "n_compared": len(diffs),
            "avg_diff_pct": round(statistics.mean(diffs_pct), 4),
            "max_diff_pct": round(max(diffs_pct), 4),
            "within_0.1pct": sum(1 for d in diffs_pct if d < 0.1),
            "within_1pct": sum(1 for d in diffs_pct if d < 1.0),
        }
        print(f"\n  样本 {summary['n_compared']}, 平均差异 {summary['avg_diff_pct']}%, "
              f"最大 {summary['max_diff_pct']}%")
        print(f"  差异 <0.1%: {summary['within_0.1pct']}/{summary['n_compared']}")
        print(f"  差异 <1%:   {summary['within_1pct']}/{summary['n_compared']}")
        return {"summary": summary, "per_stock": diffs}
    return {"summary": {"n_compared": 0, "note": "no data points"}}


# ────────────────────── Test 6: 高频压测 ──────────────────────
def test_burst(working_hosts) -> dict:
    print("\n" + "=" * 70)
    print(f" Test 6: 高频压测 (单 host 连续 {BURST_N} 次拉日线)")
    print("=" * 70)
    host = working_hosts[0]
    api = TdxHq_API(heartbeat=False)
    api.connect(host["host"], int(host["port"]), time_out=5)

    mkt, c = _qlib_to_tdx("SH600519")
    results = []
    t0 = time.time()
    for i in range(BURST_N):
        t = time.time()
        try:
            bars = api.get_security_bars(TDXParams.KLINE_TYPE_DAILY, mkt, c, 0, 30)
            dt = (time.time() - t) * 1000
            results.append({"ok": bool(bars), "kind": "OK" if bars else "EMPTY",
                            "latency_ms": round(dt, 1)})
        except Exception as e:
            dt = (time.time() - t) * 1000
            results.append({"ok": False, "kind": type(e).__name__,
                            "latency_ms": round(dt, 1)})

    total = time.time() - t0
    api.disconnect()

    ok = sum(1 for r in results if r["ok"])
    qps = BURST_N / total if total else 0
    latencies = [r["latency_ms"] for r in results]
    failure_types = defaultdict(int)
    for r in results:
        if not r["ok"]:
            failure_types[r["kind"]] += 1

    summary = {
        "n_burst": BURST_N,
        "ok": ok,
        "wall_time_s": round(total, 1),
        "effective_qps": round(qps, 1),
        "p50_ms": round(statistics.median(latencies), 1),
        "p95_ms": round(statistics.quantiles(latencies, n=20)[18], 1) if len(latencies) >= 20 else None,
        "p99_ms": round(sorted(latencies)[-2], 1),
        "failure_types": dict(failure_types),
    }
    print(f"\n  {summary['ok']}/{summary['n_burst']} 成功 ({summary['ok']/summary['n_burst']:.0%})")
    print(f"  实测 QPS = {summary['effective_qps']:.1f}")
    print(f"  P50/P95/P99 延迟: {summary['p50_ms']:.1f}ms / {summary['p95_ms']}ms / {summary['p99_ms']}ms")
    if failure_types:
        print(f"  失败分布: {dict(failure_types)}")
    return summary


# ────────────────────── 主流程 ──────────────────────
def main():
    print("=" * 70)
    print(f" 直连数据接口稳定性测试 — {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f" 样本: {len(SAMPLES)} 只覆盖大/中/小盘")
    print("=" * 70)

    all_results = {"ts": datetime.now().isoformat(), "samples": len(SAMPLES)}

    # T1
    hosts_all, hosts_ok = test_host_connectivity()
    all_results["host_connectivity"] = {
        "tested": len(hosts_all),
        "ok": len(hosts_ok),
        "ratio": round(len(hosts_ok) / len(hosts_all), 3) if hosts_all else 0,
        "ok_hosts": hosts_ok[:5],   # 前 5 个保存
    }
    if not hosts_ok:
        print("\n✗ 没有可用主站, 中止")
        return

    # T2
    daily = test_daily_fetch(hosts_ok)
    all_results["daily_fetch"] = daily

    # T3
    fivemin = test_5min_fetch(hosts_ok)
    all_results["5min_fetch"] = fivemin

    # T4
    tencent = test_tencent_coverage()
    all_results["tencent_coverage"] = tencent

    # T5
    accuracy = test_accuracy_vs_qlib(hosts_ok)
    all_results["accuracy_vs_qlib"] = accuracy

    # T6
    burst = test_burst(hosts_ok)
    all_results["burst"] = burst

    # 持久化
    out_dir = Path("G:/financial-analyst/out")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "probe_direct_data.json"
    out_path.write_text(json.dumps(all_results, ensure_ascii=False, indent=2,
                                    default=str), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f" ✓ 完成. 结果落盘: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
