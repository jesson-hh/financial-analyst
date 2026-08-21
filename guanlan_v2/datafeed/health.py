"""数据健康总闸 —— 全仓数据新鲜度一处可见(数据中台件③)。

收编 T5:此前 regen 停摆/DL 断供/正本陈旧/pit 滞后各自埋在 provenance 或日志里,
"看的人才知道"。本模块聚合所有关键数据面的新鲜度到一个只读视图,
帷幄 ww_data_health 与 GET /data/health 共享它。全逐项防御:任何读失败 →
该项 status:missing + note,绝不崩、绝不伪造新鲜。
"""
from __future__ import annotations

import json
from datetime import date as _date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 阈值(自然日/小时;超则 stale)
_V4_STALE_DAYS = 3
_BASIC_STALE_DAYS = 5
_DL_STALE_DAYS = 3      # **交易日**窗(与 dl_ensemble.DLSource.max_stale_days 对齐;
                        # 原自然日≤4:长假后首个交易日必集体误报断供)
_TENCENT_STALE_HOURS = 24
_PIT_STALE_DAYS = 3
_TAPE_STALE_MIN = 30    # 盘口快照:SWR 常态 <3min;超 30min 未刷(服务停摆/盘后)→ stale
_RADAR_STALE_HOURS = 24   # 资讯雷达:产业 RSS 日级情报;请求驱动 SWR,超 1 天未刷诚实显形
_PULSE_STALE_HOURS = 30   # 宏观温度计快照:复盘官日跑节拍,30h 容漂移;超=日跑/刷新全停

_STATUS_RANK = {"fresh": 0, "unknown": 1, "stale": 2, "missing": 3}


def _age_days(iso: Optional[str]) -> Optional[int]:
    """ISO 日期/时间串 → 距今自然日;不可解析→None。"""
    if not iso:
        return None
    s = str(iso)[:10]
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return (_date.today() - _date(y, m, d)).days
    except Exception:  # noqa: BLE001
        return None


def _age_busdays(iso: Optional[str]) -> Optional[int]:
    """ISO 日期串 → 距今工作日数(np.busday 近似交易日;周末不计入——长假仍偏保守,
    本模块只读 JSON 无交易日历,诚实近似而非冒充真日历)。不可解析→None。"""
    if not iso:
        return None
    s = str(iso)[:10]
    try:
        import numpy as _np
        y, m, d = (int(x) for x in s.split("-"))
        return int(_np.busday_count(_date(y, m, d), _date.today()))
    except Exception:  # noqa: BLE001
        return None


