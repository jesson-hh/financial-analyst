"""Join a completed strict screen to three draft-model rankings, read-only live.

No server imports, requests, training, model loading, promotion, or trading.
Only JSON/Markdown reports below this checkout's var/ may be written by main.
Model percentiles are taken from each FULL ranking, never reranked on candidates.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re

import pandas as pd

CHECKOUT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = CHECKOUT / "var" / "trend_pullback_20260912"
ASOF = "2026-09-11"
KINDS = ("rf", "xgboost", "lightgbm")
BRANCHES = ("screen_ma10", "screen_ma20")


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def digest(path: Path) -> str:
    out = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            out.update(block)
    return out.hexdigest()


def finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normal_code(value) -> str:
    text = str(value or "").strip().upper().replace(".", "")
    if re.fullmatch(r"(SH|SZ|BJ)\d{6}", text):
        return text
    if re.fullmatch(r"\d{6}(SH|SZ|BJ)", text):
        return text[-2:] + text[:6]
    raise ValueError(f"Expected an exchange-qualified stock code, got {value!r}")


def dated(value) -> str | None:
    if value is None or isinstance(value, (int, float, bool)):
        return None
    try:
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def local_artifact(directory: Path, name) -> Path:
    if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
        raise ValueError("Unsafe model artifact filename")
    path = (directory / name).resolve()
    if path.parent != directory.resolve() or not path.is_file():
        raise ValueError(f"Missing/out-of-directory artifact: {name}")
    return path


def verified_artifact(directory: Path, raw: dict) -> tuple[Path, str]:
    if not isinstance(raw, dict):
        raise ValueError("Missing artifact metadata")
    path = local_artifact(directory, raw.get("file"))
    expected = raw.get("sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Missing valid artifact SHA256")
    actual = digest(path)
    if path.stat().st_size == 0 or actual != expected:
        raise ValueError(f"Empty or hash-mismatched artifact: {path.name}")
    return path, actual


def load_model(live: Path, item: dict, settings: dict) -> tuple[dict, dict]:
    """Verify draft artifacts and return a code->score map; never unpickle."""
    kind, vid = item.get("kind"), item.get("variant_id")
    evidence = {"kind": kind, "variant_id": vid, "available": False}
    try:
        if kind not in KINDS or not isinstance(vid, str) or not re.fullmatch(r"m_[0-9a-f]{10}", vid):
            raise ValueError("Unexpected kind or variant_id")
        if item.get("status") != "completed" or (item.get("final_state") or {}).get("ok") is not True:
            raise ValueError("Training receipt is not successfully completed")
        if (item.get("request") or {}).get("candidate_only") is not True:
            raise ValueError("Receipt did not request a draft-only candidate")
        directory = (live / "guanlan_v2/strategy/vendor/artifacts/models" / vid).resolve()
        meta_path = directory / "meta.json"
        meta = read_json(meta_path)
        if meta.get("id") != vid or meta.get("kind") != kind or meta.get("status") != "draft":
            raise ValueError("Model identity/kind/draft status mismatch")
        if meta.get("asof") != ASOF:
            raise ValueError(f"Model asof must equal {ASOF}; got {meta.get('asof')}")
        provenance = meta.get("training_provenance") or {}
        if provenance.get("prediction_date") != ASOF or (provenance.get("features") or {}).get("end") != ASOF:
            raise ValueError("Model prediction date and feature-data end must equal requested asof")
        recipe = meta.get("recipe") or {}
        for key in ("universe", "features", "freq", "fwd_days", "start", "end"):
            if recipe.get(key) != settings.get(key):
                raise ValueError(f"Model recipe does not match training receipt: {key}")
        est = meta.get("estimator_artifact") or {}
        if est.get("available") is not True or est.get("format") != "trusted-local-pickle-v1":
            raise ValueError("No declared available trusted-local estimator")
        estimator_path, estimator_hash = verified_artifact(directory, est)
        ranking_path, ranking_hash = verified_artifact(directory, meta.get("ranking_artifact"))
        frame = pd.read_parquet(ranking_path)
        if frame.empty or not {"code", "date", "lgb_pct"}.issubset(frame.columns):
            raise ValueError("Ranking must contain nonempty code/date/lgb_pct columns")
        dates = frame["date"].map(dated)
        if dates.isna().any() or set(dates) != {ASOF}:
            raise ValueError(f"Every ranking row must have date {ASOF}; mixed/stale dates rejected")
        frame = frame.copy()
        frame["code"] = frame["code"].map(normal_code)
        if frame["code"].duplicated().any():
            raise ValueError("Duplicate ranking codes are ambiguous; not silently deduplicated")
        scores = {}
        for row in frame.to_dict(orient="records"):
            pct = finite(row.get("lgb_pct"))
            if pct is not None and not 0 <= pct <= 1:
                raise ValueError("lgb_pct is not in the full-pool [0,1] percentile contract")
            scores[row["code"]] = {
                "percentile": pct,
                "percentile_100": None if pct is None else 100 * pct,
                "raw_prediction": finite(row.get("lgb_score")),
                "ranking_date": ASOF,
                "missing_reason": None if pct is not None else "ranking_percentile_missing",
            }
        # Metadata is the artifact commit point. Refuse a concurrent replacement.
        if read_json(meta_path) != meta:
            raise ValueError("Model metadata changed during review")
        evidence.update({
            "available": True, "name": meta.get("name"), "status": "draft", "asof": ASOF,
            "ranking_date": ASOF, "feature_data_end": ASOF, "full_pool_rows": len(frame),
            "finite_percentile_rows": sum(x["percentile"] is not None for x in scores.values()),
            "universe": meta.get("universe"), "model_version": meta.get("model_version"),
            "meta_path": str(meta_path), "meta_sha256": digest(meta_path),
            "ranking_path": str(ranking_path), "ranking_sha256": ranking_hash,
            "estimator_path": str(estimator_path), "estimator_sha256": estimator_hash,
            "estimator_verification": "exists_nonempty_hash_verified_not_deserialized",
            "score_semantics": "full-pool model percentile; not probability, hit rate, or return forecast certainty",
            "fwd_days": recipe.get("fwd_days"), "oos_ic_5day": finite(meta.get("oos_ic")),
        })
        return evidence, scores
    except (OSError, ValueError, TypeError, KeyError) as exc:
        evidence["reason"] = f"{type(exc).__name__}: {exc}"
        return evidence, {}


def check_shape(evidence: dict, close: float, branch: str) -> None:
    """Reconcile the highest-impact numeric evidence, not just passed flags."""
    ma = {}
    for left_n, right_n in ((5, 10), (10, 20), (20, 50)):
        item = evidence[f"ma{left_n}_gt_ma{right_n}"]
        left, right = finite(item.get("left")), finite(item.get("right"))
        if left is None or right is None or not left > right > 0 or item.get("op") != ">":
            raise ValueError("MA ordering numerical evidence contradicts pass flag")
        for window, value in ((left_n, left), (right_n, right)):
            if window in ma and not math.isclose(ma[window], value, rel_tol=1e-9):
                raise ValueError("Inconsistent MA levels within one candidate")
            ma[window] = value
    for window in (5, 10, 20, 50):
        item = evidence[f"ma{window}_rising_3"]
        values = [finite(x) for x in item.get("values", [])]
        if (len(values) != 4 or any(x is None for x in values)
                or item.get("transitions") != 3 or item.get("strict") is not True
                or not all(b > a for a, b in zip(values, values[1:]))
                or not math.isclose(values[-1], ma[window], rel_tol=1e-9)):
            raise ValueError("Continuous MA rise evidence contradicts pass flag")
    if close < ma[50] - 1e-9:
        raise ValueError("Close is below MA50 despite pass flag")
    line = 10 if branch.endswith("10") else 20
    near = finite(evidence[f"near_ma{line}"].get("value"))
    pullback = finite(evidence["pullback_from_high10"].get("value"))
    if near is None or not -0.03 - 1e-10 <= near <= 0.03 + 1e-10:
        raise ValueError("Near-MA bias is outside the strict +/-3% band")
    if not math.isclose(near, close / ma[line] - 1, abs_tol=1e-9):
        raise ValueError("Near-MA evidence does not reconcile with close and MA")
    if pullback is None or not -1 <= pullback <= -0.01 + 1e-10:
        raise ValueError("Pullback from high10 is less than required 1%")


def load_candidates(live: Path, workflows: dict) -> tuple[dict, list, list]:
    selected = [x for x in workflows.get("workflows", [])
                if x.get("file") == "01_strict_pullback_screen.json"]
    if len(selected) != 1:
        raise ValueError("Exactly one 01 strict-screen workflow receipt is required")
    selected = selected[0]
    rid = selected.get("run_id")
    if not isinstance(rid, str) or not re.fullmatch(r"wr_[0-9a-f]{12,40}", rid):
        raise ValueError("01 workflow receipt has no valid final run_id")
    path = live / ".data/workflows/_runs" / f"{rid}.json"
    rec = read_json(path)
    workspace = selected.get("workspace_id") or workflows.get("workspace_id")
    if rec.get("id") != rid or rec.get("workspace_id") != workspace:
        raise ValueError("Workflow run identity/workspace mismatch")
    if rec.get("status") != "completed":
        raise ValueError(f"01 run is not successfully completed: {rec.get('status')}")
    result = rec.get("result") or {}
    if result.get("ok") is not True or result.get("node_errors"):
        raise ValueError("01 result failed or has failed nodes")
    terminals = result.get("all_terminals") or [result.get("terminal")]
    payloads = {}
    for terminal in terminals:
        if not isinstance(terminal, dict) or terminal.get("kind") != "data_screen":
            continue
        nid = terminal.get("node_id")
        if nid in payloads:
            raise ValueError("Duplicate screen terminal payload")
        payloads[nid] = terminal.get("payload") or {}
    if set(payloads) != set(BRANCHES):
        raise ValueError("Both and only MA10/MA20 screen terminal results are required")
    candidates, branches, warnings = {}, [], []
    for nid in BRANCHES:
        payload = payloads[nid]
        if payload.get("ok") is not True or payload.get("as_of") != ASOF:
            raise ValueError(f"{nid}: failed or market as_of != {ASOF}")
        if payload.get("requested_as_of") != ASOF:
            raise ValueError(f"{nid}: requested_as_of does not match the target date")
        rows = payload.get("candidates")
        if not isinstance(rows, list):
            raise ValueError(f"{nid}: candidates must be an array (empty is valid)")
        matched = payload.get("n_matched")
        if type(matched) is not int or matched < len(rows):
            raise ValueError(f"{nid}: invalid matched candidate count")
        if matched > len(rows):
            warnings.append(f"{nid} matched {matched} but returned {len(rows)}; union is truncated")
        branches.append({"node_id": nid, "as_of": ASOF, "screen_run_id": payload.get("run_id"),
                         "n_matched": matched, "returned_n": len(rows),
                         "spec_digest": payload.get("spec_digest"),
                         "data_snapshot": payload.get("data_snapshot"),
                         "metadata_provenance": payload.get("metadata_provenance"),
                         "warnings": payload.get("warnings") or []})
        seen = set()
        for row in rows:
            code = normal_code(row.get("code"))
            if code in seen:
                raise ValueError(f"{nid}: duplicate code {code}")
            seen.add(code)
            trade = (row.get("filter_evidence") or {}).get("asof_trade") or {}
            if row.get("as_of") != ASOF or trade.get("bar_date") != ASOF or trade.get("effective_asof") != ASOF:
                raise ValueError(f"{nid}/{code}: candidate/quote date mismatch")
            evidence = row.get("evidence") or {}
            required = {"ma5_gt_ma10", "ma10_gt_ma20", "ma20_gt_ma50", "close_above_ma50",
                        "ma5_rising_3", "ma10_rising_3", "ma20_rising_3", "ma50_rising_3",
                        "pullback_from_high10", "near_ma10" if nid.endswith("10") else "near_ma20"}
            if not required.issubset(evidence) or any(evidence[k].get("passed") is not True for k in required):
                raise ValueError(f"{nid}/{code}: strict shape evidence is missing or not passed")
            close = finite(row.get("close"))
            if close is None or close <= 0:
                raise ValueError(f"{nid}/{code}: invalid close")
            check_shape(evidence, close, nid)
            filters = row.get("filter_evidence") or {}
            st, liquidity = filters.get("st") or {}, filters.get("liquidity") or {}
            avg = finite(row.get("avg_amount_yuan"))
            liquid_avg = finite(liquidity.get("average_yuan"))
            if (trade.get("required") is not True or (finite(trade.get("volume")) or 0) <= 0
                    or (finite(trade.get("amount_yuan")) or 0) <= 0
                    or st.get("excluded") is not True or st.get("is_st") is not False
                    or liquidity.get("passed") is not True or liquidity.get("observations") != 20
                    or avg is None or avg < 50000000 or liquid_avg is None
                    or not math.isclose(avg, liquid_avg, rel_tol=1e-9)):
                raise ValueError(f"{nid}/{code}: liquidity/ST/trading eligibility evidence inconsistent")
            if code in candidates and not math.isclose(candidates[code]["close"], close, rel_tol=1e-10):
                raise ValueError(f"{code}: conflicting close across branches")
            merged = candidates.setdefault(code, {
                "code": code, "name": row.get("name"), "industry": row.get("industry"),
                "market_asof": ASOF, "close": close,
                "average_amount_yuan_20d": finite(row.get("avg_amount_yuan")),
                "branches": [], "branch_evidence": {}, "models": {},
            })
            merged["branches"].append(nid)
            merged["branch_evidence"][nid] = {"shape": evidence, "filters": row.get("filter_evidence") or {}}
    return ({"workflow_id": selected.get("workflow_id"), "run_id": rid,
             "workspace_id": workspace, "market_asof": ASOF, "run_record": str(path),
             "run_record_sha256": digest(path), "branches": branches},
            [candidates[code] for code in sorted(candidates)], warnings)


def build_review(live: Path, training_path: Path, workflow_path: Path) -> dict:
    report = {"schema": "guanlan.trend_pullback_candidate_review.v1",
              "created_at": datetime.now().astimezone().isoformat(), "requested_asof": ASOF,
              "status": "blocked", "models": [], "candidates": [], "issues": [],
              "sources": {"training_receipts": str(training_path), "workflow_receipts": str(workflow_path)},
              "semantics": {"model_target_days": 5, "rebalance": "day", "candidate_order": "code ascending, not recommendation order",
                            "percentile": "100 × saved full-pool lgb_pct; NOT winning probability",
                            "selection": "MA10/MA20 strict-screen union, no champion selection, no forced quota",
                            "missing": "null with explicit reason; never filled with zero"},
              "caveats": ["候选只是规则与模型读数，不是买卖建议；三个模型均为 draft。",
                          "全池百分位来自该模型当日实际可评分截面，不保证覆盖中证800全部股票。",
                          "当前成分与ST快照不等于完整历史PIT；模型分位不可解读为胜率。",
                          "该报告只联读现有产物，没有重新预测、训练、调仓或交易。"]}
    try:
        training, workflows = read_json(training_path), read_json(workflow_path)
        settings = training.get("settings") or {}
        if settings.get("end") != ASOF or settings.get("freq") != "day" or settings.get("fwd_days") != 5:
            raise ValueError("Training receipt date/frequency/horizon contract mismatch")
        report["screen"], report["candidates"], warnings = load_candidates(live, workflows)
        report["issues"].extend(warnings)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report["issues"].append(f"Strict screen unavailable: {type(exc).__name__}: {exc}")
        return report
    for kind in KINDS:
        matches = [x for x in training.get("models", []) if x.get("kind") == kind]
        if len(matches) != 1:
            model, scores = {"kind": kind, "available": False, "reason": "Exactly one completed receipt for this kind is required"}, {}
        else:
            model, scores = load_model(live, matches[0], settings)
        report["models"].append(model)
        if not model["available"]:
            report["issues"].append(f"{kind}: {model['reason']}")
        for row in report["candidates"]:
            row["models"][kind] = scores.get(row["code"], {
                "percentile": None, "percentile_100": None, "raw_prediction": None,
                "ranking_date": None,
                "missing_reason": "candidate_not_in_full_pool_ranking" if model["available"] else model["reason"],
            })
    report["candidate_count"] = len(report["candidates"])
    report["available_model_count"] = sum(x["available"] for x in report["models"])
    report["status"] = "complete" if not report["issues"] else "partial"
    return report


def md_text(value) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_markdown(report: dict) -> str:
    lines = ["# 趋势回踩候选联读", "", f"数据日期：{ASOF}；报告状态：{report['status']}。",
             "", "严格筛选决定资格；模型百分位仅作并列参考，不是胜率，不选冠军、不凑满名单。",
             "候选按代码排列。5日模型目标、日频重排；没有重新预测或交易。", ""]
    if report["issues"]:
        lines += ["## 未完成或不一致项", ""] + [f"- {md_text(x)}" for x in report["issues"]] + [""]
    if "screen" not in report:
        return "\n".join(lines + ["严格筛选证据不足，未输出候选。", ""])
    for model in report.get("models", []):
        if model.get("available"):
            metric = model.get("oos_ic_5day")
            verdict = ("未显示正向排序优势，不据此启用交易。"
                       if metric is not None and metric <= 0 else
                       "该统计不是实盘收益验证。")
            lines.append(f"- {model['kind']}：固定末20%留出5日RankIC={metric}；{verdict}")
    lines.append("")
    lines += [f"筛选 run_id：`{report['screen']['run_id']}`。合并去重后 {len(report['candidates'])} 只。", "",
              "| 代码 | 名称 | 满足分支 | 收盘 | RF全池百分位 | XGB全池百分位 | LGBM全池百分位 |",
              "|---|---|---|---:|---:|---:|---:|"]
    for row in report["candidates"]:
        vals = [row["code"], row["name"], "/".join(x.replace("screen_", "").upper() for x in row["branches"]), f"{row['close']:.3f}"]
        vals += ["缺失" if row["models"][kind]["percentile_100"] is None else f"{row['models'][kind]['percentile_100']:.2f}%" for kind in KINDS]
        lines.append("| " + " | ".join(md_text(x) for x in vals) + " |")
    if not report["candidates"]:
        lines += ["", "当日没有满足全部条件的候选；未放宽条件或补选股票。"]
    lines += ["", "## 每票形态与时间证据", ""]
    for row in report["candidates"]:
        lines += [f"### {row['code']} · {md_text(row['name'])}", "", f"行情日期 {row['market_asof']}；20日均成交额 {row['average_amount_yuan_20d']} 元。"]
        for branch, evidence in row["branch_evidence"].items():
            lines += ["", f"{branch}："]
            for key, detail in evidence["shape"].items():
                observable = {k: v for k, v in detail.items() if k not in ("label", "passed")}
                lines.append(f"- {md_text(detail.get('label') or key)}：通过；{md_text(json.dumps(observable, ensure_ascii=False, allow_nan=False))}")
        lines += [""]
        for kind in KINDS:
            value = row["models"][kind]
            lines.append(f"- {kind}：排名日期 {value['ranking_date'] or '缺失'}；" + (f"缺失原因 {md_text(value['missing_reason'])}" if value["percentile"] is None else "保存的全池百分位，未对候选重新排名。"))
        lines += [""]
    lines += ["## 模型产物核验", ""]
    for model in report["models"]:
        lines.append(f"- {model['kind']} / {model.get('variant_id') or '缺失'}：" +
                     (f"draft；模型/特征/排名日期均为 {ASOF}；全池 {model['full_pool_rows']} 行；estimator 与 ranking 文件 SHA256 已核验，未反序列化。" if model["available"] else md_text(model["reason"])))
    lines += ["", "## 限制", ""] + [f"- {x}" for x in report["caveats"]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live-root", type=Path, default=CHECKOUT,
        help="Guanlan source checkout to review (default: this checkout)",
    )
    parser.add_argument("--receipts-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    out, allowed = args.out_dir.resolve(), (CHECKOUT / "var").resolve()
    if out == allowed or allowed not in out.parents:
        raise ValueError("Reports must be written below this checkout's var/ directory")
    report = build_review(args.live_root.resolve(), args.receipts_dir / "training_receipts.json",
                          args.receipts_dir / "workflow_receipts.json")
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidate_review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (out / "candidate_review.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "candidate_count": report.get("candidate_count", 0),
                      "available_models": report.get("available_model_count", 0), "output": str(out)}, ensure_ascii=False))
    return {"complete": 0, "partial": 1, "blocked": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
