# -*- coding: utf-8 -*-
"""A 股开盘集合竞价只读数据适配器。

两条数据腿各司其职：

* 09:15--09:25 期间，通过本机通达信官方 ``tqcenter.py`` 读取实时全推快照；
* 竞价结束后，通过 pytdx 历史逐笔提取 09:15--09:25 的虚拟撮合轨迹和
  09:25 开盘撮合成交。

本模块不下单、不修改通达信配置，也不把盘后普通盘口冒充成实时竞价。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import date as date_type
from datetime import datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo


_TZ = ZoneInfo("Asia/Shanghai")
_TQ_LOCK = threading.Lock()
_TQ_SENTINEL = "__GUANLAN_TQ_JSON__"
_TQ_CHILD = r"""
import json
import sys

user_dir, strategy_path, stock_code, sentinel = sys.argv[1:5]
sys.path.insert(0, user_dir)
from tqcenter import tq

try:
    tq.initialize(strategy_path)
    payload = tq.get_full_tick(stock_code)
    print(sentinel + json.dumps(payload, ensure_ascii=False))
finally:
    tq.close()
"""


def _now_cn(now: Optional[datetime] = None) -> datetime:
    if now is None:
        return datetime.now(_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=_TZ)
    return now.astimezone(_TZ)


def _normalise_code(code: str) -> Tuple[str, str, int, str]:
    """Return ``(SH600519, 600519.SH, market, 600519)``."""
    from engine.financial_analyst.data.updaters.pytdx_pool import qlib_code_to_pytdx

    raw = str(code or "").strip().upper().replace(".", "")
    if raw.endswith("SH") and raw[:6].isdigit():
        raw = "SH" + raw[:6]
    elif raw.endswith("SZ") and raw[:6].isdigit():
        raw = "SZ" + raw[:6]
    elif raw.endswith("BJ") and raw[:6].isdigit():
        raw = "BJ" + raw[:6]
    market, number = qlib_code_to_pytdx(raw)
    prefix = {1: "SH", 0: "SZ", 2: "BJ"}[market]
    qlib = prefix + number
    return qlib, f"{number}.{prefix}", market, number


def _target_date(value: str, now: datetime) -> date_type:
    raw = str(value or "").strip().replace("-", "")
    if not raw:
        return now.date()
    if len(raw) != 8 or not raw.isdigit():
        raise ValueError("date 必须是 YYYY-MM-DD 或 YYYYMMDD")
    return datetime.strptime(raw, "%Y%m%d").date()


def _float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _local_tq_paths() -> Tuple[Path, Path, Path]:
    """Resolve official local TQ files without importing them into the API process."""
    root = Path(os.environ.get("GUANLAN_TQ_ROOT", r"D:\app\new_test2"))
    user_dir = root / "PYPlugins" / "user"
    module = user_dir / "tqcenter.py"
    # The installed SDK registers a strategy by a path under PYPlugins/user.  The
    # bundled example is inert when not imported and is accepted by InitConnect.
    strategy = Path(os.environ.get("GUANLAN_TQ_STRATEGY", str(user_dir / "tdxdata_test.py")))
    return user_dir, module, strategy


def _tq_python() -> Path:
    configured = os.environ.get("GUANLAN_TQ_PYTHON", "").strip()
    if configured:
        return Path(configured)
    bundled = Path(r"D:\app\miniconda\python.exe")
    return bundled if bundled.is_file() else Path(sys.executable)


def _run_tq_full_tick(tq_code: str) -> Dict[str, Any]:
    user_dir, module, strategy = _local_tq_paths()
    python_exe = _tq_python()
    missing = [str(p) for p in (python_exe, module, strategy) if not p.is_file()]
    if missing:
        raise RuntimeError("本机 TQ 文件缺失: " + ", ".join(missing))
    try:
        timeout_s = max(3.0, min(float(os.environ.get("GUANLAN_TQ_TIMEOUT_S", "15")), 75.0))
    except ValueError:
        timeout_s = 15.0

    # TPythClient uses the strategy path as a connection identity.  Serialize
    # calls in this process and isolate the vendor DLL in a child process so a
    # DLL error cannot take down the 9999 API/Agent runtime.
    with _TQ_LOCK:
        completed = subprocess.run(
            [str(python_exe), "-c", _TQ_CHILD, str(user_dir), str(strategy), tq_code, _TQ_SENTINEL],
            cwd=str(user_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    payload_line = next(
        (line for line in reversed(completed.stdout.splitlines()) if line.startswith(_TQ_SENTINEL)),
        "",
    )
    if completed.returncode != 0 or not payload_line:
        detail = (completed.stderr or completed.stdout or "TQ 未返回数据").strip()[-800:]
        raise RuntimeError(f"本机 TQ 调用失败(exit={completed.returncode}): {detail}")
    payload = json.loads(payload_line[len(_TQ_SENTINEL):])
    if not isinstance(payload, dict) or str(payload.get("ErrorId", "0")) != "0":
        raise RuntimeError(f"本机 TQ 返回错误: {payload}")
    return payload


def read_tq_snapshot(
    code: str,
    *,
    now: Optional[datetime] = None,
    fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Read one official local-TQ snapshot and label whether it is auction-qualified."""
    captured = _now_cn(now)
    qlib, tq_code, _market, _number = _normalise_code(code)
    report = (fetcher or _run_tq_full_tick)(tq_code)
    bid_prices, bid_volumes = report.get("Buyp") or [], report.get("Buyv") or []
    ask_prices, ask_volumes = report.get("Sellp") or [], report.get("Sellv") or []
    levels: List[Dict[str, Any]] = []
    for idx in range(max(len(bid_prices), len(ask_prices), 5)):
        if idx >= 5:
            break
        levels.append({
            "level": idx + 1,
            "bid": _float(bid_prices[idx]) if idx < len(bid_prices) else None,
            "bid_vol_lots": _int(bid_volumes[idx]) if idx < len(bid_volumes) else None,
            "ask": _float(ask_prices[idx]) if idx < len(ask_prices) else None,
            "ask_vol_lots": _int(ask_volumes[idx]) if idx < len(ask_volumes) else None,
        })
    clock = captured.timetz().replace(tzinfo=None)
    in_window = captured.weekday() < 5 and time(9, 15) <= clock < time(9, 25)
    reported_now = _float(report.get("Now"))
    indicative_price = reported_now
    indicative_price_basis = "tq_now"
    common_level1_volume_lots = None
    if in_window and levels:
        level1 = levels[0]
        bid1, ask1 = level1.get("bid"), level1.get("ask")
        # 本机 TQ/pytdx 在开盘集合竞价时会出现 Now=0、但买一=卖一=虚拟
        # 撮合价的结构。两条独立 TDX 腿实测一致；只在竞价窗口且两价相等时
        # 采用此窄口径回退，避免把普通盘中的交叉/坏报价误作参考价。
        if (indicative_price is None or indicative_price <= 0) \
                and bid1 is not None and bid1 > 0 and bid1 == ask1:
            indicative_price = bid1
            indicative_price_basis = "tdx_call_auction_bid1_equals_ask1"
            bid_lots, ask_lots = level1.get("bid_vol_lots"), level1.get("ask_vol_lots")
            if bid_lots is not None and ask_lots is not None and bid_lots == ask_lots:
                common_level1_volume_lots = bid_lots
    last_close = _float(report.get("LastClose"))
    indicative_change_pct = (
        round((indicative_price / last_close - 1.0) * 100.0, 4)
        if indicative_price is not None and last_close not in (None, 0) else None
    )
    return {
        "ok": True,
        "code": qlib,
        "date": captured.date().isoformat(),
        "mode": "live_call_auction" if in_window else "latest_quote_snapshot",
        "source": "tdx_local_tq",
        "is_call_auction": in_window,
        "captured_at": captured.isoformat(timespec="seconds"),
        "indicative_price": indicative_price,
        "indicative_price_basis": indicative_price_basis,
        "reported_now": reported_now,
        "indicative_change_pct": indicative_change_pct,
        "common_level1_volume_lots": common_level1_volume_lots,
        "last_close": last_close,
        "open": _float(report.get("Open")),
        "high": _float(report.get("Max")),
        "low": _float(report.get("Min")),
        "volume_lots": _int(report.get("Volume")),
        "amount_10k_cny": _float(report.get("Amount")),
        "latest_volume_lots": _int(report.get("NowVol")),
        "inside_lots": _int(report.get("Inside")),
        "outside_lots": _int(report.get("Outside")),
        "levels": levels,
        "note": (
            ("当前处于 09:15-09:25；TQ Now=0，参考价由买一=卖一的 TDX 竞价结构回退；"
             "共同一档量为挂单展示量，非已成交量。五档量单位为手。"
             if indicative_price_basis == "tdx_call_auction_bid1_equals_ask1" else
             "当前处于 09:15-09:25，Now 按实时竞价参考价解读；五档量单位为手。")
            if in_window else
            "当前不在 09:15-09:25；这是本机 TQ 最新报价快照，不能称为实时竞价。"
        ),
        "raw": report,
    }