def _age_hours(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(str(iso))
        return round((datetime.now() - ts).total_seconds() / 3600.0, 1)
    except Exception:  # noqa: BLE001
        return None


def _age_minutes(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return round((datetime.now() - datetime.fromisoformat(str(iso))).total_seconds() / 60.0, 1)
    except (TypeError, ValueError):
        return None


def _mtime_age_days(path: Path) -> Optional[int]:
    try:
        mt = datetime.fromtimestamp(path.stat().st_mtime)
        return (_date.today() - mt.date()).days
    except OSError:
        return None


def _read_json(path: Path) -> Optional[dict]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _tail_lines(path: Path, max_bytes: int = 65536) -> list:
    """读文件尾部 max_bytes 内的行(append-only jsonl 尾行定位用);失败→[]。
    macro 快照无轮转会无限增长,总闸绝不全量解析拖慢自己。"""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


from importlib import import_module as _import_module


import functools


@functools.lru_cache(maxsize=1)
def _paths():
    """引擎分层路径解析器(env>loaders.yaml>user>dev);失败→None(各项自行降级)。
    进程内缓存:路径配置进程存续期不变,免每次 collect 重复 find_config+yaml 解析
    (一次 collect 有 3 个 item 各调一次;评审 Minor)。"""
    try:
        from financial_analyst.data.paths import get_data_paths
        return get_data_paths()
    except Exception:  # noqa: BLE001
        return None


def _item_v4() -> Dict[str, Any]:
    try:
        from guanlan_v2 import strategy as S
        rd = S.ranking_date()
        rows = int(len(S.load_v4_ranking()))
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"v4 榜读取失败: {type(exc).__name__}"}
    sd = _age_days(rd)
    status = "unknown" if sd is None else ("stale" if sd > _V4_STALE_DAYS else "fresh")
    return {"status": status, "date": rd, "rows": rows, "stale_days": sd}


def _item_regen() -> Dict[str, Any]:
    import os
    try:
        from guanlan_v2.screen.api import _REGEN_SCHED
        enabled = bool(_REGEN_SCHED.get("enabled"))
        last = _REGEN_SCHED.get("last_auto_ts")
    except Exception:  # noqa: BLE001
        enabled, last = os.environ.get("GUANLAN_REGEN_DAILY") == "1", None
    # 调度器本身不是"数据",enabled=true→fresh、关→unknown(提示可开,不算 stale 数据)
    return {"status": "fresh" if enabled else "unknown",
            "enabled": enabled, "last_auto_ts": last,
            "note": "" if enabled else "每日自动再生未启(var/secrets.env: GUANLAN_REGEN_DAILY=1)"}


def _item_dl() -> Dict[str, Any]:
    try:
        from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
        prov = _read_json(Path(V4_RANKING_PARQUET).parent / "v4_dl_provenance.json")
    except Exception:  # noqa: BLE001
        prov = None
    if not prov:
        return {"status": "missing", "note": "无 v4_dl_provenance.json(DL 未参与/未再生)"}
    srcs = []
    any_active_stale = False
    for s in prov.get("sources") or []:
        sd = s.get("stale_days")            # 单位=交易日(dl_ensemble 落盘口径)
        active = bool(s.get("active"))
        # 活跃源:stale_days 超窗 或 未记(None)都算可疑陈旧(诚实偏保守)
        if active and (sd is None or (isinstance(sd, (int, float)) and sd > _DL_STALE_DAYS)):
            any_active_stale = True
        srcs.append({"model_id": s.get("model_id"), "active": active,
                     "stale_days": sd, "lookahead": s.get("lookahead")})
    n_active = sum(1 for s in srcs if s["active"])
    # 关键(评审 Important,真机坐实):per-source stale_days 是 regen 落盘那刻冻结的快照,
    # regen 一停摆就永远停在旧值(通常 0)→ 会把 6 天前的 DL 误报 fresh。必须再用
    # provenance 自身的 date 龄期兜底:整份产物超窗即 stale,不看冻结的 per-source。
    # 龄期改工作日计(_age_busdays):周末不再把周五产物误报 stale(交易日窗口径对齐)。
    prov_age = _age_days(prov.get("date"))
    prov_age_bd = _age_busdays(prov.get("date"))
    prov_stale = prov_age_bd is not None and prov_age_bd > _DL_STALE_DAYS
    status = "stale" if (any_active_stale or n_active == 0 or prov_stale) else "fresh"
    note = ("DL 全断供(退纯 LGB)" if n_active == 0
            else (f"DL provenance 已 {prov_age_bd} 交易日未刷新(regen 停摆?)" if prov_stale
                  else ("有活跃源超窗陈旧" if any_active_stale else "")))
    if status == "stale":
        # DL 断供 regen 不自愈(regen 只读 DL parquet 不产它),须生产器日跑挂上才回血
        import os as _os
        _on = _os.environ.get("GUANLAN_DL_DAILY") == "1"
        note = f"{note}·regen 不自愈,需 DL 生产器(GUANLAN_DL_DAILY {'已挂' if _on else '未挂'})"
    return {"status": status, "date": prov.get("date"), "prov_age_days": prov_age,
            "prov_age_busdays": prov_age_bd,
            "active": bool(prov.get("active")), "n_active": n_active,
            "sources": srcs, "note": note}


def _item_stock_basic() -> Dict[str, Any]:
    p = _paths()
    if p is None:
        return {"status": "missing", "note": "路径解析器不可用"}
    try:
        path = Path(p.parquet_root) / "tushare_stock_basic.parquet"
    except Exception:  # noqa: BLE001
        return {"status": "missing", "note": "parquet_root 缺失"}
    age = _mtime_age_days(path)
    if age is None:
        return {"status": "missing", "note": f"正本缺失: {path}"}
    status = "stale" if age > _BASIC_STALE_DAYS else "fresh"
    return {"status": status, "age_days": age,
            "note": "正本久未刷新(新股/行业变更缺席)" if status == "stale" else ""}


def _item_tencent_cache() -> Dict[str, Any]:
    p = _paths()
    if p is None:
        return {"status": "missing", "note": "路径解析器不可用"}
    try:
        path = Path(p.parquet_root).parent / "live" / "tencent" / "manifest_latest.json"
    except Exception:  # noqa: BLE001
        return {"status": "missing", "note": "路径推导失败"}
    m = _read_json(path)
    if not m:
        return {"status": "missing", "note": "无腾讯 live cache manifest(未排盘中节奏)"}
    run_at = m.get("run_at")
    age_h = _age_hours(run_at)
    status = "unknown" if age_h is None else ("stale" if age_h > _TENCENT_STALE_HOURS else "fresh")
    return {"status": status, "run_at": run_at, "age_hours": age_h,
            "latest_data_ts": m.get("latest_data_ts"),
            "note": "名为 realtime 实为手动(读 manifest 做门,勿凭目录名信新鲜)"}


def _item_pit_store() -> Dict[str, Any]:
    p = _paths()
    if p is None:
        return {"status": "missing", "note": "路径解析器不可用"}
    try:
        path = Path(p.pit_store_root) / "_meta.json"
    except Exception:  # noqa: BLE001
        return {"status": "missing", "note": "pit_store_root 缺失"}
    m = _read_json(path)
    if not m:
        return {"status": "missing", "note": "无 pit_store/_meta.json"}
    cal_end = m.get("cal_end")
    age = _age_days(cal_end)
    status = "unknown" if age is None else ("stale" if age > _PIT_STALE_DAYS else "fresh")
    return {"status": status, "cal_end": cal_end, "news_date_max": m.get("news_date_max"),
            "cal_end_age_days": age, "n_trade_days": m.get("n_trade_days")}


def _item_market_tape() -> Dict[str, Any]:
    """盘口实时快照新鲜度:读 var/live/market_tape.json 的 pulled_at 龄期。
    缺文件=missing(未预热);≤30min=fresh;超=stale(服务停摆/盘后)。属数据项,参与 overall。"""
    try:
        from guanlan_v2.datafeed.market_tape import _CACHE_PATH
        m = _read_json(Path(_CACHE_PATH))
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not m:
        return {"status": "missing", "note": "无盘口快照(未预热/首拉未完成)"}
    age = _age_minutes(m.get("pulled_at"))
    status = "unknown" if age is None else ("stale" if age > _TAPE_STALE_MIN else "fresh")
    return {"status": status, "pulled_at": m.get("pulled_at"), "age_min": age,
            "note": "盘口快照久未刷新(服务停摆/盘后?)" if status == "stale" else ""}


def _item_newsradar() -> Dict[str, Any]:
    """资讯雷达快照新鲜度(2026-07-16 收口):读 var/live/newsradar.json 的 pulled_at 龄期。
    产业 RSS 日级情报 ≤24h=fresh;超=stale——请求驱动 SWR 无人访问资讯页即不刷,诚实显形。"""
    try:
        from guanlan_v2.datafeed.newsradar import _CACHE_PATH
        m = _read_json(Path(_CACHE_PATH))
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not m:
        return {"status": "missing", "note": "无资讯雷达快照(未预热/资讯页从未访问)"}
    age_h = _age_hours(m.get("pulled_at"))
    status = "unknown" if age_h is None else ("stale" if age_h > _RADAR_STALE_HOURS else "fresh")
    return {"status": status, "pulled_at": m.get("pulled_at"), "age_hours": age_h,
            "n_sources": m.get("n_sources"),
            "note": "雷达快照久未刷新(无人访问资讯页/服务停摆?)" if status == "stale" else ""}


def _item_macro_pulse() -> Dict[str, Any]:
    """宏观温度计快照新鲜度(2026-07-16 收口):snapshots.jsonl 尾部最近合法行的 ts。
    刷新三驱动=UI 刷新/ww_macro_pulse/复盘官日跑;≤30h=fresh(容日跑漂移)。
    只读尾块 64KB(_tail_lines),脏尾行跳过取最近合法行。"""
    try:
        from guanlan_v2.macro.pulse import _SNAP_DEFAULT as _snap
        path = Path(_snap)
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not path.exists():
        return {"status": "missing", "note": "无宏观快照(温度计从未真拉成功)"}
    ts: Optional[str] = None
    for line in reversed(_tail_lines(path)):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 — append-only jsonl 脏行惯例
            continue
        if isinstance(row, dict) and row.get("ts"):
            ts = str(row["ts"])
            break
    age_h = _age_hours(ts)
    status = "unknown" if age_h is None else ("stale" if age_h > _PULSE_STALE_HOURS else "fresh")
    return {"status": status, "ts": ts, "age_hours": age_h,
            "note": "宏观快照久未落(UI 刷新/ww_macro_pulse/复盘官日跑均未动?)"
                    if status == "stale" else ""}


def _item_overseas() -> Dict[str, Any]:
    """海外看板(纯内存 SWR,运维项):冷=unknown(重启即失、按需预热,非故障);
    缓存在场报龄期,note 诚实说明「龄期大≠故障」;全部缺价=stale(上游腾讯 qt 不可达显形)。"""
    try:
        from guanlan_v2.datafeed import overseas as _ov
        data = _ov._MEM_CACHE.get("data")
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not data:
        return {"status": "unknown",
                "note": "纯内存缓存冷(重启后未预热;访问海外页/ww_overseas 即自愈)"}
    rows = data.get("rows") or []
    age_min = round((datetime.now().timestamp() - float(data.get("pulled_ts") or 0)) / 60.0, 1)
    if rows and all((not isinstance(r, dict)) or r.get("price") is None for r in rows):
        return {"status": "stale", "age_min": age_min, "n": len(rows),
                "note": "全部缺价(上游腾讯 qt 不可达?)"}
    return {"status": "fresh", "age_min": age_min, "n": data.get("n"),
            "pulled_at": data.get("pulled_at"),
            "note": "按需现拉型:龄期大只代表近期无人访问,非故障"}


def _item_kuaixun() -> Dict[str, Any]:
    """快讯门户(现拉型零快照,运维项):仅校验引擎采集器可导入——可用=fresh,
    不可导入=missing。绝不冒充内容新鲜度(内容新鲜度=调用时现拉)。"""
    try:
        _import_module("financial_analyst.data.news_pulse")
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"快讯采集器不可导入: {type(exc).__name__}"}
    return {"status": "fresh", "note": "现拉型门户(零快照):仅校验采集器可用,不代表内容新鲜"}


