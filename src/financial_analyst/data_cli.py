"""``fa data`` subcommand group — 直连数据增量更新.

子命令:
  fa data status              — 看当前 bin 数据范围 + instruments 数 + 磁盘
  fa data update              — pytdx 日线 + 5min + 腾讯 daily_basic 增量
  fa data update --skip-5min  — 只更新日线 (更快)
  fa data update --since 20260501 — 指定起始日
  fa data update --codes SH600519,SZ300750 — 限定代码

无需 Tushare token. 主站直连 + 多 host failover.
"""
from __future__ import annotations

import time
from datetime import date as _date
from pathlib import Path
from typing import List, Optional

import typer

data_app = typer.Typer(
    name="data",
    help="直连数据增量更新 (pytdx 主站 + 腾讯实时, 不需要 Tushare token).",
    no_args_is_help=True,
)


def _resolve_provider_uri(freq: str = "day") -> str:
    """解析 provider_uri (按频率).

    优先级 (从高到低):
      1. ``FA_DATA_DIR`` 环境变量 — 指向**父目录**, 自动拼 cn_data / cn_data_5min
      2. ``FA_DATA_DAY_URI`` / ``FA_DATA_5MIN_URI`` — per-freq 显式 override
      3. ``config/loaders.yaml`` 的 ``qlib_binary.provider_uri.{freq}``
      4. ``~/.financial-analyst/data/cn_data{,_5min}`` (默认)
    """
    import os

    # 1. per-freq override
    per_freq = os.environ.get(f"FA_DATA_{freq.upper()}_URI")
    if per_freq:
        return per_freq

    # 2. 父目录 override
    env_parent = os.environ.get("FA_DATA_DIR")
    if env_parent:
        sub = "cn_data" if freq == "day" else f"cn_data_{freq}"
        return os.path.join(env_parent, sub).replace("\\", "/")

    # 3. config/loaders.yaml
    try:
        from financial_analyst.data.loader_factory import _load_config
        cfg = _load_config()
        loaders = cfg.get("loaders", {}) or {}
        qb = loaders.get("qlib_binary", {}) or {}
        provider = qb.get("provider_uri", {})
        if isinstance(provider, dict) and provider.get(freq):
            return provider[freq]
        if isinstance(provider, str):
            return provider   # legacy 单值, 视为 day
    except Exception:
        pass

    # 4. 默认 workspace data dir (honours pinned workspace; falls back to HOME)
    try:
        from financial_analyst.workspace import get_workspace
        ws = get_workspace()
    except Exception:
        ws = Path.home() / ".financial-analyst"
    sub = "cn_data" if freq == "day" else f"cn_data_{freq}"
    return str(ws / "data" / sub).replace("\\", "/")


def _resolve_codes(codes_arg: Optional[str], provider_uri: str) -> List[str]:
    """解析 codes 参数: None=所有 instruments; 逗号分隔的代码列表; @file=从文件读."""
    from financial_analyst.data.bin_writer import load_instruments

    if not codes_arg:
        inst = load_instruments(provider_uri, market="all")
        if not inst:
            return []
        return sorted(inst.keys())
    if codes_arg.startswith("@"):
        # 接受两种格式:
        #   单列 "SH600519\n..."
        #   instruments tab 格式 "SH600519\t2010-01-04\t2026-05-15"
        out = []
        for line in Path(codes_arg[1:]).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line.split("\t")[0].split()[0].upper())
        return out
    return [c.strip().upper() for c in codes_arg.replace("，", ",").split(",") if c.strip()]


# ───────────────────────── status ─────────────────────────


@data_app.command("status")
def status_cmd():
    """看本机数据当前状态 (instruments 数 / 日线截止 / 5min 截止 / 磁盘)."""
    from financial_analyst.data.bin_writer import (
        load_calendar, load_instruments, get_bin_range,
    )

    day_uri = _resolve_provider_uri("day")
    fivemin_uri = _resolve_provider_uri("5min")

    typer.echo(f"日线 provider_uri:   {day_uri}")
    typer.echo(f"5min provider_uri:   {fivemin_uri}")
    typer.echo("")

    # instruments
    inst = load_instruments(day_uri, market="all")
    typer.echo(f"  instruments (all):  {len(inst)} 只")

    # 日历
    cal_day = load_calendar(day_uri, freq="day")
    cal_5min = load_calendar(fivemin_uri, freq="5min")
    typer.echo(f"  日线日历:           {len(cal_day)} 天 "
               f"({cal_day[0] if cal_day else '?'} → {cal_day[-1] if cal_day else '?'})")
    typer.echo(f"  5min  日历:         {len(cal_5min)} bar "
               f"({cal_5min[0] if cal_5min else '?'} → {cal_5min[-1] if cal_5min else '?'})")

    # 抽样股票看 bin 范围
    if inst:
        sample_code = list(inst.keys())[0]
        si, ei = get_bin_range(sample_code, "close", "day", day_uri)
        typer.echo(f"  {sample_code} close.day.bin: range [{si}, {ei}] = "
                   f"{ei - si + 1 if ei >= si else 0} 天")

    # 磁盘
    try:
        import shutil
        total, used, free = shutil.disk_usage(day_uri)
        typer.echo(f"  磁盘 (day uri):     {free/1e9:.1f}GB 可用 / {total/1e9:.0f}GB 总")
    except Exception:
        pass

    # 上次更新时间
    typer.echo("")
    typer.echo("  上次更新:")
    from financial_analyst.data import last_update as _lu
    for dt, age, stale in _lu.status_summary():
        marker = "⚠" if stale else "✓"
        typer.echo(f"    {marker} {dt:<13} {age}")
    if any(s for _, _, s in _lu.status_summary()):
        typer.echo("")
        typer.echo("  ↻ 跑 fa data refresh 增量更新所有陈旧数据")


