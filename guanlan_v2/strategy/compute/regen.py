# -*- coding: utf-8 -*-
"""引擎原生**产物再生**:用 compute/{breadth,mainline,v4} 重建三个 vendored 产物,
**不依赖 qlib 包、不需 conda stocks 环境、不跑 G:/stocks 那 5 支脚本**(py3.13 引擎 venv 即可)。

这是"彻底掉 qlib"的收口:数据每日 EOD 才变,产物本就是缓存;请求期仍读缓存(快),
缓存的**再生**改由引擎自给。等价性已由 scripts/compare_{breadth,mainline,v4}.py 证过。

跑法(引擎 venv,无 qlib):
    G:/financial-analyst/.venv/Scripts/python.exe -m guanlan_v2.strategy.compute.regen [END_DATE]

顺序(与 qlib 管线同):breadth→resid(先落,v4 要读)→ mainline → v4 → 重生 provenance。
落点 = `vendor/artifacts/`(后端消费的缓存)。再生后**需重启 9999** 让 LRU 缓存失效。
"""
from __future__ import annotations

import sys
from pathlib import Path as _Path
from typing import Optional

# 子进程跑本模块时优先用仓内 engine fork(与 server._ensure_engine_importable 同源):
# 装好的 G:/financial-analyst/src 无 fetch_financials/精算字段 → factor_ic 财务/精算族会静默缺席。
_ENGINE = _Path(__file__).resolve().parents[3] / "engine"
if _ENGINE.is_dir() and str(_ENGINE) not in sys.path:
    sys.path.insert(0, str(_ENGINE))

import pandas as pd

from guanlan_v2.strategy.paths import (
    ARTIFACTS_DIR, MARKET_BREADTH_PARQUET, V4_RANKING_PARQUET,
)
from guanlan_v2.strategy.compute.breadth import build_breadth, list_all_instruments
from guanlan_v2.strategy.compute.mainline import build_mainline
from guanlan_v2.strategy.compute.v4 import build_v4

DEFAULT_PROVIDER = "G:/stocks/stock_data/cn_data"
MAINLINE_PARQUET = ARTIFACTS_DIR / "monthly_mainlines_panel.parquet"


def _write_atomic(df: "pd.DataFrame", path, **kw) -> None:
    """原子落盘:先写 ``<path>.tmp`` 再 ``os.replace`` 覆盖(同卷原子)。
    防再生中途崩溃留半截 parquet 让正在读盘的后端(load_v4_ranking 每次读盘)报错。
    注:顺序仍是「resid 先 replace 到位 → v4 再读它」,故原子化不破坏 add_breadth_resid 依赖。"""
    import os
    tmp = str(path) + ".tmp"
    df.to_parquet(tmp, **kw)
    os.replace(tmp, str(path))


def _pid_alive(pid: int) -> bool:
    """进程是否仍在(Windows 用 OpenProcess+ExitCode;其他平台退回 os.kill(pid,0))。"""
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return code.value == 259                 # STILL_ACTIVE
    except Exception:
        import os
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
        except Exception:
            return True   # 不确定 → 保守当活,宁可拒绝并发


def _regen_lock_path():
    import tempfile
    from pathlib import Path
    return Path(tempfile.gettempdir()) / "guanlan_regen.lock"


_LOCK_MAX_AGE = 1800  # 30min:远超单次再生(~10min),超此一律视作崩溃残留可接管


def _acquire_regen_lock() -> None:
    """系统级单飞:防两个再生进程同时写产物(曾因双 server 各自起再生竞争 → 产物日期错位)。
    残留锁(写锁的进程已死 或 超 30min)自动接管;否则抛错拒绝并发。锁文件在 OS 临时目录,
    不落进 artifacts/(不扰 provenance/漂移哨兵)。"""
    import os
    import json
    import time
    p = _regen_lock_path()
    if p.exists():
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
            opid, age = int(info.get("pid", 0)), time.time() - float(info.get("ts", 0))
        except Exception:
            opid, age = 0, 1e9
        if age < _LOCK_MAX_AGE and _pid_alive(opid):
            raise RuntimeError(f"另一再生进程进行中(pid={opid}, {int(age)}s 前启动);拒绝并发写产物")
        # 否则残留锁 → 接管
    p.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}), encoding="utf-8")