def _item_probe_worker() -> Dict[str, Any]:
    """常驻 probe worker 通道自检(T2 提速面):通道死=提速蒸发但数据仍真(自动回落
    一次性子进程),运维项不拖 overall;冷却态把 last_error(含 stderr 尾)显形。"""
    from guanlan_v2.datafeed import live_client as _lc

    st = _lc.worker_status()
    if not st["enabled"]:
        return {"status": "unknown", "note": "worker 已禁用(GUANLAN_PROBE_WORKER=0),走一次性子进程"}
    if not st["script_exists"]:
        return {"status": "missing", "note": "stocks probe_live_worker.py 缺席(已回落一次性子进程)"}
    if float(st.get("cooldown_until") or 0.0) > datetime.now().timestamp():
        return {"status": "error",
                "note": f"worker 连败冷却中(已回落一次性):{str(st.get('last_error') or '')[:200]}"}
    if st["state"] == "ready" or st["alive"]:
        return {"status": "fresh", "note": ""}
    return {"status": "unknown", "note": "worker 冷(尚无调用,首调懒起)"}


_ITEMS = {"v4_ranking": _item_v4, "regen_scheduler": _item_regen, "dl_ensemble": _item_dl,
          "stock_basic": _item_stock_basic, "tencent_live_cache": _item_tencent_cache,
          "pit_store": _item_pit_store, "market_tape": _item_market_tape,
          "newsradar": _item_newsradar, "macro_pulse": _item_macro_pulse,
          "overseas": _item_overseas, "kuaixun": _item_kuaixun,
          "probe_worker": _item_probe_worker}
