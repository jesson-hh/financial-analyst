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

import shutil
import time
from datetime import date as _date, datetime
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
    include_f10: bool = typer.Option(
        False, "--include-f10",
        help="附带刷新 TDX F10 事件 (公司大事/龙虎榜/研究报告/最新提示/主力追踪)"),
    f10_universe: str = typer.Option(
        "csi500", "--f10-universe",
        help="F10 刷新范围: csi300 / csi500 / csi800 / all (默认 csi500 ~30 min)"),
    include_concepts: bool = typer.Option(
        False, "--include-concepts",
        help="附带刷新同花顺概念股清单 + 成分股 (需要 adata 包)"),
    concepts_max_age_days: int = typer.Option(
        30, "--concepts-max-age",
        help="概念成分股超过 N 日未刷的重拉 (默认 30)"),
    include_financial: bool = typer.Option(
        False, "--include-financial",
        help="附带刷新财务三表 (income/balancesheet/cashflow). 需 Tushare token."),
    financial_days: int = typer.Option(
        7, "--financial-days",
        help="财务报表拉最近 N 天的 ann_date 公告 (默认 7)"),
    include_stock_basic: bool = typer.Option(
        False, "--include-stock-basic",
        help="附带刷新 tushare_stock_basic (公司基本信息全表). 需 Tushare token."),
    tushare_token: Optional[str] = typer.Option(
        None, "--tushare-token",
        help="Tushare API token. 默认读 FA_TUSHARE_TOKEN env (https://tushare.pro 免费注册)."),
    include_northbound: bool = typer.Option(
        False, "--include-northbound",
        help="附带刷新北向 (沪深股通) 当日持仓快照 (零 token, 需 akshare 包)"),
):
    """直连增量更新所有数据 — 日线 + 5min + 当日 PE/PB/MV.

    Optional (零 token 加成):
      --include-f10           附带刷 TDX F10 事件 (公司大事/龙虎榜等)
      --include-concepts      附带刷同花顺概念股 (需装 adata)

    Example:
      fa data update                                  # 日线 + 5min + daily_basic
      fa data update --skip-5min                      # 只日线
      fa data update --codes @my.txt                  # 限定代码
      fa data update --include-f10 --f10-universe csi300   # 附带 F10
      fa data update --include-concepts               # 附带概念股
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

    # F10 / concepts / financial / stock_basic / northbound 走 paths resolver
    if (include_f10 or include_concepts or include_financial
            or include_stock_basic or include_northbound):
        from financial_analyst.data.paths import get_data_paths
        paths = get_data_paths()

        if include_f10:
            from financial_analyst.data.updaters.f10 import resolve_universe, update_f10
            t0 = time.time()
            try:
                f10_codes = resolve_universe(paths.parquet_root, f10_universe)
            except FileNotFoundError as e:
                typer.echo(f"\n[F10 ✗] universe 解析失败: {e}")
                f10_codes = []
            if f10_codes:
                typer.echo(f"\n[F10] 范围 {f10_universe} = {len(f10_codes)} 只, 拉取中...")
                stats_f10 = update_f10(
                    paths.news_data_root, paths.parquet_root,
                    f10_codes, progress=True,
                )
                typer.echo(
                    f"[F10 ✓] {stats_f10['ok']}/{stats_f10['total']} OK "
                    f"(skip={stats_f10['skipped']}, fail={stats_f10['failed']}, "
                    f"new_rows={stats_f10['new_rows']}) 耗时 {time.time() - t0:.1f}s"
                )
                if stats_f10.get("ok", 0) > 0:
                    _lu.mark_updated("f10")

        if include_concepts:
            from financial_analyst.data.updaters.concepts import update_concepts
            t0 = time.time()
            try:
                stats_con = update_concepts(
                    paths.parquet_root,
                    max_age_days=concepts_max_age_days,
                    progress=True,
                )
                typer.echo(
                    f"\n[concepts ✓] refreshed {stats_con['concepts_refreshed']} "
                    f"/ {stats_con['concepts_total']} (failed={stats_con['failed']}, "
                    f"parquet rows={stats_con['rows_written']}) 耗时 {time.time() - t0:.1f}s"
                )
                if stats_con.get("concepts_refreshed", 0) > 0:
                    _lu.mark_updated("concepts")
            except ImportError as e:
                typer.echo(f"\n[concepts ✗] adata 包未装: {e}")

        # Tushare opt-in: financial + stock_basic
        if include_financial or include_stock_basic:
            import os
            token = tushare_token or os.environ.get("FA_TUSHARE_TOKEN", "").strip()
            if not token:
                typer.echo(
                    "\n[Tushare ✗] 需要 token. 设 FA_TUSHARE_TOKEN env 或传 --tushare-token. "
                    "免费注册 https://tushare.pro/"
                )
            else:
                if include_financial:
                    from financial_analyst.data.updaters.financial import update_financial
                    t0 = time.time()
                    try:
                        stats_fin = update_financial(
                            paths.parquet_root,
                            tushare_token=token,
                            days=financial_days,
                            progress=True,
                        )
                        typer.echo(
                            f"\n[financial ✓] income +{stats_fin['income']} "
                            f"balancesheet +{stats_fin['balancesheet']} "
                            f"cashflow +{stats_fin['cashflow']} "
                            f"(失败 APIs: {len(stats_fin['failed_apis'])}) "
                            f"耗时 {time.time() - t0:.1f}s"
                        )
                        n_total = (stats_fin["income"] + stats_fin["balancesheet"]
                                   + stats_fin["cashflow"])
                        if n_total > 0:
                            _lu.mark_updated("financials")
                    except RuntimeError as e:
                        typer.echo(f"\n[financial ✗] {e}")

                if include_stock_basic:
                    from financial_analyst.data.updaters.stock_basic import update_stock_basic
                    t0 = time.time()
                    try:
                        stats_sb = update_stock_basic(
                            paths.parquet_root,
                            tushare_token=token,
                            progress=True,
                        )
                        if stats_sb["ok"]:
                            typer.echo(
                                f"\n[stock_basic ✓] {stats_sb['rows']:,} 行 "
                                f"耗时 {time.time() - t0:.1f}s"
                            )
                            _lu.mark_updated("stock_basic")
                        else:
                            typer.echo(f"\n[stock_basic ✗] {stats_sb.get('error')}")
                    except RuntimeError as e:
                        typer.echo(f"\n[stock_basic ✗] {e}")

        # northbound 零 token (akshare 路线, 跟 Tushare 流程独立)
        if include_northbound:
            from financial_analyst.data.updaters.northbound import update_northbound
            t0 = time.time()
            try:
                stats_nb = update_northbound(paths.parquet_root, progress=True)
                if stats_nb["ok"]:
                    typer.echo(
                        f"\n[northbound ✓] new={stats_nb['rows_new']} "
                        f"total={stats_nb['rows_total']} markets={stats_nb['markets']} "
                        f"耗时 {time.time() - t0:.1f}s"
                    )
                    _lu.mark_updated("northbound")
                else:
                    typer.echo(f"\n[northbound ✗] errors={stats_nb.get('errors')}")
            except ImportError as e:
                typer.echo(f"\n[northbound ✗] akshare 包未装: {e}")

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


# ───────────────────────── link (offline data wiring) ─────────────────────────


@data_app.command("link")
def link_cmd(
    src: Path = typer.Option(
        ..., "--src",
        help="本地数据目录 (含 cn_data / cn_data_5min / parquet 子目录), "
             "例如手动从网盘下载解压后的位置."),
    force: bool = typer.Option(
        False, "--force",
        help="即使 src 缺 5min / parquet 也继续 link (默认要求齐全)"),
):
    """把手动下载的数据接入工作目录 — HF 太慢时的网盘替代方案.

    流程:
      1. 你从网盘 (阿里云盘 / 夸克) 把对应数据包下载到本地任意目录
      2. 解压, 保证目录结构是 <src>/cn_data/, <src>/cn_data_5min/, <src>/parquet/
      3. fa data link --src <你解压的路径>
      4. fa data status 验证 instruments / 日历看得到

    本质: 把 config/loaders.yaml 的 provider_uri 指向你的目录,
    不 copy 不 symlink, 直接读. 节省磁盘 + 速度最快.

    Examples:
      fa data link --src D:/fa-data
      fa data link --src C:/Users/me/Downloads/fa-lite-extracted --force
    """
    from financial_analyst.workspace import config_dir as _ws_config_dir

    src_abs = src.expanduser().resolve()
    if not src_abs.exists():
        typer.echo(f"✗ 目录不存在: {src_abs}")
        raise typer.Exit(1)
    if not src_abs.is_dir():
        typer.echo(f"✗ 不是目录: {src_abs}")
        raise typer.Exit(1)

    # ── 1. Probe expected subdirs ──
    required = ["cn_data"]
    optional = ["cn_data_5min", "parquet", "news_data"]
    have = {}
    for name in required + optional:
        p = src_abs / name
        have[name] = p.exists() and p.is_dir()

    missing_required = [n for n in required if not have[n]]
    missing_optional = [n for n in optional if not have[n]]

    typer.echo(f"=== fa data link ===")
    typer.echo(f"  源目录: {src_abs}")
    typer.echo("")
    typer.echo(f"  目录扫描:")
    for name in required + optional:
        marker = "✓" if have[name] else "✗"
        kind = "(必需)" if name in required else "(可选)"
        size = ""
        if have[name]:
            try:
                # rough size — count files only, no recursive du
                n_files = sum(1 for _ in (src_abs / name).rglob("*") if _.is_file())
                size = f" [{n_files:,} files]"
            except Exception:
                pass
        typer.echo(f"    {marker} {name:<15} {kind}{size}")
    typer.echo("")

    if missing_required:
        typer.echo(f"✗ 缺少必需子目录: {missing_required}")
        typer.echo(f"  请确认 {src_abs} 下有 cn_data/ (含 calendars/ + instruments/ + features/)")
        raise typer.Exit(2)

    if missing_optional and not force:
        typer.echo(f"⚠ 缺少可选子目录: {missing_optional}")
        typer.echo(f"  这些功能受影响: " + (
            "5min K 线 " if 'cn_data_5min' in missing_optional else "") + (
            "财报 / F10 " if 'parquet' in missing_optional else "") + (
            "新闻 " if 'news_data' in missing_optional else ""))
        typer.echo(f"  加 --force 接受不完整 link, 或补全后重跑.")
        raise typer.Exit(3)

    # ── 2. Validate cn_data/ structure ──
    cn_data = src_abs / "cn_data"
    cal_file = cn_data / "calendars" / "day.txt"
    inst_file = cn_data / "instruments" / "all.txt"
    if not cal_file.exists() or not inst_file.exists():
        typer.echo(f"✗ cn_data/ 目录结构错: 没找到 calendars/day.txt 或 instruments/all.txt")
        typer.echo(f"  你可能解压错了, 看看是不是多套了一层目录?")
        raise typer.Exit(4)

    try:
        n_inst = sum(1 for _ in inst_file.read_text(encoding="utf-8").splitlines() if _.strip())
        n_days = sum(1 for _ in cal_file.read_text(encoding="utf-8").splitlines() if _.strip())
        typer.echo(f"  ✓ cn_data 校验通过: {n_inst:,} 只 instruments, {n_days:,} 天日历")
    except Exception as e:
        typer.echo(f"⚠ cn_data 校验时 IO 错误 (但 link 仍会继续): {e}")
    typer.echo("")

    # ── 3. Write loaders.yaml ──
    config_path = _ws_config_dir() / "loaders.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    src_posix = str(src_abs).replace("\\", "/")
    yaml_text = (
        f"# Auto-generated by `fa data link` {datetime.now():%Y-%m-%d %H:%M}\n"
        f"# Source: {src_abs}\n"
        f"default: qlib_binary\n"
        f"\n"
        f"loaders:\n"
        f"  qlib_binary:\n"
        f"    provider_uri:\n"
        f"      day: {src_posix}/cn_data\n"
    )
    if have["cn_data_5min"]:
        yaml_text += f"      5min: {src_posix}/cn_data_5min\n"
    if have["parquet"]:
        yaml_text += f"    parquet_root: {src_posix}/parquet\n"
    if have["news_data"]:
        yaml_text += f"    news_data_root: {src_posix}/news_data\n"

    # Backup existing
    if config_path.exists():
        bak = config_path.with_suffix(config_path.suffix
                                       + f".bak.{datetime.now():%Y%m%d_%H%M%S}")
        try:
            shutil.copy2(config_path, bak)
            typer.echo(f"  📦 备份旧配置 → {bak.name}")
        except Exception:
            pass

    config_path.write_text(yaml_text, encoding="utf-8")
    typer.echo(f"  ✓ 写入 {config_path}")
    typer.echo("")

    # ── 4. Mark last-update so the UI badge doesn't lie ──
    try:
        from financial_analyst.data import last_update as _lu
        marks = ["day"]
        if have["cn_data_5min"]:
            marks.append("5min")
        _lu.mark_many(marks)
        typer.echo(f"  ✓ last-update 时间戳已写 ({', '.join(marks)})")
    except Exception as e:
        typer.echo(f"  ⚠ 写 last-update 失败 (不影响功能): {e}")

    typer.echo("")
    typer.echo(f"=== Link 完成 ===")
    typer.echo(f"  下一步:")
    typer.echo(f"    fa data status      # 验证数据接通")
    typer.echo(f"    fa report SH600519  # 跑第一份研报")


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
