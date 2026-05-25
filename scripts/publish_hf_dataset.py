"""publish_hf_dataset.py — 把 financial-analyst 历史数据打包发到 HuggingFace.

用户首次启动 fa init 时从 HF 下这些包, 无需 Tushare token. 之后日常增量
更新走 fa data update (pytdx 主站直连).

三档预设包 (--preset):
  demo   ~500 MB  csi300 (300 只) 全历史日线 + 当前 daily_basic 快照
  lite     ~5 GB  csi800 (800 只) 全历史 + 5min ~7 天
  full   ~50 GB  全 A 股 (5500+) 完整历史 + 5min + financials

或自定义 --codes-file my.txt 控制 universe.

用法::

    # dry-run 看会上传啥
    python scripts/publish_hf_dataset.py --preset demo --dry-run

    # 真上传 (需要 HUGGINGFACE_TOKEN 环境变量)
    python scripts/publish_hf_dataset.py --preset demo --repo yifishbossman/financial-analyst-data-demo
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import date as _date
from pathlib import Path
from typing import List, Optional, Tuple

# UTF-8 console
for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

sys.path.insert(0, "G:/financial-analyst/src")

from financial_analyst.data.bin_writer import (
    code_to_fname, load_calendar, load_instruments, save_calendar,
    save_instruments,
)


# ──────────────────────── 预设 universe ────────────────────────


# 各档 parquet 白名单. demo 只挑轻量 schema (KB-MB), lite 加财务 (~750MB),
# full 加 TDX 历年财务原始 zip (~257MB). 路径相对 stock_data/parquet/ 根
# (tdx_finance/ 是 stock_data 同级, 单独走 _stage_tdx_finance).
_PARQUET_DEMO = [
    "industry_boards.parquet",        # 同花顺行业 (~39KB / 496 行)
    "concept_ths_constituent.parquet", # 概念-成份映射
    "concept_ths_index.parquet",      # 概念索引 (~13KB / 536 行)
    "index_constituents.parquet",     # CSI300/500 成份 (~83KB / 2600 行)
    "tdx_f10_index.parquet",          # F10 索引 (~263KB / 5122 行)
    "tdx_f10_warnings_latest.parquet", # 最新负向预警 (~7KB / 66 行)
    "tushare_stock_basic.parquet",    # 股票基本 (~129KB / 5502 行)
    "northbound_holding.parquet",     # 北向资金 (~192KB / 2767 行)
    "instruments.parquet",            # 仪表 universe
    "ipo_info.parquet",               # IPO
    "fincast_daily_pred.parquet",     # FinCast 模型预测 (research artifact)
    "events/",                        # 公司事件 (~0.3MB)
    "institutional/",                 # 机构持仓 (~1.8MB)
    "blocks/",                        # 板块映射 (~6.1MB)
    "xdxr/",                          # 分红除权 (~3.4MB)
]
_PARQUET_LITE = _PARQUET_DEMO + [
    "financial/",                     # 全部财报 (~735MB) — 研报必需
]
_PARQUET_FULL = _PARQUET_LITE + [
    # news 当前几乎空 (0 MB), 跳过. 未来填充再加.
]

PRESETS = {
    "demo": {
        "size_hint":  "~515 MB",
        "method":     "top-mv",   # 用 Tencent 实时拉当前 mv top N
        "n_codes":    300,        # 真当前 csi300 (而非 Qlib 历史累积 939)
        "market":     "all",      # universe pool: instruments/all.txt
        "include_5min": False,
        "parquet_include": _PARQUET_DEMO,
        "tdx_finance_zip": False,
        "description": "当前 mv top-300 演示包. 全历史日线 + 估值 + 行业/概念/F10/北向 "
                       "等核心 parquet (~15MB). 无 5min, 无完整财务报表. 上手试用.",
    },
    "lite": {
        "size_hint":  "~6 GB",
        "method":     "top-mv",
        "n_codes":    800,        # 当前 mv top-800 ≈ csi800
        "market":     "all",
        "include_5min": True,
        "parquet_include": _PARQUET_LITE,
        "tdx_finance_zip": False,
        "tdx_f10_text": True,     # 加 news_data/tdx_f10/ 全 296MB F10 原始文本
        "description": "当前 mv top-800 半技术用户包. demo 全 + 5min ~7天 + 完整财务报表 "
                       "(~735MB) + F10 原始文本 (~296MB). 适合跑 fa report 跨多股研报.",
    },
    "full": {
        "size_hint":  "~14 GB",
        "method":     "instruments",  # 全 5500+ 用 instruments 文件
        "market":     "all",
        "n_codes":    None,
        "include_5min": True,
        "parquet_include": _PARQUET_FULL,
        "tdx_finance_zip": True,      # 加 TDX 历年财报原始 (~257MB zip)
        "tdx_f10_text": True,         # 加 news_data/tdx_f10/ 全 296MB F10 原始文本
        "description": "全 A 股完整包 (含历史退市股). 量化研究员 / 重度用户. "
                       "lite 全 + TDX 历年财报原始 zip (用户跑 import_tdx_financial.py 解) "
                       "+ F10 原始文本 (公司大事/龙虎榜/主力追踪/最新提示 .txt).",
    },
}


# ──────────────────────── universe 解析 ────────────────────────


def _resolve_universe_by_top_mv(source_uri: str, market: str, top_n: int) -> List[str]:
    """从 instruments/{market}.txt 池里抽 N 只当前 mv 最大的 (走 Tencent 实时).

    几千只股票一次 80 只一批, 总 ~70 batch ~ 14s. 比走过期的 csi300 历史累积准确.
    """
    sys.path.insert(0, "G:/financial-analyst/src")
    from financial_analyst.data.collectors.tencent_quote import TencentQuoteCollector

    inst = load_instruments(source_uri, market=market)
    all_codes = sorted(inst.keys())
    if not all_codes:
        raise SystemExit(f"instruments/{market}.txt 是空")

    print(f"  从 {len(all_codes)} 只池里拉 Tencent 实时 mv (chunk 80, ~14s)...")
    collector = TencentQuoteCollector()
    code_mv: List[tuple] = []
    chunk_size = 80
    for i in range(0, len(all_codes), chunk_size):
        chunk = all_codes[i: i + chunk_size]
        try:
            quotes = collector.fetch(chunk, timeout=10.0)
            for code in chunk:
                q = quotes.get(code)
                if q and q.get("total_mv"):
                    code_mv.append((code, float(q["total_mv"])))
        except Exception as e:
            print(f"    ⚠ chunk {i}-{i+chunk_size} 失败: {e}")

    code_mv.sort(key=lambda x: -x[1])   # mv 降序
    selected = code_mv[:top_n]
    print(f"  抽出 top {len(selected)} 只 (preview):")
    sample_ranks = [1, 5, 50, 100, 300]
    for rank in sample_ranks:
        if rank <= len(selected):
            code, mv = selected[rank - 1]
            print(f"    #{rank:>4}: {code}  ¥{mv:>10.0f}亿")
    if selected[-1] != (selected[sample_ranks[-1] - 1] if sample_ranks[-1] <= len(selected) else None):
        print(f"    #{len(selected):>4}: {selected[-1][0]}  ¥{selected[-1][1]:>10.0f}亿  (tail)")
    return [c for c, _ in selected]


def _resolve_universe(args, source_uri: str) -> List[str]:
    """根据 args 选 universe 代码列表.

    优先级: --codes-file > --top-mv-n > --preset (走 instruments 或 top-mv)
    """
    if args.codes_file:
        return [line.strip().upper() for line in Path(args.codes_file).read_text(
            encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]

    if args.top_mv_n is not None:
        market = args.market or "all"
        return _resolve_universe_by_top_mv(source_uri, market, args.top_mv_n)

    if args.preset:
        preset = PRESETS[args.preset]
        method = preset.get("method", "instruments")

        if method == "top-mv":
            return _resolve_universe_by_top_mv(
                source_uri, preset["market"], preset["n_codes"]
            )

        # method == "instruments" (legacy)
        market = preset["market"]
        inst = load_instruments(source_uri, market=market)
        if not inst:
            raise SystemExit(
                f"找不到 instruments/{market}.txt 在 {source_uri}. "
                f"--preset {args.preset} 需要这个列表存在."
            )
        codes = sorted(inst.keys())
        expected = preset["n_codes"]
        if expected and abs(len(codes) - expected) > expected * 0.2:
            print(f"  ⚠ {market}.txt 有 {len(codes)} 只, 预期 {expected}. 继续.")
        return codes

    raise SystemExit("Need --preset {demo|lite|full} or --codes-file FILE or --top-mv-n N")


# ──────────────────────── staging 打包 ────────────────────────


def _stage_dataset(source_day: str, source_5min: Optional[str],
                   codes: List[str], staging: Path,
                   include_5min: bool) -> dict:
    """把 source_day + source_5min 里 codes 子集 copy 到 staging.

    staging 内布局:
      cn_data/
        calendars/day.txt
        instruments/all.txt
        features/{code}/*.day.bin
      cn_data_5min/
        calendars/5min.txt
        instruments/all.txt
        features/{code}/*.5min.bin
      README.md (auto-generated dataset card)
    """
    stats = {"day_codes": 0, "day_files": 0, "day_bytes": 0,
             "5min_codes": 0, "5min_files": 0, "5min_bytes": 0,
             "skipped": []}

    # day
    day_dst = staging / "cn_data"
    (day_dst / "calendars").mkdir(parents=True, exist_ok=True)
    (day_dst / "instruments").mkdir(parents=True, exist_ok=True)
    (day_dst / "features").mkdir(parents=True, exist_ok=True)

    # calendar + instruments
    day_cal_src = Path(source_day) / "calendars" / "day.txt"
    if day_cal_src.exists():
        shutil.copy2(day_cal_src, day_dst / "calendars" / "day.txt")

    src_inst = load_instruments(source_day, market="all")
    selected_inst = {c: src_inst[c] for c in codes if c in src_inst}
    save_instruments(selected_inst, str(day_dst), market="all")
    print(f"  day instruments: {len(selected_inst)}/{len(codes)} (源里有的部分)")

    # 每只 code 的 features
    for i, code in enumerate(codes, 1):
        if code not in src_inst:
            stats["skipped"].append(code)
            continue
        src_features = Path(source_day) / "features" / code_to_fname(code)
        if not src_features.exists():
            stats["skipped"].append(code)
            continue
        dst_features = day_dst / "features" / code_to_fname(code)
        dst_features.mkdir(parents=True, exist_ok=True)
        for bin_file in src_features.glob("*.day.bin"):
            shutil.copy2(bin_file, dst_features / bin_file.name)
            stats["day_files"] += 1
            stats["day_bytes"] += bin_file.stat().st_size
        stats["day_codes"] += 1
        if i % 100 == 0:
            print(f"    [day {i}/{len(codes)}] copied {stats['day_files']} bin, "
                  f"{stats['day_bytes']/1e6:.0f} MB")

    # 5min
    if include_5min and source_5min:
        m5_dst = staging / "cn_data_5min"
        (m5_dst / "calendars").mkdir(parents=True, exist_ok=True)
        (m5_dst / "instruments").mkdir(parents=True, exist_ok=True)
        (m5_dst / "features").mkdir(parents=True, exist_ok=True)

        m5_cal_src = Path(source_5min) / "calendars" / "5min.txt"
        if m5_cal_src.exists():
            shutil.copy2(m5_cal_src, m5_dst / "calendars" / "5min.txt")

        src_m5_inst = load_instruments(source_5min, market="all")
        selected_m5 = {c: src_m5_inst[c] for c in codes if c in src_m5_inst}
        save_instruments(selected_m5, str(m5_dst), market="all")
        print(f"  5min instruments: {len(selected_m5)}/{len(codes)}")

        for i, code in enumerate(codes, 1):
            if code not in src_m5_inst: continue
            src_features = Path(source_5min) / "features" / code_to_fname(code)
            if not src_features.exists(): continue
            dst_features = m5_dst / "features" / code_to_fname(code)
            dst_features.mkdir(parents=True, exist_ok=True)
            for bin_file in src_features.glob("*.5min.bin"):
                shutil.copy2(bin_file, dst_features / bin_file.name)
                stats["5min_files"] += 1
                stats["5min_bytes"] += bin_file.stat().st_size
            stats["5min_codes"] += 1
            if i % 200 == 0:
                print(f"    [5min {i}/{len(codes)}] copied {stats['5min_files']} bin, "
                      f"{stats['5min_bytes']/1e6:.0f} MB")

    return stats


# ──────────────────────── parquet 子集打包 ────────────────────────


def _stage_parquet(parquet_root: str, items: List[str], staging: Path) -> dict:
    """copy 指定 parquet 文件/子目录 到 staging/parquet/.

    items 元素是相对 ``parquet_root`` 的路径, 末尾 ``/`` 表示子目录.
    路径里的层级在 staging 端原样保留.
    """
    stats = {"parquet_files": 0, "parquet_bytes": 0, "skipped": []}
    src_root = Path(parquet_root)
    pq_dst = staging / "parquet"
    pq_dst.mkdir(parents=True, exist_ok=True)

    for item in items:
        src = src_root / item.rstrip("/")
        if not src.exists():
            stats["skipped"].append(item)
            continue
        dst = pq_dst / item.rstrip("/")
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
            for f in dst.rglob("*"):
                if f.is_file():
                    stats["parquet_files"] += 1
                    stats["parquet_bytes"] += f.stat().st_size
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            stats["parquet_files"] += 1
            stats["parquet_bytes"] += src.stat().st_size
    return stats


def _stage_tdx_finance(tdx_finance_root: str, staging: Path) -> dict:
    """copy 117 个 TDX 历年财报 zip 到 staging/tdx_finance/.
    Full preset 才用. 用户解包用 import_tdx_financial.py."""
    stats = {"tdx_zip_files": 0, "tdx_zip_bytes": 0}
    src_root = Path(tdx_finance_root)
    if not src_root.exists():
        return stats
    dst = staging / "tdx_finance"
    dst.mkdir(parents=True, exist_ok=True)
    for zf in src_root.glob("*.zip"):
        shutil.copy2(zf, dst / zf.name)
        stats["tdx_zip_files"] += 1
        stats["tdx_zip_bytes"] += zf.stat().st_size
    return stats


def _stage_tdx_f10(tdx_f10_root: str, staging: Path) -> dict:
    """copy news_data/tdx_f10/{code}/ 全 296MB F10 原始文本到 staging/news_data/tdx_f10/.

    Lite/Full 都用. 每只股票一目录, 含 公司大事/龙虎榜单/主力追踪/最新提示 等 .txt.
    跟 parquet 里 tdx_f10_index.parquet (索引) 配合, agent 拿事件 metadata 后
    可读 .txt 内容做详细分析.
    """
    stats = {"tdx_f10_files": 0, "tdx_f10_bytes": 0}
    src_root = Path(tdx_f10_root)
    if not src_root.exists():
        print(f"  ⚠ tdx_f10 src 不存在: {src_root}, skip")
        return stats
    dst = staging / "news_data" / "tdx_f10"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_root, dst, dirs_exist_ok=True)
    for f in dst.rglob("*"):
        if f.is_file():
            stats["tdx_f10_files"] += 1
            stats["tdx_f10_bytes"] += f.stat().st_size
    return stats


# ──────────────────────── dataset card ────────────────────────


def _gen_dataset_card(preset_name: Optional[str], codes: List[str],
                      stats: dict, day_cal: List[str], freq_5min: bool) -> str:
    """生成 README.md (HF dataset card) — 中英双语."""
    today = _date.today().isoformat()
    preset_info = PRESETS.get(preset_name, {})

    day_range = (f"{day_cal[0]} → {day_cal[-1]}" if day_cal else "?")
    n_days = len(day_cal)
    pq_bytes = stats.get("parquet_bytes", 0)
    pq_files = stats.get("parquet_files", 0)
    tdx_bytes = stats.get("tdx_zip_bytes", 0)
    tdx_files = stats.get("tdx_zip_files", 0)
    f10_bytes = stats.get("tdx_f10_bytes", 0)
    f10_files = stats.get("tdx_f10_files", 0)
    total_gb = (stats["day_bytes"] + stats["5min_bytes"] + pq_bytes
                + tdx_bytes + f10_bytes) / 1e9

    has_5min = "✅" if freq_5min else "❌"
    has_fin = "✅" if pq_bytes > 100e6 else "❌"
    has_f10 = "✅" if f10_bytes > 0 else "❌"
    has_tdx_zip = "✅" if tdx_bytes > 0 else "❌"
    repo_suffix = ('-' + preset_name) if preset_name else ''

    return f"""---
license: apache-2.0
language:
- zh
- en
size_categories:
- 1K<n<10K
task_categories:
- time-series-forecasting
- tabular-classification
tags:
- finance
- a-share
- chinese-stocks
- qlib
- quantitative-trading
- bilingual
pretty_name: "financial-analyst-data-{preset_name or 'custom'}"
---

# financial-analyst-data{repo_suffix}

> **EN**: A-share historical OHLCV + valuation + financials + TDX F10 events, packaged in Qlib binary + Parquet formats. Companion dataset for [**financial-analyst**](https://github.com/jesson-hh/financial-analyst) — a 16-agent single-stock deep-dive research workstation.
>
> **中文**: A 股历史行情 + 估值 + 财报 + TDX F10 事件数据集, Qlib 二进制 + Parquet 双格式打包. 配套 [**financial-analyst**](https://github.com/jesson-hh/financial-analyst) — 14 Agent 个股深度研究工作站使用.

**Published / 发布**: {today}  · **Size / 体量**: ~{total_gb:.2f} GB  · **License**: Apache 2.0

---

## 📊 Three Preset Tiers / 三档预设

Pick the tier that fits your use case. / 按需选择合适档位.

| | **demo** | **lite** | **full** |
|---|---|---|---|
| Size / 体量 | ~155 MB | ~3 GB | ~14 GB |
| Stocks / 股票池 | 300 (current CSI300 by mv) | 800 (CSI800 ≈) | 5500+ (all A-share incl. delisted) |
| Daily OHLCV+估值 | ✅ | ✅ | ✅ |
| 5min OHLCV | ❌ | ✅ (~7 days) | ✅ |
| Financial reports / 财务报表 | ❌ | ✅ (735 MB) | ✅ |
| F10 text / F10 原始文本 | ❌ | ✅ (1323 codes) | ✅ |
| TDX 历年财报 zip | ❌ | ❌ | ✅ (257 MB) |
| Best for / 适合 | 试用 / try-out | `fa report` 研报 / multi-stock research | 量化研究 / quant research |
| HF Repo | [data-demo](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-demo) | [data-lite](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-lite) | [data-full](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-full) |

**This repo is the `{preset_name or 'custom'}` tier.** / **此 repo 是 `{preset_name or 'custom'}` 档.**

---

## 📦 What's Included / 数据清单

### English

- **{stats['day_codes']} stocks** daily OHLCV + 7 valuation fields (PE / PB / PS / DV / MV / CIRC_MV / turnover_rate)
- **Date range (daily)**: {day_range} ({n_days} trading days)
- **5min OHLCV** {has_5min}  ({stats['5min_codes']} stocks, {stats['5min_bytes']/1e9:.2f} GB)
- **Financial reports** (parquet/financial/) {has_fin}  ({pq_files} parquet files total, {pq_bytes/1e6:.1f} MB including all parquet)
- **F10 text** (news_data/tdx_f10/) {has_f10}  ({f10_files} .txt files, {f10_bytes/1e6:.1f} MB)
- **TDX historical financial zip** (tdx_finance/) {has_tdx_zip}  ({tdx_files} zip files, {tdx_bytes/1e6:.1f} MB)

### 中文

- **{stats['day_codes']} 只股票** 日线 OHLCV + 7 个估值字段 (市盈率/市净率/市销率/股息率/总市值/流通市值/换手率)
- **日线日期范围**: {day_range} ({n_days} 个交易日)
- **5min 行情** {has_5min}  ({stats['5min_codes']} 只, {stats['5min_bytes']/1e9:.2f} GB)
- **完整财务报表** (parquet/financial/) {has_fin}  ({pq_files} 个 parquet, 共 {pq_bytes/1e6:.1f} MB)
- **F10 原始文本** (news_data/tdx_f10/) {has_f10}  ({f10_files} 个 .txt, {f10_bytes/1e6:.1f} MB) — 公司大事 / 龙虎榜单 / 主力追踪 / 最新提示
- **TDX 历年财报 zip** (tdx_finance/) {has_tdx_zip}  ({tdx_files} 个 zip, {tdx_bytes/1e6:.1f} MB) — 用 `scripts/import_tdx_financial.py` 解

---

## 🗂 Directory Layout / 目录布局

```
cn_data/                          # daily — Qlib binary
  calendars/day.txt               # trading calendar / 交易日历
  instruments/all.txt             # {stats['day_codes']} codes
  features/{{code}}/              # one dir per stock, e.g. sh600519/
    open.day.bin                  # [4-byte float32 start_idx] + [float32 array]
    high.day.bin
    low.day.bin
    close.day.bin                 # 不复权收盘 / unadjusted close
    volume.day.bin                # 手 / lots (= 100 shares)
    amount.day.bin                # 元 / CNY
    pe_ttm.day.bin                # TTM PE ratio
    pb.day.bin
    ps_ttm.day.bin                # may be NaN for some history
    dv_ttm.day.bin                # dividend yield %, may be NaN
    total_mv.day.bin              # 万元 / 10K CNY
    circ_mv.day.bin               # 流通市值万元 / circulating mv (10K CNY)
    turnover_rate.day.bin         # %
{'cn_data_5min/                     # 5-minute — same Qlib binary layout, rolling ~7 days' if freq_5min else ''}
parquet/                          # 非时序结构化数据 / non-time-series structured data
  industry_boards.parquet         # 同花顺一级行业 / 10jqka level-1 industry
  index_constituents.parquet      # CSI300 / CSI500 成份 / constituents
  tdx_f10_index.parquet           # F10 事件索引 (公司大事/龙虎榜/研报)
  tdx_f10_warnings_latest.parquet # 最新负向预警 / latest warning events
  northbound_holding.parquet      # 北向资金持仓 / northbound stake
  tushare_stock_basic.parquet     # 股票基本信息 / basic listing info
  concept_ths_*.parquet           # 同花顺概念 / 10jqka concept boards
  events/                         # 公司公告 / company filings
  institutional/                  # 机构持仓 / institutional holders
  blocks/                         # 板块映射 / sector mappings
  xdxr/                           # 分红除权 / dividends + splits
{'  financial/                      # 完整财务报表 (~735MB) — 资产/负债/利润/现金流/指标' if pq_bytes > 100e6 else ''}
{'tdx_finance/                      # TDX 历年财报原始 zip (用户解压用 scripts/import_tdx_financial.py)' if tdx_bytes > 0 else ''}
{'news_data/tdx_f10/{{code}}/         # F10 原始 .txt — 跟 tdx_f10_index 配合用' if f10_bytes > 0 else ''}
```

---

## 🚀 Usage / 使用方法

### Option 1 — via `financial-analyst` CLI (recommended / 推荐)

**EN**: Easiest. The CLI handles download, path setup, and integration.

**中文**: 最简. CLI 自动下载、配置路径、衔接 agent.

```bash
pip install financial-analyst

# Interactive wizard, picks this dataset / 交互向导自动用本数据集
fa init

# Generate a deep-dive research report on 茅台 / 跑研报
fa report SH600519
```

### Option 2 — Direct download + Qlib

**EN**: Pull the dataset with `huggingface_hub`, then use Qlib's `D.features()` API.

**中文**: 用 `huggingface_hub` 下载, 用 Qlib `D.features()` API 读数据.

```python
from huggingface_hub import snapshot_download

local_dir = snapshot_download(
    repo_id="yifishbossman/financial-analyst-data{repo_suffix}",
    repo_type="dataset",
    local_dir="~/.financial-analyst/data",
)

import qlib
from qlib.data import D
qlib.init(provider_uri=f"{{local_dir}}/cn_data", region="cn")

df = D.features(
    ["SH600519"], ["$close", "$volume", "$pe_ttm", "$total_mv"],
    start_time="2024-01-01", end_time="2026-05-31", freq="day",
)
print(df.tail())
```

### Option 3 — Read Parquet directly with pandas

**EN**: Non-time-series data (financials / industry / F10 / events) is plain Parquet.

**中文**: 非时序数据 (财报 / 行业 / F10 / 事件) 是普通 Parquet, pandas 直接读.

```python
import pandas as pd

# Industry classification for all listed stocks / 全市场行业分类
ind = pd.read_parquet(f"{{local_dir}}/parquet/tushare_stock_basic.parquet")

# Latest TDX F10 negative warnings (last 7 days) / TDX F10 最新 7 天负向事件
warn = pd.read_parquet(f"{{local_dir}}/parquet/tdx_f10_warnings_latest.parquet")

# CSI300 / 500 constituents / 沪深 300/500 成份
idx = pd.read_parquet(f"{{local_dir}}/parquet/index_constituents.parquet")
```

---

## 📐 Units & Conventions / 单位与约定

| Field / 字段 | Unit / 单位 | Notes / 说明 |
|---|---|---|
| open / high / low / close | 元 / CNY | not adjusted / 不复权 — adjustment factor not included |
| volume | 手 (= 100 股) / lots | Tushare convention; **NOT** pytdx convention (股 / shares) |
| amount | 元 / CNY | 成交额 / turnover value |
| pe_ttm / pb / ps_ttm | (无单位) / dimensionless | TTM ratios / 滚动 12 月 |
| dv_ttm | % | dividend yield TTM / 股息率 |
| total_mv / circ_mv | 万元 / 10K CNY | total / circulating market cap / 总/流通市值 |
| turnover_rate | % | daily turnover ratio / 换手率 |

---

## 🔗 Data Sources & Lineage / 数据来源

### English

- **OHLCV + valuation**: Tushare Pro (`pro.daily` + `daily_basic`), HTTP endpoint, ~5500 A-share tickers
- **5min OHLCV**: TDX main sites via `pytdx` (free, no token required for historical 5min)
- **Financial reports**: Tushare Pro (`fina_indicator`, `income`, `balancesheet`, `cashflow`)
- **TDX F10 events**: `pytdx` direct connection to broker hosts (招商证券/东兴/华泰 etc.), parsed company events / 龙虎榜 / institutional flows
- **For daily updates** (post-download): users run `fa data update` which pulls from pytdx main sites (free, no token) + Tencent `qt.gtimg.cn` realtime (free, no cookie). See [direct-data-stability research](https://github.com/jesson-hh/financial-analyst/blob/main/docs/research/2026-05-23-direct-data-stability.md).

### 中文

- **日线 OHLCV + 估值**: Tushare Pro (`pro.daily` + `daily_basic`) HTTP 接口, 覆盖全 A 股约 5500 只
- **5min 行情**: TDX 主站经 `pytdx` 拉取 (零成本, 历史 5min 不需 token)
- **财务报表**: Tushare Pro (`fina_indicator`, `income`, `balancesheet`, `cashflow`)
- **TDX F10 事件**: `pytdx` 直连券商主站 (招商证券/东兴/华泰 等), 解析公司大事 / 龙虎榜 / 主力追踪
- **下载后日常更新**: 用户跑 `fa data update`, 走 pytdx 主站 (免 token) + 腾讯 `qt.gtimg.cn` 实时 (免 cookie). 详见 [直连数据稳定性研究](https://github.com/jesson-hh/financial-analyst/blob/main/docs/research/2026-05-23-direct-data-stability.md).

---

## ⚠️ Disclaimer / 免责声明

**EN**: This dataset is provided strictly for **research and educational purposes**. Data accuracy is not guaranteed; verify independently before any trading decision. The publisher assumes no liability for losses incurred from use of this data. Redistribution must comply with original source terms — see [Tushare ToS](https://tushare.pro).

**中文**: 本数据集仅供**学术研究 / 教学使用**, 不保证数据准确性. 任何投资决策须自行独立验证, 因使用本数据造成的损失发布方概不负责. 二次分发须遵守原始数据源条款 — 参考 [Tushare 服务协议](https://tushare.pro).

---

## 📄 License / 许可

- **Code / 工具脚本**: Apache 2.0 (financial-analyst toolchain)
- **Data / 数据本体**: 遵从原始数据源 (Tushare / TDX) 各自的使用条款 / refer to original sources
- **Citation / 引用**: If you use this dataset in a paper, please cite [financial-analyst](https://github.com/jesson-hh/financial-analyst) / 论文引用请标注 [financial-analyst](https://github.com/jesson-hh/financial-analyst)

---

## 🔄 Updating This Dataset / 数据更新

**EN**: This snapshot is static. For incremental daily updates (post-market each day), install the `financial-analyst` package locally:

**中文**: HF 上的快照是静态的. 每天盘后增量更新, 本地装 `financial-analyst` 包:

```bash
pip install financial-analyst
fa data update           # incremental day OHLCV + valuation via pytdx (free)
fa data update --5min    # incremental 5min via TDX local client
fa data update --f10     # refresh TDX F10 events for watched stocks
```

---

## 🤝 Contributing / 贡献

**EN**: Issues / PRs welcome on the [main repo](https://github.com/jesson-hh/financial-analyst). For dataset-specific issues (missing codes, schema questions), file an issue tagged `dataset`.

**中文**: Bug 反馈 / 功能建议请去 [主仓库](https://github.com/jesson-hh/financial-analyst). 数据集相关问题 (代码缺失 / schema 问题) 请加 `dataset` 标签.

---

<sub>Generated by `scripts/publish_hf_dataset.py` on {today} · v1.0.1 · bilingual zh/en</sub>
"""


# ──────────────────────── 上传 ────────────────────────


def _upload(staging: Path, repo_id: str, token: str, dry_run: bool) -> None:
    if dry_run:
        print(f"\n[DRY-RUN] would upload {staging} → {repo_id} (skipped)")
        return

    from huggingface_hub import HfApi
    api = HfApi(token=token)

    # repo create (idempotent — exist_ok)
    print(f"\n  HF: create_repo {repo_id} (dataset)")
    api.create_repo(repo_id, repo_type="dataset", exist_ok=True, private=False)

    # upload_large_folder: chunked commits + resume. demo 155MB ~10K bin files
    # 用 upload_folder 单次 create_commit 在 ~60s httpx ReadTimeout 出错.
    # upload_large_folder 内部 batch ~25 files/commit, 失败可 resume.
    print(f"  HF: upload_large_folder {staging} → {repo_id}")
    t = time.time()
    api.upload_large_folder(
        folder_path=str(staging),
        repo_id=repo_id,
        repo_type="dataset",
        ignore_patterns=["__pycache__", "*.pyc", ".DS_Store"],
        print_report=False,  # 默认每 60s 打报告太吵
    )
    print(f"  HF: upload done ({time.time() - t:.1f}s)")
    print(f"\n  ✓ Dataset live at https://huggingface.co/datasets/{repo_id}")


# ──────────────────────── main ────────────────────────


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", choices=list(PRESETS), help="预设包")
    ap.add_argument("--codes-file", help="自定义 universe (一行一个 code, # 注释)")
    ap.add_argument("--top-mv-n", type=int, default=None,
                    help="从 instruments/all.txt 池里走 Tencent 实时 mv 选 top-N. "
                         "覆盖 --preset 的 universe 解析.")
    ap.add_argument("--market", default=None,
                    help="--top-mv-n 用的 universe pool, 默认 all")
    ap.add_argument("--source-day", default="G:/stocks/stock_data/cn_data",
                    help="日线源目录")
    ap.add_argument("--source-5min", default="G:/stocks/stock_data/cn_data_5min",
                    help="5min 源目录")
    ap.add_argument("--source-parquet", default="G:/stocks/stock_data/parquet",
                    help="parquet 源目录")
    ap.add_argument("--source-tdx-finance", default="G:/stocks/stock_data/tdx_finance",
                    help="TDX 历年财报 zip 源目录 (full 用)")
    ap.add_argument("--source-tdx-f10", default="G:/stocks/news_data/tdx_f10",
                    help="TDX F10 原始文本目录 (lite/full 用)")
    ap.add_argument("--include-5min", action="store_true",
                    help="包含 5min (preset 内置, 自定义需指定)")
    ap.add_argument("--no-5min", action="store_true", help="禁用 5min (覆盖 preset)")
    ap.add_argument("--no-parquet", action="store_true",
                    help="禁用 parquet 部分 (覆盖 preset, 只发 bin)")
    ap.add_argument("--staging", default="G:/financial-analyst/.staging_hf",
                    help="临时打包目录")
    ap.add_argument("--repo", help="HuggingFace repo_id, e.g. yifishbossman/financial-analyst-data-demo")
    ap.add_argument("--token", default=os.environ.get("HUGGINGFACE_TOKEN", ""),
                    help="HF token (默认读 env HUGGINGFACE_TOKEN)")
    ap.add_argument("--dry-run", action="store_true", help="只 stage, 不上传")
    args = ap.parse_args()

    if not args.preset and not args.codes_file and args.top_mv_n is None:
        ap.error("--preset OR --codes-file OR --top-mv-n required")
    if not args.dry_run and not args.repo:
        ap.error("--repo required unless --dry-run")
    if not args.dry_run and not args.token:
        ap.error("HUGGINGFACE_TOKEN env or --token required unless --dry-run")

    # 决定 freq
    if args.no_5min:
        include_5min = False
    elif args.include_5min:
        include_5min = True
    elif args.preset:
        include_5min = PRESETS[args.preset]["include_5min"]
    else:
        include_5min = False

    print(f"=== publish_hf_dataset — {args.preset or 'custom'} ===")
    print(f"  source day:   {args.source_day}")
    print(f"  source 5min:  {args.source_5min if include_5min else '(skip)'}")
    print(f"  staging:      {args.staging}")
    print(f"  repo:         {args.repo or '(dry-run)'}")
    print()

    # 1. universe
    codes = _resolve_universe(args, args.source_day)
    print(f"  universe: {len(codes)} 代码 (前 5: {codes[:5]})")

    # 2. clear staging
    staging = Path(args.staging)
    if staging.exists():
        print(f"  ⚠ clearing existing staging {staging}")
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    # 3. stage
    t0 = time.time()
    stats = _stage_dataset(args.source_day,
                            args.source_5min if include_5min else None,
                            codes, staging, include_5min)
    print(f"\n  staged bin in {time.time() - t0:.1f}s:")
    print(f"    day:  {stats['day_codes']} codes, {stats['day_files']} bin files, "
          f"{stats['day_bytes']/1e9:.2f} GB")
    if include_5min:
        print(f"    5min: {stats['5min_codes']} codes, {stats['5min_files']} bin files, "
              f"{stats['5min_bytes']/1e9:.2f} GB")
    if stats["skipped"]:
        print(f"    skipped {len(stats['skipped'])} codes (not in source instruments)")

    # 3b. parquet (preset 内置 white-list, 可 --no-parquet 关掉)
    stats["parquet_files"] = 0
    stats["parquet_bytes"] = 0
    stats["tdx_zip_files"] = 0
    stats["tdx_zip_bytes"] = 0
    if args.no_parquet:
        print("  parquet: skipped (--no-parquet)")
    elif args.preset:
        preset = PRESETS[args.preset]
        pq_items = preset.get("parquet_include", [])
        if pq_items:
            t1 = time.time()
            pq_stats = _stage_parquet(args.source_parquet, pq_items, staging)
            stats["parquet_files"] = pq_stats["parquet_files"]
            stats["parquet_bytes"] = pq_stats["parquet_bytes"]
            print(f"  staged parquet in {time.time() - t1:.1f}s: "
                  f"{pq_stats['parquet_files']} files, {pq_stats['parquet_bytes']/1e6:.1f} MB")
            if pq_stats["skipped"]:
                print(f"    skipped (missing): {pq_stats['skipped']}")
        if preset.get("tdx_finance_zip"):
            t2 = time.time()
            tx_stats = _stage_tdx_finance(args.source_tdx_finance, staging)
            stats["tdx_zip_files"] = tx_stats["tdx_zip_files"]
            stats["tdx_zip_bytes"] = tx_stats["tdx_zip_bytes"]
            print(f"  staged tdx_finance in {time.time() - t2:.1f}s: "
                  f"{tx_stats['tdx_zip_files']} zips, {tx_stats['tdx_zip_bytes']/1e6:.1f} MB")
        if preset.get("tdx_f10_text"):
            t3 = time.time()
            f10_stats = _stage_tdx_f10(args.source_tdx_f10, staging)
            stats["tdx_f10_files"] = f10_stats["tdx_f10_files"]
            stats["tdx_f10_bytes"] = f10_stats["tdx_f10_bytes"]
            print(f"  staged tdx_f10 text in {time.time() - t3:.1f}s: "
                  f"{f10_stats['tdx_f10_files']} files, {f10_stats['tdx_f10_bytes']/1e6:.1f} MB")

    # 4. dataset card
    day_cal = load_calendar(args.source_day, freq="day")
    card = _gen_dataset_card(args.preset, codes, stats, day_cal, include_5min)
    (staging / "README.md").write_text(card, encoding="utf-8")
    print(f"\n  dataset card → {staging / 'README.md'}")

    # 5. upload
    _upload(staging, args.repo, args.token, args.dry_run)

    print(f"\n=== done. total: {time.time() - t0:.1f}s ===")


if __name__ == "__main__":
    main()