# 运维类项(非"数据新鲜度")不参与 overall 恶化:regen 调度是开关,关着不代表数据陈旧,
# 否则生产常态(opt-in 未开)会把 overall 恒拉成 unknown 掩盖真实数据面(评审 Minor)。
# overseas(纯内存,重启即冷)/kuaixun(现拉型零快照)同理:unknown 是常态非故障。
# newsradar 同理(2026-07-16 评审确认):纯请求驱动 SWR 无定时生产者,无人访问资讯页
# 是常态——归数据项会让周末 overall 恒 stale,噪声经复盘官日调 ww_data_health 进晨报,
# 掩盖 DL 断供等真故障;items 里仍诚实显形 stale,只是不拖 overall。
# probe_worker(2026-07-30 T2 评审):常驻 worker 通道死=提速蒸发但数据仍真(自动回落
# 一次性子进程),属运维态;必须显形否则可静默失效数周,但不拖 overall。
_OPS_ITEMS = {"regen_scheduler", "overseas", "kuaixun", "newsradar", "probe_worker"}


def collect_data_health() -> Dict[str, Any]:
    """聚合全仓数据新鲜度:{ok, generated_at, overall:{status,stale[],missing[]}, items:{}}。
    逐项独立防御(单项抛异常也不拖垮整体);overall 只反映数据面(运维项 _OPS_ITEMS 除外)。"""
    items: Dict[str, Any] = {}
    for name, fn in _ITEMS.items():
        try:
            items[name] = fn()
        except Exception as exc:  # noqa: BLE001 — 单项异常降级为 missing,绝不崩
            items[name] = {"status": "missing", "note": f"{type(exc).__name__}: {exc}"}
    stale = [k for k, v in items.items() if v.get("status") == "stale"]
    missing = [k for k, v in items.items() if v.get("status") == "missing"]
    data_status = [v.get("status", "unknown") for k, v in items.items() if k not in _OPS_ITEMS]
    overall = max(data_status, key=lambda s: _STATUS_RANK.get(s, 1), default="unknown")
    return {"ok": True, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "overall": {"status": overall, "stale": stale, "missing": missing},
            "items": items}
