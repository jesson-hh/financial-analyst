"""Build one canonical MCP report from the audited frozen six-strategy comparison.

This script neither runs models/backtests nor renders, publishes, or selects trades.
Usage: python scripts/build_strategy_comparison_report.py
The root agent validates artifact.json and owns the single visible MCP handoff.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/trend_strategy_comparison_20260912"
PLAN_SHA256 = "ccaefca902c2dd8b1c1c47d5a489cdfbe3bbadf5bb5618a475c89e71a2b17cfe"
TITLE = "Trend Pullback Strategy Review"
ORDER = [
    "trend_baseline", "pullback_ma10", "pullback_ma20",
    "pullback_ma10_or_ma20", "pullback_dry_volume", "pullback_rebound",
]
PERIODS = {"development": ("2023-04-03", "2024-12-31"),
           "validation": ("2025-01-01", "2025-12-31"),
           "recent_descriptive": ("2026-01-01", "2026-09-11"),
           "full": ("2023-04-03", "2026-09-11")}
METRIC_FIELDS = [
    "net_total_return", "max_drawdown", "mean_exposure", "noncash_days",
    "annualized_traded_notional_over_NAV", "total_cost_CNY",
    "stale_mark_holding_days", "n_days",
]

# These are executed report projections, not invented upstream analysis queries.
# Bindings carry the immutable comparison document and explicit presentation labels.
COMPARISON_SQL = """SELECT
  CAST(o.key AS INTEGER) + 1 AS "order",
  json_extract(s.value, '$.id') AS strategy_id,
  json_extract(s.value, '$.name') AS strategy,
  json_extract(s.value, '$.periods.validation.net_total_return') AS validation_net_return,
  json_extract(s.value, '$.periods.validation.max_drawdown') AS validation_drawdown,
  json_extract(s.value, '$.periods.recent_descriptive.net_total_return') AS recent_net_return,
  json_extract(s.value, '$.periods.full.net_total_return') AS full_net_return,
  1 - json_extract(s.value, '$.periods.validation.mean_exposure') AS validation_mean_cash,
  json_extract(s.value, '$.periods.validation.annualized_traded_notional_over_NAV') AS validation_annualized_turnover,
  json_extract(s.value, '$.periods.validation.total_cost_CNY') AS validation_cost_CNY,
  json_extract(s.value, '$.cost_sensitivity.zero_cost.validation.net_total_return') AS validation_zero_cost_return,
  json_extract(s.value, '$.cost_sensitivity.double_cost.validation.net_total_return') AS validation_double_cost_return,
  json_extract(s.value, '$.cost_sensitivity.double_cost.validation.net_total_return')
    - json_extract(s.value, '$.periods.validation.net_total_return') AS double_cost_minus_base,
  json_extract(s.value, '$.periods.validation.n_days') AS validation_days,
  json_extract(s.value, '$.periods.validation.noncash_days') AS validation_noncash_days,
  json_extract(s.value, '$.periods.validation.stale_mark_holding_days') AS validation_stale_holding_days,
  json_extract(:evidence_json, '$.' || json_extract(s.value, '$.id')) AS validation_evidence,
  json_extract(s.value, '$.receipt.run_id') AS run_id,
  1 - json_extract(s.value, '$.periods.full.mean_exposure') AS full_mean_cash,
  json_extract(s.value, '$.periods.full.total_cost_CNY') AS full_cost_CNY,
  json_extract(s.value, '$.capacity_proxy.p95_participation_proxy') AS p95_participation_proxy,
  json_extract(s.value, '$.periods.full.execution_failure_count') AS full_execution_failures
FROM json_each(:comparison_json, '$.strategies') AS s
JOIN json_each(:order_json) AS o ON o.value = json_extract(s.value, '$.id')
ORDER BY CAST(o.key AS INTEGER)"""

WEEKLY_SQL = """SELECT
  json_extract(w.value, '$.date') AS date,
  json_extract(s.value, '$.name') || CASE WHEN o.value = 'trend_baseline' THEN ' (baseline)' ELSE '' END AS strategy,
  o.value AS strategy_id,
  json_extract(w.value, '$.nav') AS nav,
  json_extract(w.value, '$.exposure') AS exposure,
  json_extract(w.value, '$.holdings') AS holdings,
  1000000 AS initial_capital_CNY,
  o.value = 'trend_baseline' AS is_baseline,
  CASE WHEN json_extract(w.value, '$.date') < '2025-01-01' THEN 'development'
       WHEN json_extract(w.value, '$.date') < '2026-01-01' THEN 'validation'
       ELSE 'recent_descriptive' END AS period,
  json_extract(s.value, '$.receipt.run_id') AS run_id
FROM json_each(:comparison_json, '$.strategies') AS s
JOIN json_each(:order_json) AS o ON o.value = json_extract(s.value, '$.id')
CROSS JOIN json_each(s.value, '$.weekly_nav') AS w
ORDER BY CAST(o.key AS INTEGER), json_extract(w.value, '$.date')"""

DIAGNOSTICS_SQL = """SELECT
  CAST(r.key AS INTEGER) + 1 AS "order",
  json_extract(r.value, '$.check') AS "check",
  json_extract(r.value, '$.available') AS available,
  json_extract(r.value, '$.total') AS total,
  CAST(json_extract(r.value, '$.available') AS REAL) / json_extract(r.value, '$.total') AS fraction,
  COALESCE(json_extract(r.value, '$.unit'), '条') AS unit,
  COALESCE(json_extract(r.value, '$.note'), json_extract(r.value, '$.available_meaning'), '') AS note
