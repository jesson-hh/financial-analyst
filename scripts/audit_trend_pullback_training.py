"""Audit fresh trend model candidates using local artifacts, never mutate live.

--freeze captures pre-training hashes before new candidate IDs exist. Output is
restricted to this checkout's var/ directory; no server imports or pickle loads.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sys

CHECKOUT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = CHECKOUT / "var" / "trend_pullback_20260912"
EXPECTED_PARAMS = {
    "rf": {"n_estimators": 100, "max_depth": None},
    "xgboost": {"n_estimators": 100, "max_depth": 3, "learning_rate": 0.05, "subsample": 0.8},
    "lightgbm": {"n_estimators": 100, "num_leaves": 15, "learning_rate": 0.05},
}
START, ASOF, HORIZON = "2023-01-01", "2026-09-11", 5


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return value


def check(rows: list, name: str, passed: bool, actual=None, expected=None) -> None:
    rows.append({"name": name, "passed": bool(passed), "actual": actual, "expected": expected})


def finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def date_string(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone() if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def file_reference(directory: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError(f"Unsafe artifact reference: {name!r}")
    target = (directory / name).resolve()
    if target.parent != directory.resolve():
        raise ValueError("Artifact reference escaped model directory")
    return target


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def snapshot(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    if not path.is_file():
        raise ValueError(f"Expected a regular file: {path}")
    return {"exists": True, "bytes": path.stat().st_size, "sha256": sha256(path)}


def output_path(path: Path) -> Path:
    target = path.resolve()
    allowed = (CHECKOUT / "var").resolve()
    if target == allowed or allowed not in target.parents:
        raise ValueError("Audit outputs must stay inside this checkout's var directory")
    return target


def write_json(path: Path, payload: dict) -> None:
    target = output_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def freeze(live: Path, baseline: Path) -> dict:
    if baseline.exists():
        raise ValueError(f"Refusing to replace pre-training baseline: {baseline}")
    artifacts = live / "guanlan_v2" / "strategy" / "vendor" / "artifacts"
    models = artifacts / "models"
    selected = {p.resolve() for p in models.rglob("*") if p.is_file()}
    selected.update((artifacts / name).resolve() for name in (
        "models/_default.json", "v4_ranking_latest.parquet", "v4_dims_latest.parquet"))
    entries = {}
    for path in sorted(selected):
        if live not in path.parents:
            raise ValueError(f"Unexpected path outside live root: {path}")
        entries[path.relative_to(live).as_posix()] = snapshot(path)
    payload = {"schema": "guanlan.trend_training_baseline.v1", "created_at": datetime.now().astimezone().isoformat(),
               "live_root": str(live), "files": entries,
               "existing_model_ids": sorted(p.name for p in models.iterdir() if p.is_dir()) if models.exists() else [],
               "default_pointer": "guanlan_v2/strategy/vendor/artifacts/models/_default.json"}
    write_json(baseline, payload)
    return {"ok": True, "baseline": str(baseline), "files": len(entries),
            "model_ids": len(payload["existing_model_ids"]),
            "default": entries[payload["default_pointer"]]}


def audit_baseline(live: Path, path: Path) -> dict:
    saved = read_json(path)
    rows = []
    check(rows, "baseline_live_root", Path(saved.get("live_root", "")).resolve() == live,
          saved.get("live_root"), str(live))
    entries = saved.get("files") or {}
    check(rows, "baseline_nonempty", bool(entries), len(entries), ">0")
    changed = []
    for relative, old in entries.items():
        target = (live / relative).resolve()
        if live not in target.parents:
            raise ValueError(f"Baseline path escaped live root: {relative}")
        actual = snapshot(target)
        if actual != old:
            changed.append({"path": relative, "before": old, "after": actual})
    check(rows, "all_existing_artifacts_unchanged", not changed, changed, [])
    default = saved.get("default_pointer")
    check(rows, "default_pointer_was_frozen", default in entries, default)
    return {"passed": all(row["passed"] for row in rows), "checks": rows,
            "changed": changed, "existing_model_ids": saved.get("existing_model_ids") or [],
            "created_at": saved.get("created_at")}


def trading_calendar(live: Path) -> list[str]:
    # The configured dataset has future preallocated calendar dates. Only dates
    # through the requested as-of are eligible for label-maturity calculations.
    source = Path("G:/stocks/stock_data/cn_data/calendars/day.txt")
    if not source.is_file():
        return []
    return sorted({line.strip() for line in source.read_text(encoding="utf-8").splitlines()
                   if date_string(line.strip()) and START <= line.strip() <= ASOF})


def audit_candidate(live: Path, receipt: dict, features: list[str], mature_end: str | None) -> dict:
    import numpy as np
    import pandas as pd

    rows, warnings = [], []
    vid = str(receipt.get("variant_id") or "")
    kind = str(receipt.get("kind") or "")
    result = {"variant_id": vid, "kind": kind, "checks": rows, "warnings": warnings}
    check(rows, "variant_id_is_safe", bool(re.fullmatch(r"m_[a-zA-Z0-9_-]+", vid)), vid)
    if not rows[-1]["passed"]:
        result["passed"] = False
        return result
    directory = (live / "guanlan_v2/strategy/vendor/artifacts/models" / vid).resolve()
    if live not in directory.parents:
        raise ValueError("Candidate directory escaped live root")
    request = receipt.get("request") or {}
    check(rows, "explicit_candidate_only_request", request.get("candidate_only") is True,
          request.get("candidate_only"), True)
    check(rows, "supported_requested_kind", kind in EXPECTED_PARAMS, kind, sorted(EXPECTED_PARAMS))
    meta_path = directory / "meta.json"
    check(rows, "metadata_exists", meta_path.is_file(), str(meta_path))
    if not meta_path.is_file():
        result["passed"] = False
        return result
    meta = read_json(meta_path)
    result["name"] = meta.get("name")
    for key, expected in (("id", vid), ("kind", kind), ("source", "workflow"),
                          ("status", "draft"), ("asof", ASOF), ("n_features", 4),
                          ("universe", "csi800"), ("retrainable", True)):
        check(rows, f"meta.{key}", meta.get(key) == expected, meta.get(key), expected)
    recipe = meta.get("recipe") or {}
    request_recipe = request.get("recipe") or {}
    for key, expected in (("features", features), ("label", "fwd_ret"), ("fwd_days", HORIZON),
                          ("universe", "csi800"), ("start", START), ("end", ASOF), ("freq", "day")):
        check(rows, f"recipe.{key}", recipe.get(key) == expected, recipe.get(key), expected)
        check(rows, f"request_matches_recipe.{key}", request_recipe.get(key) == recipe.get(key),
              request_recipe.get(key), recipe.get(key))
    for key, expected in EXPECTED_PARAMS.get(kind, {}).items():
        check(rows, f"actual_hyper.{key}", (meta.get("hyper") or {}).get(key) == expected,
              (meta.get("hyper") or {}).get(key), expected)
    check(rows, "requested_data_end", meta.get("requested_data_end") == ASOF,
          meta.get("requested_data_end"), ASOF)

    artifact_hashes = {}
    for label, key in (("ranking", "ranking_artifact"), ("estimator", "estimator_artifact")):
        desc = meta.get(key) or {}
        if label == "estimator":
            check(rows, "estimator.available", desc.get("available") is True, desc.get("available"), True)
            check(rows, "estimator.format", desc.get("format") == "trusted-local-pickle-v1",
                  desc.get("format"), "trusted-local-pickle-v1")
        try:
            artifact = file_reference(directory, desc.get("file"))
            state = snapshot(artifact)
            check(rows, f"{label}.file_exists", state["exists"], str(artifact))
            check(rows, f"{label}.sha256", state.get("sha256") == desc.get("sha256") and bool(desc.get("sha256")),
                  state.get("sha256"), desc.get("sha256"))
            check(rows, f"{label}.nonempty", state.get("bytes", 0) > 100,
                  state.get("bytes"), ">100 bytes")
            artifact_hashes[label] = state.get("sha256")
            if label == "ranking" and state["exists"]:
                ranking = pd.read_parquet(artifact)
                required = {"code", "date", "lgb_score", "lgb_pct", "lgb_rank"}
                check(rows, "ranking.columns", required.issubset(ranking.columns), list(ranking.columns), sorted(required))
                if required.issubset(ranking.columns):
                    dates = sorted(ranking["date"].astype(str).unique().tolist())
                    check(rows, "ranking.date", dates == [ASOF], dates, [ASOF])
                    check(rows, "ranking.row_count", len(ranking) == meta.get("ranking_rows") and 0 < len(ranking) <= 800,
                          len(ranking), meta.get("ranking_rows"))
                    check(rows, "ranking.unique_codes", not ranking["code"].duplicated().any(),
                          int(ranking["code"].nunique()), len(ranking))
                    check(rows, "ranking.finite_predictions", bool(np.isfinite(ranking["lgb_score"].to_numpy()).all()))
                    check(rows, "ranking.percentiles_match_scores",
                          bool(np.allclose(ranking["lgb_pct"], ranking["lgb_score"].rank(pct=True))))
                    check(rows, "ranking.ranks_match_scores",
                          bool(np.array_equal(ranking["lgb_rank"], ranking["lgb_score"].rank(ascending=False, method="first"))))
                    if len(ranking) < 800:
                        warnings.append(f"最新排名仅 {len(ranking)}/800 只，需按缺失特征解释覆盖差异，不能称全池均可预测。")
                    result["ranking_rows"] = len(ranking)
        except (OSError, ValueError, TypeError) as exc:
            check(rows, f"{label}.artifact_read", False, f"{type(exc).__name__}: {exc}")

    # Match the persisted registry's content identity without importing live
    # server modules or executing the trusted-local estimator pickle.
    core = {key: meta.get(key) for key in ("kind", "recipe", "asof", "base_features", "factor_ids", "hyper",
                                          "training_provenance", "estimator_artifact")}
    core["ranking_sha256"] = artifact_hashes.get("ranking")
    if artifact_hashes.get("estimator"):
        core["estimator_actual_sha256"] = artifact_hashes["estimator"]
    version = "mv_" + hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
    check(rows, "model_content_version", meta.get("model_version") == version, meta.get("model_version"), version)

    provenance = meta.get("training_provenance") or {}
    check(rows, "provenance.schema", provenance.get("schema") == "guanlan.training_provenance.v1",
          provenance.get("schema"))
    check(rows, "provenance.features", provenance.get("feature_expressions") == features,
          provenance.get("feature_expressions"), features)
    check(rows, "provenance.prediction_date", provenance.get("prediction_date") == ASOF,
          provenance.get("prediction_date"), ASOF)
    check(rows, "provenance.no_false_pit_claim", provenance.get("strict_pit") is False,
          provenance.get("strict_pit"), False)
    label = provenance.get("label_spec") or {}
    for key, expected in (("expression", "fwd_ret"), ("horizon", HORIZON), ("label_span", HORIZON)):
        check(rows, f"label.{key}", label.get(key) == expected, label.get(key), expected)
    final_fit = provenance.get("final_fit") or {}
    labels = provenance.get("labels") or {}
    fe_bounds = provenance.get("features") or {}
    for name, bounds in (("features", fe_bounds), ("labels", labels), ("final_fit", final_fit)):
        valid = date_string(bounds.get("start")) and date_string(bounds.get("end"))
        check(rows, f"{name}.date_bounds", valid and START <= bounds["start"] <= bounds["end"] <= ASOF,
              bounds, f"{START}..{ASOF}")
    check(rows, "features.cover_latest", fe_bounds.get("end") == ASOF, fe_bounds.get("end"), ASOF)
    check(rows, "final_fit.row_count", final_fit.get("n_rows") == meta.get("train_rows") and
          isinstance(meta.get("train_rows"), int) and meta["train_rows"] >= 500,
          final_fit.get("n_rows"), meta.get("train_rows"))
    check(rows, "label_maturity_calendar_available", mature_end is not None, mature_end)
    if mature_end:
        for name, bounds in (("labels", labels), ("final_fit", final_fit)):
            check(rows, f"{name}.mature_tail", date_string(bounds.get("end")) and bounds["end"] <= mature_end,
                  bounds.get("end"), f"<= {mature_end}")
    check(rows, "fit_not_after_labels", date_string(final_fit.get("end")) and date_string(labels.get("end"))
          and final_fit["end"] <= labels["end"], final_fit.get("end"), labels.get("end"))

    evaluation = provenance.get("evaluation") or {}
    split = evaluation.get("split") or {}
    check(rows, "holdout.scope", evaluation.get("metric_scope") == "purged_chronological_holdout",
          evaluation.get("metric_scope"))
    check(rows, "holdout.ready", evaluation.get("ready") is True, evaluation.get("ready"), True)
    check(rows, "holdout.label_span", evaluation.get("label_span") == HORIZON, evaluation.get("label_span"), HORIZON)
    check(rows, "holdout.purge_policy", split.get("policy") == "label_endpoint_strictly_before_test",
          split.get("policy"))
    check(rows, "holdout.endpoint_source", split.get("endpoint_source") == "per_code_observations",
          split.get("endpoint_source"))
    check(rows, "holdout.horizon", split.get("label_horizon") == HORIZON, split.get("label_horizon"), HORIZON)
    purged = split.get("purged_dates")
    check(rows, "holdout.purged_dates", isinstance(purged, int) and purged >= HORIZON, purged, f">={HORIZON}")
    label_end, test_start = split.get("train_label_end"), split.get("test_start")
    check(rows, "holdout.no_label_overlap", date_string(label_end) and date_string(test_start) and label_end < test_start,
          label_end, f"< {test_start}")
    check(rows, "holdout.test_start_consistent", evaluation.get("test_start") == test_start,
          evaluation.get("test_start"), test_start)
    hold_train = evaluation.get("train") or {}
    check(rows, "holdout.training_before_test", date_string(hold_train.get("end")) and date_string(test_start)
          and hold_train["end"] < test_start, hold_train.get("end"), f"< {test_start}")
    check(rows, "holdout.training_before_purged_end", date_string(hold_train.get("end"))
          and date_string(split.get("train_end")) and hold_train["end"] <= split["train_end"],
          hold_train.get("end"), split.get("train_end"))
    test_end = evaluation.get("test_end")
    check(rows, "holdout.test_labels_mature", date_string(test_end) and date_string(labels.get("end"))
          and test_end <= labels["end"], test_end, labels.get("end"))
    requested, scored = evaluation.get("n_test_dates_requested"), evaluation.get("n_test_dates_scored")
    expected_holdout = max(1, int(labels["n_dates"] * 0.2)) if isinstance(labels.get("n_dates"), int) else None
    check(rows, "holdout.twenty_percent_mature_dates", requested == expected_holdout and expected_holdout is not None,
          requested, expected_holdout)
    check(rows, "holdout.scored_dates", isinstance(requested, int) and isinstance(scored, int)
          and 0 < scored <= requested, scored, requested)
    ic = meta.get("oos_ic")
    check(rows, "holdout.finite_ic", finite(ic) and -1 <= ic <= 1, ic, "finite [-1,1]")
    if finite(ic) and ic <= 0:
        warnings.append(f"OOS RankIC={ic}，非正值；训练完成不代表模型有预测优势。")
    for key in ("feature_snapshot_sha256", "label_snapshot_sha256"):
        value = provenance.get(key)
        check(rows, f"provenance.{key}", isinstance(value, str) and bool(re.fullmatch(r"[a-f0-9]{64}", value)), value)
    manifest = provenance.get("technical_provenance_manifest") or {}
    if manifest.get("available") is True:
        try:
            actual = snapshot(file_reference(directory, manifest.get("file")))
            check(rows, "technical_manifest.digest", actual.get("sha256") == manifest.get("sha256")
                  and bool(manifest.get("sha256")), actual.get("sha256"), manifest.get("sha256"))
        except (OSError, ValueError) as exc:
            check(rows, "technical_manifest.read", False, str(exc))
    else:
        warnings.append("完整技术数据清单不可用；有特征/标签摘要hash不等于具有历史PIT或逐行独立复算证据。")
    result.update({"passed": all(row["passed"] for row in rows), "asof": meta.get("asof"),
                   "hyper": meta.get("hyper"), "train_rows": meta.get("train_rows"),
                   "label_end": labels.get("end"), "final_fit_end": final_fit.get("end"),
                   "oos_ic": ic, "holdout": evaluation,
                   "feature_snapshot_sha256": provenance.get("feature_snapshot_sha256"),
                   "label_snapshot_sha256": provenance.get("label_snapshot_sha256"),
                   "estimator_sha256": artifact_hashes.get("estimator"),
                   "model_version": meta.get("model_version")})
    return result


def audit(live: Path, baseline_path: Path, receipt_path: Path, design_path: Path, out: Path) -> dict:
    baseline = audit_baseline(live, baseline_path)
    receipt = read_json(receipt_path)
    design = read_json(design_path)
    features = design.get("raw_features") or []
    if not isinstance(features, list) or len(features) != 4 or not all(isinstance(x, str) for x in features):
        raise ValueError("factor_design.raw_features must contain the fixed four raw DSL expressions")
    candidates = receipt.get("models") or []
    if not isinstance(candidates, list) or not all(isinstance(row, dict) for row in candidates):
        raise ValueError("training_receipts.models must be a list of model receipts")
    rows = []
    kinds = [str(row.get("kind") or "") for row in candidates]
    ids = [str(row.get("variant_id") or "") for row in candidates]
    check(rows, "exactly_three_requested_models", sorted(kinds) == sorted(EXPECTED_PARAMS), kinds, sorted(EXPECTED_PARAMS))
    check(rows, "unique_new_model_ids", len(ids) == len(set(ids)) and bool(ids)
          and not set(ids).intersection(baseline["existing_model_ids"]), ids)
    frozen_at, started_at = timestamp(baseline.get("created_at")), timestamp(receipt.get("started_at"))
    check(rows, "baseline_predates_training", frozen_at is not None and started_at is not None and frozen_at <= started_at,
          baseline.get("created_at"), receipt.get("started_at"))
    calendar = trading_calendar(live)
    mature_end = calendar[-(HORIZON + 1)] if len(calendar) > HORIZON else None
    models = []
    for candidate in candidates:
        try:
            models.append(audit_candidate(live, candidate, features, mature_end))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            models.append({"variant_id": candidate.get("variant_id"), "kind": candidate.get("kind"),
                           "passed": False, "warnings": [],
                           "checks": [{"name": "candidate_audit_completed", "passed": False,
                                       "actual": f"{type(exc).__name__}: {exc}"}]})
    for key in ("feature_snapshot_sha256", "label_snapshot_sha256"):
        hashes = [model.get(key) for model in models]
        check(rows, f"models_share_{key}", len(hashes) == 3 and all(hashes) and len(set(hashes)) == 1, hashes)
    estimator_hashes = [model.get("estimator_sha256") for model in models]
    check(rows, "distinct_estimator_artifacts", len(estimator_hashes) == 3 and all(estimator_hashes)
          and len(set(estimator_hashes)) == 3, estimator_hashes)
    result = {"schema": "guanlan.trend_training_qa.v1", "created_at": datetime.now().astimezone().isoformat(),
              "passed": baseline["passed"] and all(row["passed"] for row in rows)
                        and len(models) == 3 and all(model["passed"] for model in models),
              "live_root": str(live), "receipts": str(receipt_path), "baseline": baseline,
              "checks": rows, "models": models, "label_maturity_latest_calendar_date": mature_end,
              "limitations": [
                  "此为持久产物完整性与已记录训练证据审计，不依赖HTTP ok；未反序列化/执行pickle或独立重训。",
                  "检验标签成熟日期上界及purge记录，不是逐行独立重建训练矩阵。",
                  "持久候选的末20%留出评估与工作流4折滚动OOS不同；不能混合比较同一指标。",
                  "目标为5个有效观测日close-to-close收益；日调仓不等于1日预测，回测成交从下一开盘开始。",
                  "原始4因子模型不强制趋势回踩资格；手操候选需另经筛选图。",
                  "静态2026-03-31中证800成分；strict_pit:false，不等于最新成分或历史可交易全证据。",
                  "不证明实盘收益、不自动选优、不更改默认，也不证明已部署每日自动重训/调仓任务。",
              ]}
    write_json(out / "training_qa.json", result)
    markdown = ["# 趋势回踩候选模型验收", "", f"结果：{'通过' if result['passed'] else '未通过'}。", "",
                "| 模型 | asof | 训练行数 | 标签末日 | OOS RankIC | 完整性 |", "|---|---|---:|---|---:|---|"]
    for model in models:
        markdown.append(f"| {model['kind']} · {model['variant_id']} | {model.get('asof')} | {model.get('train_rows')} | "
                        f"{model.get('label_end')} | {model.get('oos_ic')} | {'通过' if model['passed'] else '未通过'} |")
    markdown += ["", f"旧模型及默认指针：{'均未改变' if baseline['passed'] else '存在差异或基线问题，请查JSON'}。", ""]
    failed = [("全局", row) for row in rows + baseline["checks"] if not row["passed"]]
    failed += [(model["variant_id"], row) for model in models for row in model["checks"] if not row["passed"]]
    if failed:
        markdown += ["## 未通过检查", ""] + [f"- {owner}：{row['name']}；实际={row.get('actual')}；预期={row.get('expected')}" for owner, row in failed] + [""]
    markdown += ["## 边界与风险", ""]
    markdown += [f"- {item}" for item in result["limitations"]]
    markdown += [f"- {model['kind']}：{item}" for model in models for item in model["warnings"]]
    output_path(out / "training_qa.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return {"passed": result["passed"], "json": str(out / "training_qa.json"), "markdown": str(out / "training_qa.md"),
            "model_results": [{key: model.get(key) for key in ("variant_id", "kind", "passed", "oos_ic", "asof")} for model in models]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-root", type=Path, default=CHECKOUT,
        help="Guanlan source checkout to audit (default: this checkout)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--design", type=Path)
    parser.add_argument("--freeze", action="store_true")
    args = parser.parse_args()
    live = args.live_root.resolve()
    out = output_path(args.output_dir)
    baseline = output_path(args.baseline or out / "training_baseline.json")
    if args.freeze:
        print(json.dumps(freeze(live, baseline), ensure_ascii=False))
        return 0
    result = audit(live, baseline, args.receipts or out / "training_receipts.json",
                   args.design or out / "factor_design.json", out)
    print(json.dumps(json_safe(result), ensure_ascii=False, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
