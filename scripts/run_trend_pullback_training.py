"""Train three predeclared draft candidates through the installed local API.

No default-model changes, automatic retries, parameter searches, or publication.
Receipts are written before/after each external action; an uncertain POST must be
inspected manually, not retried. Run the separate audit --freeze before this.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/trend_pullback_20260912"
API = "http://127.0.0.1:9999"
MODELS = [
    ("rf", "RF", {"n_estimators": 100}),
    ("xgboost", "XGBoost", {"n_estimators": 100, "max_depth": 3,
                            "learning_rate": 0.05, "subsample": 0.8}),
    ("lightgbm", "LightGBM", {"n_estimators": 100, "num_leaves": 15,
                              "learning_rate": 0.05}),
]


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def api(path, body=None):
    req = urllib.request.Request(API + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def persist(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    design = json.loads((OUT / "factor_design.json").read_text(encoding="utf-8"))
    settings = {"features": design["raw_features"], "label": "fwd_ret", "fwd_days": 5,
                "universe": "csi800", "benchmark": "csi300", "start": "2023-01-01",
                "end": "2026-09-11", "freq": "day"}
    plan = [{"kind": kind, "name": f"趋势回踩 · {name} · 日频候选 · 20260911",
             "candidate_only": True, "recipe": {**settings, "params": params}}
            for kind, name, params in MODELS]
    if not args.execute:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return
    target = OUT / "training_receipts.json"
    if target.exists():
        raise RuntimeError("Receipts already exist: inspect them; no implicit retraining.")
    if api("/model/promote/status")["state"]["running"]:
        raise RuntimeError("Existing promotion is running; no task started.")
    receipt = {"schema": "guanlan.trend_training.v1", "started_at": now(),
               "settings": settings, "rebalance": "day", "models": [],
               "pool_caveat": "2026-03-31 static CSI800 snapshot; not historical PIT membership"}
    persist(target, receipt)
    for request in plan:
        item = {"kind": request["kind"], "name": request["name"], "request": request,
                "status": "submitting", "started_at": now()}
        receipt["models"].append(item)
        persist(target, receipt)
        try:
            start = api("/model/promote", request)
            item["start_response"] = start
            item["variant_id"] = start.get("variant_id")
            item["status"] = "running" if start.get("started") else "rejected"
            persist(target, receipt)
            if not start.get("ok") or not start.get("started"):
                raise RuntimeError(f"Start rejected: {start}")
            print(json.dumps({"kind": item["kind"], "variant_id": item["variant_id"],
                              "status": "running"}, ensure_ascii=False), flush=True)
            deadline = time.monotonic() + 3600
            last_progress = None
            while True:
                state = api("/model/promote/status")["state"]
                if state.get("variant_id") != item["variant_id"]:
                    raise RuntimeError("Global training status changed owner; inspect artifact manually.")
                item["last_state"] = state
                persist(target, receipt)
                progress = (state.get("phase"), state.get("step"), state.get("label"))
                if progress != last_progress:
                    print(json.dumps({"kind": item["kind"], "progress": progress}, ensure_ascii=False), flush=True)
                    last_progress = progress
                if not state.get("running"):
                    item["final_state"] = state
                    item["status"] = "completed" if state.get("ok") else "failed"
                    item["completed_at"] = now()
                    persist(target, receipt)
                    print(json.dumps({"kind": item["kind"], "status": item["status"],
                                      "variant_id": item["variant_id"]}, ensure_ascii=False), flush=True)
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Polling budget reached; job may still be running. No retry.")
                time.sleep(10)
        except Exception as exc:
            item["orchestrator_error"] = f"{type(exc).__name__}: {exc}"
            receipt["stopped_at"] = now()
            persist(target, receipt)
            raise
    receipt["completed_at"] = now()
    persist(target, receipt)


if __name__ == "__main__":
    main()
