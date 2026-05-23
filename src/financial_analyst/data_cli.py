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

    # 4. 默认家目录
    home = Path.home() / ".financial-analyst" / "data" / ("cn_data" if freq == "day"
                                                          else f"cn_data_{freq}")
    return str(home).replace("\\", "/")


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

        # 5min
        if not skip_5min:
            t0 = time.time()
            stats_5min = update_5min_batch(fivemin_uri, codes_list, n_bars=n_5min,
                                            client=client, progress=True)
            typer.echo(f"\n[5min ✓] {stats_5min['ok']}/{stats_5min['total']} OK "
                       f"({stats_5min['empty']} 空, {stats_5min['failed']} 失败) "
                       f"耗时 {time.time() - t0:.1f}s")
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

    typer.echo(f"\n=== 完成. 总耗时 {time.time() - overall_t:.1f}s ===")


# ───────────────────────── bootstrap (stub) ─────────────────────────


@data_app.command("bootstrap")
def bootstrap_cmd(
    package: str = typer.Option(
        "demo", help="演示包: demo (csi300×1y, ~500MB) / lite (csi800×3y, ~5GB) / full (~50GB)"),
    target: Optional[Path] = typer.Option(
        None, help="数据目标目录, 默认 ~/.financial-analyst/data/"),
):
    """从 HuggingFace 下载历史数据包.

    [P1 待实现] 现在只是 stub. 完整实现见 docs/research/2026-05-23-direct-data-stability.md.
    """
    typer.echo("[stub] fa data bootstrap 尚未实现 — 见 P1 roadmap.")
    typer.echo("       临时: 手工 copy G:/stocks/stock_data 到 ~/.financial-analyst/data/")
    typer.echo("       或编辑 config/loaders.yaml 把 provider_uri 指向已有目录.")
    raise typer.Exit(2)