def _release_regen_lock() -> None:
    """只删自己写的锁(避免误删接管者的锁)。"""
    import os
    import json
    p = _regen_lock_path()
    try:
        if not p.exists():
            return
        try:
            info = json.loads(p.read_text(encoding="utf-8"))
            if int(info.get("pid", -1)) != os.getpid():
                return
        except Exception:
            pass
        p.unlink()
    except Exception:
        pass


def _latest_trade_date(provider_uri: str) -> str:
    """实际数据最新交易日 = close 与 daily_basic(pe_ttm)**共同覆盖**的最新日。

    注意:日历(day.txt)预排到年底有未来空日,不能用 cal[-1];且 close 可能比
    daily_basic 先到(2026-06-12 事故:end 取了 close 最新日 06-11,而 pe/市值
    只到 06-09 → v4 五维评分在 end 日截面全 NaN → v4_total 全空)。
    取常态交易的大盘股(茅台/平安/招行)close 与 pe_ttm 两个 bin dropna 末日的
    较小者 = 全字段齐的真·最新数据日。"""
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    ld = QlibBinaryLoader(provider_uri)
    for probe in ("SH600519", "SZ000001", "SH600036"):
        s = ld._read_bin(probe, "close")
        if s is None:
            continue
        sd = s.dropna()
        if not len(sd):
            continue
        d_end = pd.Timestamp(sd.index[-1])
        b = ld._read_bin(probe, "pe_ttm")
        if b is not None:
            bd = b.dropna()
            if len(bd):
                d_end = min(d_end, pd.Timestamp(bd.index[-1]))
        return str(d_end.date())
    return str(pd.Timestamp(ld._load_calendar("day")[-1]).date())  # 兜底