def _fetch_history_rows(market: int, number: str, date_int: int) -> List[Dict[str, Any]]:
    """Fetch pages backwards until the 09:15 boundary is reached."""
    from engine.financial_analyst.data.updaters.pytdx_pool import PytdxClient

    client = PytdxClient()
    rows: List[Dict[str, Any]] = []
    page_size = 2000
    try:
        for page in range(40):
            chunk = client.call(
                "get_history_transaction_data", market, number, page * page_size, page_size, date_int
            ) or []
            rows.extend(dict(row) for row in chunk if isinstance(row, dict))
            times = [str(row.get("time") or "")[:5] for row in chunk if isinstance(row, dict)]
            if len(chunk) < page_size or any(t and t <= "09:15" for t in times):
                break
    finally:
        client.close()
    return rows


def read_history_auction(
    code: str,
    target: date_type,
    *,
    fetcher: Optional[Callable[[int, str, int], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Extract the historical virtual-price path and the 09:25 opening match."""
    qlib, _tq_code, market, number = _normalise_code(code)
    date_int = int(target.strftime("%Y%m%d"))
    raw_rows = (fetcher or _fetch_history_rows)(market, number, date_int) or []
    selected: List[Tuple[int, Dict[str, Any]]] = []
    for seq, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            continue
        hhmm = str(row.get("time") or "")[:5]
        if "09:15" <= hhmm <= "09:25":
            selected.append((seq, row))
    selected.sort(key=lambda item: (str(item[1].get("time") or ""), item[0]))

    points: List[Dict[str, Any]] = []
    for _seq, row in selected:
        hhmm = str(row.get("time") or "")[:5]
        volume = _int(row.get("vol") if row.get("vol") is not None else row.get("volume")) or 0
        side = _int(row.get("buyorsell"))
        kind = "opening_match" if hhmm == "09:25" and volume > 0 else "virtual_match"
        points.append({
            "time": str(row.get("time") or ""),
            "price": _float(row.get("price")),
            "vol_lots": volume,
            "kind": kind,
            "tdx_side": side,
        })

    virtual = [point for point in points if point["kind"] == "virtual_match"]
    matches = [point for point in points if point["kind"] == "opening_match"]
    opening_match = None
    if matches:
        opening_match = {
            "time": matches[-1]["time"],
            "price": matches[-1]["price"],
            "vol_lots": sum(int(point["vol_lots"] or 0) for point in matches),
        }
    start_price = next((point["price"] for point in virtual if point["price"] is not None), None)
    final_price = next((point["price"] for point in reversed(virtual) if point["price"] is not None), None)
    change_pct = (
        round((final_price / start_price - 1.0) * 100.0, 4)
        if start_price not in (None, 0) and final_price is not None else None
    )
    ok = bool(points)
    return {
        "ok": ok,
        "code": qlib,
        "date": target.isoformat(),
        "mode": "postmarket_call_auction",
        "source": "tdx_public_history_transaction",
        "is_call_auction": ok,
        "auction_window": "09:15-09:25",
        "points": points,
        "opening_match": opening_match,
        "metrics": {
            "observation_count": len(points),
            "virtual_observation_count": len(virtual),
            "start_virtual_price": start_price,
            "final_virtual_price": final_price,
            "virtual_path_change_pct": change_pct,
        },
        "note": (
            "TDX 历史逐笔：09:15-09:24 的 vol=0/side=8 行按虚拟撮合轨迹展示；"
            "09:25 有量行是开盘集合竞价成交，量单位为手。该数据不含未匹配买卖量。"
            if ok else
            "该日期未取得 09:15-09:25 逐笔，可能是非交易日、代码无成交或 TDX 历史源不可用。"
        ),
    }


def read_auction(
    code: str,
    date: str = "",
    mode: str = "auto",
    *,
    now: Optional[datetime] = None,
    tq_fetcher: Optional[Callable[[str], Dict[str, Any]]] = None,
    history_fetcher: Optional[Callable[[int, str, int], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Unified entry used by HTTP and ``ww_auction`` Agent tool."""
    current = _now_cn(now)
    try:
        target = _target_date(date, current)
        selected_mode = str(mode or "auto").strip().lower()
        if selected_mode not in {"auto", "live", "history"}:
            raise ValueError("mode 只能是 auto、live 或 history")
        in_window = (
            target == current.date()
            and current.weekday() < 5
            and time(9, 15) <= current.timetz().replace(tzinfo=None) < time(9, 25)
        )
        if selected_mode == "live" or (selected_mode == "auto" and in_window):
            return read_tq_snapshot(code, now=current, fetcher=tq_fetcher)
        return read_history_auction(code, target, fetcher=history_fetcher)
    except Exception as exc:  # noqa: BLE001 - data source errors must degrade honestly
        return {
            "ok": False,
            "code": str(code or "").strip().upper(),
            "date": str(date or current.date().isoformat()),
            "mode": str(mode or "auto"),
            "source": "tdx_local_tq/tdx_public_history_transaction",
            "is_call_auction": False,
            "points": [],
            "opening_match": None,
            "note": f"竞价数据不可用: {type(exc).__name__}: {exc}",
        }
