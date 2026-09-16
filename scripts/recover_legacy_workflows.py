#!/usr/bin/env python3
"""Evidence-first workflow recovery. Dry-run never changes the source directory.

Only existing deterministic migrations that pass complete validation may be
applied. Legacy model/validate/screen boards remain byte-for-byte untouched.
Applying requires a prior manifest with the exact source-record SHA-256; every
changed record is backed up verbatim before an atomic replacement. Running again
does not rewrite timestamps, metadata, or backups for already repaired records.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "engine"))

from guanlan_v2.workflow.api import validate_graph
from guanlan_v2.workflow.migrations import WORKFLOW_SCHEMA_VERSION, migrate_graph

SAFE_ID = re.compile(r"[A-Za-z0-9_-]+\Z")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _graph_sha(graph):
    return _sha(json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _contained(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside requested root: {path}")
    return resolved


def _model_evidence(model_id, model_root):
    if not isinstance(model_id, str) or not SAFE_ID.fullmatch(model_id):
        return {"model_id": model_id, "status": "invalid_model_reference"}
    if model_root is None:
        return {"model_id": model_id, "status": "not_checked"}
    path = _contained(model_root / model_id / "meta.json", model_root)
    if not path.is_file():
        return {"model_id": model_id, "path": str(path), "status": "metadata_missing"}
    raw = path.read_bytes()
    metadata = json.loads(raw)
    files = sorted(p.name for p in path.parent.iterdir() if p.is_file())
    return {"model_id": model_id, "path": str(path), "status": "metadata_present",
            "sha256": _sha(raw), "metadata": metadata, "files": files,
            "recipe_available": isinstance(metadata.get("recipe"), dict) and bool(metadata["recipe"].get("features")),
            "estimator_artifact_declared": isinstance(metadata.get("estimator_artifact"), dict),
            "warning": "Current metadata/recipe/ranking do not prove the historical fitted estimator or original validation version."}


def _formula_evidence(graph, factor_root):
    from financial_analyst.factors.zoo.expr import validate_expr

    named = {}
    if factor_root is not None:
        for directory in (factor_root / "base", factor_root / "mined"):
            for path in sorted(directory.glob("*.json")):
                payload = json.loads(path.read_bytes())
                for entry in payload if isinstance(payload, list) else [payload]:
                    if isinstance(entry, dict) and entry.get("name"):
                        named.setdefault(entry["name"], []).append({"file": str(path.resolve()), "entry": entry})
    results = []
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") not in {"formula", "factorlib"}:
            continue
        params = node.get("params") or {}
        expr = str(params.get("expr") or "")
        entry = {"node_id": node.get("id"), "type": node.get("type"), "expr": expr}
        try:
            validate_expr(expr)
            entry["dsl_name_validation"] = "passed"
        except ValueError as exc:
            entry["dsl_name_validation"] = "failed"
            entry["error"] = str(exc)
        if expr in named:
            entry["current_named_factor_matches"] = named[expr]
            entry["note"] = "Named-library evidence is retained, not automatically substituted for an old custom target or composite."
        results.append(entry)
    return results


def _backup_and_replace(path, raw, record, expected_sha, root):
    path = _contained(path, root)
    if expected_sha != _sha(raw) or path.read_bytes() != raw:
        raise ValueError(f"Source changed since dry-run: {path.name}")
    backup = _contained(root / "_recovery_backups" / _sha(raw) / path.name, root)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        if backup.read_bytes() != raw:
            raise ValueError(f"Backup content mismatch: {backup}")
    else:
        with backup.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != raw:
            raise ValueError(f"Source changed while preparing recovery: {path.name}")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return str(backup)


def recover_root(root, *, ids=None, model_root=None, factor_root=None, apply=False, expected_hashes=None):
    root = Path(root).resolve()
    if not root.is_dir() or root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("Use an existing, explicitly scoped workflow-record directory")
    if apply and expected_hashes is None:
        raise ValueError("Apply requires SHA-256 evidence from a prior dry-run manifest")
    if ids is None:
        paths = sorted(root.glob("w*.json"))
    else:
        if any(not isinstance(wid, str) or not SAFE_ID.fullmatch(wid) for wid in ids):
            raise ValueError("Invalid workflow ID")
        paths = [root / f"{wid}.json" for wid in sorted(set(ids))]
    snapshots = {str(_contained(path, root)): path.read_bytes() for path in paths}
    summary = {"root": str(root), "apply": apply, "items": [], "changed_records": 0,
               "safe_repair_candidates": 0, "manual_rebuild_required": 0,
               "source_file_sha256": {path: _sha(raw) for path, raw in snapshots.items()}}
    planned = []
    for path in paths:
        path = _contained(path, root)
        raw = snapshots[str(path)]
        record = json.loads(raw)
        graph = record.get("graph") or {}
        before_ok, before_errors = validate_graph(graph, require_complete=True)
        migrated, migration = migrate_graph(graph, previous_migration=record.get("migration"))
        after_ok, after_errors = validate_graph(migrated, require_complete=True)
        # Legacy intent must never be replaced even if a future validator becomes
        # permissive. A successful structural check is not semantic provenance.
        safe = bool(migration["changed"] and after_ok and not migration["needs_review"]
                    and not migration.get("legacy_recovery"))
        recovery = migration.get("legacy_recovery") or {}
        model_ids = sorted({ref["model_id"] for ref in recovery.get("model_references", [])
                            if isinstance(ref.get("model_id"), str)})
        backups = []
        for candidate in sorted((root / "_migration_backups").glob(f"*/{path.name}")):
            candidate = _contained(candidate, root)
            backup_raw = candidate.read_bytes()
            backup_record = json.loads(backup_raw)
            backup_graph = backup_record.get("graph") or {}
            backups.append({"path": str(candidate), "sha256": _sha(backup_raw),
                            "graph_sha256": _graph_sha(backup_graph), "graph_equal_to_current": backup_graph == graph,
                            "name": backup_record.get("name"), "original_ts": backup_record.get("ts")})
        item = {"id": record.get("id", path.stem), "name": record.get("name"), "path": str(path),
                "sha256": _sha(raw), "graph_sha256": _graph_sha(graph), "original_graph": graph,
                "before_ok": before_ok, "before_errors": before_errors,
                "after_ok": after_ok, "after_errors": after_errors, "migration": migration,
                "safe_repair_candidate": safe, "backups": backups,
                "model_evidence": [_model_evidence(mid, model_root) for mid in model_ids],
                "formula_evidence": _formula_evidence(graph, factor_root) if recovery else [],
                "status": "safe_migration_available" if safe else ("already_valid" if before_ok else "manual_rebuild_required")}
        summary["safe_repair_candidates"] += int(safe)
        summary["manual_rebuild_required"] += int(not before_ok and not safe)
        summary["items"].append(item)
        if apply and safe:
            expected = expected_hashes.get(str(path))
            if expected != _sha(raw):
                raise ValueError(f"Source changed or missing from prior manifest: {path.name}")
            updated = deepcopy(record)
            updated.update({"graph": migrated, "schema_version": WORKFLOW_SCHEMA_VERSION,
                            "migration": migration, "validation": {"ok": True, "errors": [], "mode": "strict_recovery"}})
            planned.append((path, raw, updated, expected, item))
    # Preflight all eligible records before any replacement. Files that remain
    # invalid do not even receive new timestamps or metadata annotations.
    for path, raw, updated, expected, item in planned:
        item["backup_path"] = _backup_and_replace(path, raw, updated, expected, root)
        item["status"] = "repaired"
        summary["changed_records"] += 1
    summary["source_files_unchanged"] = all(Path(path).read_bytes() == raw for path, raw in snapshots.items())
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--id", action="append", dest="ids")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--factor-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expect-manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error("Diagnostic output must be outside the source workflow directory")
    expected = None
    if args.expect_manifest:
        manifest = json.loads(args.expect_manifest.read_bytes())
        if Path(manifest["root"]).resolve() != args.root.resolve():
            parser.error("Manifest root differs from requested workflow root")
        expected = manifest["source_file_sha256"]
    result = recover_root(args.root, ids=args.ids, model_root=args.model_root,
                          factor_root=args.factor_root, apply=args.apply, expected_hashes=expected)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key not in {"items", "source_file_sha256"}}, ensure_ascii=False, indent=2))
    for item in result["items"]:
        print(json.dumps({"id": item["id"], "status": item["status"],
                          "classification": item["migration"].get("legacy_recovery", {}).get("classification"),
                          "backups_equal": [b["graph_equal_to_current"] for b in item["backups"]],
                          "model_status": {m["model_id"]: m["status"] for m in item["model_evidence"]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
