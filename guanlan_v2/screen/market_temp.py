# -*- coding: utf-8 -*-
"""市场温度上下文 —— ②决策层护盾 v4.4 的数据组装(全读缓存/快照,请求路径零网络阻塞)。

四块独立组装,单块挂 → 该块 None + notes 记一条,绝不拖垮其余块:
  global —— 全球情绪温度计快照末行(var/macro_pulse/snapshots.jsonl)。
            **绝不调 build_pulse**:它在快照过期时会现拉 Polymarket/Kalshi(网络阻塞),
            这里只读已落盘快照,过期用 stale_min 诚实显形。
  board  —— 打板生态(datafeed.market_tape.read_tape,SWR 秒回缓存,绝不阻塞网络)。
  flow   —— 大盘资金五档(**只读 fundflow live 缓存**:read_live 的冷启动语义=同步阻塞
            真拉,这里先探缓存、缺缓存走后台预热,详见 flow 块注释;fetch_market row 的
            main_net 单位=元,换算成亿命名 main_net_yi)。
  llm    —— LLM 大盘判读(datafeed.sentiment.latest_market,纯文件读零 LLM)。

gate 合成(保守口径,拆成纯函数 _gate 便于测试):
  risk_off = A股打板温度 ≤25(冰点)或 大盘主力净额 ≤-300亿(大幅流出)
  overheat = A股打板温度 ≥85 且 炸板率 ≥0.35(过热且分化)
  温度与主力净额全缺 → gate=None(数据不足护盾休眠,诚实,绝不猜)

g_temp(海外预测市场温度)只作展示不进闸——事件概率的方向语义因主题而异
(锚点 direction 虽已消化单市场方向,但主题覆盖残缺时均值会漂移),
不能当硬信号;进闸的只有 A 股自身的打板温度与主力资金。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional


def _num(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _gate(astock_temp: Any, main_net_yi: Any, break_rate: Any) -> Optional[Dict[str, Any]]:
    """市场温度闸(纯函数):{level, reasons} | None。

    温度与主力净额全缺 → None(护盾休眠);reasons 逐条写触发依据与数值(诚实显形)。
    risk_off 优先于 overheat(两者理论上互斥:温度 ≤25 与 ≥85 不可同真,
    但主力大流出 + 高温并存时按保守取 risk_off)。
    """
    at = _num(astock_temp)
    mn = _num(main_net_yi)
    br = _num(break_rate)
    if at is None and mn is None:
        return None
    reasons: List[str] = []
    if at is not None and at <= 25:
        reasons.append(f"A股打板温度 {at:g} ≤25(冰点)")
    if mn is not None and mn <= -300:
        reasons.append(f"大盘主力净额 {mn:+.1f}亿 ≤-300亿(大幅流出)")
    if reasons:
        return {"level": "risk_off", "reasons": reasons}
    if at is not None and at >= 85 and br is not None and br >= 0.35:
        return {"level": "overheat",
                "reasons": [f"A股打板温度 {at:g} ≥85(过热)且炸板率 {br:.0%} ≥35%(分化)"]}
    return {"level": "neutral", "reasons": []}


def build_market_temp(now: Optional[datetime] = None) -> Dict[str, Any]:
    """组装市场温度上下文:{gate, global, board, flow, llm, notes}。

    四块各自独立 try/except:任一块(含其 import)挂 → 该块 None + note,函数绝不抛。
    """
    ref_now = now or datetime.now()
    notes: List[str] = []

    # ── global:全球情绪温度计快照末行(只读 jsonl,绝不 build_pulse 触网)──
    g: Optional[Dict[str, Any]] = None
    try:
        from guanlan_v2.macro.pulse import _SNAP_DEFAULT, _read_snapshots
        snaps = _read_snapshots(_SNAP_DEFAULT)
        if snaps:
            snap = snaps[-1]
            temps = [v for v in (snap.get("temps") or {}).values()
                     if isinstance(v, (int, float))]
            stale_min = None
            try:
                ts = datetime.strptime(str(snap.get("ts")), "%Y-%m-%dT%H:%M:%S")
                stale_min = round((ref_now - ts).total_seconds() / 60, 1)
            except ValueError:
                pass
            g = {"g_temp": round(sum(temps) / len(temps), 1) if temps else None,
                 "astock_temp": _num(snap.get("astock_temp")),
                 "ts": snap.get("ts"), "stale_min": stale_min}
        else:
            notes.append("global:无温度计快照(macro_pulse 从未成功拉取)")
    except Exception as e:  # noqa: BLE001 — 单块挂不拖垮
        g = None
        notes.append(f"global 块异常: {type(e).__name__}: {e}")

    # ── board:打板生态(read_tape SWR 秒回,warming=首拉未落 → 诚实 None)──
    b: Optional[Dict[str, Any]] = None
    try:
        from guanlan_v2.datafeed.market_tape import read_tape
        t = read_tape()
        if t.get("warming"):
            notes.append("board:盘口快照预热中(后台首拉已触发,本次无数据)")
        else:
            der = t.get("derived") or {}
            b = {"zt_count": der.get("zt_count"), "zb_count": der.get("zb_count"),
                 "break_rate": der.get("break_rate"),
                 "promotion_rate": der.get("promotion_rate"),
                 "age_s": (t.get("freshness") or {}).get("overall_age_s")}
    except Exception as e:  # noqa: BLE001
        b = None
        notes.append(f"board 块异常: {type(e).__name__}: {e}")

    # ── flow:大盘资金五档(**只读缓存**;main_net 元→亿)──
    # read_live 的冷启动语义=同步阻塞真拉(build_live=industry+concept+market 3 个 probe
    # 子进程,live_client 超时 90s/个 → 单次最坏 ~270s),且拉失败不落缓存 → 新部署/var 被清
    # +源拒连时每次 /screen/run 都重复阻塞。故这里先探缓存:有 → read_live 秒回(过期由其
    # 内部后台单飞刷新);无 → 触发后台单飞预热 + 本次诚实 None,read_tape warming 同款范式。
    f: Optional[Dict[str, Any]] = None
    try:
        from guanlan_v2.fundflow import pulse as ff_pulse
        if ff_pulse._load_live_cache("industry") is None:
            ff_pulse._trigger_live_refresh("industry")   # 后台预热(单飞,失败绝不冒泡)
            notes.append("flow:大盘资金无缓存(后台预热已触发,本次无数据)")
        else:
            d = ff_pulse.read_live("industry")
            market = d.get("market") or {}
            if market:
                mn = _num(market.get("main_net"))    # fetch_market row 单位=元
                f = {"main_net_yi": round(mn / 1e8, 2) if mn is not None else None,
                     "pulled_at": d.get("pulled_at")}
            else:
                notes.append("flow:大盘资金五档缺失(源降级)")
    except Exception as e:  # noqa: BLE001
        f = None
        notes.append(f"flow 块异常: {type(e).__name__}: {e}")

    # ── llm:LLM 大盘判读(纯文件读;今日无记录 → None 诚实)──
    llm: Optional[Dict[str, Any]] = None
    try:
        from guanlan_v2.datafeed.sentiment import latest_market
        mk = latest_market()
        llm = {"market_read": mk.get("market_read"), "market_tilt": mk.get("market_tilt"),
               "as_of": mk.get("as_of")}
        if all(v is None for v in llm.values()):
            llm = None
            notes.append("llm:今日无大盘判读记录")
    except Exception as e:  # noqa: BLE001
        llm = None
        notes.append(f"llm 块异常: {type(e).__name__}: {e}")

    gate = _gate((g or {}).get("astock_temp"),
                 (f or {}).get("main_net_yi"),
                 (b or {}).get("break_rate"))
    return {"gate": gate, "global": g, "board": b, "flow": f, "llm": llm, "notes": notes}
