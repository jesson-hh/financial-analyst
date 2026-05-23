"""probe_pytdx_vs_tushare.py — pytdx vs Tushare 同一只股票日线数据准确性对照.

对 20 只代表股近 30 天日线, 逐日对比 close/volume 差异. 判断 pytdx 能否完全替代
Tushare 做日常数据拉取.
"""
from __future__ import annotations

import os
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"

import statistics
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

# Load .env
from dotenv import load_dotenv
load_dotenv("G:/financial-analyst/.env", override=True)  # 覆盖系统已有的

import requests
import pandas as pd
from pytdx.config.hosts import hq_hosts
from pytdx.hq import TdxHq_API
from pytdx.params import TDXParams

TUSHARE_URL = "http://api.tushare.pro"   # HTTP, 不走 HTTPS, 避 Clash 拦截


def ts_query(api_name, token, **params):
    """Tushare HTTP POST 调用 (绕开 ts.pro_api 的 HTTPS 路径)."""
    req = {"api_name": api_name, "token": token, "params": params}
    sess = requests.Session()
    sess.trust_env = False  # 关键: 不走系统代理
    r = sess.post(TUSHARE_URL, json=req, timeout=30)
    d = r.json()
    if d.get("code") != 0:
        raise Exception(d.get("msg", "unknown"))
    return pd.DataFrame(d["data"]["items"], columns=d["data"]["fields"])

SAMPLES = [
    ("SH600519", "贵州茅台"), ("SH601318", "中国平安"), ("SZ300750", "宁德时代"),
    ("SH600036", "招商银行"), ("SH601398", "工商银行"), ("SZ000858", "五粮液"),
    ("SH600887", "伊利股份"), ("SZ002594", "比亚迪"), ("SH601012", "隆基绿能"),
    ("SH600276", "恒瑞医药"), ("SH600030", "中信证券"), ("SH600900", "长江电力"),
    ("SH600009", "上海机场"), ("SZ000333", "美的集团"), ("SH601888", "中国中免"),
    ("SH605358", "立昂微"), ("SH600666", "奥瑞德"), ("SH688256", "寒武纪-U"),
    ("SH603259", "药明康德"), ("SZ300059", "东方财富"),
]


def _qlib_to_tdx(code):
    if code.startswith("SH"): return 1, code[2:]
    if code.startswith("SZ"): return 0, code[2:]
    raise ValueError(code)


def _qlib_to_ts(code):
    if code.startswith("SH"): return code[2:] + ".SH"
    if code.startswith("SZ"): return code[2:] + ".SZ"
    raise ValueError(code)