# ───────────────────────── update ─────────────────────────


@data_app.command("update")
def update_cmd(
    codes: Optional[str] = typer.Option(
        None, "--codes",
        help="逗号分隔代码 (SH600519,SZ300750) / @file 文件 / 留空=所有 instruments"),
    n_daily: int = typer.Option(30, help="日线拉最近 N 根 (增量=30, 回补=800)"),
    n_5min: int = typer.Option(240, help="5min 拉最近 N 根 (240≈5 个交易日)"),
    skip_5min: bool = typer.Option(False, help="跳过 5min 更新 (只更日线)"),
    skip_basic: bool = typer.Option(False, help="跳过 daily_basic (PE/PB/MV) 更新"),
    trade_date: Optional[str] = typer.Option(
        None, "--trade-date",
        help="daily_basic 写入的日期 YYYY-MM-DD, 默认今日"),
):
    """直连增量更新所有数据 — 日线 + 5min + 当日 PE/PB/MV.

    Example:
      fa data update                    # 全 instruments, 默认 N
      fa data update --skip-5min        # 只日线
      fa data update --codes @my.txt   # 只更新文件里的代码
    """
    day_uri = _resolve_provider_uri("day")
    fivemin_uri = _resolve_provider_uri("5min")
    codes_list = _resolve_codes(codes, day_uri)

    if not codes_list:
        typer.echo("✗ 没有要更新的代码. instruments 是空? 先 fa data bootstrap 拉历史包.")
        raise typer.Exit(1)

    typer.echo(f"=== fa data update — {len(codes_list)} 只 ===")
    typer.echo(f"日线 → {day_uri}")
    typer.echo(f"5min → {fivemin_uri}")
    typer.echo("")

    from financial_analyst.data.updaters.pytdx_pool import PytdxClient
    from financial_analyst.data.updaters.pytdx_kline import (
        update_daily_batch, update_5min_batch,
    )
    from financial_analyst.data.updaters.tencent_basic import (
        update_daily_basic_today,
    )
    from financial_analyst.data import last_update as _lu

    client = PytdxClient()
    typer.echo(f"  pytdx connected to {client.host or '(lazy)'}")

    overall_t = time.time()
    try:
        # 日线
        t0 = time.time()
        stats_daily = update_daily_batch(day_uri, codes_list, n_bars=n_daily,
                                          client=client, progress=True)
        typer.echo(f"\n[日线 ✓] {stats_daily['ok']}/{stats_daily['total']} OK "
                   f"({stats_daily['empty']} 空, {stats_daily['failed']} 失败) "
                   f"耗时 {time.time() - t0:.1f}s")
        if stats_daily.get("ok", 0) > 0:
            _lu.mark_updated("day")

        # 5min
        if not skip_5min:
            t0 = time.time()
            stats_5min = update_5min_batch(fivemin_uri, codes_list, n_bars=n_5min,
                                            client=client, progress=True)
            typer.echo(f"\n[5min ✓] {stats_5min['ok']}/{stats_5min['total']} OK "
                       f"({stats_5min['empty']} 空, {stats_5min['failed']} 失败) "
                       f"耗时 {time.time() - t0:.1f}s")
            if stats_5min.get("ok", 0) > 0:
                _lu.mark_updated("5min")
    finally:
        client.close()

    # daily_basic (走腾讯实时, 不用 pytdx)
    if not skip_basic:
        t0 = time.time()
        stats_basic = update_daily_basic_today(day_uri, codes_list,
                                                trade_date=trade_date)
        typer.echo(f"\n[daily_basic ✓] {stats_basic['ok']}/{stats_basic['total']} OK "
                   f"(no_quote={stats_basic['no_quote']}, "
                   f"missing_pe={stats_basic['missing_pe']}) "
                   f"耗时 {time.time() - t0:.1f}s")
        if stats_basic.get("ok", 0) > 0:
            _lu.mark_updated("daily_basic")

    typer.echo(f"\n=== 完成. 总耗时 {time.time() - overall_t:.1f}s ===")


# ───────────────────────── refresh (smart auto) ─────────────────────────


