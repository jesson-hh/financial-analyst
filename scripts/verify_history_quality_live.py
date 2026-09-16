"""Verify live quality rejection using a prior immutable artifact, no new trades.

Runs one durable diagnostic graph to prove the history node stops before ledger
simulation; keeps the original six run receipts unchanged.
"""
from __future__ import annotations
import json
from pathlib import Path
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var/trend_strategy_comparison_20260912"


def api(path, data=None):
    req = urllib.request.Request("http://127.0.0.1:9999" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as response:
        return json.load(response)


def main():
    original = json.loads((OUT / "runs/trend_baseline.json").read_text(encoding="utf-8"))
    record = original.get("run") or original
    payload = record["result"]["terminal"]["payload"]
    denied = api("/backtest/daily-screen", {"history": {"__dt": "screen_history",
        "history_artifact": payload["history_artifact"]}, "params": {}, "workspace_id": "rw_general"})
    assert denied.get("ok") is False and "价格质量未通过" in denied.get("reason", ""), denied
    receipt_path = OUT / "quality_gate_live_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path.exists() else {
        "run_id": "wr_" + uuid.uuid4().hex[:24], "workspace_id": "rw_general",
        "name": "价格质量门禁验收 · 趋势基线", "original_run_id": record["id"]}
    receipt["old_artifact_rejected"] = denied
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    started = api("/workflow/run/start", {"run_id": receipt["run_id"], "workspace_id": "rw_general",
        "name": receipt["name"], "graph": record["graph"]})
    assert started.get("ok") is True, started
    print(json.dumps({"started": receipt["run_id"], "old_artifact_rejected": True}), flush=True)
    began = time.monotonic()
    while time.monotonic() - began < 300:
        final = api("/workflow/run/status/" + receipt["run_id"] + "?workspace_id=rw_general")
        current = final.get("run") or final
        if current.get("status") not in {"queued", "running"}:
            receipt["final"] = final
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            encoded = json.dumps(current, ensure_ascii=False)
            assert "价格质量未通过" in encoded and current.get("result", {}).get("node_errors"), current.get("status")
            terminal = current.get("result", {}).get("terminal") or {}
            assert (terminal.get("payload") or {}).get("method") != "daily_screen_backtest"
            print(json.dumps({"gate_verified": True, "run_id": receipt["run_id"],
                "status": current.get("status"), "node_errors": current["result"]["node_errors"]}, ensure_ascii=False), flush=True)
            return
        time.sleep(2)
    raise TimeoutError("Diagnostic graph still running; do not restart it")


if __name__ == "__main__":
    main()