def main():
    print(f"=== pytdx vs Tushare 数据准确性对照 (近 30 天) ===\n")

    # 连 pytdx
    api = TdxHq_API(heartbeat=False)
    for name, host, port in hq_hosts:
        try:
            if api.connect(host, int(port), time_out=3):
                print(f"pytdx host: {host}:{port}")
                break
        except Exception:
            continue
    else:
        print("✗ pytdx 没连上, 中止")
        return

    # 连 Tushare (HTTP POST, 不走 ts.pro_api 的 HTTPS)
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        print("✗ 没有 TUSHARE_TOKEN, 中止")
        return
    print(f"Tushare endpoint: {TUSHARE_URL} (HTTP)")

    all_diffs = []
    code_summary = []
    for code, name in SAMPLES:
        mkt, c = _qlib_to_tdx(code)
        ts_code = _qlib_to_ts(code)

        # pytdx
        t = time.time()
        try:
            bars = api.get_security_bars(TDXParams.KLINE_TYPE_DAILY, mkt, c, 0, 30)
            pytdx_ms = (time.time() - t) * 1000
            if not bars:
                print(f"  ✗ {code} {name}: pytdx empty (可能退市/合并)")
                continue
        except Exception as e:
            print(f"  ✗ {code} {name}: pytdx err {e}")
            continue

        # Tushare (HTTP POST)
        t = time.time()
        try:
            df_ts = ts_query("daily", token, ts_code=ts_code, limit=30)
            ts_ms = (time.time() - t) * 1000
            if df_ts.empty:
                print(f"  ⚠ {code} {name}: Tushare empty")
                continue
        except Exception as e:
            print(f"  ✗ {code} {name}: Tushare err {e}")
            continue

        # 对齐日期: pytdx datetime 'YYYY-MM-DD HH:MM' → 'YYYYMMDD'
        pytdx_dict = {b["datetime"][:10].replace("-", ""): b for b in bars}
        ts_dict = dict(zip(df_ts["trade_date"], df_ts.to_dict("records")))

        common = sorted(set(pytdx_dict) & set(ts_dict))
        if not common:
            print(f"  ✗ {code} {name}: 没有共同日期")
            continue

        # 逐日 diff
        diffs_close = []
        diffs_vol = []
        for d in common:
            p_close = float(pytdx_dict[d]["close"])
            t_close = float(ts_dict[d]["close"])
            p_vol = float(pytdx_dict[d]["vol"])     # 手
            t_vol = float(ts_dict[d]["vol"])         # Tushare 也是手
            if t_close > 0:
                diffs_close.append(abs(p_close - t_close) / t_close * 100)
            if t_vol > 0:
                diffs_vol.append(abs(p_vol - t_vol) / t_vol * 100)

        if not diffs_close:
            continue

        avg_close_diff = statistics.mean(diffs_close)
        max_close_diff = max(diffs_close)
        avg_vol_diff = statistics.mean(diffs_vol) if diffs_vol else 0

        mark = "✓" if avg_close_diff < 0.01 else ("⚠" if avg_close_diff < 0.5 else "✗")
        print(f"  {mark} {code} {name:10s} "
              f"n_common={len(common)} "
              f"close_diff_avg={avg_close_diff:.4f}% max={max_close_diff:.4f}% | "
              f"vol_diff_avg={avg_vol_diff:.4f}% | "
              f"pytdx={pytdx_ms:.0f}ms ts={ts_ms:.0f}ms")

        all_diffs.extend(diffs_close)
        code_summary.append({
            "code": code, "name": name, "n_common": len(common),
            "close_diff_avg": round(avg_close_diff, 4),
            "close_diff_max": round(max_close_diff, 4),
            "vol_diff_avg": round(avg_vol_diff, 4),
            "pytdx_ms": round(pytdx_ms, 0),
            "tushare_ms": round(ts_ms, 0),
        })

    api.disconnect()

    if all_diffs:
        print(f"\n=== 汇总 ({len(code_summary)} 只 × ~30 天 = {len(all_diffs)} 日 close 对照) ===")
        print(f"  平均 close 差异: {statistics.mean(all_diffs):.5f}%")
        print(f"  最大 close 差异: {max(all_diffs):.5f}%")
        print(f"  完全一致 (<0.001%): {sum(1 for d in all_diffs if d < 0.001)}/{len(all_diffs)} = "
              f"{sum(1 for d in all_diffs if d < 0.001)/len(all_diffs):.0%}")
        print(f"  差异 <0.01%:       {sum(1 for d in all_diffs if d < 0.01)}/{len(all_diffs)} = "
              f"{sum(1 for d in all_diffs if d < 0.01)/len(all_diffs):.0%}")
        print(f"  差异 <0.1%:        {sum(1 for d in all_diffs if d < 0.1)}/{len(all_diffs)} = "
              f"{sum(1 for d in all_diffs if d < 0.1)/len(all_diffs):.0%}")
        if code_summary:
            avg_pytdx = statistics.mean(c["pytdx_ms"] for c in code_summary)
            avg_ts = statistics.mean(c["tushare_ms"] for c in code_summary)
            print(f"\n  平均单次 pytdx: {avg_pytdx:.0f}ms  vs  Tushare: {avg_ts:.0f}ms "
                  f"(pytdx 快 {avg_ts/avg_pytdx:.1f}x)")


if __name__ == "__main__":
    main()
