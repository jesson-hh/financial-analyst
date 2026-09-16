"""数据健康总闸 —— 全仓数据新鲜度一处可见(数据中台件③)。

收编 T5:此前 regen 停摆/DL 断供/正本陈旧/pit 滞后各自埋在 provenance 或日志里,
"看的人才知道"。本模块聚合所有关键数据面的新鲜度到一个只读视图,
帷幄 ww_data_health 与 GET /data/health 共享它。全逐项防御:任何读失败 →
该项 status:missing + note,绝不崩、绝不伪造新鲜。
"""
from __future__ import annotations

import functools
import json
from datetime import date as _date, datetime
from importlib import import_module as _import_module
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
            "note": "" if enabled else "每日自动再生未启(进程/部署环境: GUANLAN_REGEN_DAILY=1)"}


def _item_core_data_refresh_scheduler() -> Dict[str, Any]:
    """Live configuration and last receipt of the upstream data producer."""

    try:
        from guanlan_v2.datafeed.core_refresh_scheduler import (
            probe_core_refresh_scheduler,
        )

        return probe_core_refresh_scheduler()
    except Exception:  # noqa: BLE001 — health collection must remain defensive
        return {
            "status": "error", "enabled": False,
            "note": "核心数据盘后生产任务探针不可用",
        }


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
        return {"status": "missing", "required": False, "deprecated": True,
                "note": ("无旧版腾讯 live cache manifest；当前实时行情已走 live_client，"
                         "该兼容缓存缺失会显形但不再压低整体数据健康")}
    run_at = m.get("run_at")
    age_h = _age_hours(run_at)
    status = "unknown" if age_h is None else ("stale" if age_h > _TENCENT_STALE_HOURS else "fresh")
    return {"status": status, "required": False, "deprecated": True,
            "run_at": run_at, "age_hours": age_h,
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


def _item_pit_trust() -> Dict[str, Any]:
    """Expose strict-PIT deployment readiness without exposing secret material.

    This is an operational readiness item rather than a data-freshness item:
    current research remains available, while historical paths keep their
    existing fail-closed behaviour when the trust anchor is absent.
    """
    try:
        from financial_analyst.data.pit_preflight import deployment_preflight

        result = deployment_preflight()
    except Exception:  # noqa: BLE001 — never project exception text from a trust probe
        return {
            "status": "missing", "quality": "degraded", "ready": False,
            "fail_closed": True, "blockers": ["pit_preflight_unavailable"],
            "note": "严格 PIT 部署预检不可用；历史研究保持 fail-closed",
        }

    blockers = list(result.get("blockers") or [])
    checks = result.get("checks") or {}
    hk_clock = checks.get("hong_kong_clock") or {}
    ntp = checks.get("ntp_attestation") or {}
    if result.get("ready"):
        status = "fresh"
        note = "严格 PIT 密钥、轮换 keyring 与可信时钟预检已通过"
    else:
        status = "stale" if blockers == ["pit_ntp_attestation_stale"] else "missing"
        note = (
            "严格 PIT 尚未达到生产部署前提，历史研究保持 fail-closed: "
            + ",".join(blockers)
        )
    return {
        "status": status,
        "quality": "complete" if result.get("ready") else "degraded",
        "ready": bool(result.get("ready")),
        "fail_closed": True,
        "schema_version": result.get("schema_version"),
        "active_key_id": result.get("active_key_id"),
        "verify_key_count": int(result.get("verify_key_count") or 0),
        "hk_clock_aware": hk_clock.get("status") == "pass",
        "hk_clock_timezone": hk_clock.get("timezone"),
        "ntp_status": ntp.get("status"),
        "ntp_checked_at": ntp.get("checked_at"),
        "ntp_age_seconds": ntp.get("age_seconds"),
        "blockers": blockers,
        "note": note,
    }


def _item_market_tape() -> Dict[str, Any]:
    """盘口核心四池新鲜度 + 可选分源降级。

    ``pulled_at`` 继续保留“所有分源最旧水位”的兼容语义；健康状态改看
    ``core_pulled_at``，所以行业榜等可选源陈旧会作为 warning 显形，却不会把
    新鲜的涨停/炸板/跌停/昨涨停核心快照误报成整体停摆。
    """
    try:
        from guanlan_v2.datafeed import market_tape as _mt
        _CACHE_PATH = _mt._CACHE_PATH
        m = _read_json(Path(_CACHE_PATH))
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not m:
        return {"status": "missing", "note": "无盘口快照(未预热/首拉未完成)"}
    fr = _mt._freshness(m, _TAPE_STALE_MIN * 60)
    core_anchor = m.get("core_pulled_at") or m.get("pulled_at")
    age_s = fr.get("overall_age_s")
    age = round(float(age_s) / 60.0, 1) if age_s is not None else None
    missing_core = list(fr.get("missing_core_sources") or [])
    mismatched_core = list(fr.get("mismatched_core_sources") or [])
    degraded_sources = list(fr.get("degraded_sources") or [])
    degraded_details = list(fr.get("degraded_details") or [])
    status = "stale" if fr.get("stale") else ("unknown" if age is None else "fresh")
    quality = "degraded" if degraded_sources or missing_core or mismatched_core else "complete"
    if status == "stale":
        if mismatched_core:
            note = "盘口核心四池交易日不一致，已禁止混算: " + "、".join(mismatched_core)
        else:
            note = "盘口核心四池久未刷新或缺腿(服务停摆/上游失败?)"
    elif degraded_sources:
        detail_notes = []
        for detail in degraded_details:
            if not isinstance(detail, dict):
                continue
            reasons = "、".join(str(x) for x in (detail.get("reasons") or []) if x)
            detail_notes.append(f"{detail.get('source')}({reasons or '局部降级'})")
        note = "核心盘口新鲜；局部降级: " + (
            "；".join(detail_notes) if detail_notes else "、".join(sorted(set(degraded_sources)))
        )
    else:
        note = ""
    return {"status": status, "quality": quality,
            "pulled_at": m.get("pulled_at"), "core_pulled_at": core_anchor,
            "age_min": age, "oldest_source_age_min": _age_minutes(m.get("pulled_at")),
            "board_date": m.get("board_date"), "missing_core_sources": missing_core,
            "expected_session": fr.get("latest_session"),
            "mismatched_core_sources": mismatched_core,
            "market_closed": fr.get("market_closed"),
            "closed_snapshot_valid": fr.get("closed_snapshot_valid"),
            "degraded_sources": sorted(set(degraded_sources)),
            "degraded_details": degraded_details, "note": note}


def _item_newsradar() -> Dict[str, Any]:
    """资讯雷达新鲜度 + 逐源覆盖质量；两者分开，避免“缓存新=106源全好”的假话。"""
    try:
        from guanlan_v2.datafeed.newsradar import _CACHE_PATH
        m = _read_json(Path(_CACHE_PATH))
    except Exception as exc:  # noqa: BLE001
        return {"status": "missing", "note": f"{type(exc).__name__}"}
    if not m:
        return {"status": "missing", "note": "无资讯雷达快照(未预热/资讯页从未访问)"}
    age_h = _age_hours(m.get("pulled_at"))
    status = "unknown" if age_h is None else ("stale" if age_h > _RADAR_STALE_HOURS else "fresh")
    source_rows = m.get("source_status") if isinstance(m.get("source_status"), list) else []
    has_source_health = bool(source_rows) and m.get("sources_ok") is not None
    tried = m.get("sources_tried", m.get("n_sources"))
    ok = m.get("sources_ok") if has_source_health else None
    failed = m.get("sources_failed") if has_source_health else None
    if failed is None and has_source_health:
        failed = sum(1 for row in source_rows
                     if isinstance(row, dict) and row.get("status") == "error")
    quality = ("degraded" if not has_source_health or int(failed or 0) > 0 else "complete")
    notes = []
    if status == "stale":
        notes.append("雷达快照久未刷新(无人访问资讯页/服务停摆?)")
    if not has_source_health:
        notes.append("旧缓存无逐源成功/失败账；后台刷新后补齐，不能声称所有配置源健康")
    elif int(failed or 0) > 0:
        notes.append(f"缓存新鲜但 {failed}/{tried} 个源本轮失败，其余源结果仍可用")
    coverage = (round(float(ok) / float(tried), 4)
                if isinstance(ok, (int, float)) and isinstance(tried, (int, float)) and tried else None)
    return {"status": status, "quality": quality,
            "pulled_at": m.get("pulled_at"), "age_hours": age_h,
            "n_sources": m.get("n_sources"), "sources_tried": tried,
            "sources_ok": ok, "sources_with_items": m.get("sources_with_items") if has_source_health else None,
            "sources_empty": m.get("sources_empty") if has_source_health else None,
            "sources_failed": failed, "source_coverage_ratio": coverage,
            "cache_schema": m.get("cache_schema"), "note": "；".join(notes)}


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


_ITEMS = {"v4_ranking": _item_v4, "regen_scheduler": _item_regen,
          "core_data_refresh_scheduler": _item_core_data_refresh_scheduler,
          "dl_ensemble": _item_dl,
          "stock_basic": _item_stock_basic, "tencent_live_cache": _item_tencent_cache,
          "pit_store": _item_pit_store, "pit_trust": _item_pit_trust,
          "market_tape": _item_market_tape,
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
_OPS_ITEMS = {"regen_scheduler", "core_data_refresh_scheduler",
              "overseas", "kuaixun", "newsradar", "probe_worker",
              "pit_trust",
              # 旧版手工腾讯缓存已被 live_client 实时门面替代；继续展示兼容状态，
              # 但其缺失不等于当前行情数据缺失。
              "tencent_live_cache"}


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
    warnings = [k for k, v in items.items()
                if v.get("quality") == "degraded" or v.get("required") is False]
    data_status = [v.get("status", "unknown") for k, v in items.items() if k not in _OPS_ITEMS]
    overall = max(data_status, key=lambda s: _STATUS_RANK.get(s, 1), default="unknown")
    return {"ok": True, "generated_at": datetime.now().isoformat(timespec="seconds"),
            "overall": {"status": overall, "stale": stale, "missing": missing,
                        "warnings": warnings},
            "items": items}


def build_legacy_status_compat(
    refresh_items: list[dict[str, Any]], *, market_session: str,
) -> Dict[str, Any]:
    """Build ``/data/status`` without maintaining a second health truth.

    The vendored Buddy UI historically consumes a list of updater timestamp
    rows from ``financial_analyst.data.last_update``.  Those timestamps are
    action receipts, not evidence that the shared Qlib/v4/PIT artifacts are
    fresh; treating their aggregate as system health caused ``/data/status``
    and ``/data/health`` to disagree for months.

    Keep the rows and their aggregate under an explicitly deprecated
    ``refresh_tracker`` compatibility surface.  The canonical ``overall`` and
    ``data_health`` fields are the exact same snapshot returned by
    :func:`collect_data_health`, so new callers have one source of truth.
    Top-level ``items``/``stale_count`` remain for the old UI only.
    """

    rows = [dict(row) for row in refresh_items]
    refresh_stale_count = sum(
        1 for row in rows if row.get("stale") and row.get("implemented")
    )
    health = collect_data_health()
    overall = dict(health.get("overall") or {})
    return {
        "schema_version": "data-status/v2",
        "ok": True,
        "source_of_truth": "/data/health",
        # Deprecated compatibility fields.  Do not use them as fleet health.
        "items": rows,
        "stale_count": refresh_stale_count,
        "any_stale": refresh_stale_count > 0,
        "market_session": market_session,
        "refresh_tracker": {
            "scope": "legacy_workspace_action_receipts",
            "deprecated_for_health": True,
            "stale_count": refresh_stale_count,
            "any_stale": refresh_stale_count > 0,
            "items": rows,
        },
        # Canonical fields: identical semantics and one collection snapshot.
        "overall": overall,
        "canonical_any_stale": overall.get("status") in {"stale", "missing"},
        "data_health": health,
    }
