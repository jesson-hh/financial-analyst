#!/usr/bin/env python3
"""Audit or safely migrate saved Guanlan workflow JSON records.

Dry-run is the default.  ``--apply`` creates a timestamped copy of every
changed record before atomically replacing it.  Ambiguous legacy boards are
annotated for review but their graph is never rewritten.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from guanlan_v2.workflow.api import validate_graph
from guanlan_v2.workflow.migrations import (
    WORKFLOW_SCHEMA_VERSION,
    migrate_graph,
)


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def audit_root(root: Path, apply: bool = False) -> Dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = root / "_migration_backups" / stamp
    summary: Dict[str, Any] = {
        "root": str(root.resolve()), "apply": apply, "total": 0,
        "valid_before": 0, "valid_after": 0, "graphs_repaired": 0,
        "needs_review": 0, "corrupt": 0, "items": [],
    }
    for path in sorted(root.glob("*.json")):
        summary["total"] += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            summary["corrupt"] += 1
            summary["items"].append({
                "file": path.name, "status": "corrupt",
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue
        if not isinstance(record, dict):
            summary["corrupt"] += 1
            summary["items"].append({"file": path.name, "status": "corrupt"})
            continue

        old_graph = record.get("graph") or {}
        before_ok, before_errors = validate_graph(old_graph, require_complete=True)
        migrated, report = migrate_graph(old_graph, previous_migration=record.get("migration"))
        after_ok, after_errors = validate_graph(migrated, require_complete=True)
        summary["valid_before"] += int(before_ok)
        summary["valid_after"] += int(after_ok)
        summary["graphs_repaired"] += int(report["changed"] and after_ok)
        summary["needs_review"] += int(bool(report["needs_review"]) or not after_ok)

        item = {
            "file": path.name, "id": record.get("id") or path.stem,
            "name": record.get("name") or "", "before_ok": before_ok,
            "after_ok": after_ok, "changed": report["changed"],
            "needs_review": report["needs_review"],
            "before_errors": before_errors, "after_errors": after_errors,
        }
        summary["items"].append(item)
        if not apply:
            continue

        updated = dict(record)
        if report["changed"] and after_ok:
            updated["graph"] = migrated
        updated["schema_version"] = (
            WORKFLOW_SCHEMA_VERSION if after_ok else int(record.get("schema_version") or 1)
        )
        updated["migration"] = report
        updated["validation"] = {
            "ok": after_ok,
            "errors": after_errors,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if updated == record:
            continue
        backup_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_root / path.name)
        _atomic_write(path, updated)

    if apply and backup_root.exists():
        summary["backup_root"] = str(backup_root.resolve())
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()
    result = audit_root(args.root, apply=args.apply)
    if args.summary_only:
        result = {key: value for key, value in result.items() if key != "items"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["corrupt"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
