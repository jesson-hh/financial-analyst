# -*- coding: utf-8 -*-
"""guanlan 原生市场状态生成器 —— 自包含, 不依赖 qlib / fa-watch-wt。

替代 ``fa-watch-wt/research/scripts/export_market_status.py``。直读引擎 day 二进制
(``QlibBinaryLoader``, py3.13 可跑), 现算三源写 ``market_status.json``:

  - **limit_ups**(涨停家数): 最新交易日全市场 ``ret`` 阈值计数
    (主板 ≥9.5%&<19.5% / 双创 ≥19.5% / 跌停 ≤-9.5% + 涨跌家数)。
  - **regime**(轻量): 上证趋势(close vs MA20) + 市场宽度(% 个股 close>MA20)
    → 牛/熊/震荡。**lite 口径**, 非 fa-watch-wt 的 DFM 模型;``source`` 标
    ``'guanlan-lite'`` 诚实区分, 待逐位一致再补 DFM。
  - **mainline**(读): ``monthly_mainlines_panel.parquet`` top-N by ex_60d
    (月级信号, 带 as-of)。

输出落 **仓内**(env ``MARKET_STATUS_PATH`` 或 ``repo/data/market_status.json``)。引擎
``financial_analyst.watch.market_status.default_market_status_path()`` 经 env 覆盖读它
→ 后端走 guanlan, **不写 G:/stocks**(数据只读引用)。

跑::

    python -m guanlan_v2.strategy.market_status [--date YYYY-MM-DD] [--out PATH]

只读全市场 close(+amount)单遍, 不碰 bin/日历(遵守数据写入红线)。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from pathlib import Path
from typing import List, Optional

import numpy as np
import pandas as pd

# 阈值口径对齐 compute.breadth / r27 (主板涨停 9.5%~19.5% / 双创 ≥19.5% / 跌停 ≤-9.5%)
LIMIT_UP_10_LO, LIMIT_UP_10_HI = 0.095, 0.195
LIMIT_UP_20 = 0.195
LIMIT_DOWN_10 = -0.095
MA_WIN = 20
BENCH_INDEX = "SH000001"   # 上证综指; bin 无指数则 regime 退化为仅 breadth


def _provider_uri() -> str:
    """引擎 day 二进制根(如 ``G:/stocks/stock_data/cn_data``), 经 get_data_paths 解析。"""
    from financial_analyst.data.paths import get_data_paths

    uri = get_data_paths().qlib_uri
    return str(uri["day"] if isinstance(uri, dict) else uri)


def _mainline_panel_path(provider_uri: str) -> Path:
    """``monthly_mainlines_panel.parquet`` 路径。

    优先 regen 自产的 **artifacts 版**(``strategy.ranking.MAINLINE_PARQUET``,qlib 管线退役后
    唯一在刷新的);缺失才回落 stocks 侧 ``<stocks>/strategy/mainline/``(该处已成陈尸,双源分叉修:
    market_status 的『主线』曾读 06-08 旧面板而选股 L2 读 artifacts 07-02 版,同月两个答案)。
    provider = ``.../stocks/stock_data/cn_data`` → ``.parent.parent`` = ``.../stocks``。
    """
    try:
        from guanlan_v2.strategy.ranking import MAINLINE_PARQUET
        if MAINLINE_PARQUET.exists():
            return MAINLINE_PARQUET
    except Exception:  # noqa: BLE001 — 取不到 artifacts 常量则回落 stocks 侧
        pass
    return Path(provider_uri).parent.parent / "strategy" / "mainline" / "monthly_mainlines_panel.parquet"


def default_out_path() -> Path:
    """仓内输出路径(env ``MARKET_STATUS_PATH`` 优先, 否则 ``repo/data/market_status.json``)。"""
    env = os.environ.get("MARKET_STATUS_PATH")
    if env:
        return Path(env)
    # 本文件: guanlan-v2/guanlan_v2/strategy/market_status.py → parents[2] = repo 根
    return Path(__file__).resolve().parents[2] / "data" / "market_status.json"


def _latest_trade_date(loader, bench: str = "SH600519") -> str:
    """最新【有数据】交易日 = bench 最后非 NaN close 的日期(同 export 原口径)。"""
    c = loader._read_bin(bench, "close")
    if c is None or not len(c):
        raise RuntimeError(f"无法读 {bench} close(检查 provider_uri)")
    c = c.dropna()
    if not len(c):
        raise RuntimeError(f"{bench} close 全 NaN")
    return str(pd.Timestamp(c.index[-1]).date())


def _snapshot(loader, codes: List[str], asof: str) -> dict:
    """单遍读全市场 close(+amount): asof 当日涨停家数 + 市场宽度(% close>MA20)。

    只计 asof 当日**有 bar** 的股(停牌/未上市跳过), 涨跌家数再过滤 amount>0
    (停牌当日)。市场宽度 = 当日 close>20 日均线 的占比。
    """
    asof_ts = pd.Timestamp(asof)
    start_ts = asof_ts - pd.Timedelta(days=60)   # 够 MA20 + 上一交易日
    lu10 = lu20 = ld10 = up = dn = flat = n = 0
    above = ma_n = 0
    for code in codes:
        c = loader._read_bin(code, "close")
        if c is None or not len(c):
            continue
        a = loader._read_bin(code, "amount")
        df = pd.DataFrame({"close": c})
        df["amount"] = a if a is not None else np.nan
        df = df.loc[(df.index >= start_ts) & (df.index <= asof_ts)]
        if df.empty or pd.Timestamp(df.index[-1]) != asof_ts:
            continue                              # asof 当日无 bar → 跳
        close = df["close"].ffill()
        ret = close.pct_change(fill_method=None)
        r = ret.iloc[-1]
        amt = df["amount"].iloc[-1]
        if pd.notna(r) and (pd.isna(amt) or amt > 0):
            n += 1
            if r > 0:
                up += 1
            elif r < 0:
                dn += 1
            else:
                flat += 1
            if LIMIT_UP_10_LO <= r < LIMIT_UP_10_HI:
                lu10 += 1
            elif r >= LIMIT_UP_20:
                lu20 += 1
            elif r <= LIMIT_DOWN_10:
                ld10 += 1
        if len(close) >= MA_WIN and pd.notna(close.iloc[-1]):
            ma = close.iloc[-MA_WIN:].mean()
            if pd.notna(ma):
                ma_n += 1
                if close.iloc[-1] > ma:
                    above += 1
    breadth_pct = round(100.0 * above / ma_n, 1) if ma_n else None
    return {
        "limit_up_total": lu10 + lu20, "limit_up_10": lu10, "limit_up_20": lu20,
        "limit_down": ld10, "up_count": up, "down_count": dn, "n": n,
        "_breadth_pct": breadth_pct,
    }


def _regime(loader, asof: str, breadth_pct: Optional[float]) -> dict:
    """轻量 regime: 上证 close vs MA20(趋势) + 市场宽度 → 牛/熊/震荡(保守, 默认震荡)。"""
    asof_ts = pd.Timestamp(asof)
    idx_up = None
    try:
        c = loader._read_bin(BENCH_INDEX, "close")
        if c is not None and len(c):
            c = c.loc[c.index <= asof_ts].dropna()
            if len(c) >= MA_WIN:
                idx_up = bool(c.iloc[-1] > c.iloc[-MA_WIN:].mean())
    except Exception:
        idx_up = None
    bp = breadth_pct if breadth_pct is not None else 50.0
    # 保守口径(crude 信号不轻易喊牛/熊, 对齐生产 DFM 在 ~18% 宽度仍叫 oscillating):
    # 仅宽度极端(且若有指数则趋势同向)才离开「震荡」, 否则默认震荡 + 让 breadth_pct 数字
    # 自身说明强弱(synthesis 会把「市场宽度仅 16.8%」如实呈现)。
    if bp >= 65 and idx_up is not False:
        regime = "bull"
    elif bp <= 12 and idx_up is not True:
        regime = "bear"
    else:
        regime = "oscillating"
    return {
        "regime": regime,
        # 轻量口径; idx 不可用时标 breadth-only 诚实区分; 补 DFM 逐位一致后换 'dfm'
        "source": "guanlan-lite" if idx_up is not None else "guanlan-lite (breadth-only, 无指数趋势)",
        "breadth_pct": breadth_pct,
        "index_above_ma20": idx_up,
        "params": {},
    }


def _mainline(panel_path: Path, top_n: int = 5) -> dict:
    """读 ``monthly_mainlines_panel.parquet``: 最新月 top-N by ex_60d(优先 mainline/initiation)。"""
    if not panel_path.exists():
        return {"as_of": None, "n_mainline": 0, "top": [], "note": "panel missing"}
    cols = ["datetime", "industry", "status", "ex_60d", "top10_ratio_60d", "lu_count_60d_sum"]
    m = pd.read_parquet(panel_path, columns=cols)
    latest = m["datetime"].max()
    cur = m[m["datetime"] == latest].copy()
    n_main = int((cur["status"] == "mainline").sum())
    act = cur[cur["status"].isin(["mainline", "initiation"])]
    if act.empty:
        act = cur
    top = act.nlargest(top_n, "ex_60d")
    rows = [
        {
            "industry": str(r.industry), "status": str(r.status),
            "ex_60d": round(float(r.ex_60d), 2) if pd.notna(r.ex_60d) else None,
            "top10_ratio_60d": round(float(r.top10_ratio_60d), 3) if pd.notna(r.top10_ratio_60d) else None,
            "lu_count_60d": int(r.lu_count_60d_sum) if pd.notna(r.lu_count_60d_sum) else None,
        }
        for r in top.itertuples()
    ]
    return {"as_of": str(pd.Timestamp(latest).date()), "n_mainline": n_main, "top": rows}


def generate(date: Optional[str] = None, out_path: Optional[str] = None,
             now_iso: Optional[str] = None) -> dict:
    """生成 market_status dict 并写盘; 返回 dict(含 ``out`` 路径)。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from guanlan_v2.strategy.compute.breadth import list_all_instruments

    provider = _provider_uri()
    loader = QlibBinaryLoader(provider)
    codes = list_all_instruments(provider)
    # 最近 ~6 个有数据交易日 (用 SH600519, 几乎不停牌)
    _bc = loader._read_bin("SH600519", "close")
    _recent = [str(pd.Timestamp(d).date())
               for d in (_bc.dropna().index[-6:] if _bc is not None and len(_bc) else [])]

    if date:
        asof = date
        snap = _snapshot(loader, codes, asof)
    else:
        # 完整收盘日守卫: 从最新交易日往回, 取第一个真·完整收盘日, 跳过两类不可用日:
        #   ① 今日且未收盘 —— A 股 15:00 收盘, 盘中 bar 是实时价非收盘价, 涨停/宽度只是
        #      半天累积 (本地时间 <15 时一律跳今日, 用上一完整日);
        #   ② 全市场覆盖不完整 (n<4500, 正常收盘日 ~5000+ 股) —— ingest 写到一半。
        # 都不完整 → 取覆盖最多的那天 (尽力而为)。常态(收盘后最新日完整)首试即中。
        today = _dt.date.today()
        now_hour = _dt.datetime.now().hour
        cands = list(reversed(_recent)) or [_latest_trade_date(loader)]
        best = None   # (n, date, snap)
        snap = None
        asof = None
        for cand in cands:
            try:
                cd = _dt.date.fromisoformat(cand)
            except Exception:  # noqa: BLE001
                cd = None
            if cd == today and now_hour < 15:   # 今日盘中 → 跳, 用上一完整日
                continue
            s = _snapshot(loader, codes, cand)
            if best is None or s.get("n", 0) > best[0]:
                best = (s.get("n", 0), cand, s)
            if s.get("n", 0) >= 4500:
                asof, snap = cand, s
                break
        if snap is None:
            if best is not None:
                asof, snap = best[1], best[2]
            else:                               # 极端: 候选全被时间跳过 → 退用最新
                asof = cands[0]
                snap = _snapshot(loader, codes, asof)
    breadth_pct = snap.pop("_breadth_pct")
    out = {
        "date": asof,
        "generated_at": now_iso or _dt.datetime.now().isoformat(timespec="seconds"),
        "regime": _regime(loader, asof, breadth_pct),
        "limit_ups": snap,
        "mainline": _mainline(_mainline_panel_path(provider)),
    }
    op = Path(out_path) if out_path else default_out_path()
    op.parent.mkdir(parents=True, exist_ok=True)
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    return {"out": str(op), **out}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="交易日 YYYY-MM-DD(默认最新有数据交易日)")
    ap.add_argument("--out", default=None, help="输出路径(默认 env MARKET_STATUS_PATH 或 repo/data/)")
    args = ap.parse_args()
    res = generate(date=args.date, out_path=args.out)
    r, lu, ml = res["regime"], res["limit_ups"], res["mainline"]
    print(f"[market_status] as_of={res['date']} regime={r['regime']} ({r['source']}) "
          f"breadth={r.get('breadth_pct')}% idx_above_ma20={r.get('index_above_ma20')}")
    print(f"[market_status] 涨停 {lu['limit_up_total']} (主板{lu['limit_up_10']}/双创{lu['limit_up_20']}) "
          f"跌停 {lu['limit_down']} | 涨{lu['up_count']}/跌{lu['down_count']} (n={lu['n']})")
    print(f"[market_status] 主线 {ml['n_mainline']} (as-of {ml['as_of']}) "
          f"top={[t['industry'] for t in ml['top']]}")
    print(f"[market_status] wrote -> {res['out']}")


if __name__ == "__main__":
    main()