@data_app.command("refresh")
def refresh_cmd(
    codes: Optional[str] = typer.Option(
        None, "--codes",
        help="限定代码 (逗号 / @file). 默认全 instruments."),
    skip_5min: bool = typer.Option(
        False, "--skip-5min", help="跳过 5min 更新, 更快."),
    force: bool = typer.Option(
        False, "--force", help="忽略 'recently updated' 检查, 全部重拉."),
):
    """智能增量刷新 — 自动判断哪些数据该更新, 然后跑.

    跟 `fa data update` 区别:
      * 默认查 last-update tracker, 24h 内更新过的数据类型跳过 (除非 --force)
      * 跳过被 --skip-5min 排除的类型 (不算它陈旧)
      * 写 last-update 时间戳, 供 `fa data status` + `fa start` banner 用
    """
    from financial_analyst.data import last_update as _lu

    if not force:
        # Only consider the types we're actually going to attempt.
        # If the caller said --skip-5min, ignore 5min staleness.
        relevant = [dt for dt in _lu.IMPLEMENTED_TYPES
                    if not (skip_5min and dt == "5min")]
        stale = [dt for dt in relevant if _lu.is_stale(dt)]
        if not stale:
            typer.echo("✓ 所有数据都在 24h 内更新过, 无事可做.")
            typer.echo("  跑 fa data refresh --force 强制重拉, 或 fa data status 看详情.")
            raise typer.Exit(0)
        typer.echo(f"陈旧数据类型 ({len(stale)}): " + " · ".join(stale))
    else:
        typer.echo("--force 强制重拉所有")

    # Delegate to update_cmd (calling the function directly since they're in same module)
    return update_cmd(
        codes=codes,
        n_daily=30,
        n_5min=240,
        skip_5min=skip_5min,
        skip_basic=False,
        trade_date=None,
    )


# ───────────────────────── bootstrap (stub) ─────────────────────────


@data_app.command("bootstrap")
def bootstrap_cmd(
    preset: str = typer.Option(
        "demo", "--preset",
        help="数据包预设: demo (~500MB csi300 hist) / lite (~5GB csi800+5min) / full (~50GB)"),
    target: Optional[Path] = typer.Option(
        None, "--target",
        help="数据目标目录, 默认 ~/.financial-analyst/data/"),
    force: bool = typer.Option(
        False, "--force", help="目标目录已有数据时强制覆盖"),
):
    """从 HuggingFace 下载历史数据包 (一次性 bootstrap).

    例:
      fa data bootstrap                    # 演示包 ~500MB 到默认路径
      fa data bootstrap --preset lite      # ~5GB csi800+5min
      fa data bootstrap --target D:/data   # 自定义路径

    之后每日增量走 ``fa data update`` (pytdx 主站直连, 无 token).
    """
    from financial_analyst.init_cli import HF_PACKAGES, _download_package, _write_loaders_config

    if preset not in HF_PACKAGES:
        typer.echo(f"未知 preset {preset!r}. 可选: {list(HF_PACKAGES)}")
        raise typer.Exit(1)

    pkg = HF_PACKAGES[preset]
    target_dir = target or (Path.home() / ".financial-analyst" / "data")
    target_dir = Path(target_dir).expanduser().resolve()

    # 检测已有数据
    if (target_dir / "cn_data" / "instruments" / "all.txt").exists() and not force:
        from financial_analyst.data.bin_writer import load_instruments
        existing = load_instruments(str(target_dir / "cn_data"), market="all")
        typer.echo(f"⚠ {target_dir} 已经有 {len(existing)} 只 instruments.")
        typer.echo(f"  跳过下载. 加 --force 覆盖, 或换 --target 路径.")
        raise typer.Exit(0)

    typer.echo(f"=== fa data bootstrap — preset={preset} ===")
    typer.echo(f"  HF repo:   {pkg['repo_id']}")
    typer.echo(f"  目标目录:  {target_dir}")
    typer.echo(f"  约大小:    {pkg['size_hint']}")
    typer.echo()

    ok = _download_package(preset, target_dir)
    if not ok:
        typer.echo()
        typer.echo("✗ 下载失败. 可能原因:")
        typer.echo("  1. HF dataset 还没 publish (maintainer 跑 publish_hf_dataset.py 上传, 见")
        typer.echo("     docs/setup/hf_publish_guide.md)")
        typer.echo("  2. 网络不稳定, 重试一次")
        typer.echo("  3. 国内 HF CDN 偶尔需要 HTTPS_PROXY")
        typer.echo()
        typer.echo("临时替代: 手工 copy 任意 Qlib 数据目录到 ~/.financial-analyst/data/cn_data/,")
        typer.echo("         或编辑 config/loaders.yaml 把 provider_uri 指向已有目录.")
        raise typer.Exit(2)

    # 写 config/loaders.yaml 指向新目录
    config_path = Path(__file__).resolve().parent.parent.parent / "config" / "loaders.yaml"
    if config_path.parent.exists():
        _write_loaders_config(target_dir, config_path)

    typer.echo()
    typer.echo("✓ bootstrap 完成. 下一步:")
    typer.echo("  fa data status                — 看数据状态")
    typer.echo("  fa report SH600519            — 跑第一份研报")
    typer.echo("  fa data update                — 每日增量更新 (pytdx 直连)")