def regen_all(provider_uri: str = DEFAULT_PROVIDER, end: Optional[str] = None) -> dict:
    """再生三产物到 vendored,返回各自的 (rows, path)。end 缺省=日历最新交易日。
    系统级单飞:同一时刻只允许一个再生进程写产物(锁在 finally 必释放)。"""
    if end is None:
        end = _latest_trade_date(provider_uri)
    _acquire_regen_lock()   # 跨进程单飞:拒绝并发再生(防产物日期错位)
    try:
        codes = list_all_instruments(provider_uri)   # 复用同一份代码表
        out: dict = {"end": end, "n_codes": len(codes)}

        # 1) 节奏:breadth panel + resid → 先写 resid(v4 的 add_breadth_resid 要读它)
        print(f"[regen] breadth → resid (end={end}) ...", flush=True)
        _panel, resid = build_breadth(provider_uri, end=end, codes=codes)
        _write_atomic(resid, MARKET_BREADTH_PARQUET)
        out["breadth_resid"] = (len(resid), str(MARKET_BREADTH_PARQUET))
        print(f"  resid {len(resid)} 行 -> {MARKET_BREADTH_PARQUET}", flush=True)

        # 2) 主线:月度面板(含 status)
        print("[regen] mainline → monthly_mainlines ...", flush=True)
        ml = build_mainline(provider_uri, end=end, codes=codes)
        _write_atomic(ml, MAINLINE_PARQUET)
        out["mainline"] = (len(ml), str(MAINLINE_PARQUET))
        print(f"  mainline {len(ml)} 行 -> {MAINLINE_PARQUET}", flush=True)

        # 3) v4:38 因子 + LGB(cpu) + 顶200 评分 → 7 列排名(读上面刚落的 resid)
        #    顺带 health 出参:同模型近60有标签日逐日 rank-IC(体检回看,见 model_health.py 口径)
        print("[regen] v4 → v4_ranking_latest (含 LGB 训练,稍慢) ...", flush=True)
        _health: dict = {}
        # #7 B3 集成:离线只读 var/v4_fincast_pred.parquet(GPU 批算产出)→ 有当日预测则混进 v4 score,
        #    无则诚实退纯 LGB(文件现不存在 → 字节等价旧行为)。b3 provenance 落盘供 serving/UI 诚实徽章。
        from pathlib import Path as _P
        _b3: dict = {}
        _fincast_p = _P(__file__).resolve().parents[3] / "var" / "v4_fincast_pred.parquet"
        v4out = build_v4(provider_uri, end=end, codes=codes, date_str=end,
                         health=_health, fincast_path=str(_fincast_p), b3=_b3)
        _write_atomic(v4out, V4_RANKING_PARQUET, index=False)
        out["v4"] = (len(v4out), str(V4_RANKING_PARQUET))
        out["v4_b3"] = dict(_b3) if _b3 else {"active": False, "reason": "未启用"}
        try:   # b3 provenance 旁路落盘(serving 读它判「纯 LGB」vs「FinCast 混合」+ look-ahead)
            import json as _json
            _b3_side = V4_RANKING_PARQUET.parent / "v4_b3_provenance.json"
            _b3_side.write_text(_json.dumps({"date": end, **(_b3 or {})}, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as _e:  # noqa: BLE001 — provenance 落盘失败不阻断再生
            print(f"  [warn] v4_b3 provenance 落盘失败: {type(_e).__name__}: {_e}", flush=True)
        print(f"  v4 {len(v4out)} 行 (顶200 v4_total notnull={int(v4out['v4_total'].notna().sum())}) "
              f"· B3 {('混合 w_fc=%.2f' % _b3.get('w_fc', 0)) if _b3.get('active') else '纯 LGB'} -> {V4_RANKING_PARQUET}", flush=True)

        # 3.4) 模型体检:回看 IC 落盘 + 当日快照入档 + vintage 真 OOS 增量(失败不阻断)
        print("[regen] model_health → 体检回看 + 快照入档 + vintage ...", flush=True)
        try:
            from guanlan_v2.strategy.model_health import (
                append_score_history, update_vintage_ic, write_backcast,
            )
            n_bc = write_backcast(_health.get("ic_series") or [], end) if _health.get("ic_series") else 0
            n_hist = append_score_history(v4out, end)
            n_vin = update_vintage_ic(provider_uri)
            out["model_health"] = {"backcast_days": n_bc, "history_days": n_hist, "vintage_days": n_vin}
            if _health.get("error"):
                out["model_health"]["backcast_error"] = _health["error"]
            print(f"  体检回看 {n_bc} 日 · 快照档 {n_hist} 日 · vintage {n_vin} 日", flush=True)
        except Exception as e:  # noqa: BLE001
            out["model_health"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] model_health 失败(不阻断): {type(e).__name__}: {e}", flush=True)

        # 3.5) 因子库实测 IC(选股页 2.0:~56 因子 csi300 近60日 rank-IC;失败不阻断三产物)
        print("[regen] factor_ic → 因子库实测IC(csi300·近60日)...", flush=True)
        try:
            from guanlan_v2.screen.factor_ic import FACTOR_IC_PARQUET, compute_catalog_ic
            n_ic = compute_catalog_ic(end=end)
            out["factor_ic"] = (n_ic, str(FACTOR_IC_PARQUET))
            print(f"  factor_ic {n_ic} 因子 -> {FACTOR_IC_PARQUET}", flush=True)
        except Exception as e:  # noqa: BLE001
            out["factor_ic"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] factor_ic 失败(不阻断): {type(e).__name__}: {e}", flush=True)

        # 3.6) 因子 vintage IC(逐日截面 + 单票 tsic 真 OOS;失败不阻断三产物)
        print("[regen] factor_vintage → 逐日 vintage IC(截面 csi300 + 单票 tsic)...", flush=True)
        try:
            from guanlan_v2.screen.factor_vintage import compute_factor_vintage, CS_IC_PARQUET
            n_v = compute_factor_vintage(end=end)
            out["factor_vintage"] = (n_v, str(CS_IC_PARQUET))
            print(f"  factor_vintage cs={n_v['cs_rows']} tsic={n_v['tsic_rows']} -> {CS_IC_PARQUET}", flush=True)
        except Exception as e:  # noqa: BLE001
            out["factor_vintage"] = f"skipped: {type(e).__name__}: {e}"
            print(f"  [warn] factor_vintage 失败(不阻断): {type(e).__name__}: {e}", flush=True)

        # 4) 重生漂移哨兵
        print("[regen] 重生 provenance ...", flush=True)
        from guanlan_v2.strategy.regen_provenance import regen as _regen_prov
        _regen_prov()
        out["provenance"] = "regenerated"
        return out
    finally:
        _release_regen_lock()


if __name__ == "__main__":
    _end = sys.argv[1] if len(sys.argv) > 1 else None
    res = regen_all(end=_end)
    print("\n[regen] 完成:")
    for k, v in res.items():
        print(f"  {k}: {v}")
    print("\n注:再生后需重启 9999 让后端 LRU 缓存失效(load_v4_ranking/mainline_status_map/market_cycle)。")