FROM json_each(:comparison_json, '$.diagnostics.coverage_rows') AS r
ORDER BY CAST(r.key AS INTEGER)"""


def project_sql(comparison, sql, **extra_bindings):
    bindings = {"comparison_json": json.dumps(comparison, ensure_ascii=False, allow_nan=False),
                "order_json": json.dumps(ORDER), **extra_bindings}
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute(sql, bindings)]
    return rows


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


def percent(value):
    return f"{number(value, 'percentage'):+.2%}"


def quality_label(item, period="validation"):
    p = item["periods"][period]
    flags = []
    if p["noncash_days"] < 60:
        flags.append("少于60个非现金日")
    if p["stale_mark_holding_days"] > 0:
        flags.append("存在陈旧估值")
    if p.get("terminal_unresolved_positions"):
        flags.append("期末持仓未解决")
    if item.get("quality_flags", {}).get("has_invalid_screen_dates"):
        flags.append("存在无效筛选日")
    return "；".join(flags) or "账本通过；仍受静态池限制"


def validate_input(comparison):
    """Fail closed: an incomplete or changed experiment is not a complete report."""
    json.dumps(comparison, allow_nan=False)
    if comparison.get("schema") != "guanlan.strategy_comparison.v1":
        raise ValueError("Unexpected comparison schema")
    if comparison.get("performance_ready") is False or comparison.get("diagnostics", {}).get("blocking_reasons"):
        raise ValueError("Data-quality blockers prohibit performance comparison; use diagnostics_only")
    for key in ("all_six_complete", "shared_panel_verified", "shared_provenance_verified"):
        if comparison.get(key) is not True:
            raise ValueError("Comparison guard not passed: " + key)
    if comparison.get("status") != "complete" or comparison.get("plan_sha256") != PLAN_SHA256:
        raise ValueError("Comparison incomplete or frozen plan changed")
    if comparison.get("automatic_trading") is not False or comparison.get("unseen_oos_claim") is not False:
        raise ValueError("Research/no-unseen-OOS boundary must be explicit")
    strategies = comparison.get("strategies", [])
    if len(strategies) != 6 or {s.get("id") for s in strategies} != set(ORDER):
        raise ValueError("Exactly the six frozen strategies are required, including weak variants")
    dates = None
    run_ids = set()
    for item in strategies:
        if item.get("audit", {}).get("passed") is not True:
            raise ValueError("Ledger audit did not pass: " + item["id"])
        receipt = item["receipt"]
        run_id = receipt.get("run_id", "")
        if receipt.get("status") != "completed" or not re.fullmatch(r"wr_[a-zA-Z0-9]+", run_id):
            raise ValueError("Missing completed durable run receipt")
        run_ids.add(run_id)
        for pid, bounds in PERIODS.items():
            p = item["periods"][pid]
            for field in METRIC_FIELDS:
                number(p[field], f"{item['id']}.{pid}.{field}")
            if p["n_days"] <= 0 or not bounds[0] <= p["start"] <= p["end"] <= bounds[1]:
                raise ValueError("Invalid period calendar: " + pid)
            if not 0 <= p["mean_exposure"] <= 1 + 1e-8:
                raise ValueError("Exposure outside unlevered range")
            if not 0 <= p["noncash_days"] <= p["n_days"]:
                raise ValueError("Noncash denominator mismatch")
            for cost in ("zero_cost", "double_cost"):
                number(item["cost_sensitivity"][cost][pid]["net_total_return"], cost)
        weekly = item["weekly_nav"]
        seen = [r["date"] for r in weekly]
        if len(seen) < 8 or seen != sorted(set(seen)):
            raise ValueError("Weekly NAV lacks a unique ordered trend")
        if dates is not None and seen != dates:
            raise ValueError("Strategies have different weekly NAV calendars")
        dates = seen
        for row in weekly:
            for field in ("nav", "exposure", "holdings"):
                number(row[field], "weekly " + field)
            if row["nav"] <= 0 or not PERIODS["full"][0] <= row["date"] <= PERIODS["full"][1]:
                raise ValueError("Invalid observed weekly NAV point")
    if len(run_ids) != 6 or len(dates or []) * 6 > 2000:
        raise ValueError("Run IDs must be unique; bounded trend dataset must stay within 2,000 rows")
    return sorted(strategies, key=lambda s: ORDER.index(s["id"]))


def pareto_front(strategies):
    """Descriptive return/drawdown/turnover frontier, never an automatic winner."""
    eligible = [s for s in strategies
                if s["periods"]["validation"]["noncash_days"] >= 60
                and s["periods"]["validation"]["stale_mark_holding_days"] == 0
                and not s["periods"]["validation"].get("terminal_unresolved_positions")
                and not s.get("quality_flags", {}).get("has_invalid_screen_dates")]
    def values(s):
        p = s["periods"]["validation"]
        return (p["net_total_return"], p["max_drawdown"],
                -p["annualized_traded_notional_over_NAV"])
    out = []
    for a in eligible:
        av = values(a)
        dominated = any(all(bv >= aa - 1e-12 for bv, aa in zip(values(b), av))
                        and any(bv > aa + 1e-12 for bv, aa in zip(values(b), av))
                        for b in eligible if b["id"] != a["id"])
        if not dominated:
            out.append(a)
    return out


def build_diagnostic_artifact(comparison, *, source_sha256=None, generated_at=None):
    """A completed diagnostic report, not a partially credible return comparison.

    Schema: diagnostics.blocking_reasons/next_steps are nonempty string arrays;
    coverage_rows contains check/available/total and optional unit/note.
    available is the count named by check, never a universal quality-pass count.
    Only the explicitly named OHLC contradiction check becomes a bar; overlapping
    measures remain a labeled table. Strategy returns are never copied to output.
    """
    json.dumps(comparison, allow_nan=False)
    if comparison.get("schema") != "guanlan.strategy_comparison.v1":
        raise ValueError("Unexpected comparison schema")
    if comparison.get("plan_sha256") != PLAN_SHA256:
        raise ValueError("Frozen experiment plan changed")
    if comparison.get("automatic_trading") is not False or comparison.get("unseen_oos_claim") is True:
        raise ValueError("Diagnostic research boundary must be explicit")
    diagnostics = comparison.get("diagnostics", {})
    for name in ("blocking_reasons", "next_steps"):
        entries = diagnostics.get(name)
        if not isinstance(entries, list) or not entries or not all(isinstance(x, str) and x.strip() for x in entries):
            raise ValueError("Diagnostic " + name + " must be a nonempty string array")
    input_rows = diagnostics.get("coverage_rows")
    if not isinstance(input_rows, list) or not 1 <= len(input_rows) <= 40:
        raise ValueError("Diagnostic report requires 1–40 verified count rows, not invented chart data")
    checks = set()
    for row in input_rows:
        if not isinstance(row.get("check"), str) or not row["check"].strip() or row["check"] in checks:
            raise ValueError("Each diagnostic check needs a unique descriptive label")
        checks.add(row["check"])
        available = number(row.get("available"), "diagnostic count")
        total = number(row.get("total"), "diagnostic denominator")
        if available != int(available) or total != int(total) or not 0 <= available <= total or total <= 0:
            raise ValueError("Diagnostic count needs a positive explicit integer denominator")
    chart_check = diagnostics.get("chart_check")
    if chart_check is None:
        normalized_matches = [check for check in checks if re.sub(r"\s+", "", check) == "OHLC硬约束矛盾"]
        if len(normalized_matches) != 1:
            raise ValueError("Diagnostic OHLC contradiction label must be unambiguous")
        chart_check = normalized_matches[0]
    if chart_check not in checks or "OHLC" not in chart_check or "矛盾" not in chart_check:
        raise ValueError("Diagnostic chart must explicitly identify verified OHLC contradictions")
    rows = project_sql(comparison, DIAGNOSTICS_SQL)
    chart_rows = [r for r in rows if r["check"] == chart_check]
    finding = chart_rows[0]
    title = "Strategy Validation Blocked"
    generated_at = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_sha256 = source_sha256 or hashlib.sha256(
        json.dumps(comparison, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    run_ids = sorted({s.get("receipt", {}).get("run_id") for s in comparison.get("strategies", [])
                      if re.fullmatch(r"wr_[a-zA-Z0-9]+", s.get("receipt", {}).get("run_id", ""))})
    # Chart source states the actual additional WHERE that produces its one row.
    chart_sql = "SELECT * FROM (\n" + DIAGNOSTICS_SQL + "\n) WHERE \"check\" = :chart_check"
    sql_chart_rows = project_sql(comparison, chart_sql, chart_check=chart_check)
    if sql_chart_rows != chart_rows:
        raise ValueError("Diagnostic chart SQL differs from the verified count")
    base_source = {
        "id": "diagnostics", "label": "Verified replay data diagnostics", "path": "comparison.json",
        "sha256": source_sha256, "plan_sha256": PLAN_SHA256, "durable_runs": run_ids,
        "query": {"engine": "SQLite3 JSON1 report projection", "language": "sql", "sql": DIAGNOSTICS_SQL,
                  "description": "Executed SELECT extracts only audited count rows and their explicit denominators from comparison.json. analyze_strategy_comparison.py supplies diagnostics using guanlan_v2/workflow/history_price_quality.py. SQL performs report projection, not market-data certification. No returns or strategy rankings are included.",
                  "bindings": {"comparison_json": "Entire comparison.json document; sha256 above"},
                  "tables_used": ["comparison.json", "experiment_plan.json", "scripts/analyze_strategy_comparison.py",
                                  "guanlan_v2/workflow/history_price_quality.py"],
                  "metric_definitions": {
                      "available": "Verified count for the event explicitly named by check; not necessarily a passed-check count.",
                      "total": "Explicit denominator at that row's grain; cannot sum across checks or assume denominators match.",
                      "fraction": "available / total; a count share, not a probability of loss or an estimate of return bias.",
                      "OHLC contradiction": "Intraday hard price relationships fail. The other rows are not certified good data by passing this one test."}}}
    chart_source = {**base_source, "id": "ohlc_diagnostic", "label": "OHLC contradiction share",
                    "query": {**base_source["query"], "sql": chart_sql,
                              "bindings": {"comparison_json": "Entire comparison.json document; sha256 above",
                                           "chart_check": chart_check}}}
    sources = [base_source, chart_source,
               {"id": "implementation", "label": "Guanlan daily replay method", "path": "docs/daily_strategy_replay.md"},
               {"id": "builder", "label": "Report projection implementation", "path": "scripts/build_strategy_comparison_report.py"},
               {"id": "price_quality", "label": "Historical price quality checks", "path": "guanlan_v2/workflow/history_price_quality.py"},
               {"id": "order_timing", "label": "Backtrader order execution reference",
                "href": "https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/"},
               {"id": "time_split", "label": "scikit-learn time-series evaluation reference",
                "href": "https://sklearn.org/stable/modules/cross_validation.html"}]
    reasons = "\n".join("- " + reason for reason in diagnostics["blocking_reasons"])
    next_steps = "\n".join(f"{i}. {step}" for i, step in enumerate(diagnostics["next_steps"], 1))
    count_sentence = (f"共{int(finding['total']):,}个已检查行情行中，"
                      f"{int(finding['available']):,}行违反OHLC硬约束（{finding['fraction']:.4%}）。")
    source_counts = diagnostics.get("source_price_quality", {}).get("counts", {})
    coverage_sentence = ""
    if all(key in source_counts for key in ("source_start", "source_end", "source_codes", "source_calendar_days")):
        coverage_sentence = (f"**核查范围：** {source_counts['source_start']}至{source_counts['source_end']}，"
                             f"{int(number(source_counts['source_codes'], 'source codes'))}只股票、"
                             f"{int(number(source_counts['source_calendar_days'], 'source days'))}个源交易日，包含策略启动前的均线预热区间。\n\n")
        base_source["query"]["filters"] = {key: source_counts[key] for key in
                                              ("source_start", "source_end", "source_codes", "source_calendar_days")}
        base_source["price_fingerprint"] = diagnostics.get("source_price_quality", {}).get("price_fingerprint", {})
    def md(identifier, body, source="diagnostics"):
        block = {"id": identifier, "type": "markdown", "body": body, "layout": "full"}
        if source:
            block["sourceId"] = source
        return block
    chart = {
        "id": "ohlc_share", "title": "OHLC硬约束矛盾占比",
        "subtitle": "仅显示这一项已核实异常；其余行情行不等于已通过质量审查",
        "showDescription": True, "type": "horizontalBar", "intent": "comparison",
        "question": "What share of inspected price rows violates the specified OHLC hard constraints?",
        "dataset": "ohlc_diagnostic", "sourceId": "ohlc_diagnostic", "layout": "full",
        "valueFormat": "percent", "palette": {"kind": "categorical"},
        "labels": {"values": "all"}, "settings": {"sort": "none", "showValues": True},
        "referenceLines": [{"axis": "y", "value": 0, "label": "0%", "color": "neutral", "lineStyle": "solid"}],
        "encodings": {"x": {"field": "check", "type": "nominal", "label": "检查项"},
                      "y": {"field": "fraction", "type": "quantitative", "label": "该项矛盾行/已检查行情行", "format": "percent"},
                      "tooltip": [{"field": "available", "type": "quantitative", "label": "矛盾行数", "format": "integer"},
                                  {"field": "total", "type": "quantitative", "label": "已检查行情行", "format": "integer"},
                                  {"field": "note", "type": "nominal", "label": "限定条件"}]}}
    table = {
        "id": "diagnostic_checks", "title": "已核查数据：数量与分母",
        "subtitle": "每行使用自己的明确分母；检查项可能重叠，不能相加为总坏数据量",
        "showDescription": True, "dataset": "diagnostic_counts", "sourceId": "diagnostics",
        "defaultSort": {"field": "order", "direction": "asc"}, "density": "spacious",
        "columns": [{"field": "order", "label": "序号", "type": "number"},
                    {"field": "check", "label": "核查对象", "type": "text"},
                    {"field": "available", "label": "该项数量", "format": "integer"},
                    {"field": "total", "label": "该项分母", "format": "integer"},
                    {"field": "unit", "label": "计数单位", "type": "text"},
                    {"field": "fraction", "label": "占该项分母", "format": "percent"},
                    {"field": "note", "label": "解释边界", "type": "text"}]}
    blocks = [
        md("title", "# " + title, None),
        md("executive_summary", "## Executive Summary\n\n"
           "- **策略收益比较已阻断，不能据此选优。** 本报告是数据诊断，不是回测有效性证明；不会展示任何收益排名、净值曲线或优胜策略。\n"
           f"- **存在可核查的行情矛盾。** {count_sentence}比例较小也不能证明影响较小：持仓权重与发生时点会放大损失。\n"
           "- **复权与公司行动覆盖需要先接通。** 隔夜异常可能来自正常市场、公司行动或坏行情，不能全部归为坏数据，更不能静默剔除后再排名。\n"
           "- **仍限于选股与策略研究。** 先修复并重跑同一冻结计划，不调参、不自动下单，2026也不改称全新盲测。"),
        md("blocking_findings", "## 数据问题阻断的是收益判断，而不只是界面展示\n\n" + reasons
           + "\n\n历史筛选和日频账户能执行，不等于账户收益已经可比。必须同时满足行情内部一致、信号复权口径一致、持仓公司行动可解释，才有条件讨论不同规则的差异。"),
        md("ohlc_context", "## 先核对价格之间的硬约束\n\n" + coverage_sentence + count_sentence
           + "图表从零开始，只呈现这一项矛盾占比；没有把未命中此项的行情行涂成‘合格’。"
             "OHLC约束之外仍可能存在复权、单位、停牌、缺价及历史股票池问题。这个比例不能估计策略损失，也不能推导修复后的收益。"),
        {"id": "ohlc_chart", "type": "chart", "chartId": chart["id"], "layout": "full"},
        md("coverage_context", "## 各项缺口使用不同分母，不能直接合并\n\n"
           "下表原样保留已核实计数与计数单位。行情行、隔夜异常事件、持仓日和公司行动事件不是同一粒度；"
           "即使都写成百分比，也不适合排序或相加。‘有复权因子’仅说明可获得该字段，不等于现金分红、送转、配股和退市现金流已被账户准确处理。"),
        {"id": "diagnostic_table", "type": "table", "tableId": table["id"], "layout": "full"},
        md("next_steps", "## 先修正输入和账户，再重跑冻结策略\n\n" + next_steps
           + "\n\n六个变体及其失败记录继续保留；修复属于数据和实现版本变更，不在看到结果后选择性调整形态阈值。"),
        md("further_questions", "## 解除阻断前必须回答的问题\n\n"
           "- **公司行动后的价格和持股权益是否连续可解释？** 需要区分信号复权序列与可成交原始价格，并逐事件核查持股数量、现金权益和缺失覆盖。\n"
           "- **同样规则在合格数据上的结论是否稳定？** 修复后按原日历、固定Top10槽位和成本重跑六组；2025仍为验证段，2026仍只作已接触的近期描述。", None),
        md("caveats", "## 本报告的证据边界\n\n"
           "这里不公布故障行情产生的净收益、最大回撤或排名，避免把机械除权变化误读成策略成败。"
           "本报告只确认输入中审计程序已经核实的计数，不把异常自动归因为某一家数据提供方或某一策略。\n\n"
           "即使本轮缺口修复，静态中证800历史池、当前ST标签、分数股、统一费用、涨跌停及停牌成交约束仍限制实战外推。"
           "缺失行情不能前向填成未来价格；零成本结果必须是同一信号的反事实重跑，不能简单把现金费用加回。"),
    ]
    artifact = {"surface": "report", "manifest": {"version": 1, "surface": "report", "title": title,
                "generatedAt": generated_at, "blocks": blocks, "charts": [chart], "tables": [table], "sources": sources},
                "snapshot": {"version": 1, "status": "ready", "generatedAt": generated_at,
                             "validation_status": "diagnostics_only",
                             "datasets": {"diagnostic_counts": rows, "ohlc_diagnostic": sql_chart_rows}},
                "sources": sources}
    notes = {"delivery_mode": "mcp-app", "visible_render_performed": False,
             "status": "diagnostics_only", "strategy_comparison_blocked": True,
             "schema_validation": "local checks passed; native validator must pass before rendering",
             "report_source": "comparison.json", "source_sha256": source_sha256,
             "builder": "scripts/build_strategy_comparison_report.py", "durable_runs": run_ids,
             "chart_map": [{"id": chart["id"], "family": "comparison", "type": "horizontalBar",
                            "fields": ["check", "fraction"], "rows": 1, "zero_origin": True,
                            "claim": "Only the verified OHLC contradiction share; complement is NOT quality-certified"}],
             "structure_map": {"Title": "title", "Executive summary": "executive_summary",
                               "Key findings with visual evidence": ["blocking_findings", "ohlc_context", "ohlc_chart", "coverage_context", "diagnostic_table"],
                               "Recommended next steps": "next_steps", "Further questions": "further_questions",
                               "Caveats and assumptions": "caveats"},
             "omissions": {"returns_and_ranking": "blocked by data-quality/corporate-action issues",
                           "strategy_winner": "not supported", "parallel_html": "single MCP surface",
                           "sites_publish": "not requested", "live_trading": "out of scope"},
             "qa": {"no_return_or_ranking_datasets": True, "nonoverlapping_chart_measure": True,
                    "each_denominator_explicit": True, "render_inspection_required": True},
             "review_preview": rows[:10]}
    if len(json.dumps(artifact, ensure_ascii=False, allow_nan=False).encode()) > 3_000_000:
        raise ValueError("Canonical MCP artifact exceeds 3MB")
    return artifact, notes


def build_artifact(comparison, *, source_sha256=None, generated_at=None):
    if comparison.get("status") == "diagnostics_only":
        return build_diagnostic_artifact(comparison, source_sha256=source_sha256,
                                         generated_at=generated_at)
    strategies = validate_input(comparison)
    generated_at = generated_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_sha256 = source_sha256 or hashlib.sha256(
        json.dumps(comparison, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    lookup, weekly, periods = [], [], []
    for index, s in enumerate(strategies, 1):
        v, r, full = (s["periods"][pid] for pid in ("validation", "recent_descriptive", "full"))
        z = s["cost_sensitivity"]["zero_cost"]["validation"]["net_total_return"]
        d = s["cost_sensitivity"]["double_cost"]["validation"]["net_total_return"]
        rec = {
            "order": index, "strategy_id": s["id"], "strategy": s["name"],
            "validation_net_return": v["net_total_return"],
            "validation_drawdown": v["max_drawdown"],
            "recent_net_return": r["net_total_return"],
            "full_net_return": full["net_total_return"],
            "validation_mean_cash": 1 - v["mean_exposure"],
            "validation_annualized_turnover": v["annualized_traded_notional_over_NAV"],
            "validation_cost_CNY": v["total_cost_CNY"],
            "validation_zero_cost_return": z, "validation_double_cost_return": d,
            "double_cost_minus_base": d - v["net_total_return"],
            "validation_days": v["n_days"], "validation_noncash_days": v["noncash_days"],
            "validation_stale_holding_days": v["stale_mark_holding_days"],
            "validation_evidence": quality_label(s),
            "run_id": s["receipt"]["run_id"],
            "full_mean_cash": 1 - full["mean_exposure"],
            "full_cost_CNY": full["total_cost_CNY"],
            "p95_participation_proxy": s.get("capacity_proxy", {}).get("p95_participation_proxy"),
            "full_execution_failures": full.get("execution_failure_count"),
        }
        lookup.append(rec)
        for pid in PERIODS:
            p = s["periods"][pid]
            periods.append({"strategy": s["name"], "strategy_id": s["id"], "period": pid,
                            **{k: p[k] for k in ("start", "end", "n_days", "noncash_days",
                              "net_total_return", "max_drawdown", "mean_exposure",
                              "annualized_traded_notional_over_NAV", "total_cost_CNY",
                              "stale_mark_holding_days")}, "run_id": s["receipt"]["run_id"]})
        # Keep audit context richer than the three chart fields.
        for row in s["weekly_nav"]:
            weekly.append({"date": row["date"], "strategy": s["name"] + (" (baseline)" if s["id"] == "trend_baseline" else ""),
                           "strategy_id": s["id"], "nav": row["nav"],
                           "exposure": row["exposure"], "holdings": row["holdings"],
                           "initial_capital_CNY": 1000000,
                           "is_baseline": s["id"] == "trend_baseline",
                           "period": "development" if row["date"] < "2025-01-01" else
                                     "validation" if row["date"] < "2026-01-01" else "recent_descriptive",
                           "run_id": s["receipt"]["run_id"]})
    sql_lookup = project_sql(comparison, COMPARISON_SQL,
                             evidence_json=json.dumps({s["id"]: quality_label(s) for s in strategies}, ensure_ascii=False))
    sql_weekly = project_sql(comparison, WEEKLY_SQL)
    # Independently match all projected chart/table fields, including nulls.
    if sql_lookup != lookup or sql_weekly != weekly:
        raise ValueError("SQLite chart projection differs from the reviewed comparison fields")
    lookup, weekly = sql_lookup, sql_weekly
    ordered = sorted(lookup, key=lambda r: (-r["validation_net_return"], r["order"]))
    best, worst = ordered[0], ordered[-1]
    positive = [r for r in lookup if r["validation_net_return"] > 0]
    all_nonpositive = not positive
    sparse = [s["name"] for s in strategies if s["periods"]["validation"]["noncash_days"] < 60]
    stale = [s["name"] for s in strategies if s["periods"]["validation"]["stale_mark_holding_days"] > 0]
    unstable = [s["name"] for s in strategies
                if s["periods"]["validation"]["net_total_return"] *
                   s["periods"]["recent_descriptive"]["net_total_return"] < 0]
    frontier = pareto_front(strategies)
    baseline = next(r for r in lookup if r["strategy_id"] == "trend_baseline")
    outliers = comparison.get("source_price_profile", {}).get("overnight_ratio_outside_075_125", 0)
    bottom_line = ("六组规则在2025验证段均未取得正净收益，目前没有胜出策略。"
                   if all_nonpositive else
                   f"六组中{len(positive)}组在2025验证段取得正净收益，但尚不能据此认定存在稳定胜出策略。")
    caution_parts = ["中证800静态成分与当前ST元数据带来历史时点偏差；2026已被旧研究接触，不是盲测。"]
    if sparse:
        caution_parts.append("2025非现金日不足60天：" + "、".join(sparse) + "，证据不足。")
    if stale:
        caution_parts.append("2025存在陈旧估值：" + "、".join(stale) + "，其收益与回撤不能无条件比较。")
    if outliers:
        caution_parts.append(f"价源含{outliers}个开盘/前一交易日收盘比超出0.75–1.25的观察，可能是行情、复权或数据异常；尚未自动归因或剔除。")
    comparison_caution = "\n\n".join(caution_parts)

    def md(identifier, body, source="comparison"):
        block = {"id": identifier, "type": "markdown", "body": body, "layout": "full"}
        if source:
            block["sourceId"] = source
        return block

    exec_summary = (
        "## Executive Summary\n\n"
        f"- **选股与历史验证已形成可追溯研究结果。** {bottom_line}\n"
        f"- **2025排序只是条件内比较。** 净收益最高为{best['strategy']}（{percent(best['validation_net_return'])}），"
        f"最低为{worst['strategy']}（{percent(worst['validation_net_return'])}）；相同资金、固定Top10槽位和成本下比较，不能只按收益给冠军。\n"
        "- **2026只作近期描述。** 下文同时披露六组、成本敏感性及现金暴露；规则不因结果变差而删除或调参。\n"
        "- **用于手工筛选研究，不自动下单。** 候选是符合形态的股票，不是未来上涨概率；结论受静态股票池、缺价估值和成交假设限制。"
    )
    primary = (
        "## 先看2025净结果，再看回撤和资金使用\n\n"
        f"**{bottom_line}** 趋势基线的2025净收益为{percent(baseline['validation_net_return'])}。"
        "下图以零为参照，条形完整保留正负值；表中额外显示回撤、仓位和换手，避免把更低仓位或更高交易成本忽略。\n\n"
        "**统一口径：** 2025年1月1日至12月31日的实际交易日，沿用2023年4月3日起的连续账户，不在分段边界免费重置。"
        "收盘计算条件，次日开盘尝试原选股票；100万元、10个固定槽位，不足10只留现金。"
        "净收益包含双边3bp佣金、卖出5bp税、双边5bp滑点。\n\n" + comparison_caution
    )
    if frontier:
        tradeoff = "在非稀疏、无陈旧估值且无无效筛选日的组合中，收益较高、回撤较浅、换手较低三项共同衡量的非劣集合为：" + "、".join(s["name"] for s in frontier) + "。"
    else:
        tradeoff = "没有组合同时满足本报告用于非劣比较的最小证据条件；先保留结果供研究，不给优胜标签。"
    tradeoff += (" 非劣只表示存在取舍；即使入列，验证段不盈利也不等于值得采用。" if all_nonpositive else
                 " 非劣只表示在这三项之间未被另一组全面超过，不证明未来优势，也未校正多重尝试。")
    recent_positive = sum(r["recent_net_return"] > 0 for r in lookup)
    recent = (
        "## 近期表现不能替代验证段\n\n"
        f"**2026年截至9月11日，有{recent_positive}组净收益为正。** "
        + ("与2025收益方向相反的规则有：" + "、".join(unstable) + "；这些只能称阶段性差异。" if unstable else
           "六组没有出现2025与2026净收益符号相反，但方向一致仍不构成独立预测证据。")
        + "\n\n下图取每周最后一个实际交易日的账户净值，保留现金；每条曲线都从同一连续账户计算。"
          "曲线说明收益形成路径，日频账本才用于计算回撤和换手，不能拿周抽样低估日内或逐日风险。"
          "2026不足全年且并非全新盲测，不直接用累计收益与2025全年判胜负。"
    )
    double_positive = sum(r["validation_double_cost_return"] > 0 for r in lookup)
    sensitivity = (
        "## 更高成本下还能否保留研究价值\n\n"
        f"**将佣金、卖出税与滑点同时加倍后，2025仍有{double_positive}组净收益为正。** "
        "以下逐组展示零成本、原成本与双倍成本结果，原规则、信号日历及入选股票保持一致。\n\n"
        "**零成本是重新执行同一信号的反事实账户，不是净收益直接加回现金费用。** "
        "成本改变净值、可用现金和成交数量，所以资金路径可以不同；列名中的零成本不能当作实际可获得的毛收益。"
        "年化换手为逐日成交参考金额/开盘调仓前净值的均值乘252，不是股票更换比例。"
    )
    next_steps = (
        "## 用固定规则做前向观察，不追逐本次第一名\n\n"
        "1. 保留全部六组与冻结版本，先用最新候选检查形态和逐条入选证据；不切换默认模型、不下单。\n"
        "2. "
        + ("当前没有验证段盈利组，先研究现金暴露、换手和价源影响；不要通过反向因子或放松条件把结果调成正数。"
           if all_nonpositive else "将净收益、回撤、成本敏感性和证据完整性一起看；只把非劣组合列入后续观察，不自动认定冠军。")
        + "\n3. 用后续未接触的新日期继续记录候选和模拟结果。任何阈值变动应另建版本并注明新假设，旧结果保持不变。"
    )
    questions = (
        "## 仍需回答的两个问题\n\n"
        "- **改善历史池和成交约束后，排序是否还能保持？** 需要历史成分、历史ST与开盘时点停牌/涨跌停约束，以及一致复权和公司行动处理。\n"
        "- **规则是否能在新的行情阶段稳定工作？** 需要真正未被本轮研究接触的前向样本，而不是继续在同一段历史找最高收益。"
    )
    caveats = (
        "## 研究边界决定结论力度\n\n"
        "双边统一比例成本用于公平比较，并非按历史日期还原真实税费。分数股模拟不含最低佣金、整手、市场冲击或竞价排队成交保证。"
        "不能成交的入选股票不以未来有收益的股票替补；已有缺价持仓用最近已知价估值并披露，期末并非强制卖出的现金收益。\n\n"
        "容量列仅是100万元模拟下单笔参考成交额相对过去20日均成交额的代理，不能推出可容纳资金规模。"
        "账户正收益日比例、完整平仓回合胜率和选股命中率不是同一个指标。六个相关变体的探索比较不证明因果或统计显著性。\n\n"
        + comparison_caution
    )
    definitions = {
        "validation_net_return": "Continuous account end equity / prior close equity before first 2025 trading day - 1; fees/tax/slippage included.",
        "validation_drawdown": "Minimum daily NAV / running daily NAV peak - 1 within 2025, including initial period anchor.",
        "validation_mean_cash": "Mean cash/equity over all 2025 ledger days; empty-candidate days retained.",
        "validation_annualized_turnover": "Mean daily sum(abs(executed quantity)*reference open)/pretrade opening equity *252; doubled conventional one-way turnover.",
        "zero_cost_counterfactual": "Same immutable signals, selections, dates and initial capital rerun with all cost rates zero; quantity/cash paths may differ.",
        "weekly_nav": "Last observed trading-day normalized account NAV each W-FRI week; full daily NAV used for risk statistics.",
        "noncash_days": "Days with opening OR closing inventory, including exit days; fewer than60 flagged insufficient.",
    }
    source = {"id": "comparison", "label": "Audited six-strategy comparison", "path": "comparison.json",
              "query": {"engine": "SQLite3 JSON1 report projection", "language": "sql",
                        "sql": COMPARISON_SQL,
                        "bindings": {"comparison_json": "Entire comparison.json document; sha256 below",
                                     "order_json": ORDER,
                                     "evidence_json": {s["id"]: quality_label(s) for s in strategies}},
                        "id": "trend_strategy_comparison_20260912",
                        "description": "Executed SQLite JSON1 SELECT projects chart/table rows from the comparison.json binding. Upstream metrics are computed in Python, not SQL: analyze_strategy_comparison.py reconciles durable run cash/quantity/NAV and next-day selections, then computes continuous-account phase metrics and zero/double-cost replays. build_strategy_comparison_report.py checks SQL rows against Python field extraction and does not rerun backtests.",
                        "tables_used": ["comparison.json", "experiment_plan.json",
                                        "scripts/analyze_strategy_comparison.py",
                                        "guanlan_v2/workflow/daily_simulator.py"],
                        "filters": {"evaluation": "2023-04-03 through 2026-09-11",
                                    "primary_comparison": "2025 calendar year",
                                    "recent_descriptive": "2026-01-01 through 2026-09-11; not pristine unseen",
                                    "universe": "csi800 static membership dated 2026-03-31",
                                    "rules": "Six frozen variants; no outcome-dependent exclusions",
                                    "ranking": "negative min absolute MA10/MA20 bias; code tie-break",
                                    "costs": "commission3bp both sides,sell tax5bp,slippage5bp both sides",
                                    "weekly_sampling": "Last observed trading day per W-FRI week; all six retained"},
                        "metric_definitions": definitions},
              "sha256": source_sha256, "plan_sha256": PLAN_SHA256,
              "durable_runs": [s["receipt"]["run_id"] for s in strategies]}
    weekly_source = {**source, "id": "weekly_projection", "label": "Continuous weekly NAV projection",
                     "query": {**source["query"], "sql": WEEKLY_SQL,
                               "bindings": {"comparison_json": "Entire comparison.json document; sha256 below",
                                            "order_json": ORDER}}}
    sources = [source, weekly_source,
        {"id": "implementation", "label": "Guanlan daily replay method", "path": "docs/daily_strategy_replay.md"},
        {"id": "order_timing", "label": "Backtrader order execution reference",
         "href": "https://www.backtrader.com/docu/order-creation-execution/order-creation-execution/"},
        {"id": "time_split", "label": "scikit-learn time-series evaluation reference",
         "href": "https://sklearn.org/stable/modules/cross_validation.html"}]
    bar = {"id": "validation_returns", "title": "2025年净收益比较", "subtitle": "六组冻结规则；费用已计入；零线区分盈亏",
           "showDescription": True, "type": "horizontalBar", "intent": "comparison",
           "dataset": "strategy_comparison", "sourceId": "comparison", "layout": "full",
           "question": "How do all six frozen strategies compare on 2025 net return?",
           "valueFormat": "percent", "palette": {"kind": "categorical"},
           "labels": {"values": "all"}, "settings": {"sort": "descending", "showValues": True},
           "referenceLines": [{"axis": "y", "value": 0, "label": "0%", "color": "neutral", "lineStyle": "solid"}],
           "encodings": {"x": {"field": "strategy", "type": "nominal", "label": "策略"},
                         "y": {"field": "validation_net_return", "type": "quantitative", "label": "净收益", "format": "percent"},
                         "tooltip": [{"field": "validation_drawdown", "type": "quantitative", "label": "2025回撤", "format": "percent"},
                                     {"field": "validation_noncash_days", "type": "quantitative", "label": "非现金日"},
                                     {"field": "validation_evidence", "type": "nominal", "label": "证据限制"}]}}
    line = {"id": "weekly_equity", "title": "六组策略账户净值", "subtitle": "2023年4月至2026年9月；周末实际交易日抽样；初始资金归一为1",
            "showDescription": True, "type": "line", "intent": "trend", "dataset": "weekly_nav",
            "sourceId": "weekly_projection", "layout": "full", "valueFormat": "number",
            "question": "Do the six paths behave consistently across development, validation, and recent descriptive periods?",
            "palette": {"kind": "categorical"}, "legend": {"position": "bottom", "sort": "spec"},
            "labels": {"values": "endpoints"}, "settings": {"showPoints": "never"},
            "referenceLines": [{"axis": "y", "value": 1, "label": "初始净值1", "color": "neutral", "lineStyle": "solid"}],
            "encodings": {"x": {"field": "date", "type": "temporal", "label": "实际交易日"},
                          "y": {"field": "nav", "type": "quantitative", "label": "净值"},
                          "color": {"field": "strategy", "type": "nominal", "label": "策略"},
                          "tooltip": [{"field": "exposure", "type": "quantitative", "label": "仓位", "format": "percent"},
                                      {"field": "holdings", "type": "quantitative", "label": "持股数"},
                                      {"field": "period", "type": "nominal", "label": "阶段"}]}}
    table_columns = [
        {"field": "order", "label": "序号", "type": "number"},
        {"field": "strategy", "label": "规则", "type": "text"},
        {"field": "validation_net_return", "label": "2025净收益", "format": "percent"},
        {"field": "validation_drawdown", "label": "2025回撤", "format": "percent"},
        {"field": "recent_net_return", "label": "2026截至9月11日", "format": "percent"},
        {"field": "full_net_return", "label": "全期净收益", "format": "percent"},
        {"field": "validation_mean_cash", "label": "2025平均现金", "format": "percent"},
        {"field": "validation_annualized_turnover", "label": "2025年化成交额/NAV", "format": "number"},
        {"field": "validation_evidence", "label": "2025证据限制", "type": "text"}]
    tables = [
        {"id": "comparison_values", "title": "六组规则的收益与风险", "subtitle": "按冻结顺序显示；2026为非完整年度描述段",
         "showDescription": True, "dataset": "strategy_comparison", "sourceId": "comparison",
         "defaultSort": {"field": "order", "direction": "asc"}, "columns": table_columns, "density": "spacious"},
        {"id": "cost_values", "title": "2025年成本敏感性", "subtitle": "同一信号和入选股票，重新执行反事实账户",
         "showDescription": True, "dataset": "strategy_comparison", "sourceId": "comparison",
         "defaultSort": {"field": "order", "direction": "asc"},
         "columns": [table_columns[0], table_columns[1],
                     {"field": "validation_zero_cost_return", "label": "零成本反事实", "format": "percent"},
                     table_columns[2],
                     {"field": "validation_double_cost_return", "label": "双倍成本", "format": "percent"},
                     {"field": "validation_cost_CNY", "label": "原成本支出(元)", "format": "number"},
                     {"field": "double_cost_minus_base", "label": "双倍减原成本", "format": "percent", "movement": True}],
         "density": "spacious"}]
    blocks = [md("title", "# " + TITLE, None), md("executive_summary", exec_summary),
              md("validation_context", primary),
              {"id": "validation_chart", "type": "chart", "chartId": bar["id"], "layout": "full"},
              md("tradeoffs", "## 收益、回撤和换手需要一起取舍\n\n" + tradeoff),
              {"id": "comparison_table", "type": "table", "tableId": tables[0]["id"], "layout": "full"},
              md("recent_context", recent),
              {"id": "weekly_chart", "type": "chart", "chartId": line["id"], "layout": "full"},
              md("cost_context", sensitivity),
              {"id": "cost_table", "type": "table", "tableId": tables[1]["id"], "layout": "full"},
              md("next_steps", next_steps), md("further_questions", questions, None),
              md("caveats", caveats)]
    artifact = {"surface": "report",
                "manifest": {"version": 1, "surface": "report", "title": TITLE,
                             "generatedAt": generated_at, "blocks": blocks,
                             "charts": [bar, line], "tables": tables, "sources": sources},
                "snapshot": {"version": 1, "status": "ready", "generatedAt": generated_at,
                             "datasets": {"strategy_comparison": lookup, "weekly_nav": weekly,
                                          "period_context": periods}},
                "sources": sources}
    notes = {
        "delivery_mode": "mcp-app", "audience": "product stakeholders",
        "visible_render_performed": False, "source_sha256": source_sha256,
        "schema_validation": "not yet called; root validates complete real artifact before rendering",
        "report_source": "comparison.json", "builder": "scripts/build_strategy_comparison_report.py",
        "upstream_analysis": "scripts/analyze_strategy_comparison.py",
        "period_keys": list(PERIODS), "no_current_rule_retuning": True,
        "structure_map": {
            "Title": "title", "Executive summary": "executive_summary",
            "Key findings with visual evidence": ["validation_context", "validation_chart", "tradeoffs", "comparison_table",
                                                   "recent_context", "weekly_chart", "cost_context", "cost_table"],
            "Recommended next steps": "next_steps", "Further questions": "further_questions",
            "Caveats and assumptions": "caveats; material limitations also adjacent to validation findings"},
        "chart_map": [
            {"id": bar["id"], "segment": "2025 primary comparison", "family": "comparison", "type": "horizontalBar",
             "fields": ["strategy", "validation_net_return"], "rows": len(lookup),
             "claim": "all six signed net returns, not winner certification", "zero_origin": True,
             "palette_policy": "categorical native styling with direct category labels; no redundant color encoding",
             "native_horizontal_contract": "category remains encodings.x and quantitative measure encodings.y; runtime flips presentation"},
            {"id": line["id"], "segment": "continuous paths", "family": "trend", "type": "line",
             "fields": ["date", "nav", "strategy"], "rows": len(weekly),
             "claim": "weekly shapes including cash, while daily ledger supplies risk metrics",
             "palette_policy": "Native categorical palette; baseline name is explicitly labeled. Consistent series order; no unsupported manual palette or dash override.",
             "palette_limitation": "Canonical renderer owns category colors. Parent accepted this supported palette; final visible label and contrast inspection remains required."}],
        "omissions": {"parallel_html": "not requested; single MCP report surface",
                      "scatter": "only six closely related variants; exact tradeoffs clearer in table",
                      "daily_curve": "weekly chart bounded; full daily ledger retained by upstream source",
                      "live_trading": "out of scope", "best_model": "model scores not used"},
        "review_preview": {"strategy_comparison": lookup[:10], "weekly_nav": weekly[:10]},
        "qa": {"all_six_retained": len(lookup) == 6,
               "all_chart_rows_under_2000": len(weekly) <= 2000,
               "data_has_no_nonfinite_values": True,
               "no_machine_local_source_paths": True,
               "render_inspection_required": True},
    }
    payload = json.dumps(artifact, ensure_ascii=False, allow_nan=False).encode()
    if len(payload) > 3_000_000:
        raise ValueError("Canonical MCP artifact exceeds 3MB")
    return artifact, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=OUT / "comparison.json")
    parser.add_argument("--output", type=Path, default=OUT / "artifact.json")
    args = parser.parse_args()
    raw = args.input.read_bytes()
    artifact, notes = build_artifact(json.loads(raw), source_sha256=hashlib.sha256(raw).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    note_path = args.output.with_name("report_source_notes.json")
    note_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"ok": True, "artifact": str(args.output), "source_notes": str(note_path),
                      "rendered": False, "status": notes.get("status", "complete"),
                      "strategies_compared": len(artifact["snapshot"]["datasets"].get("strategy_comparison", [])),
                      "weekly_rows": len(artifact["snapshot"]["datasets"].get("weekly_nav", []))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
