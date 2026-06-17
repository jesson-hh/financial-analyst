# -*- coding: utf-8 -*-
"""一次性 demo:造几个单票因子例子,打 /factor/tsic 真跑 P1-P3 回测,打印精简摘要。
不动引擎/后端,只是 HTTP 客户端。"""
import json, urllib.request, sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

URL = "http://127.0.0.1:9999/factor/tsic"

# 统一小池 + 近 2 年窗(后端默认),让池级净值曲线有多票等权
COMMON = {"universe": "sample30", "fwd_days": 20, "direction": 0,
          "benchmark": "000300.SH"}

EXAMPLES = [
    {"name": "20日价格动量(追涨)",
     "body": {**COMMON, "expr_or_name": "close/delay(close,20)-1", "fwd_days": 20}},
    {"name": "5日短期反转(抄底·取反)",
     "body": {**COMMON, "expr_or_name": "close/delay(close,5)-1", "fwd_days": 5, "direction": -1}},
    {"name": "20日低波动(买稳·取反)",
     "body": {**COMMON, "expr_or_name": "stddev(close/delay(close,1)-1,20)", "fwd_days": 20, "direction": -1}},
    {"name": "量价相关(20日)",
     "body": {**COMMON, "expr_or_name": "correlation(close,volume,20)", "fwd_days": 20}},
]


def post(body):
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read().decode("utf-8"))


def g(d, *ks):
    for k in ks:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def fmt(v, p=3):
    if v is None:
        return "—"
    try:
        return f"{float(v):+.{p}f}"
    except Exception:
        return str(v)


for ex in EXAMPLES:
    print("=" * 72)
    print(f"# {ex['name']}  expr={ex['body']['expr_or_name']!r} "
          f"fwd={ex['body']['fwd_days']} dir={ex['body'].get('direction', 0)}")
    try:
        res = post(ex["body"])
    except Exception as e:
        print(f"  POST 失败: {type(e).__name__}: {e}")
        continue
    if not res.get("ok", True) and res.get("reason"):
        print(f"  诚实失败: {res.get('reason')}")
        continue
    sm = res.get("summary") or {}
    rows = res.get("rows") or res.get("codes_tsic") or []
    n_rows = len(rows)
    valid = [r for r in rows if isinstance(r, dict) and r.get("tsic") is not None]
    print(f"  覆盖: {n_rows} 票 (有效时序IC {len(valid)})")
    # P1
    print(f"  [P1 相关·稳定] 均值Pearson-IC={fmt(g(sm,'mean_pearson'))} "
          f"均值ICIR={fmt(g(sm,'mean_icir'))} IC胜率={fmt(g(sm,'ic_win_pool'),2)} "
          f"命中率={fmt(g(sm,'mean_hit'),2)} 分位单调={fmt(g(sm,'mean_mono'),2)}")
    print(f"  [P1 显著]     NW-t显著占比={fmt(g(sm,'nw_sig_ratio'),2)} "
          f"PT显著占比={fmt(g(sm,'pt_sig_ratio'),2)} "
          f"IC半衰期={g(sm,'half_life')} 峰值h={g(sm,'peak_h')}")
    # P2
    print(f"  [P2 样本外]   均值R²ₒₛ={fmt(g(sm,'mean_r2os'),4)} "
          f"R²ₒₛ>0占比={fmt(g(sm,'r2os_pos_ratio'),2)} "
          f"CW显著占比={fmt(g(sm,'cw_sig_ratio'),2)}")
    # P3
    tp = sm.get("timing_pool") or {}
    nav = sm.get("timing_nav") or []
    print(f"  [P3 择时回测] 池Sharpe={fmt(g(tp,'pool_sharpe'),2)} "
          f"持有Sharpe={fmt(g(tp,'bh_sharpe'),2)} "
          f"差={fmt(g(tp,'delta_sharpe'),2)} "
          f"超额年化={fmt(g(tp,'ann_excess'),3)} "
          f"胜持有占比={fmt(g(tp,'frac_beat_bh'),2)} "
          f"回撤={fmt(g(tp,'pool_maxdd'),3)}")
    if nav:
        last = nav[-1]
        print(f"               净值点={len(nav)} 末值[策略={fmt(last[1],3)} 持有={fmt(last[2],3)}]")
print("=" * 72)
print("done")
