"""Frozen six-strategy experiment via durable Guanlan workflows, not orders.

build: save local graph definitions and validate. run: save each private graph,
execute serially through server-owned jobs, preserve every final receipt.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/trend_strategy_comparison_20260912"
PLAN_HASH = "ccaefca902c2dd8b1c1c47d5a489cdfbe3bbadf5bb5618a475c89e71a2b17cfe"
sys.path[:0] = [str(ROOT), str(ROOT / "engine")]


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def api(path, data=None):
    request = urllib.request.Request("http://127.0.0.1:9999" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        result = json.load(response)
    if result.get("ok") is not True:
        raise RuntimeError(result.get("reason") or str(result)[:1000])
    return result


def graphs():
    path = OUT / "experiment_plan.json"
    if hashlib.sha256(path.read_bytes()).hexdigest() != PLAN_HASH:
        raise RuntimeError("Frozen experiment plan changed; do not run a different experiment under the same identity")
    plan = json.loads(path.read_text(encoding="utf-8"))
    from guanlan_v2.workflow.authoring import validate_authoring
    result = []
    for index, variant in enumerate(plan["variants"], 1):
        history = {**variant["history_screen_params"], "label": variant["name"],
                   "evaluation_start": plan["common"]["sample"]["eval_start"]}
        graph = {"nodes": [
            {"id": "source", "type": "source", "x": 70, "y": 80, "params": plan["common"]["source_params"]},
            {"id": "history", "type": "history_screen", "x": 360, "y": 80, "params": history},
            {"id": "daily", "type": "daily_backtest", "x": 680, "y": 80, "params": plan["common"]["daily_backtest_params"]}],
            "edges": [{"from": ["source", "data"], "to": ["history", "src"]},
                      {"from": ["history", "history"], "to": ["daily", "history"]}]}
        validation = validate_authoring(graph)
        if not validation["ok"]:
            raise RuntimeError(validation)
        name = f"日频策略对比 {index:02d} · {variant['name']}"
        save(OUT / "graphs" / (variant["id"] + ".json"), graph)
        result.append({"variant_id": variant["id"], "name": name, "graph": graph,
                       "validation": validation})
    save(OUT / "graph_manifest.json", {"plan_sha256": PLAN_HASH, "graphs": result})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["build", "run"])
    args = parser.parse_args()
    prepared = graphs()
    print(json.dumps({"event": "graphs_validated", "count": len(prepared)}), flush=True)
    if args.mode == "build":
        return
    receipt_path = OUT / "workflow_receipts.json"
    receipts = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else []
    for row in prepared:
        existing = next((x for x in receipts if x["variant_id"] == row["variant_id"]), None)
        if existing is None:
            saved = api("/workflow/save", {"name": row["name"], "graph": row["graph"], "workspace_id": "rw_general"})
            record = saved.get("item") or saved.get("workflow") or saved
            wid = record.get("id") or saved.get("workflow_id")
            if not wid:
                raise RuntimeError("Workflow save response lacks id: " + str(saved)[:1000])
            existing = {"variant_id": row["variant_id"], "workflow_id": wid,
                        "run_id": "wr_" + uuid.uuid4().hex[:24], "workspace_id": "rw_general",
                        "name": row["name"], "status": "prepared"}
            receipts.append(existing)
            save(receipt_path, receipts)
        if existing["status"] == "completed":
            continue
        api("/workflow/run/start", {"graph": row["graph"], "name": row["name"],
            "run_id": existing["run_id"], "workspace_id": "rw_general"})
        print(json.dumps({"event": "started", **existing}, ensure_ascii=False), flush=True)
        began, last_notice = time.monotonic(), 0
        while time.monotonic() - began < 1800:
            final = api("/workflow/run/status/" + existing["run_id"] + "?workspace_id=rw_general")
            rec = final.get("run") or final.get("item") or final
            status = rec.get("status")
            if status not in {"queued", "running"}:
                save(OUT / "runs" / (row["variant_id"] + ".json"), final)
                existing["status"] = status
                save(receipt_path, receipts)
                print(json.dumps({"event": "settled", "variant": row["variant_id"], "status": status,
                                  "reason": rec.get("reason")}, ensure_ascii=False), flush=True)
                if status != "completed":
                    raise RuntimeError("Preserved failed run; fix implementation before starting remaining variants")
                break
            if time.monotonic() - last_notice > 45:
                print(json.dumps({"event": "progress", "variant": row["variant_id"], "status": status}), flush=True)
                last_notice = time.monotonic()
            time.sleep(3)
        else:
            raise TimeoutError("Server run still active; do not restart or assume failed")


if __name__ == "__main__":
    main()
