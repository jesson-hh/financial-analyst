# -*- coding: utf-8 -*-
"""guanlan 大盘状态刷新 + 收盘后自动调度。

- `POST /market_status/refresh` —— 后台线程重生成 `data/market_status.json`(幂等)。
- `GET  /market_status/refresh_state` —— 轮询刷新进度。
- `start_market_status_scheduler()` —— 进程内后台调度:服务启动按需刷一次 + 每日收盘后
  (默认本地 18:00,env `MARKET_STATUS_REFRESH_HOUR`)自动刷一次,免手动点击 / 跑 CLI。

**读** 仍走引擎 `GET /watch/market_status`(经 `MARKET_STATUS_PATH` env 读仓内 json)。
生成器(`guanlan_v2.strategy.market_status`)内含**盘中守卫**:覆盖不完整(n<4500)自动
回退上一完整收盘日,故调度即便在 ingest 半途触发也不会落半天数据。
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import os
import threading
import time

from fastapi import APIRouter

# 进程级刷新状态 + 去重锁(手动 POST 与定时调度共用 → 不并发重算)
_state = {"running": False, "last_error": None, "last_date": None,
          "last_out": None, "last_reason": None, "last_finished": None}
_lock = threading.Lock()
_scheduler_started = False


def _trigger_refresh(reason: str = "manual") -> bool:
    """启动后台重生成线程;幂等。返回 True=已启动, False=已有任务在跑。"""
    with _lock:
        if _state["running"]:
            return False
        _state["running"] = True
    _state["last_error"] = None
    _state["last_reason"] = reason

    def _worker():
        try:
            from guanlan_v2.strategy.market_status import generate
            res = generate()
            _state["last_date"] = res.get("date")
            _state["last_out"] = res.get("out")
            _state["last_finished"] = _dt.datetime.now().isoformat(timespec="seconds")
        except Exception as exc:  # noqa: BLE001 — 失败记台账不崩
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            _state["running"] = False

    threading.Thread(target=_worker, name=f"market-status-refresh-{reason}",
                     daemon=True).start()
    return True


def _json_generated_date():
    """当前 market_status.json 的 generated_at 日期 (YYYY-MM-DD; 无/坏→None)。

    廉价判新用(只读 json, 不扫全市场),让启动期不会每次重启都重算今日已生成的产物。
    """
    try:
        from guanlan_v2.strategy.market_status import default_out_path
        p = default_out_path()
        if not p.exists():
            return None
        with open(p, encoding="utf-8") as f:
            d = _json.load(f)
        return (d.get("generated_at") or "")[:10] or None
    except Exception:  # noqa: BLE001
        return None


def start_market_status_scheduler() -> None:
    """启动后台调度线程(幂等, 进程级只起一次)。

    - 启动期:若 json 缺失或非今日生成 → 刷一次(boot 即新;今日已生成则跳过, 避免每次
      重启都重算)。
    - 每日:本地时间过 `MARKET_STATUS_REFRESH_HOUR`(默认 18 时, 收盘 15:00 + EOD ingest
      之后)触发一次(每日历日一次)。盘中守卫在 generate 内, 早触发也只会落上一完整日。
    """
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    refresh_hour = int(os.environ.get("MARKET_STATUS_REFRESH_HOUR", "18"))
    check_every = max(60, int(os.environ.get("MARKET_STATUS_CHECK_EVERY", "600")))

    def _loop():
        # 启动期按需刷(今日已生成则跳过)
        try:
            if _json_generated_date() != _dt.date.today().isoformat():
                _trigger_refresh("startup")
        except Exception:  # noqa: BLE001
            pass
        last_sched_date = None
        while True:
            try:
                time.sleep(check_every)
                now = _dt.datetime.now()
                if now.hour >= refresh_hour and last_sched_date != now.date():
                    if _trigger_refresh("scheduled"):
                        last_sched_date = now.date()
            except Exception:  # noqa: BLE001 — 调度循环永不因单次异常退出
                continue

    threading.Thread(target=_loop, name="market-status-scheduler", daemon=True).start()


def build_market_router() -> APIRouter:
    """大盘状态刷新路由(guanlan 应用层,不改 engine)。"""
    router = APIRouter()

    @router.post("/market_status/refresh")
    def refresh_market_status():
        """后台重生成 market_status.json;幂等(已在跑则不重复起线程)。"""
        started = _trigger_refresh("manual")
        return {"ok": True, "status": "started" if started else "already_running",
                "last_date": _state["last_date"]}

    @router.get("/market_status/refresh_state")
    def refresh_state():
        """轮询刷新进度(running / last_date / last_reason / last_error)。"""
        return {"ok": True, **_state}

    return router
