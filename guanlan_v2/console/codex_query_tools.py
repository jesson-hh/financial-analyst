"""Strict query-only research adapters for the scoped Codex MCP.

The legacy alpha-zoo tools mix useful calculations with optional persistence,
PATH-selected subprocesses and universe strings that may be arbitrary files.
This module keeps the calculation but removes those authorities:

* only server-known named universes are accepted;
* universe membership is resolved from bundled lists or the platform F10
  constituent parquet, never from a model-supplied path or a home override;
* the Qlib binary loader is instantiated directly (local reads only), so a
  future default-loader switch cannot turn a Codex query into a network/cache
  write;
* no bench CSV, research archive or background process is created.
"""
from __future__ import annotations

import math
import re
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)\d{6}$")
_BUNDLED_UNIVERSES = frozenset({"sample30", "csi300_active"})
_F10_UNIVERSES = frozenset({"csi300", "csi500", "csi800"})
CODEX_FACTOR_UNIVERSES = _BUNDLED_UNIVERSES | _F10_UNIVERSES
# Full-zoo bench evaluates hundreds of formulas.  Keep the first release on
# the bundled 30-stock sample so one scoped query cannot monopolise the server
# with a 500-800 stock panel.  Broader universes can be reconsidered after a
# separate worker/timeout quota exists.
CODEX_ALPHA_BENCH_UNIVERSES = frozenset({"sample30"})
_TRUSTED_ALPHA_MODULES = {
    "alpha101": "financial_analyst.factors.zoo.alpha101.alphas",
    "gtja191": "financial_analyst.factors.zoo.gtja191.alphas",
    "qlib158": "financial_analyst.factors.zoo.qlib158.alphas",
}
# A deterministic cross-family baseline keeps an interactive query bounded.
# ``top`` controls display only in the upstream runner, so passing all 442
# built-ins would otherwise occupy the shared server for minutes.
_ALPHA_BENCH_BASELINE_V1 = (
    "alpha001", "alpha006", "alpha012", "alpha019",
    "alpha026", "alpha033", "alpha041", "alpha060",
    "gtja001", "gtja005", "gtja012", "gtja021",
    "gtja033", "gtja044", "gtja055", "gtja088",
    "qlib_BETA20", "qlib_CNTD20", "qlib_CNTP20", "qlib_CORD20",
    "qlib_CORR20", "qlib_IMAX20", "qlib_IMIN20", "qlib_KMID",
)
_ALPHA_BENCH_LOCK = threading.BoundedSemaphore(1)


def _trusted_alpha_specs():
    """Return only formulas shipped by the installed financial-analyst zoo.

    The process registry can also contain user-saved formulas.  Those are not
    safe to execute in a query-only boundary because their compute callable is
    mutable process state and may contain side effects.
    """
    from financial_analyst.factors.zoo.registry import list_alphas

    out = []
    for spec in list_alphas():
        expected = _TRUSTED_ALPHA_MODULES.get(str(spec.family))
        module = str(getattr(spec.compute, "__module__", ""))
        if expected and module == expected:
            out.append(spec)
    return tuple(sorted(out, key=lambda spec: spec.name))


def codex_trusted_alpha_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in _trusted_alpha_specs())


def codex_alpha_bench_names() -> tuple[str, ...]:
    trusted = set(codex_trusted_alpha_names())
    return tuple(name for name in _ALPHA_BENCH_BASELINE_V1 if name in trusted)


def _trusted_alpha_spec(name: str):
    key = str(name or "").strip()
    for spec in _trusted_alpha_specs():
        if spec.name == key:
            return spec
    raise ValueError(f"Codex factor_report 只允许内置已注册 Alpha 名称: {key or '(空)'}")


