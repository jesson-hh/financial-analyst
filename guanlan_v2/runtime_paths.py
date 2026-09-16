"""Stable test-process output isolation for the module-level ASGI app.

The production server intentionally exposes one module-level ``app``.  Pytest
imports that same object during collection, before function-scoped fixtures can
redirect its stores.  This module provides one explicit test mode with one
process-stable root, configured before any application module is imported.

It is not auto-detected from ``pytest`` or command names.  Both
``GUANLAN_TESTING=1`` and an absolute ``GUANLAN_TEST_ROOT`` are required; a
missing/unsafe root fails closed instead of silently falling back to production
``var``/``.data`` paths.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import MutableMapping


TEST_MODE_ENV = "GUANLAN_TESTING"
TEST_ROOT_ENV = "GUANLAN_TEST_ROOT"
RUNTIME_ROOT_ENV = "GUANLAN_RUNTIME_ROOT"
_CONFIGURED_ROOT_ENV = "GUANLAN_TEST_OUTPUTS_CONFIGURED_ROOT"

# Normal subsystem overrides.  Values are relative to the process-stable test
# root.  Assignment (rather than setdefault) is deliberate: a test process must
# not inherit production output locations from its parent shell.
TEST_OUTPUT_LAYOUT = {
    RUNTIME_ROOT_ENV: ".",
    "GUANLAN_CONSOLE_ROOT": "console",
    "GUANLAN_WISDOM_ROOT": "wisdom/cards",
    "FA_WISDOM_ROOT": "wisdom/engine",
    "GUANLAN_WORKFLOW_ROOT": "workflows",
    "GUANLAN_REPORTS_DIR": "reports",
    "GUANLAN_ARCHIVE_DIR": "archive",
    "GUANLAN_RESEARCH_STORE_ROOT": "research",
    "GUANLAN_COMPANY_MAPPING_DB": "research/company_mappings.db",
    "GUANLAN_SEATS_VAR_ROOT": "seats",
    "GUANLAN_WATCH_ALERT_ROOT": "watch_alerts",
    "GUANLAN_ORCH_STORE_ROOT": "orchestration",
    "GUANLAN_EVENTS_DB": "events/events.db",
    "GUANLAN_MARKET_TAPE_PATH": "live/market_tape.json",
    "GUANLAN_NEWSRADAR_PATH": "live/newsradar.json",
    "GUANLAN_OVERSEAS_PATH": "live/overseas.json",
    "GUANLAN_PROBE_WORKER_LOG": "live/probe_worker.stderr.log",
    "GUANLAN_FUNDFLOW_LIVE_DIR": "live/fundflow",
    "GUANLAN_FUNDFLOW_ROOT": "fundflow",
    "GUANLAN_SNAPSHOT_ARCHIVE_DIR": "snapshots",
    "GUANLAN_MACRO_ROOT": "macro",
    "GUANLAN_SENTIMENT_ROOT": "sentiment",
    "GUANLAN_SCREEN_ROOT": "screen",
    "GUANLAN_DATA_SCREEN_RUN_DIR": "screen/data_runs",
    "GUANLAN_AUTONOMY_ROOT": "autonomy",
    "GUANLAN_REPORT_EVIDENCE_DIR": "reports/evidence",
    "FA_CONVERSATIONS_DIR": "engine/conversations",
    "FA_CACHE_DIR": "engine/cache",
}


def configure_test_output_environment(
    environ: MutableMapping[str, str] | None = None,
    *,
    repo_root: Path | str | None = None,
) -> Path | None:
    """Bind all reviewed output overrides when explicit test mode is active."""

    target_env = os.environ if environ is None else environ
    if target_env.get(TEST_MODE_ENV) != "1":
        return None
    raw = str(target_env.get(TEST_ROOT_ENV) or "").strip()
    if not raw:
        raise RuntimeError("GUANLAN_TESTING=1 requires GUANLAN_TEST_ROOT")
    requested = Path(raw).expanduser()
    if not requested.is_absolute():
        raise RuntimeError("GUANLAN_TEST_ROOT must be absolute")
    root = requested.resolve()

    if repo_root is not None:
        repo = Path(repo_root).resolve()
        forbidden = {repo, repo / "var", repo / ".data"}
        if root in forbidden or repo in root.parents:
            raise RuntimeError("GUANLAN_TEST_ROOT must stay outside the repository")

    # ``tests/conftest.py`` establishes the process-stable bindings before
    # collection.  A later first import of ``guanlan_v2.server`` must not undo
    # function-scoped fixture refinements (notably the per-test Events DB).
    # The marker contains only the already-public temporary root.  A mismatch
    # fails closed rather than accepting two competing shared-app roots.
    configured_raw = str(target_env.get(_CONFIGURED_ROOT_ENV) or "").strip()
    if configured_raw:
        configured = Path(configured_raw).expanduser()
        if not configured.is_absolute() or configured.resolve() != root:
            raise RuntimeError("test output root changed after configuration")
        return root

    for key, relative in TEST_OUTPUT_LAYOUT.items():
        target_env[key] = str(root if relative == "." else root / relative)
    # TestClient lifespans must not start schedulers/writers/network prewarm.
    target_env["GUANLAN_SERVER_BACKGROUND"] = "0"
    target_env["GUANLAN_EVENTS_INGEST"] = "0"
    target_env[_CONFIGURED_ROOT_ENV] = str(root)
    return root
