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
    python scripts/publish_hf_dataset.py --preset demo --repo jesson-hh/financial-analyst-data-demo
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


PRESETS = {
    "demo": {
        "size_hint":  "~500 MB",
        "method":     "top-mv",   # 用 Tencent 实时拉当前 mv top N
        "n_codes":    300,        # 真当前 csi300 (而非 Qlib 历史累积 939)
        "market":     "all",      # universe pool: instruments/all.txt
        "include_5min": False,
        "description": "当前 mv top-300 演示包 (真 csi300 当前成份). 全历史日线 + daily_basic 快照, 无 5min.",
    },
    "lite": {
        "size_hint":  "~5 GB",
        "method":     "top-mv",
        "n_codes":    800,        # 当前 mv top-800 ≈ csi800
        "market":     "all",
        "include_5min": True,
        "description": "当前 mv top-800 半技术用户包. 全历史日线 + 5min ~7天 + financials.",
    },
    "full": {
        "size_hint":  "~50 GB",
        "method":     "instruments",  # 全 5500+ 用 instruments 文件
        "market":     "all",
        "n_codes":    None,
        "include_5min": True,
        "description": "全 A 股完整包 (含历史退市股). 量化研究员 / 重度用户用.",
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


# ──────────────────────── dataset card ────────────────────────


def _gen_dataset_card(preset_name: Optional[str], codes: List[str],
                      stats: dict, day_cal: List[str], freq_5min: bool) -> str:
    """生成 README.md (HF dataset card)."""
    today = _date.today().isoformat()
    preset_info = PRESETS.get(preset_name, {})

    day_range = (f"{day_cal[0]} → {day_cal[-1]}" if day_cal else "?")
    n_days = len(day_cal)
    total_gb = (stats["day_bytes"] + stats["5min_bytes"]) / 1e9

    return f"""---
license: apache-2.0
language:
- zh
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
---

# financial-analyst-data{('-' + preset_name) if preset_name else ''}

A-share historical price + valuation data **packaged for [financial-analyst](https://github.com/jesson-hh/financial-analyst)** —
the 14-agent single-stock deep-dive research workstation.

**Published**: {today}
**Preset**: `{preset_name or 'custom'}` — {preset_info.get('description', '')}
**Size**: ~{total_gb:.1f} GB

## What's included

- **{stats['day_codes']} stocks** daily OHLCV + 7 valuation fields (PE/PB/PS/DV/MV/CIRC_MV/turnover_rate)
- **Date range (daily)**: {day_range} ({n_days} trading days)
{'- **5min OHLCV** for the same stocks (rolling ~7-100 days)' if freq_5min else ''}
- Qlib binary format (`.bin` files, `[4-byte float32 start_idx] + [float32 array]`)
- Compatible with [Microsoft Qlib](https://github.com/microsoft/qlib) and financial-analyst loaders

## Directory layout

```
cn_data/                          # daily
  calendars/day.txt
  instruments/all.txt             # {stats['day_codes']} codes
  features/{{code}}/              # e.g. sh600519/
    open.day.bin
    high.day.bin
    low.day.bin
    close.day.bin
    volume.day.bin
    amount.day.bin
    pe_ttm.day.bin
    pb.day.bin
    ps_ttm.day.bin (may be NaN if Tushare-free source)
    dv_ttm.day.bin (may be NaN)
    total_mv.day.bin (单位: 万元)
    circ_mv.day.bin (单位: 万元)
    turnover_rate.day.bin (单位: %)
{'cn_data_5min/                     # 5-minute, similar layout' if freq_5min else ''}
```

## Usage

### Option 1 — via `financial-analyst` CLI (recommended)

```bash
pip install financial-analyst
fa init  # interactive wizard, picks up this dataset
fa report SH600519
```

### Option 2 — direct download + Qlib

```python
from huggingface_hub import snapshot_download
local_dir = snapshot_download(
    repo_id="jesson-hh/financial-analyst-data{('-' + preset_name) if preset_name else ''}",
    repo_type="dataset",
    local_dir="~/.financial-analyst/data",
)

import qlib
qlib.init(provider_uri="~/.financial-analyst/data/cn_data", region="cn")
from qlib.data import D
df = D.features(["SH600519"], ["$close", "$volume"],
                start_time="2024-01-01", end_time="2026-05-30", freq="day")
```

## Units & conventions

| Field | Unit | Notes |
|-------|------|-------|
| open / high / low / close | 元 (CNY) | not adjusted (前复权) — adjustment factor not included |
| volume | 手 (= 100 股) | Tushare convention; **NOT** pytdx convention (股) |
| amount | 元 | 成交额 |
| pe_ttm / pb / ps_ttm | (无单位) | TTM ratios |
| dv_ttm | % | dividend yield TTM |
| total_mv / circ_mv | 万元 | total / circulating market cap |
| turnover_rate | % | daily turnover ratio |

## Data sources & lineage

- **OHLCV + financials**: Tushare Pro (`pro.daily` + `daily_basic`), HTTP endpoint
- **5min OHLCV**: TDX main sites via pytdx
- **For daily updates** (post-download): users run `fa data update` which pulls from
  pytdx main sites (free, no token) + Tencent qt.gtimg.cn realtime (free, no cookie).
  See [docs/research/2026-05-23-direct-data-stability.md](https://github.com/jesson-hh/financial-analyst/blob/main/docs/research/2026-05-23-direct-data-stability.md).

## Disclaimer

This dataset is for **research and educational purposes only**. Data accuracy not
guaranteed; do not use for trading without independent verification. See
[Tushare ToS](https://tushare.pro) for redistribution terms.

## License

Apache 2.0 (code) — refer to original sources (Tushare / TDX) for data licensing.

---

Generated by `scripts/publish_hf_dataset.py` v1.
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

    print(f"  HF: upload_folder {staging} → {repo_id}")
    t = time.time()
    api.upload_folder(
        folder_path=str(staging),
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Publish {staging.name} ({_date.today().isoformat()})",
        ignore_patterns=["__pycache__", "*.pyc", ".DS_Store"],
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
    ap.add_argument("--include-5min", action="store_true",
                    help="包含 5min (preset 内置, 自定义需指定)")
    ap.add_argument("--no-5min", action="store_true", help="禁用 5min (覆盖 preset)")
    ap.add_argument("--staging", default="G:/financial-analyst/.staging_hf",
                    help="临时打包目录")
    ap.add_argument("--repo", help="HuggingFace repo_id, e.g. jesson-hh/fa-data-demo")
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
    print(f"\n  staged in {time.time() - t0:.1f}s:")
    print(f"    day:  {stats['day_codes']} codes, {stats['day_files']} bin files, "
          f"{stats['day_bytes']/1e9:.2f} GB")
    if include_5min:
        print(f"    5min: {stats['5min_codes']} codes, {stats['5min_files']} bin files, "
              f"{stats['5min_bytes']/1e9:.2f} GB")
    if stats["skipped"]:
        print(f"    skipped {len(stats['skipped'])} codes (not in source instruments)")

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
