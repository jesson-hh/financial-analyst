"""Security guards for credential fallback and shared-app test outputs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from guanlan_v2.runtime_paths import (
    TEST_OUTPUT_LAYOUT,
    configure_test_output_environment,
)
from guanlan_v2.runtime_secrets import (
    ALLOWED_CREDENTIAL_KEYS,
    load_credential_file,
    status_is_value_free,
)


def test_credential_file_is_exact_allowlist_and_value_free(tmp_path: Path) -> None:
    secret_a = "do-not-project-alpha"
    secret_b = "do-not-project-beta"
    source = tmp_path / "credentials.env"
    source.write_text(
        "DEEPSEEK_API_KEY=" + secret_a + "\n"
        "GL_PIT_HMAC_KEY=" + secret_b + "\n"
        "GUANLAN_MCP_WRITE=1\n"
        "GUANLAN_SERVER_BACKGROUND=1\n"
        "GUANLAN_EVENTS_INGEST=1\n"
        "GUANLAN_TELEGRAM_ENABLED=1\n"
        "GUANLAN_REGEN_DAILY=1\n"
        "UNKNOWN_PROVIDER_API_KEY=unknown-secret\n",
        encoding="utf-8",
    )
    target: dict[str, str] = {}

    status = load_credential_file(source, environ=target)

    assert target == {
        "DEEPSEEK_API_KEY": secret_a,
        "GL_PIT_HMAC_KEY": secret_b,
    }
    assert set(status["loaded_keys"]) == {"DEEPSEEK_API_KEY", "GL_PIT_HMAC_KEY"}
    assert set(status["blocked_keys"]) == {
        "GUANLAN_EVENTS_INGEST",
        "GUANLAN_MCP_WRITE",
        "GUANLAN_REGEN_DAILY",
        "GUANLAN_SERVER_BACKGROUND",
        "GUANLAN_TELEGRAM_ENABLED",
        "UNKNOWN_PROVIDER_API_KEY",
    }
    assert status_is_value_free(status)
    projected = json.dumps(status, sort_keys=True)
    assert secret_a not in projected
    assert secret_b not in projected
    assert "unknown-secret" not in projected


def test_credential_file_rejects_duplicate_and_preserves_existing(tmp_path: Path) -> None:
    source = tmp_path / "credentials.env"
    source.write_text(
        "OPENAI_API_KEY=first-file-value\n"
        "OPENAI_API_KEY=second-file-value\n"
        "ANTHROPIC_API_KEY=file-value\n"
        "TUSHARE_TOKEN=\n"
        "not a valid assignment\n"
        "lowercase_api_key=hidden\n",
        encoding="utf-8",
    )
    target = {"ANTHROPIC_API_KEY": "inherited-value"}

    status = load_credential_file(source, environ=target)

    assert target == {"ANTHROPIC_API_KEY": "inherited-value"}
    assert status["existing_keys"] == ["ANTHROPIC_API_KEY"]
    assert status["duplicate_keys"] == ["OPENAI_API_KEY"]
    assert status["empty_keys"] == ["TUSHARE_TOKEN"]
    assert status["invalid_line_count"] == 2
    assert "first-file-value" not in json.dumps(status)
    assert "second-file-value" not in json.dumps(status)


def test_default_loader_skips_file_in_explicit_test_mode(tmp_path: Path) -> None:
    source = tmp_path / "must-not-open.env"
    source.write_text("DEEPSEEK_API_KEY=must-not-load\n", encoding="utf-8")
    target = {"GUANLAN_TESTING": "1"}

    status = load_credential_file(
        source, environ=target, skip_when_testing=True,
    )

    assert status["state"] == "skipped_testing"
    assert status["file_present"] is False
    assert "DEEPSEEK_API_KEY" not in target


def test_credential_allowlist_covers_reviewed_provider_and_pit_names() -> None:
    assert {
        "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY", "KIMI_API_KEY",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TUSHARE_TOKEN",
        "FA_TUSHARE_TOKEN", "HUGGINGFACE_TOKEN", "TDX_API_KEY",
        "GUANLAN_MCP_BEARER_TOKEN", "GL_PIT_HMAC_KEY_ID",
        "GL_PIT_HMAC_KEY", "GL_PIT_HMAC_VERIFY_KEYS",
    } == set(ALLOWED_CREDENTIAL_KEYS)


def test_test_output_configuration_fails_closed_without_safe_root(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="requires GUANLAN_TEST_ROOT"):
        configure_test_output_environment(
            {"GUANLAN_TESTING": "1"}, repo_root=tmp_path,
        )
    with pytest.raises(RuntimeError, match="must stay outside"):
        configure_test_output_environment(
            {"GUANLAN_TESTING": "1", "GUANLAN_TEST_ROOT": str(tmp_path / "var")},
            repo_root=tmp_path,
        )


def test_test_output_configuration_is_idempotent_for_fixture_refinements(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    root = tmp_path / "runtime"
    case_db = tmp_path / "case" / "events.db"
    repo.mkdir()
    target = {
        "GUANLAN_TESTING": "1",
        "GUANLAN_TEST_ROOT": str(root),
    }

    assert configure_test_output_environment(target, repo_root=repo) == root
    target["GUANLAN_EVENTS_DB"] = str(case_db)

    assert configure_test_output_environment(target, repo_root=repo) == root
    assert target["GUANLAN_EVENTS_DB"] == str(case_db)


@pytest.mark.parametrize(
    "module_name",
    ["financial_analyst.cli", "financial_analyst.mcp_server"],
)
def test_engine_entrypoint_import_is_side_effect_free_in_explicit_test_mode(
    tmp_path: Path,
    module_name: str,
) -> None:
    safe_cache = tmp_path / "isolated" / "cache"
    escaped_cache = tmp_path / "escaped" / "cache"
    plugin_probe = tmp_path / "plugin-was-loaded"
    plugin = tmp_path / "probe_plugin.py"
    plugin.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['FA_PLUGIN_PROBE']).write_text('loaded', encoding='utf-8')\n",
        encoding="utf-8",
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "plugins.yaml").write_text(
        f'load_at_startup:\n  - "{plugin.as_posix()}"\n',
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        f"FA_CACHE_DIR={escaped_cache}\n"
        "DEEPSEEK_API_KEY=credential-from-dotenv\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "GUANLAN_TESTING": "1",
            "FA_CACHE_DIR": str(safe_cache),
            "DEEPSEEK_API_KEY": "inherited-test-credential",
            "FA_CONFIG_DIR": str(config_dir),
            "FA_PLUGIN_PROBE": str(plugin_probe),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    repo_root = Path(__file__).resolve().parents[1]
    python_paths = [str(repo_root / "engine"), str(repo_root)]
    if env.get("PYTHONPATH"):
        python_paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_paths)
    script = (
        "import importlib, os, sys\n"
        "importlib.import_module(sys.argv[1])\n"
        "assert os.environ['FA_CACHE_DIR'] == sys.argv[2]\n"
        "assert os.environ['DEEPSEEK_API_KEY'] == 'inherited-test-credential'\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, module_name, str(safe_cache)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert not plugin_probe.exists()


def test_shared_server_app_and_reviewed_output_roots_are_test_isolated(
    tmp_path: Path,
) -> None:
    import guanlan_v2.server as server
    from financial_analyst.buddy.conversations import ConversationStore
    from guanlan_v2.archive import api as archive_api
    from guanlan_v2.autonomy import jobs as autonomy_jobs
    from guanlan_v2.cards.store import CardStore
    from guanlan_v2.console.store import ConsoleStore
    from guanlan_v2.datafeed import sentiment
    from guanlan_v2.macro import pulse as macro_pulse
    from guanlan_v2.reports import api as reports_api
    from guanlan_v2.reports import evidence as report_evidence
    from guanlan_v2.research import store as research_store
    from guanlan_v2.screen import picks
    from guanlan_v2.seats import api as seats_api
    from guanlan_v2.seats import realtime_store
    from guanlan_v2.watch_alerts.core import AlertStore
    from guanlan_v2.workflow.run_store import WorkflowRunStore
    from guanlan_v2.workflow.store import WorkflowStore

    root = Path(os.environ["GUANLAN_TEST_ROOT"]).resolve()

    def beneath(value: Path | str) -> bool:
        return Path(value).resolve().is_relative_to(root)

    def isolated(value: Path | str) -> bool:
        resolved = Path(value).resolve()
        return resolved.is_relative_to(root) or resolved.is_relative_to(
            tmp_path.resolve()
        )

    # Every configured subsystem path is process-stable and below one root.
    # Importing ``server`` is itself allowed to re-assert these shared-app
    # bindings if a function-scoped fixture temporarily narrowed one of them.
    escaped_output_bindings = {
        name: os.environ[name]
        for name in TEST_OUTPUT_LAYOUT
        if not isolated(os.environ[name])
    }
    assert escaped_output_bindings == {}
    assert server._TEST_OUTPUT_ROOT == root
    assert server.app.state.test_output_root == str(root)
    assert isolated(server.app.state.console_store.root)
    assert isolated(ConsoleStore().root)
    assert isolated(CardStore().root)
    assert isolated(WorkflowStore().root)
    assert isolated(WorkflowRunStore().root)
    assert isolated(ConversationStore().dir)
    assert isolated(AlertStore().root)
    assert isolated(reports_api._STORE)
    assert isolated(archive_api._DIR)
    assert isolated(research_store.RUNS_PATH)
    assert isolated(research_store.ROUNDS_PATH)
    assert isolated(seats_api._DEC_LOG)
    assert isolated(realtime_store.DEFAULT_DB_PATH)
    assert isolated(macro_pulse._SNAP_DEFAULT)
    assert isolated(sentiment._ROOT)
    assert isolated(picks.PICKS_PATH)
    assert isolated(autonomy_jobs.JOBS_PATH)
    assert isolated(report_evidence._DEFAULT_OUT_DIR)

    # The module-level app is not recreated between imports/tests, and default
    # server import did not inspect the real fallback credentials file.
    from guanlan_v2.server import app as same_app
    assert same_app is server.app
    assert server._SECRETS_ENV_STATUS["state"] == "skipped_testing"
    assert status_is_value_free(server.app.state.secrets_env_status)


def test_runtime_credential_status_endpoint_is_value_free() -> None:
    from fastapi.testclient import TestClient

    import guanlan_v2.server as server

    with TestClient(server.app) as client:
        response = client.get("/runtime/secrets-env-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "skipped_testing"
    assert status_is_value_free(payload)
    assert "path" not in payload
    assert "error" not in payload
