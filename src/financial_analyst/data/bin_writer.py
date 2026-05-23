"""Qlib 二进制文件读写工具 (vendored from G:/stocks/src/data/bin_writer.py).

不依赖 Qlib 运行时, 直接操作 .bin 文件.

格式: ``[4字节 float32 start_index] + [float32 数据数组]``
目录结构::

    {provider_uri}/
      calendars/{freq}.txt
      instruments/all.txt
      features/{instrument}/
        {field}.{freq}.bin

**核心安全函数**: ``safe_merge_write()`` — 读旧 → 合并 → 写回, **绝不丢失历史数据**.
增量更新**必须**走这个; raw ``write_bin`` 整体覆盖, 只有全量导入场景才能用.

这套工具在 2026-04-14 之后定型 — 那次事故同样的写盘逻辑在 4 个脚本里复刻,
其中一个忘了合并保护直接整体覆盖, 把 5500 只股票 7 个估值字段历史从 ~2500 天
剪到 6 天. 单一入口 + safe_merge_write 强制约束后再没复发.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd


# Windows 保留设备名 (不能作为目录名). 见 microsoft.com/.../naming-files
_RESERVED_NAMES = (
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(10)}
    | {f"LPT{i}" for i in range(10)}
)
_QLIB_PREFIX = "_qlib_"


# ──────────────────────── 代码 ↔ 目录名 ────────────────────────


def code_to_fname(code: str) -> str:
    """股票代码 → bin 目录名 (lowercase, 处理 Windows 保留名).

    Examples:
        >>> code_to_fname("SH600519")
        'sh600519'
        >>> code_to_fname("CON")   # Windows 设备名
        '_qlib_con'
    """
    if str(code).upper() in _RESERVED_NAMES:
        code = _QLIB_PREFIX + str(code)
    return str(code).lower()


def fname_to_code(fname: str) -> str:
    """bin 目录名 → 股票代码 (UPPER)."""
    if fname.startswith(_QLIB_PREFIX):
        fname = fname[len(_QLIB_PREFIX):]
    return fname.upper()


# ──────────────────────── 日历管理 ────────────────────────


def load_calendar(provider_uri: str, freq: str = "day") -> List[str]:
    """加载 ``{provider_uri}/calendars/{freq}.txt``, 返回日期字符串列表 (升序)."""
    cal_path = Path(provider_uri) / "calendars" / f"{freq}.txt"
    if not cal_path.exists():
        return []
    with open(cal_path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def build_calendar_index(calendar: List[str]) -> Dict[str, int]:
    """日历列表 → ``{date_str: position}``. 写 bin 时用这个把日期转成 bin 内位置."""
    return {d: i for i, d in enumerate(calendar)}


def _is_weekend(date_str: str) -> bool:
    from datetime import date as _date
    try:
        y, m, d = map(int, date_str[:10].split("-"))
        return _date(y, m, d).weekday() >= 5
    except Exception:
        return False


def save_calendar(calendar: List[str], provider_uri: str, freq: str = "day") -> None:
    """保存完整日历. day 频率强制过滤周末日期 (A 股不交易)."""
    if freq == "day":
        weekend = [d for d in calendar if _is_weekend(d)]
        if weekend:
            print(f"[bin_writer.save_calendar] 拦截 {len(weekend)} 个周末日期: "
                  f"{weekend[:5]}...")
            calendar = [d for d in calendar if not _is_weekend(d)]
    cal_dir = Path(provider_uri) / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / f"{freq}.txt").write_text("\n".join(calendar) + "\n", encoding="utf-8")


def append_calendar(new_dates: List[str], provider_uri: str, freq: str = "day") -> int:
    """追加新日期到日历 (自动去重 + 排序). 返回实际追加数. day 频率拒绝周末."""
    existing = load_calendar(provider_uri, freq)
    existing_set = set(existing)
    candidates = [d for d in new_dates if d not in existing_set]
    if freq == "day":
        weekend = [d for d in candidates if _is_weekend(d)]
        if weekend:
            print(f"[bin_writer.append_calendar] 拒绝 {len(weekend)} 个周末日期: "
                  f"{weekend[:5]}...")
        candidates = [d for d in candidates if not _is_weekend(d)]
    if not candidates:
        return 0
    save_calendar(sorted(existing + candidates), provider_uri, freq)
    return len(candidates)


# ──────────────────────── Instruments 管理 ────────────────────────


def load_instruments(provider_uri: str, market: str = "all") -> Dict[str, Tuple[str, str]]:
    """加载 ``instruments/{market}.txt``, 返回 ``{code: (start_date, end_date)}``."""
    path = Path(provider_uri) / "instruments" / f"{market}.txt"
    if not path.exists():
        return {}
    out: Dict[str, Tuple[str, str]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                out[parts[0]] = (parts[1], parts[2])
    return out


def save_instruments(instruments: Dict[str, Tuple[str, str]],
                     provider_uri: str, market: str = "all") -> None:
    """保存 instruments. 写法: ``CODE\\tSTART\\tEND``, 按 code 排序."""
    inst_dir = Path(provider_uri) / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"{code}\t{start}\t{end}"
             for code, (start, end) in sorted(instruments.items())]
    (inst_dir / f"{market}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_instrument_range(code: str, start_date: str, end_date: str,
                            provider_uri: str, market: str = "all") -> None:
    """扩展单只股票的 (start, end) 范围. 用于增量后同步 instruments 文件."""
    instruments = load_instruments(provider_uri, market)
    if code in instruments:
        old_start, old_end = instruments[code]
        start_date = min(start_date, old_start)
        end_date = max(end_date, old_end)
    instruments[code] = (start_date, end_date)
    save_instruments(instruments, provider_uri, market)


# ──────────────────────── Bin 文件 I/O ────────────────────────


def _bin_path(instrument: str, field: str, freq: str, provider_uri: str) -> Path:
    return (Path(provider_uri) / "features" / code_to_fname(instrument)
            / f"{field.lower()}.{freq}.bin")


def read_bin(instrument: str, field: str, freq: str,
             provider_uri: str) -> Tuple[int, np.ndarray]:
    """读 .bin 文件, 返回 ``(start_index, float32_array)``.

    不存在或空文件 → ``(0, empty)``. start_index 是该字段在日历里第一个有效位置.
    """
    path = _bin_path(instrument, field, freq, provider_uri)
    if not path.exists():
        return 0, np.array([], dtype="<f")
    raw = np.fromfile(str(path), dtype="<f")
    if len(raw) == 0:
        return 0, np.array([], dtype="<f")
    return int(raw[0]), raw[1:]


def write_bin(instrument: str, field: str, freq: str, provider_uri: str,
              start_index: int, values: np.ndarray) -> None:
    """**⚠ 覆盖模式 — 慎用**. 把旧文件整体替换.

    旧 start_index 之前的历史会被截断丢失. 增量更新**务必**用 ``safe_merge_write``.
    只有以下场景才允许直接 write_bin:
      - 全量导入 (ZIP 源首次写入)
      - 调用端已经在调用前完成合并 (例如 ``safe_merge_write`` 内部)
    """
    path = _bin_path(instrument, field, freq, provider_uri)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.hstack([start_index, values]).astype("<f").tofile(str(path))


def safe_merge_write(instrument: str, field: str, freq: str, provider_uri: str,
                     positions, values) -> None:
    """**安全合并写入** — 增量更新的唯一正确姿势.

    行为:
      - 旧 bin 不存在 → 直接写新数据
      - 新位置完全在旧范围内 → 就地覆盖对应位置
      - 新位置在旧范围之后 → 自动扩展 bin (中间 gap 填 NaN)
      - 新位置跨旧范围前后 → 合并为新范围, 保留旧数据, 新数据覆盖重合位置

    Args:
        positions: list[int] 要写入的日历位置 (必须升序)
        values: list[float] 对应的值 (与 positions 同长度)

    Examples:
        >>> safe_merge_write("SH600519", "pe_ttm", "day", PROVIDER_URI,
        ...                  positions=[8614, 8615, 8616],
        ...                  values=[20.13, 20.05, 19.98])
    """
    positions = list(positions)
    values = list(values)
    if not positions:
        return
    if len(positions) != len(values):
        raise ValueError(f"positions/values 长度不一致: "
                         f"{len(positions)} vs {len(values)}")

    old_si, old_data = read_bin(instrument, field, freq, provider_uri)

    # 旧 bin 空: 直接写
    if len(old_data) == 0:
        first_pos = positions[0]
        last_pos = positions[-1]
        arr = np.full(last_pos - first_pos + 1, np.nan, dtype=np.float32)
        for pos, val in zip(positions, values):
            arr[pos - first_pos] = val
        write_bin(instrument, field, freq, provider_uri, first_pos, arr)
        return

    old_ei = old_si + len(old_data) - 1
    new_first = positions[0]
    new_last = positions[-1]
    merged_si = min(old_si, new_first)
    merged_ei = max(old_ei, new_last)

    merged = np.full(merged_ei - merged_si + 1, np.nan, dtype=np.float32)
    merged[old_si - merged_si: old_si - merged_si + len(old_data)] = old_data
    for pos, val in zip(positions, values):
        merged[pos - merged_si] = val

    write_bin(instrument, field, freq, provider_uri, merged_si, merged)


def get_bin_range(instrument: str, field: str, freq: str,
                  provider_uri: str) -> Tuple[int, int]:
    """快速看 bin 文件的范围 ``(start_index, end_index)``, 不读数据.

    文件不存在或空: ``(0, -1)`` (end < start = 空).
    """
    path = _bin_path(instrument, field, freq, provider_uri)
    if not path.exists():
        return 0, -1
    file_size = path.stat().st_size
    n_values = (file_size // 4) - 1   # 减去 header
    if n_values <= 0:
        return 0, -1
    with open(path, "rb") as f:
        start_index = int(np.frombuffer(f.read(4), dtype="<f")[0])
    return start_index, start_index + n_values - 1