def _clean_codes(values: Iterable[Any], *, universe: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in values:
        code = str(raw or "").strip().upper()
        if not _CODE_RE.fullmatch(code):
            continue
        if code not in seen:
            seen.add(code)
            out.append(code)
    if not out:
        raise ValueError(f"内置股票池 {universe} 当前没有可用代码")
    if len(out) > 1200:
        raise ValueError(f"内置股票池 {universe} 超过 Codex 查询上限 1200 只")
    return out


def _trusted_universe_codes(universe: str) -> List[str]:
    """Resolve one exact named universe without consulting caller paths/home."""
    name = str(universe or "").strip()
    if name in _BUNDLED_UNIVERSES:
        from financial_analyst._config import bundled_config_dir

        root = (Path(bundled_config_dir()) / "universes").resolve()
        path = (root / f"{name}.txt").resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:  # pragma: no cover - name is a fixed enum
            raise ValueError("内置股票池路径越界") from exc
        if not path.is_file():
            raise ValueError(f"内置股票池文件缺失: {name}")
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.split("#", 1)[0].strip()
            if value:
                rows.append(value)
        return _clean_codes(rows, universe=name)

    if name in _F10_UNIVERSES:
        from financial_analyst.data.paths import get_data_paths
        from financial_analyst.data.updaters.f10 import resolve_universe

        paths = get_data_paths()
        rows = resolve_universe(paths.parquet_root, name)
        return _clean_codes(rows, universe=name)

    raise ValueError(f"Codex 只允许内置股票池: {name or '(空)'}")


def _read_only_loaders():
    """Return local-binary quote/benchmark/optional-industry readers only."""
    from financial_analyst.data.paths import get_data_paths
    from financial_analyst.data.loaders.qlib_binary import QlibBinaryLoader
    from financial_analyst.data.loaders.benchmark import BenchmarkLoader

    loader = QlibBinaryLoader(get_data_paths().qlib_uri)
    benchmark_loader = BenchmarkLoader(loader=loader, benchmark="csi300")

    # Do not call industry_map_path(): that legacy helper mkdirs the home cache.
    # Passing an existing exact path to IndustryLoader is a read-only operation.
    industry_loader = None
    cache_path = Path.home() / ".financial-analyst" / "cache" / "industry_map.parquet"
    if cache_path.is_file():
        from financial_analyst.data.loaders.industry import IndustryLoader
        industry_loader = IndustryLoader(cache_path=cache_path)
    return loader, benchmark_loader, industry_loader


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    return f"{number:+.{digits}f}"


def codex_alpha_bench_query_impl(
    universe: str = "sample30",
    since: str = "2024-06-01",
    until: str = "2024-12-31",
    top: int = 15,
    *,
    save: bool = False,
) -> Dict[str, Any]:
    """Run the alpha battery in-process without saving or spawning a CLI."""
    if save is not False:
        raise ValueError("Codex alpha_bench 不允许保存")
    if universe not in CODEX_ALPHA_BENCH_UNIVERSES:
        raise ValueError(f"Codex alpha_bench 不允许股票池: {universe}")
    if not _ALPHA_BENCH_LOCK.acquire(blocking=False):
        raise RuntimeError("已有 Alpha 只读跑分正在执行，请稍后重试")
    try:
        codes = _trusted_universe_codes(universe)
        trusted_names = list(codex_alpha_bench_names())
        if not trusted_names:
            raise RuntimeError("内置 Alpha 注册表当前不可用")
        loader, benchmark_loader, industry_loader = _read_only_loaders()
        from financial_analyst.factors.zoo.panel import PanelData
        from financial_analyst.factors.zoo.bench_runner import run_bench

        panel = PanelData.from_loader(
            loader, codes, since, until, freq="day",
            industry_loader=industry_loader, benchmark_loader=benchmark_loader,
        )
        results = run_bench(panel, names=trusted_names, prev_bench=None)
    finally:
        _ALPHA_BENCH_LOCK.release()
    limit = max(1, min(int(top), 25))
    rows = results.head(limit)
    lines = [
        f"# Alpha 内置基线组只读跑分 · {universe}",
        f"{since}~{until} · {panel.n_codes()} 只 · {panel.n_dates()} 个交易日",
        f"仅执行固定内置基线组 {len(trusted_names)} 项；未保存 bench CSV，"
        "未读取上次 bench，不启动后台任务。",
        "",
        "name | family | RankIC | RankIR | hit | n_dates | state",
    ]
    for _, row in rows.iterrows():
        hit = row.get("hit_rate")
        hit_text = (f"{float(hit):.1%}" if hit is not None
                    and math.isfinite(float(hit)) else "—")
        lines.append(
            f"{row.get('name')} | {row.get('family')} | "
            f"{_fmt(row.get('rank_ic'), 4)} | {_fmt(row.get('rank_ir'), 3)} | "
            f"{hit_text} | {int(row.get('n_dates') or 0)} | {row.get('state') or '—'}"
        )
    errors = int((results.get("status") != "ok").sum()) if len(results) else 0
    if errors:
        lines.append(f"\n⚠ {errors} 个因子计算失败；失败项未冒充有效结果。")
    return {
        "ok": True, "content": "\n".join(lines), "artifact": None,
        "raw": {"universe": universe, "n_codes": panel.n_codes(),
                "n_dates": panel.n_dates(), "shown": len(rows),
                "evaluated": len(trusted_names), "baseline": "builtin_v1",
                "errors": errors, "persisted": False},
    }


def codex_factor_report_query_impl(
    expr_or_name: str,
    universe: str = "csi500",
    freq: str = "month",
    start: Optional[str] = None,
    end: Optional[str] = None,
    *,
    archive: bool = False,
    note: str = "",
) -> Dict[str, Any]:
    """Build a full factor report over explicit codes without archiving."""
    if archive is not False or note:
        raise ValueError("Codex factor_report 不允许归档或备注写入")
    if universe not in CODEX_FACTOR_UNIVERSES:
        raise ValueError(f"Codex factor_report 不允许股票池: {universe}")
    expr = str(expr_or_name or "").strip()
    if not expr:
        raise ValueError("factor_report 缺少 expr_or_name")

    # Reject an unknown/free-form expression before opening even the local
    # panel.  Besides being safer, this prevents an invalid call from paying
    # the cost of a two-year csi500 load.
    spec = _trusted_alpha_spec(expr)
    codes = _trusted_universe_codes(universe)
    last = end or date.today().isoformat()
    first = start or (date.fromisoformat(last) - timedelta(days=365 * 2)).isoformat()
    loader, _benchmark_loader, industry_loader = _read_only_loaders()
    from financial_analyst.factors.eval import EvalConfig
    from financial_analyst.factors.eval.report import build_report
    from financial_analyst.factors.zoo.panel import PanelData

    panel = PanelData.from_loader(
        loader, codes, first, last, freq="day",
        industry_loader=industry_loader,
    )
    # Do not execute the legacy free-form expression DSL here.  Attribute
    # calls on a pandas Series (for example ``close.to_csv(path)``) would be a
    # write primitive even with Python builtins removed.  Scoped Codex calls
    # therefore select one immutable built-in registry entry only.
    compute, family, label = spec.compute, spec.family, spec.name
    config = EvalConfig(universe=universe, freq=freq, start=first, end=last)
    report = build_report(panel, compute, config, label, family)
    if report.status != "ok":
        return {
            "ok": False, "artifact": None,
            "content": f"因子评测未完成(status={report.status}): {report.error}",
            "raw": {"status": report.status, "persisted": False},
        }

    meta = report.meta
    if int(meta.n_dates or 0) <= 0:
        return {
            "ok": False, "artifact": None,
            "content": (
                f"因子评测无有效调仓期: {meta.factor} · {universe} · "
                f"{meta.start}~{meta.end}。未写 research archive。"
            ),
            "raw": {"status": "empty", "universe": universe,
                    "n_codes": int(meta.n_codes or 0), "n_dates": 0,
                    "persisted": False},
        }

    ic = report.ic
    quantile = report.quantile
    portfolio = report.portfolio
    char = report.characteristics
    lines = [
        f"# 因子评测只读报告 · {meta.factor}",
        f"池 {universe} ({meta.n_codes} 只) · {meta.freq}频 · "
        f"{meta.start}~{meta.end} · {meta.n_dates} 期 · fwd={meta.fwd_days}d",
        "本次未写 research archive。",
        "",
        f"IC = {_fmt(ic.ic_mean, 4)} | ICIR = {_fmt(ic.icir)} | "
        f"t = {_fmt(ic.ic_tstat, 2)} | 命中 = {_fmt(ic.ic_win_rate, 2)}",
        f"RankIC = {_fmt(ic.rank_ic_mean, 4)} | RankICIR = {_fmt(ic.rank_icir)}",
        f"十分位单调性 = {_fmt(quantile.monotonicity, 2)} | "
        f"多空价差(年化) = {_fmt(quantile.long_short_spread, 3)}",
        f"多空组合: 年化 = {_fmt(portfolio.ann_return, 3)} | "
        f"Sharpe = {_fmt(portfolio.sharpe, 2)} | "
        f"最大回撤 = {_fmt(portfolio.max_drawdown, 3)} | "
        f"换手 = {_fmt(portfolio.turnover, 2)} | 胜率 = {_fmt(portfolio.win_rate, 2)}",
        f"覆盖率 = {_fmt(char.coverage, 2)} | 自相关 = {_fmt(char.autocorr_1, 2)} | "
        f"半衰期 = {'—' if char.half_life < 0 else format(char.half_life, '.0f')} 期",
    ]
    lines.extend(f"⚠ {warning}" for warning in report.warnings)
    return {
        "ok": True, "content": "\n".join(lines), "artifact": None,
        "raw": {"universe": universe, "n_codes": meta.n_codes,
                "n_dates": meta.n_dates, "persisted": False},
    }
