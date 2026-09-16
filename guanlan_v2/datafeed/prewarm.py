# -*- coding: utf-8 -*-
"""启动后台预热(2026-07-28 提速)—— server bind 后把各 SWR 数据面从冷拉到就位。

此前 server 无任何预热,重启后每个页面首开都要等各自的 SWR 冷刷(market_tape 10-26s /
overseas ~25s(批量改造后 ~2s)/ newsradar 最坏 ~1min),本模块等于替用户"先把每页
都点开了一遍":全部走各面现成的公开读接口,SWR 单飞/诚实降级语义原样复用。

红线:fire-and-forget daemon 线程,绝不阻塞 lifespan/事件循环;预热失败静默(各面
自己的 note/stale 显形),绝不伪造新鲜;newsradar 靠 read_radar_swr 的 TTL 条件触发
(磁盘缓存 30min 内秒回；外源数量以运行时 catalog 为准);fundflow 有缓存且盘外时 frozen 短路秒回
不触网,**缓存缺失的冷启动会真做一次首拉建仓**(一次性成本,换切页秒回)。
GUANLAN_DATAFEED_PREWARM=0 可整体禁用。
"""
from __future__ import annotations

import os
import threading
import time

_LOCK = threading.Lock()
_STARTED = [False]


def _run() -> None:
    """预热主体:顺序=用户最可能先开的页;probe 系共享 live_client 起跑闸,串行不添乱。
    每一步(含各模块 import)独立守卫——单面炸绝不拖垮其余面,也绝不带 traceback 冒泡
    (评审:此前三模块 import 在守卫外,一处 ImportError 会吞掉整个预热)。"""

    def _step(fn) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 — 预热失败绝不冒泡,各面自己诚实显形
            pass

    def _tape() -> None:
        from guanlan_v2.datafeed import market_tape
        market_tape.read_tape()                 # 全市场快照(选股决策条/宏观页/研报都吃)

    def _overseas() -> None:
        from guanlan_v2.datafeed import overseas
        overseas.read_overseas()                # 海外看板(批量后 ~2s;有盘缓存则仅补新鲜)

    def _fundflow(kind: str):
        def _pull() -> None:
            from guanlan_v2.fundflow import pulse
            pulse.read_live(kind)               # 有缓存且盘外=frozen 秒回;冷启动一次性首拉
        return _pull

    def _radar() -> None:
        from guanlan_v2.datafeed import newsradar
        newsradar.read_radar_swr()              # TTL 条件触发:缓存 30min 内不重抓；源数取运行时 catalog

    _step(_tape)
    _step(_overseas)
    _step(_fundflow("industry"))
    _step(_fundflow("concept"))
    _step(_radar)


def start_prewarm(delay_s: float = 3.0) -> bool:
    """幂等启动一次预热 daemon 线程(进程生命周期内只跑一趟)。
    返回是否本次真启动;GUANLAN_DATAFEED_PREWARM=0/false/no 禁用。"""
    if os.environ.get("GUANLAN_DATAFEED_PREWARM", "1").strip().lower() in ("0", "false", "no"):
        return False
    with _LOCK:
        if _STARTED[0]:
            return False
        _STARTED[0] = True

    def _delayed() -> None:
        time.sleep(max(0.0, delay_s))   # 让 bind/看门狗心跳先稳,预热不抢启动窗口
        _run()

    try:
        threading.Thread(target=_delayed, daemon=True, name="datafeed-prewarm").start()
    except RuntimeError:
        _STARTED[0] = False
        return False
    return True
