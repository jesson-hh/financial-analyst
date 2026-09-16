"""Compact, read-only workflow evidence extraction; never touches run state.

Exit 0 means records were readable and correctly scoped, NOT successful
backtests. See workflow_execution_passed and exact node_errors in the report.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

CHECKOUT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = CHECKOUT / "var/trend_pullback_20260912"


def output_target(path: Path) -> Path:
    path = path.resolve()
    allowed = (CHECKOUT / "var").resolve()
    if allowed not in path.parents:
        raise ValueError("Output must remain inside this checkout's var directory")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def ic_compact(value):
    value = value if isinstance(value, dict) else {}
    return {key: value.get(key) for key in (
        "rank_ic", "rank_ic_mean", "rank_icir", "ic", "ic_mean", "icir", "ic_tstat") if key in value}


def artifact_evidence(artifact):
    if not isinstance(artifact, dict):
        return None
    expected = artifact.get("digest")
    body = {key: value for key, value in artifact.items() if key not in {"digest", "id"}}
    actual = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    source = artifact.get("source") or {}
    feature = source.get("fe") or {}
    model = source.get("model") or {}
    split = source.get("split") or {}
    segments = split.get("segments") or []
    # These are directly persisted original-model fold metrics. Do not replace
    # them with the analysis node's retrospective segmentation or IC-decay.
    folds = [{key: segment.get(key) for key in (
        "fold", "rank_ic", "n_test_dates", "test_start", "train_end", "train_label_end",
        "label_horizon", "purged_dates", "policy", "endpoint_source")}
             for segment in segments if isinstance(segment, dict)]
    values = [fold["rank_ic"] for fold in folds if finite(fold.get("rank_ic"))]
    purge_ok = all(isinstance(fold.get("train_label_end"), str)
                   and isinstance(fold.get("test_start"), str)
                   and fold["train_label_end"] < fold["test_start"]
                   and fold.get("label_horizon") == feature.get("fwd_days") for fold in folds)
    signal = artifact.get("signal") or {}
    signal_rows = signal.get("rows") or []
    dates = [str(row[0])[:10] for row in signal_rows if isinstance(row, list) and len(row) == 3]
    return {
        "id": artifact.get("id"), "kind": artifact.get("kind"), "digest": expected,
        "digest_verified": expected == actual and artifact.get("id") == f"fa_{actual[:24]}",
        "signal_rows": signal.get("n"), "signal_rows_count_consistent": signal.get("n") == len(signal_rows),
        "signal_start": min(dates) if dates else None, "signal_end": max(dates) if dates else None,
        "model_kind": model.get("kind"), "actual_hyperparams": model.get("hyperparams"),
        "training_target_fwd_days": feature.get("fwd_days"), "feature_label_field": feature.get("label"),
        "training_sample_freq": feature.get("freq"), "requested_start": feature.get("start"),
        "requested_end": feature.get("end"), "prediction_scope": split.get("prediction_scope"),
        "native_rolling_folds": folds, "fold_purge_checked": bool(folds) and purge_ok,
        "native_fold_ic_all_negative": bool(values) and len(values) == len(folds) and all(value < 0 for value in values),
        "native_fold_ic_positive_ratio": sum(value > 0 for value in values) / len(values) if values else None,
        "unweighted_mean_native_fold_ic": sum(values) / len(values) if values else None,
        "native_model_headline_metrics": None,
        "native_model_headline_availability": "模型完整metrics未存入此运行记录；node_results仅ok/type摘要。不得以分析终端指标冒充。",
        "split_metadata_note": "n_train_dates/initial_holdout/train_frac是初始单次切分记录；实际滚动OOS边界须读segments与signal日期。",
    }


def terminal_evidence(terminal):
    payload = terminal.get("payload") or {}
    meta = payload.get("meta") or {}
    report = payload.get("report") or {}
    decay = payload.get("ic_decay") or []
    artifact = artifact_evidence(payload.get("factor_artifact"))
    target_horizon = artifact.get("training_target_fwd_days") if artifact else None
    target_decay = next((row for row in decay if isinstance(row, dict) and row.get("h") == target_horizon), None)
    # A terminal may be a daily screen or factor analysis, not an account curve.
    return {
        "node_id": terminal.get("node_id"), "kind": terminal.get("kind"),
        "payload_ok": payload.get("ok"), "method": payload.get("method"),
        "factor_name": payload.get("_label") or payload.get("expr_or_name") or meta.get("factor"),
        "evaluation_fwd_days": payload.get("fwd_days", meta.get("fwd_days")),
        "headline_ic": ic_compact(payload.get("headline_ic")),
        "report_ic": ic_compact(report.get("ic") or payload.get("ic")),
        "analysis_reevaluation_at_training_horizon": target_decay,
        "ic_decay": [row for row in decay if isinstance(row, dict) and row.get("h") in (1, 3, 5, 10, 20)],
        "evaluation_metadata": {key: meta.get(key) for key in ("start", "end", "n_dates", "n_codes", "freq", "fwd_days")},
        "artifact": artifact,
        "result_interpretation": ("原生滚动模型信号的分析重评，不是回测账户收益。" if terminal.get("kind") == "analysis"
                                  else "按终端类型解释，不因ok=true自动视作可交易策略。"),
        "warnings": payload.get("warnings") or [],
    }


def extract_run(live: Path, receipt: dict, expected_workspace: str):
    run_id = receipt.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"wr_[0-9a-f]{12,40}", run_id):
        return {"read_ok": False, "run_id": run_id, "name": receipt.get("name"),
                "status": "not_started", "reason": "receipt无合法run_id", "workflow_execution_passed": False}
    path = live / ".data/workflows/_runs" / f"{run_id}.json"
    try:
        raw = path.read_bytes()
        record = json.loads(raw.decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {"read_ok": False, "run_id": run_id, "name": receipt.get("name"),
                "status": "unreadable", "reason": str(exc), "workflow_execution_passed": False}
    owner = record.get("workspace_id")
    if owner != expected_workspace or record.get("scope") != "workspace" or record.get("id") != run_id:
        return {"read_ok": False, "run_id": run_id, "status": "scope_mismatch",
                "workflow_execution_passed": False, "reason": "run identity/workspace does not match receipt"}
    result = record.get("result") or {}
    errors = result.get("node_errors") or []
    state = record.get("node_state") or {}
    bad_nodes = {key: value for key, value in state.items() if value.get("state") != "done"}
    raw_terminals = result.get("all_terminals") or ([result["terminal"]] if result.get("terminal") else [])
    terminals = [terminal_evidence(terminal) for terminal in raw_terminals if isinstance(terminal, dict)]
    backtests = [terminal for terminal in terminals if terminal["kind"] == "backtest"]
    artifacts = [terminal["artifact"] for terminal in terminals if terminal.get("artifact")]
    passed = (record.get("status") == "completed" and result.get("ok") is True and not errors and not bad_nodes)
    return {
        "read_ok": True, "run_id": run_id, "workflow_id": receipt.get("workflow_id"),
        "name": receipt.get("name") or record.get("name"), "workspace_id": owner,
        "record_sha256": hashlib.sha256(raw).hexdigest(), "status": record.get("status"),
        "workflow_execution_passed": passed, "result_ok": result.get("ok"),
        "evidence_integrity_passed": all(item["digest_verified"] and item["signal_rows_count_consistent"]
                                         and (not item["native_rolling_folds"] or item["fold_purge_checked"])
                                         for item in artifacts),
        "result_ok_semantics": "只表示选中了一个主终端；有node_errors或completed_with_errors仍是部分失败。",
        "elapsed_sec": result.get("elapsed_sec"), "node_count": len(state),
        "done_nodes": sum(value.get("state") == "done" for value in state.values()),
        "unfinished_or_failed_nodes": bad_nodes,
        "node_errors": errors, "reason": record.get("reason") or result.get("reason"),
        "warnings": result.get("warnings") or [], "terminals": terminals,
        "backtest_account_available": bool(backtests) and all(t["payload_ok"] is True for t in backtests)
                                       and not any(error.get("type") == "backtest" for error in errors),
        "selected_terminal_metrics_not_necessarily_backtest": result.get("metrics"),
    }


def summarize(live: Path, receipts_path: Path, out: Path):
    receipts = json.loads(receipts_path.read_text(encoding="utf-8-sig"))
    workspace = receipts.get("workspace_id")
    if not isinstance(workspace, str) or not re.fullmatch(r"rw_(?:general|[0-9a-f]{12})", workspace):
        raise ValueError("Expected an explicit valid workspace_id in receipts")
    runs = [extract_run(live, receipt, receipt.get("workspace_id") or workspace)
            for receipt in receipts.get("workflows", [])]
    summary = {
        "schema": "guanlan.trend_workflow_qa.v1", "created_at": datetime.now().astimezone().isoformat(),
        "audit_read_ok": bool(runs) and all(run["read_ok"] for run in runs),
        "evidence_integrity_passed": bool(runs) and all(run.get("evidence_integrity_passed") is True for run in runs),
        "workflow_execution_passed": bool(runs) and all(run["workflow_execution_passed"] for run in runs),
        "runs": runs,
        "limitations": [
            "只读持久JSON快照，不import WorkflowRunStore，避免读取孤儿运行时修改其状态。",
            "原生模型5日滚动折指标、分析终端1日重评、分析终端5日IC-decay必须分开。",
            "当前run只持久化主/并行终端及节点成功摘要；未保存的model.metrics不猜测、不冒充已取到。",
            "分析终端的portfolio/Sharpe不能在回测失败时当作已完成的TopN账户回测。",
            "负IC不自动翻方向；工作流运行完成不等于获得实战优势。",
        ]}
    output_target(out / "workflow_qa.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    lines = ["# 趋势回踩工作流结果审查", "", f"记录读取：{'完整' if summary['audit_read_ok'] else '部分待补'}；"
             f"全部节点执行：{'通过' if summary['workflow_execution_passed'] else '未全部通过'}。", "",
             "| 工作流 | 状态 | 原生5日滚动折RankIC | 分析1日RankIC | 分析5日重评RankIC | 回测账户 |",
             "|---|---|---|---:|---:|---|"]
    error_lines = []
    for run in runs:
        terminal = next((t for t in run.get("terminals", []) if t.get("artifact")), {})
        artifact = terminal.get("artifact") or {}
        folds = artifact.get("native_rolling_folds") or []
        fold_values = ", ".join(f"{fold['rank_ic']:.6f}" if finite(fold.get("rank_ic")) else "NA" for fold in folds) or "未记录/非ML"
        one = terminal.get("headline_ic", {}).get("rank_ic") if terminal.get("evaluation_fwd_days") == 1 else None
        five = (terminal.get("analysis_reevaluation_at_training_horizon") or {}).get("rank_ic")
        lines.append(f"| {run.get('name') or run['run_id']} | {run['status']} | {fold_values} | {one} | {five} | "
                     f"{'有完成产物' if run.get('backtest_account_available') else '无完成产物'} |")
        for error in run.get("node_errors", []):
            error_lines.append(f"- {run['run_id']} / {error.get('nid')}：{error.get('error')}")
    if error_lines:
        lines += ["", "## 失败节点原文", ""] + error_lines
    factor_terminals = [(run, terminal) for run in runs for terminal in run.get("terminals", [])
                        if terminal.get("kind") == "analysis" and not terminal.get("artifact")]
    if factor_terminals:
        lines += ["", "## 独立因子分析（不是模型OOS，也不是账户回测）", "",
                  "| 节点 | 1日RankIC | 5日RankIC |", "|---|---:|---:|"]
        for run, terminal in factor_terminals:
            one = terminal.get("headline_ic", {}).get("rank_ic") if terminal.get("evaluation_fwd_days") == 1 else None
            five = next((row.get("rank_ic") for row in terminal.get("ic_decay", []) if row.get("h") == 5), None)
            lines.append(f"| {terminal.get('node_id')} | {one} | {five} |")
    lines += ["", "## 解释边界", ""] + [f"- {value}" for value in summary["limitations"]]
    output_target(out / "workflow_qa.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"audit_read_ok": summary["audit_read_ok"], "workflow_execution_passed": summary["workflow_execution_passed"],
            "runs": [{key: run.get(key) for key in ("run_id", "status", "done_nodes", "node_count", "workflow_execution_passed")} for run in runs],
            "json": str(out / "workflow_qa.json"), "markdown": str(out / "workflow_qa.md")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-root", type=Path, default=CHECKOUT,
        help="Guanlan source checkout to audit (default: this checkout)",
    )
    parser.add_argument("--receipts", type=Path, default=DEFAULT_OUT / "workflow_receipts.json")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    result = summarize(args.live_root.resolve(), args.receipts, args.output_dir)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["audit_read_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
