import asyncio
import contextvars
import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guanlan_v2.console.api import build_console_router
from guanlan_v2.console import api as console_api
from guanlan_v2.console.store import ConsoleStore


def test_failed_report_receipt_prefers_stdout_root_error_over_stderr_warning(tmp_path):
    pack = tmp_path / "engine_evidence_pack.json"
    pack.write_bytes(b"{}")
    result_path = tmp_path / "report_result.json"
    out_dir = tmp_path / "report_out"
    out_dir.mkdir()
    request_sha = "a" * 64
    receipt = {
        "schema_version": "company-report-process-result@2",
        "job_id": "bg_test",
        "code": "SH600667",
        "request_sha256": request_sha,
        "evidence_pack_sha256": hashlib.sha256(pack.read_bytes()).hexdigest(),
        "report_out_dir_sha256": hashlib.sha256(
            os.path.normcase(str(out_dir.resolve())).encode("utf-8")
        ).hexdigest(),
        "ok": False,
        "error": "legacy report exited 1",
        "stdout_tail": "Failed.\nreport-writer: JSONDecodeError: Unterminated string",
        "stderr_tail": "FutureWarning: pct_change default is deprecated",
    }
    result_path.write_text(json.dumps(receipt), encoding="utf-8")

    result = console_api._company_report_result(
        result_path=result_path, bg_id="bg_test", code="SH600667",
        request_sha256=request_sha, evidence_pack_path=str(pack),
        report_out_dir=out_dir,
    )

    assert result is not None and result["ok"] is False
    assert "JSONDecodeError: Unterminated string" in result["content"]
    assert "FutureWarning" not in result["content"]


class _Event:
    def __init__(self, kind, payload=None):
        self.kind = kind
        self.payload = payload


def _wait(store, sid, predicate, *, tries=120):
    events = []
    for _ in range(tries):
        events = store.read_events(sid)
        if predicate(events):
            return events
        time.sleep(0.05)
    return events


def _world(tmp_path):
    store = ConsoleStore(tmp_path)
    workspace = store.workspace_store.create({
        "kind": "stock",
        "code": "SH603986",
        "name": "兆易创新",
        "display_name": "兆易创新",
        "root_mode": "managed",
        "agent_mode": "research",
    })
    sid = store.create_session(workspace_id=workspace["id"])["id"]
    return store, workspace, sid


class _CompanyAgent:
    manages_own_history = True
    supports_scoped_company_research = True

    def __init__(self):
        self.messages = []
        self.launch_result = None

    async def run_turn(
        self, _text, confirm_callback=None,
        company_research_launch_callback=None, allowed_tools=None,
        thinking_mode="standard",
    ):
        args = {"asof": "2026-08-30"}
        yield _Event("tool_call", {
            "name": "ww_workspace_company_research_run", "args": args,
        })
        approved = await confirm_callback(
            "ww_workspace_company_research_run", args,
        )
        if approved:
            self.launch_result = await company_research_launch_callback(args)
            yield _Event("tool_result", {
                "name": "ww_workspace_company_research_run",
                "content": self.launch_result["content"],
                "is_error": False,
                "side_effect": {},
            })
        else:
            yield _Event("tool_result", {
                "name": "ww_workspace_company_research_run",
                "content": "用户未批准；未启动。",
                "is_error": True,
                "side_effect": {},
            })
        yield _Event("done")


class _BypassCompanyAgent:
    manages_own_history = True
    supports_scoped_company_research = True

    def __init__(self):
        self.error = None

    async def run_turn(
        self, _text, confirm_callback=None,
        company_research_launch_callback=None, allowed_tools=None,
        thinking_mode="standard",
    ):
        try:
            await company_research_launch_callback({"asof": "2026-08-30"})
        except Exception as exc:  # the test asserts the closed boundary below
            self.error = exc
        yield _Event("done")


class _ContextDetachedCompanyAgent(_CompanyAgent):
    """Mirror the Codex MCP HTTP task, which has no Console ContextVars."""

    def __init__(self, *, detached_context=None, launch_stub=None):
        super().__init__()
        self.detached_context = detached_context or contextvars.Context()
        self.launch_stub = launch_stub
        self.launch_calls = []

    async def run_turn(
        self, _text, confirm_callback=None,
        company_research_launch_callback=None, allowed_tools=None,
        thinking_mode="standard",
    ):
        args = {"asof": "2026-08-30"}
        yield _Event("tool_call", {
            "name": "ww_workspace_company_research_run", "args": args,
        })
        approved = await asyncio.create_task(
            confirm_callback("ww_workspace_company_research_run", args),
            context=self.detached_context,
        )
        if approved:
            self.launch_calls.append(dict(args))
            launcher = self.launch_stub or company_research_launch_callback
            self.launch_result = await launcher(args)
        yield _Event("done")


def test_confirmed_codex_company_research_runs_real_lane_and_finalizes_once(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    import guanlan_v2.glmcp.company_research as company_mcp

    monkeypatch.setenv("GUANLAN_COMPANY_RESEARCH_PIPELINE", "legacy")

    store, workspace, sid = _world(tmp_path)
    report_path = tmp_path / "legacy-report.md"
    report_path.write_text("# 兆易创新\n\n真实 legacy report fixture\n", encoding="utf-8")
    prepared_id = "wa_aaaaaaaaaaaaaaaa"
    final_id = "wa_bbbbbbbbbbbbbbbb"
    prepared_calls = []
    finalizer_calls = []

    def prepare(*, asof, _global_event_write, _newsradar_allow_refresh):
        context = workspace_tools.CTX_WORKSPACE.get()
        prepared_calls.append({
            "asof": asof,
            "workspace_id": context["workspace_id"],
            "global_event_write": _global_event_write,
            "newsradar_allow_refresh": _newsradar_allow_refresh,
        })
        return {"ok": True, "asset": {"id": prepared_id}}

    def finalize(
        current_store, current_sid, workspace_id, prepared_asset_id,
        code, md_path, *, _effect_key, _effect_context,
    ):
        finalizer_calls.append({
            "store": current_store,
            "sid": current_sid,
            "workspace_id": workspace_id,
            "prepared_asset_id": prepared_asset_id,
            "code": code,
            "md_path": md_path,
            "effect_key": _effect_key,
            "effect_context": _effect_context,
        })
        asset = {
            "id": final_id,
            "workspace_id": workspace_id,
            "source": "ww_workspace_company_research_run",
            "prepared_asset_id": prepared_asset_id,
            "finalizer_effect_key": _effect_key,
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "run_id": "run_final",
            "name": "兆易创新 · 深度研报",
            "status": "complete",
            "attachments": {"documents": [{"document_id": "doc_final"}]},
        }
        return {
            "ok": True,
            "asset": asset,
            "artifact": company_mcp.artifact_for_finalized_company_asset(asset),
        }

    monkeypatch.setattr(
        workspace_tools, "workspace_company_research_prepare_impl", prepare,
    )
    monkeypatch.setattr(
        workspace_tools, "resolve_company_report_pack",
        lambda *_args: str(tmp_path / "prepared-pack.json"),
    )
    monkeypatch.setattr(workspace_tools, "finalize_company_research_report", finalize)
    monkeypatch.setattr(
        company_mcp, "find_finalized_company_asset", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        console_api, "_call_durable_company_report",
        lambda **kwargs: {
            "ok": True,
            "content": (
                f"legacy report complete for {kwargs['code']} "
                f"at {kwargs['asof']}"
            ),
            "md_path": str(report_path),
            "durable_receipt": {
                "report_sha256": hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
            },
        },
    )
    monkeypatch.setattr(console_api, "_archive_research", lambda **_kwargs: True)
    monkeypatch.setattr(console_api, "_BG_PROGRESS_POLL", 0.01)

    agent = _CompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))
    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        before = _wait(store, sid, lambda events: any(
            event["type"] == "confirm_request" for event in events
        ))
        request = next(
            event for event in before if event["type"] == "confirm_request"
        )
        assert request["tool"] == "ww_workspace_company_research_run"
        assert request["args"] == {"asof": "2026-08-30"}
        assert (store.get_meta(sid).get("bg") or {}) == {}

        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "y",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "task_update"
            and row.get("kind") == "company_research_v1"
            and row.get("status") == "done"
            for row in rows
        ))

    assert agent.launch_result["receipt"]["workspace_id"] == workspace["id"]
    assert agent.launch_result["receipt"]["code"] == "SH603986"
    assert agent.launch_result["receipt"]["status"] == "queued"
    assert prepared_calls == [{
        "asof": "2026-08-30",
        "workspace_id": workspace["id"],
        "global_event_write": False,
        "newsradar_allow_refresh": False,
    }]
    assert len(finalizer_calls) == 1
    assert finalizer_calls[0]["workspace_id"] == workspace["id"]
    assert finalizer_calls[0]["prepared_asset_id"] == prepared_id
    assert finalizer_calls[0]["code"] == "SH603986"
    receipt = store.get_meta(sid)["bg"][agent.launch_result["receipt"]["job_id"]]
    assert receipt["status"] == "done" and receipt["ok"] is True, receipt.get("note")
    assert receipt["schema_version"] == "codex-company-research-receipt@1"
    assert receipt["job_id"] == agent.launch_result["receipt"]["job_id"]
    assert receipt["accepted_at"] == agent.launch_result["receipt"]["accepted_at"]
    assert len(receipt["workspace_subject_sha256"]) == 64
    assert len(receipt["workspace_root_sha256"]) == 64
    assert receipt["prepared_asset_id"] == prepared_id
    assert receipt["asset_id"] == final_id
    assert receipt["asset_status"] == "complete"
    assert receipt["pipeline_mode"] == "legacy"
    assert receipt["typed_plan"] is None
    assert receipt["finalizer_invoked"] is True
    assert receipt["adopted_existing_asset"] is False
    assert receipt["finalizer_replayed"] is False
    assert receipt["report_sha256"] == hashlib.sha256(
        report_path.read_bytes()
    ).hexdigest()
    assert any(
        row["type"] == "tool_result"
        and row.get("tool") == "ww_workspace_company_research_run"
        and (row.get("artifact") or {}).get("payload", {}).get("asset_id") == final_id
        for row in events
    )


def test_confirmed_company_research_default_uses_typed_v2_contract(
    tmp_path, monkeypatch,
):
    """The production Console route freezes and executes the reviewed v2 DAG."""
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    import guanlan_v2.glmcp.company_research as company_mcp

    monkeypatch.delenv("GUANLAN_COMPANY_RESEARCH_PIPELINE", raising=False)
    store, workspace, sid = _world(tmp_path)
    pack_path = tmp_path / "typed-prepared-pack.json"
    pack_path.write_text('{"fixture":"prepared"}', encoding="utf-8")
    report_path = tmp_path / "typed-report.md"
    report_path.write_text("# 兆易创新\n\ntyped-v2 report fixture\n", encoding="utf-8")
    prepared_id = "wa_1111111111111111"
    final_id = "wa_2222222222222222"
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    risk_sha = "5" * 64
    gate_sha = "6" * 64
    finalizer_calls = []

    monkeypatch.setattr(
        workspace_tools,
        "workspace_company_research_prepare_impl",
        lambda **_kwargs: {"ok": True, "asset": {"id": prepared_id}},
    )
    monkeypatch.setattr(
        workspace_tools,
        "resolve_company_report_pack",
        lambda *_args: str(pack_path),
    )
    monkeypatch.setattr(
        workspace_tools,
        "_internal_company_asset",
        lambda *_args: {
            "id": prepared_id,
            "workspace_id": workspace["id"],
            "source": "ww_workspace_company_research_prepare",
        },
    )
    monkeypatch.setattr(
        workspace_tools,
        "evaluate_company_research_report_gates",
        lambda *_args: {
            "ok": True,
            "report_sha256": report_sha,
            "risk_contract": {"status": "complete", "gaps": []},
            "risk_contract_sha256": risk_sha,
            "report_gate": {"status": "complete", "violations": []},
            "report_gate_sha256": gate_sha,
        },
    )

    def finalize(
        current_store, current_sid, workspace_id, prepared_asset_id,
        code, md_path, *, _effect_key, _effect_context,
    ):
        finalizer_calls.append((
            workspace_id, prepared_asset_id, code, md_path,
            _effect_key, _effect_context,
        ))
        asset = {
            "id": final_id,
            "workspace_id": workspace_id,
            "source": "ww_workspace_company_research_run",
            "prepared_asset_id": prepared_asset_id,
            "finalizer_effect_key": _effect_key,
            "report_sha256": report_sha,
            "risk_contract_sha256": risk_sha,
            "report_gate_sha256": gate_sha,
            "run_id": "run_typed_final",
            "name": "兆易创新 · typed-v2 深度研报",
            "status": "complete",
            "attachments": {"documents": []},
        }
        return {
            "ok": True,
            "asset": asset,
            "artifact": company_mcp.artifact_for_finalized_company_asset(asset),
        }

    monkeypatch.setattr(
        workspace_tools, "finalize_company_research_report", finalize,
    )
    monkeypatch.setattr(
        company_mcp, "find_finalized_company_asset", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        console_api,
        "_call_durable_company_report",
        lambda **_kwargs: {
            "ok": True,
            "content": "typed-v2 report complete",
            "md_path": str(report_path),
            "durable_receipt": {
                "schema_version": "company-report-process-result@1",
                "result_sha256": "4" * 64,
                "report_sha256": report_sha,
            },
        },
    )
    monkeypatch.setattr(console_api, "_archive_research", lambda **_kwargs: True)
    monkeypatch.setattr(console_api, "_BG_PROGRESS_POLL", 0.01)

    agent = _CompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))
    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "confirm_request" for row in rows
        ))
        request = next(
            row for row in events if row["type"] == "confirm_request"
        )
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "y",
        }).json()["ok"]
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update"
            and row.get("kind") == "company_research_v1"
            and row.get("status") in {"done", "error"}
            for row in rows
        ))

    job_id = agent.launch_result["receipt"]["job_id"]
    receipt = store.get_meta(sid)["bg"][job_id]
    assert receipt["status"] == "done" and receipt["ok"] is True, receipt.get("note")
    assert receipt["pipeline_mode"] == "typed_v2"
    assert receipt["typed_plan"]["plan_digest"] == receipt["typed_plan_digest"]
    assert receipt["typed_budget_reservation"]["reserved_tokens"] == 160000
    assert receipt["typed_budget_reservation"]["reserved_llm_invocations"] == 25
    assert receipt["typed_budget_reservation"]["ledger_id"].endswith(
        "declaration-only-not-a-runtime-ledger"
    )
    assert receipt["budget_runtime_enforced"] is False
    assert receipt["budget_enforcement"] == (
        "declared_plan_budget_with_wall_clock_timeout_only"
    )
    assert receipt["typed_plan_approval"]["actor_id"] == (
        f"console-confirmation:{receipt['confirmation_id']}"
    )
    assert receipt["typed_execution"]["plan_digest"] == receipt["typed_plan_digest"]
    assert receipt["typed_execution"]["asset_id"] == final_id
    assert receipt["typed_execution"]["risk_contract_sha256"] == risk_sha
    assert receipt["typed_execution"]["report_gate_sha256"] == gate_sha
    assert receipt["finalizer_invoked"] is True
    assert receipt["finalizer_replayed"] is False
    assert len(finalizer_calls) == 1
    assert finalizer_calls[0][:4] == (
        workspace["id"], prepared_id, workspace["code"], str(report_path),
    )
    assert finalizer_calls[0][4] == receipt["finalizer_effect_key"]
    assert finalizer_calls[0][5]["typed_plan_digest"] == receipt["typed_plan_digest"]


def test_rejected_codex_company_research_creates_no_background_receipt(
    tmp_path,
):
    store, _workspace, sid = _world(tmp_path)
    agent = _CompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))
    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "confirm_request" for row in rows
        ))
        request = next(
            row for row in events if row["type"] == "confirm_request"
        )
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "n",
        }).json()["ok"]
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update" and row.get("status") == "done"
            for row in rows
        ))

    assert agent.launch_result is None
    assert (store.get_meta(sid).get("bg") or {}) == {}


def test_company_research_confirmation_survives_detached_mcp_context(tmp_path):
    """The trusted Console binding must cross the Codex MCP ASGI task boundary."""
    store, workspace, sid = _world(tmp_path)
    agent = _ContextDetachedCompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))

    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] in {"confirm_request", "confirm_resolved"}
            for row in rows
        ))
        request = next(
            (row for row in events if row["type"] == "confirm_request"), None,
        )
        assert request is not None, events
        assert request["tool"] == "ww_workspace_company_research_run"
        assert request["args"] == {"asof": "2026-08-30"}
        assert workspace["code"] in "\n".join(request["precheck"])
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "n",
        }).json()["ok"]
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update" and row.get("status") == "done"
            for row in rows
        ))

    assert agent.launch_result is None
    assert (store.get_meta(sid).get("bg") or {}) == {}


def test_detached_company_confirmation_approves_one_bound_stub_launch(tmp_path):
    """A detached Codex callback emits one gate and launches exactly once."""
    from guanlan_v2.console import workspace_tools
    from guanlan_v2.glmcp.company_research import (
        CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA,
        CODEX_COMPANY_RESEARCH_RECIPE,
        company_research_binding_sha256,
    )
    from guanlan_v2.glmcp.scope import (
        workspace_root_sha256,
        workspace_subject_sha256,
    )

    store, workspace, sid = _world(tmp_path)
    subject_sha = workspace_subject_sha256(workspace)
    root_sha = workspace_root_sha256(
        workspace_tools._root_for(store, workspace["id"]),
    )
    binding_sha = company_research_binding_sha256(
        sid=sid,
        workspace_id=workspace["id"],
        code=workspace["code"],
        subject_sha256=subject_sha,
        root_sha256=root_sha,
    )
    stub_calls = []

    async def launch_stub(args):
        stub_calls.append(dict(args))
        return {
            "ok": True,
            "content": "stub launch queued",
            "receipt": {
                "schema_version": CODEX_COMPANY_RESEARCH_RECEIPT_SCHEMA,
                "recipe_id": CODEX_COMPANY_RESEARCH_RECIPE,
                "job_id": "bg_0123456789",
                "status": "queued",
                "sid": sid,
                "workspace_id": workspace["id"],
                "code": workspace["code"],
                "binding_sha256": binding_sha,
                "asof": args["asof"],
                "accepted_at": "2026-08-30T00:00:00+00:00",
            },
        }

    agent = _ContextDetachedCompanyAgent(launch_stub=launch_stub)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))

    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "confirm_request" for row in rows
        ))
        requests = [
            row for row in events if row["type"] == "confirm_request"
        ]
        assert len(requests) == 1
        request = requests[0]
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "y",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "task_update" and row.get("status") == "done"
            for row in rows
        ))

    assert len([
        row for row in events if row["type"] == "confirm_request"
    ]) == 1
    assert agent.launch_calls == [{"asof": "2026-08-30"}]
    assert stub_calls == [{"asof": "2026-08-30"}]
    receipt = agent.launch_result["receipt"]
    assert receipt["status"] == "queued"
    assert receipt["sid"] == sid
    assert receipt["workspace_id"] == workspace["id"]
    assert receipt["code"] == workspace["code"]
    assert receipt["binding_sha256"] == company_research_binding_sha256(
        sid=receipt["sid"],
        workspace_id=receipt["workspace_id"],
        code=receipt["code"],
        subject_sha256=subject_sha,
        root_sha256=root_sha,
    )
    assert (store.get_meta(sid).get("bg") or {}) == {}


def test_detached_company_confirmation_ignores_wrong_ambient_workspace(tmp_path):
    """An untrusted detached-task ContextVar cannot replace the turn binding."""
    from guanlan_v2.console import workspace_tools

    store, workspace, sid = _world(tmp_path)
    wrong_code = "SZ000001"
    wrong_context = contextvars.Context()
    wrong_context.run(workspace_tools.CTX_WORKSPACE.set, {
        "sid": sid,
        "workspace_id": "rw_wrong",
        "workspace": {
            "id": "rw_wrong",
            "kind": "stock",
            "code": wrong_code,
            "name": "错误标的",
        },
    })
    agent = _ContextDetachedCompanyAgent(detached_context=wrong_context)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))

    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "confirm_request" for row in rows
        ))
        requests = [
            row for row in events if row["type"] == "confirm_request"
        ]
        assert len(requests) == 1
        precheck = "\n".join(requests[0]["precheck"])
        assert workspace["code"] in precheck
        assert wrong_code not in precheck
        assert client.post("/console/confirm", json={
            "turn_id": requests[0]["turn_id"],
            "confirm_id": requests[0]["confirm_id"],
            "choice": "n",
        }).json()["ok"]
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update" and row.get("status") == "done"
            for row in rows
        ))

    assert agent.launch_calls == []
    assert (store.get_meta(sid).get("bg") or {}) == {}


def test_company_research_private_launch_callback_cannot_bypass_confirm(tmp_path):
    store, _workspace, sid = _world(tmp_path)
    agent = _BypassCompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))
    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "绕过确认测试",
        }).json()["ok"]
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update" and row.get("status") == "done"
            for row in rows
        ))

    assert agent.error is not None
    assert "确认" in str(agent.error)
    assert (store.get_meta(sid).get("bg") or {}) == {}


def test_company_research_restart_fails_closed_or_adopts_exact_asset(
    tmp_path, monkeypatch,
):
    import guanlan_v2.glmcp.company_research as company_mcp

    store, workspace, sid = _world(tmp_path)
    store.merge_meta_sub(sid, "bg", "bg_1111111111", {
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "code": workspace["code"],
        "workspace_id": workspace["id"],
        "status": "queued",
    })
    store.merge_meta_sub(sid, "bg", "bg_2222222222", {
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "code": workspace["code"],
        "workspace_id": workspace["id"],
        "prepared_asset_id": "wa_aaaaaaaaaaaaaaaa",
        "report_sha256": "c" * 64,
        "status": "finalizing",
    })
    store.merge_meta_sub(sid, "bg", "bg_3333333333", {
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "code": workspace["code"],
        "workspace_id": workspace["id"],
        "prepared_asset_id": "wa_dddddddddddddddd",
        "report_sha256": "d" * 64,
        "status": "finalizing",
    })
    asset = {
        "id": "wa_cccccccccccccccc",
        "workspace_id": workspace["id"],
        "source": "ww_workspace_company_research_run",
        "prepared_asset_id": "wa_aaaaaaaaaaaaaaaa",
        "report_sha256": "c" * 64,
        "run_id": "run_recovered",
        "name": "兆易创新 · 深度研报",
        "status": "complete",
        "attachments": {"documents": []},
    }
    calls = []

    def find(
        _store, *, workspace_id, prepared_asset_id, report_sha256,
        finalizer_effect_key="",
    ):
        assert finalizer_effect_key == ""
        calls.append((workspace_id, prepared_asset_id, report_sha256))
        return dict(asset) if report_sha256 == "c" * 64 else None

    monkeypatch.setattr(company_mcp, "find_finalized_company_asset", find)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: _CompanyAgent(),
    ))

    background = store.get_meta(sid)["bg"]
    interrupted = background["bg_1111111111"]
    assert interrupted["status"] == "error"
    assert interrupted["ok"] is False
    assert interrupted["interrupted"] is True
    assert interrupted["finalizer_replayed"] is False
    adopted = background["bg_2222222222"]
    assert adopted["status"] == "done" and adopted["ok"] is True
    assert adopted["asset_id"] == asset["id"]
    assert adopted["recovered_after_restart"] is True
    assert adopted["finalizer_replayed"] is False
    not_found = background["bg_3333333333"]
    assert not_found["status"] == "error" and not_found["ok"] is False
    assert not_found["interrupted"] is True
    assert not_found["finalizer_replayed"] is False
    assert calls == [
        (workspace["id"], "wa_aaaaaaaaaaaaaaaa", "c" * 64),
        (workspace["id"], "wa_dddddddddddddddd", "d" * 64),
    ]
    events = store.read_events(sid)
    assert any(
        row["type"] == "tool_result"
        and (row.get("artifact") or {}).get("payload", {}).get("asset_id")
        == asset["id"]
        for row in events
    )
    assert any(
        row["type"] == "task_update"
        and row.get("task_id") == "bg_1111111111"
        and row.get("interrupted") is True
        for row in events
    )


def test_company_research_cancel_after_queue_stops_before_report(
    tmp_path, monkeypatch,
):
    """A queued job is cancellable without touching foreground-turn cancel.

    Preparation may already be inside its private scoped write when the request
    arrives.  The cancellation is therefore cooperative: let that write close,
    then prove the report process and finalizer never start.
    """
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools

    store, _workspace, sid = _world(tmp_path)
    entered_prepare = threading.Event()
    release_prepare = threading.Event()
    report_calls = []
    finalizer_calls = []

    def prepare(**_kwargs):
        entered_prepare.set()
        assert release_prepare.wait(timeout=5)
        return {"ok": True, "asset": {"id": "wa_aaaaaaaaaaaaaaaa"}}

    monkeypatch.setattr(
        workspace_tools, "workspace_company_research_prepare_impl", prepare,
    )
    monkeypatch.setattr(
        console_api, "_call_durable_company_report",
        lambda **kwargs: report_calls.append(kwargs),
    )
    monkeypatch.setattr(
        workspace_tools, "finalize_company_research_report",
        lambda *_args: finalizer_calls.append(_args),
    )

    agent = _CompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))
    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "confirm_request" for row in rows
        ))
        request = next(
            row for row in events if row["type"] == "confirm_request"
        )
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "y",
        }).json()["ok"]
        assert entered_prepare.wait(timeout=5)
        job_id = agent.launch_result["receipt"]["job_id"]
        response = client.post("/console/company-research/cancel", json={
            "sid": sid, "job_id": job_id,
        })
        assert response.status_code == 202
        assert response.json()["status"] == "cancelling"
        release_prepare.set()
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update"
            and row.get("task_id") == job_id
            and row.get("status") == "cancelled"
            for row in rows
        ))
        terminal = client.post("/console/company-research/cancel", json={
            "sid": sid, "job_id": job_id,
        })
        assert terminal.status_code == 200
        assert terminal.json()["already_terminal"] is True

    receipt = store.get_meta(sid)["bg"][job_id]
    assert receipt["status"] == "cancelled"
    assert receipt["pipeline_mode"] == "typed_v2"
    assert receipt["typed_plan_digest"]
    assert receipt["cancelled_stage"] == "after_prepare"
    assert receipt["finalizer_replayed"] is False
    assert report_calls == []
    assert finalizer_calls == []


def test_company_research_cancel_rejects_exactly_once_finalizing(
    tmp_path,
):
    store, workspace, sid = _world(tmp_path)
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: _CompanyAgent(),
    ))
    job_id = "bg_5555555555"
    store.merge_meta_sub(sid, "bg", job_id, {
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "workspace_id": workspace["id"],
        "code": workspace["code"],
        "status": "finalizing",
    })
    with TestClient(app) as client:
        response = client.post("/console/company-research/cancel", json={
            "sid": sid, "job_id": job_id,
        })
    assert response.status_code == 409
    assert "exactly-once" in response.json()["reason"]
    assert "不能取消或重放" in response.json()["reason"]
    assert store.get_meta(sid)["bg"][job_id]["status"] == "finalizing"


def test_company_research_cancel_during_archive_cannot_cross_finalizer_boundary(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    import guanlan_v2.glmcp.company_research as company_mcp

    store, _workspace, sid = _world(tmp_path)
    report_path = tmp_path / "cancel-race-report.md"
    report_path.write_text("# 兆易创新\n\ncancel race\n", encoding="utf-8")
    pack_path = tmp_path / "cancel-race-pack.json"
    pack_path.write_text("{}", encoding="utf-8")
    entered_archive = threading.Event()
    release_archive = threading.Event()
    finalizer_calls = []

    monkeypatch.setattr(
        workspace_tools, "workspace_company_research_prepare_impl",
        lambda **_kwargs: {
            "ok": True, "asset": {"id": "wa_abababababababab"},
        },
    )
    monkeypatch.setattr(
        workspace_tools, "resolve_company_report_pack",
        lambda *_args: str(pack_path),
    )
    monkeypatch.setattr(
        console_api, "_call_durable_company_report",
        lambda **_kwargs: {
            "ok": True, "content": "done", "md_path": str(report_path),
            "durable_receipt": {
                "schema_version": "company-report-process-result@2",
                "report_sha256": hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
            },
        },
    )

    def blocked_archive(**_kwargs):
        entered_archive.set()
        assert release_archive.wait(timeout=5)
        return True

    monkeypatch.setattr(console_api, "_archive_research", blocked_archive)
    monkeypatch.setattr(
        console_api, "_finalize_company_research_typed_v2",
        lambda **kwargs: finalizer_calls.append(kwargs),
    )
    monkeypatch.setattr(
        company_mcp, "find_finalized_company_asset",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(console_api, "_BG_PROGRESS_POLL", 0.01)

    agent = _CompanyAgent()
    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: agent,
    ))
    with TestClient(app) as client:
        assert client.post("/console/send", json={
            "sid": sid, "text": "研究当前公司",
        }).json()["ok"]
        events = _wait(store, sid, lambda rows: any(
            row["type"] == "confirm_request" for row in rows
        ))
        request = next(row for row in events if row["type"] == "confirm_request")
        assert client.post("/console/confirm", json={
            "turn_id": request["turn_id"],
            "confirm_id": request["confirm_id"],
            "choice": "y",
        }).json()["ok"]
        assert entered_archive.wait(timeout=5)
        job_id = agent.launch_result["receipt"]["job_id"]
        response = client.post("/console/company-research/cancel", json={
            "sid": sid, "job_id": job_id,
        })
        assert response.status_code == 202
        release_archive.set()
        _wait(store, sid, lambda _rows: (
            store.get_meta(sid)["bg"][job_id].get("status") == "cancelled"
        ))

    receipt = store.get_meta(sid)["bg"][job_id]
    assert receipt["status"] == "cancelled"
    assert receipt["cancelled_stage"] == "before_finalizing"
    assert finalizer_calls == []


def test_company_research_restart_closes_persisted_cancelling_job(tmp_path):
    store, workspace, sid = _world(tmp_path)
    job_id = "bg_5656565656"
    store.merge_meta_sub(sid, "bg", job_id, {
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "workspace_id": workspace["id"],
        "code": workspace["code"],
        "status": "cancelling",
        "cancel_requested": True,
    })

    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: _CompanyAgent(),
    ))

    receipt = store.get_meta(sid)["bg"][job_id]
    assert receipt["status"] == "cancelled"
    assert receipt["cancelled_stage"] == "service_restart"
    assert receipt["finalizer_replayed"] is False


def test_company_research_opted_in_running_receipt_resumes_same_job(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    from guanlan_v2.glmcp.scope import (
        workspace_root_sha256,
        workspace_subject_sha256,
    )
    import guanlan_v2.glmcp.company_research as company_mcp

    monkeypatch.setenv("GUANLAN_COMPANY_RESEARCH_PIPELINE", "legacy")

    store, workspace, sid = _world(tmp_path)
    root = workspace_tools._root_for(store, workspace["id"])
    job_id = "bg_4444444444"
    prepared_id = "wa_aaaaaaaaaaaaaaaa"
    final_id = "wa_bbbbbbbbbbbbbbbb"
    report_path = tmp_path / "resumed-report.md"
    report_path.write_text("# 兆易创新\n\n恢复后的真实报告\n", encoding="utf-8")
    calls = {"prepare": 0, "report": [], "finalize": 0}
    binding_sha = company_mcp.company_research_binding_sha256(
        sid=sid,
        workspace_id=workspace["id"],
        code=workspace["code"],
        subject_sha256=workspace_subject_sha256(workspace),
        root_sha256=workspace_root_sha256(root),
    )
    store.merge_meta_sub(sid, "bg", job_id, {
        "schema_version": "codex-company-research-receipt@1",
        "job_id": job_id,
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "workspace_id": workspace["id"],
        "code": workspace["code"],
        "name": workspace["name"],
        "asof": "2026-08-30",
        "binding_sha256": binding_sha,
        "workspace_subject_sha256": workspace_subject_sha256(workspace),
        "workspace_root_sha256": workspace_root_sha256(root),
        "status": "running",
        "checkpoint": "report_running",
        "resume_supported": True,
        "resume_count": 0,
        "report_runner_pid": 9876,
    })

    def prepare(**_kwargs):
        calls["prepare"] += 1
        return {"ok": True, "asset": {"id": prepared_id}}

    def durable(**kwargs):
        calls["report"].append(kwargs)
        return {
            "ok": True,
            "content": "resumed",
            "md_path": str(report_path),
            "durable_receipt": {
                "report_sha256": hashlib.sha256(
                    report_path.read_bytes()
                ).hexdigest(),
            },
        }

    def finalize(*_args, _effect_key, _effect_context):
        calls["finalize"] += 1
        asset = {
            "id": final_id,
            "workspace_id": workspace["id"],
            "source": "ww_workspace_company_research_run",
            "prepared_asset_id": prepared_id,
            "finalizer_effect_key": _effect_key,
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "run_id": "run_resumed",
            "name": "兆易创新 · 深度研报",
            "status": "complete",
            "attachments": {"documents": []},
        }
        return {
            "ok": True, "asset": asset,
            "artifact": company_mcp.artifact_for_finalized_company_asset(asset),
        }

    monkeypatch.setattr(
        workspace_tools, "workspace_company_research_prepare_impl", prepare,
    )
    monkeypatch.setattr(
        workspace_tools, "resolve_company_report_pack",
        lambda *_args: str(tmp_path / "prepared-pack.json"),
    )
    monkeypatch.setattr(console_api, "_call_durable_company_report", durable)
    monkeypatch.setattr(
        workspace_tools, "finalize_company_research_report", finalize,
    )
    monkeypatch.setattr(
        company_mcp, "find_finalized_company_asset", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(console_api, "_archive_research", lambda **_kwargs: True)
    monkeypatch.setattr(console_api, "_BG_PROGRESS_POLL", 0.01)

    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: _CompanyAgent(),
    ))
    with TestClient(app):
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update"
            and row.get("task_id") == job_id
            and row.get("status") == "done"
            for row in rows
        ))

    receipt = store.get_meta(sid)["bg"][job_id]
    assert receipt["status"] == "done" and receipt["ok"] is True
    assert receipt["resume_count"] == 1
    assert receipt["resumed_after_restart"] is True
    assert receipt["asset_id"] == final_id
    assert receipt["finalizer_replayed"] is False
    assert calls["prepare"] == 1
    assert len(calls["report"]) == 1
    assert calls["report"][0]["bg_id"] == job_id
    assert calls["report"][0]["existing_pid"] == 9876
    assert calls["finalize"] == 1


@pytest.mark.parametrize(
    ("starting_status", "refuse_ambiguous_replay"),
    [
        pytest.param("running", False, id="running"),
        pytest.param("finalizing", False, id="finalizing"),
        pytest.param("finalizing", True, id="finalizing-recrash-fail-closed"),
    ],
)
def test_typed_company_research_restart_revalidates_contract_and_resumes_same_job(
    tmp_path, monkeypatch, starting_status, refuse_ambiguous_replay,
):
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    from guanlan_v2.glmcp.scope import (
        workspace_root_sha256,
        workspace_subject_sha256,
    )
    import guanlan_v2.glmcp.company_research as company_mcp

    store, workspace, sid = _world(tmp_path)
    root = workspace_tools._root_for(store, workspace["id"])
    subject_sha = workspace_subject_sha256(workspace)
    root_sha = workspace_root_sha256(root)
    binding_sha = company_mcp.company_research_binding_sha256(
        sid=sid,
        workspace_id=workspace["id"],
        code=workspace["code"],
        subject_sha256=subject_sha,
        root_sha256=root_sha,
    )
    job_id = "bg_7777777777"
    confirmation_id = "cf_restart_typed_v2"
    confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan, reservation, approval = console_api._build_frozen_company_v2_plan(
        sid=sid,
        workspace=workspace,
        workspace_id=workspace["id"],
        code=workspace["code"],
        job_id=job_id,
        asof="2026-08-30",
        binding_sha256=binding_sha,
        workspace_subject_sha256=subject_sha,
        workspace_root_sha256=root_sha,
        confirmation_id=confirmation_id,
        confirmed_at=confirmed_at,
    )
    prepared_id = "wa_7777777777777777"
    final_id = "wa_8888888888888888"
    pack_path = tmp_path / "typed-resume-pack.json"
    pack_path.write_text('{"resume":true}', encoding="utf-8")
    report_path = tmp_path / "typed-resume-report.md"
    report_path.write_text("# 兆易创新\n\ntyped restart report\n", encoding="utf-8")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    effect_key = console_api._company_finalizer_effect_key(
        job_id=job_id,
        workspace_id=workspace["id"],
        prepared_asset_id=prepared_id,
        code=workspace["code"],
        report_sha256=report_sha,
        binding_sha256=binding_sha,
        pipeline_mode="typed_v2",
        typed_plan_digest=plan.plan_digest,
    )
    calls = {"prepare": 0, "report": [], "typed_finalize": []}

    store.merge_meta_sub(sid, "bg", job_id, {
        "schema_version": "codex-company-research-receipt@1",
        "job_id": job_id,
        "kind": "company_research_v1",
        "recipe_id": "company_research_v1",
        "workspace_id": workspace["id"],
        "code": workspace["code"],
        "name": workspace["name"],
        "asof": "2026-08-30",
        "binding_sha256": binding_sha,
        "workspace_subject_sha256": subject_sha,
        "workspace_root_sha256": root_sha,
        "pipeline_mode": "typed_v2",
        "confirmation_id": confirmation_id,
        "confirmation_at": confirmed_at,
        "typed_plan": plan.model_dump(mode="json"),
        "typed_plan_digest": plan.plan_digest,
        "typed_catalog_digest": plan.catalog_digest,
        "typed_schema_registry_digest": plan.schema_registry_digest,
        "typed_budget_reservation": reservation.model_dump(mode="json"),
        "typed_plan_approval": approval.model_dump(mode="json"),
        "budget_enforcement": "declared_plan_budget_with_wall_clock_timeout_only",
        "budget_runtime_enforced": False,
        "status": starting_status,
        "checkpoint": starting_status,
        "prepared_asset_id": prepared_id,
        "report_sha256": report_sha,
        "resume_supported": True,
        "resume_count": 0,
        "report_runner_pid": 7777,
        **({"finalizer_effect_key": effect_key}
           if starting_status == "finalizing" else {}),
    })

    def prepare(**_kwargs):
        calls["prepare"] += 1
        return {"ok": True, "asset": {"id": prepared_id}}

    def durable(**kwargs):
        calls["report"].append(kwargs)
        return {
            "ok": True,
            "content": "typed resume complete",
            "md_path": str(report_path),
            "durable_receipt": {
                "schema_version": "company-report-process-result@1",
                "report_sha256": report_sha,
            },
        }

    def typed_finalize(**kwargs):
        calls["typed_finalize"].append(kwargs)
        assert kwargs["typed_plan"]["plan_digest"] == plan.plan_digest
        if refuse_ambiguous_replay:
            raise workspace_tools.CompanyFinalizerReplayRefused(
                "finalizer effect 已开始但无可核验最终资产；拒绝不安全重放"
            )
        asset = {
            "id": final_id,
            "workspace_id": workspace["id"],
            "source": "ww_workspace_company_research_run",
            "prepared_asset_id": prepared_id,
            "finalizer_effect_key": kwargs["finalizer_effect_key"],
            "report_sha256": report_sha,
            "risk_contract_sha256": "5" * 64,
            "report_gate_sha256": "6" * 64,
            "run_id": "run_typed_resumed",
            "name": "兆易创新 · typed resumed",
            "status": "complete",
            "attachments": {"documents": []},
        }
        return {
            "ok": True,
            "asset": asset,
            "artifact": company_mcp.artifact_for_finalized_company_asset(asset),
            "typed_execution": {
                "pipeline_mode": "typed_v2",
                "plan_digest": plan.plan_digest,
                "asset_id": final_id,
                "finalizer_replayed": False,
            },
        }

    monkeypatch.setattr(
        workspace_tools, "workspace_company_research_prepare_impl", prepare,
    )
    monkeypatch.setattr(
        workspace_tools, "resolve_company_report_pack", lambda *_args: str(pack_path),
    )
    monkeypatch.setattr(console_api, "_call_durable_company_report", durable)
    monkeypatch.setattr(
        console_api, "_finalize_company_research_typed_v2", typed_finalize,
    )
    monkeypatch.setattr(
        company_mcp, "find_finalized_company_asset", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(console_api, "_archive_research", lambda **_kwargs: True)
    monkeypatch.setattr(console_api, "_BG_PROGRESS_POLL", 0.01)

    app = FastAPI()
    app.include_router(build_console_router(
        store=store, agent_factory=lambda _sid: _CompanyAgent(),
    ))
    if starting_status == "finalizing":
        pending = store.get_meta(sid)["bg"][job_id]
        assert pending["status"] == "finalizing"
        assert pending["checkpoint"] == "finalizer_resume_pending"
    with TestClient(app):
        _wait(store, sid, lambda rows: any(
            row["type"] == "task_update"
            and row.get("task_id") == job_id
            and row.get("status") in {"done", "error"}
            for row in rows
        ))

    receipt = store.get_meta(sid)["bg"][job_id]
    if refuse_ambiguous_replay:
        assert receipt["status"] == "error" and receipt["ok"] is False
        assert receipt["finalizer_invoked"] is True
        assert receipt["finalizer_replayed"] is None
        assert receipt["finalizer_replay_refused"] is True
        assert receipt["finalizer_effect_state"] == "ambiguous_in_progress"
        assert "拒绝不安全重放" in receipt["note"]
        assert calls["prepare"] == 0
        assert len(calls["report"]) == 1
        assert calls["report"][0]["allow_spawn"] is False
        assert len(calls["typed_finalize"]) == 1
        return
    assert receipt["status"] == "done" and receipt["ok"] is True, receipt.get("note")
    assert receipt["resume_count"] == 1
    assert receipt["resumed_after_restart"] is True
    assert receipt["pipeline_mode"] == "typed_v2"
    assert receipt["typed_execution"]["plan_digest"] == plan.plan_digest
    assert receipt["asset_id"] == final_id
    assert receipt["finalizer_replayed"] is False
    assert calls["prepare"] == (0 if starting_status == "finalizing" else 1)
    assert len(calls["report"]) == 1
    assert calls["report"][0]["bg_id"] == job_id
    assert calls["report"][0]["existing_pid"] == 7777
    assert calls["report"][0]["allow_spawn"] is (
        starting_status != "finalizing"
    )
    assert len(calls["typed_finalize"]) == 1


def test_typed_company_receipt_refuses_budget_or_approval_drift(tmp_path):
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    from guanlan_v2.glmcp.scope import (
        workspace_root_sha256,
        workspace_subject_sha256,
    )
    import guanlan_v2.glmcp.company_research as company_mcp

    store, workspace, sid = _world(tmp_path)
    root = workspace_tools._root_for(store, workspace["id"])
    subject_sha = workspace_subject_sha256(workspace)
    root_sha = workspace_root_sha256(root)
    binding_sha = company_mcp.company_research_binding_sha256(
        sid=sid,
        workspace_id=workspace["id"],
        code=workspace["code"],
        subject_sha256=subject_sha,
        root_sha256=root_sha,
    )
    job_id = "bg_9999999999"
    confirmation_id = "cf_contract_test"
    confirmed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    plan, reservation, approval = console_api._build_frozen_company_v2_plan(
        sid=sid,
        workspace=workspace,
        workspace_id=workspace["id"],
        code=workspace["code"],
        job_id=job_id,
        asof="2026-08-30",
        binding_sha256=binding_sha,
        workspace_subject_sha256=subject_sha,
        workspace_root_sha256=root_sha,
        confirmation_id=confirmation_id,
        confirmed_at=confirmed_at,
    )
    receipt = {
        "pipeline_mode": "typed_v2",
        "confirmation_id": confirmation_id,
        "confirmation_at": confirmed_at,
        "typed_plan": plan.model_dump(mode="json"),
        "typed_plan_digest": plan.plan_digest,
        "typed_catalog_digest": plan.catalog_digest,
        "typed_schema_registry_digest": plan.schema_registry_digest,
        "typed_budget_reservation": reservation.model_dump(mode="json"),
        "typed_plan_approval": approval.model_dump(mode="json"),
        "budget_enforcement": "declared_plan_budget_with_wall_clock_timeout_only",
        "budget_runtime_enforced": False,
    }
    loaded = console_api._validate_company_v2_receipt_contract(
        receipt,
        job_id=job_id,
        workspace_id=workspace["id"],
        code=workspace["code"],
        asof="2026-08-30",
        binding_sha256=binding_sha,
    )
    assert loaded.plan_digest == plan.plan_digest

    bad_budget = {**receipt, "typed_budget_reservation": {
        **receipt["typed_budget_reservation"], "reserved_tokens": 1,
    }}
    with pytest.raises(RuntimeError, match="预算"):
        console_api._validate_company_v2_receipt_contract(
            bad_budget,
            job_id=job_id,
            workspace_id=workspace["id"],
            code=workspace["code"],
            asof="2026-08-30",
            binding_sha256=binding_sha,
        )

    false_hard_budget_claim = {**receipt, "budget_runtime_enforced": True}
    with pytest.raises(RuntimeError, match="预算"):
        console_api._validate_company_v2_receipt_contract(
            false_hard_budget_claim,
            job_id=job_id,
            workspace_id=workspace["id"],
            code=workspace["code"],
            asof="2026-08-30",
            binding_sha256=binding_sha,
        )

    bad_approval = {**receipt, "confirmation_id": "cf_tampered"}
    with pytest.raises(RuntimeError, match="确认"):
        console_api._validate_company_v2_receipt_contract(
            bad_approval,
            job_id=job_id,
            workspace_id=workspace["id"],
            code=workspace["code"],
            asof="2026-08-30",
            binding_sha256=binding_sha,
        )


def test_typed_finalizer_concurrent_reentry_adopts_without_second_effect(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as console_api
    from guanlan_v2.console import workspace_tools
    from guanlan_v2.glmcp.scope import (
        workspace_root_sha256,
        workspace_subject_sha256,
    )
    import guanlan_v2.glmcp.company_research as company_mcp

    store, workspace, sid = _world(tmp_path)
    root = workspace_tools._root_for(store, workspace["id"])
    subject_sha = workspace_subject_sha256(workspace)
    root_sha = workspace_root_sha256(root)
    binding_sha = company_mcp.company_research_binding_sha256(
        sid=sid,
        workspace_id=workspace["id"],
        code=workspace["code"],
        subject_sha256=subject_sha,
        root_sha256=root_sha,
    )
    job_id = "bg_1212121212"
    plan, _reservation, _approval = console_api._build_frozen_company_v2_plan(
        sid=sid,
        workspace=workspace,
        workspace_id=workspace["id"],
        code=workspace["code"],
        job_id=job_id,
        asof="2026-08-30",
        binding_sha256=binding_sha,
        workspace_subject_sha256=subject_sha,
        workspace_root_sha256=root_sha,
        confirmation_id="cf_finalizer_reentry",
        confirmed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    prepared_id = "wa_1212121212121212"
    report_path = tmp_path / "finalizer-reentry.md"
    report_path.write_text("# 兆易创新\n\nonce\n", encoding="utf-8")
    pack_path = tmp_path / "finalizer-reentry-pack.json"
    pack_path.write_text("{}", encoding="utf-8")
    report_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
    committed = []
    calls = []
    finalizer_entered = threading.Event()
    release_finalizer = threading.Event()

    def find(*_args, **_kwargs):
        return committed[0] if committed else None

    def finalize(**_kwargs):
        calls.append(True)
        finalizer_entered.set()
        assert release_finalizer.wait(timeout=5)
        asset = {
            "id": "wa_3434343434343434",
            "workspace_id": workspace["id"],
            "source": "ww_workspace_company_research_run",
            "prepared_asset_id": prepared_id,
            "finalizer_effect_key": _kwargs["finalizer_effect_key"],
            "report_sha256": report_sha,
            "risk_contract_sha256": "5" * 64,
            "report_gate_sha256": "6" * 64,
            "run_id": "run_finalizer_once",
            "name": "兆易创新 · once",
            "status": "complete",
            "attachments": {"documents": []},
        }
        committed.append(asset)
        return {
            "ok": True,
            "asset": asset,
            "artifact": company_mcp.artifact_for_finalized_company_asset(asset),
            "typed_execution": {
                "pipeline_mode": "typed_v2",
                "plan_digest": plan.plan_digest,
                "asset_id": asset["id"],
                "finalizer_replayed": False,
            },
        }

    monkeypatch.setattr(company_mcp, "find_finalized_company_asset", find)
    monkeypatch.setattr(
        console_api, "_finalize_company_research_typed_v2", finalize,
    )
    kwargs = {
        "store": store,
        "sid": sid,
        "job_id": job_id,
        "workspace_id": workspace["id"],
        "prepared_asset_id": prepared_id,
        "code": workspace["code"],
        "asof": "2026-08-30",
        "binding_sha256": binding_sha,
        "prepared_pack_path": str(pack_path),
        "report_result": {"durable_receipt": {"schema_version": "2"}},
        "md_path": str(report_path),
        "pipeline_mode": "typed_v2",
        "typed_plan": plan.model_dump(mode="json"),
    }
    results = []
    errors = []

    def invoke():
        try:
            results.append(console_api._finalize_company_research_once(**kwargs))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first_thread = threading.Thread(target=invoke)
    second_thread = threading.Thread(target=invoke)
    first_thread.start()
    assert finalizer_entered.wait(timeout=5)
    second_thread.start()
    time.sleep(0.05)
    release_finalizer.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive() and not second_thread.is_alive()
    assert errors == []
    assert len(results) == 2
    assert results[0]["asset"]["id"] == results[1]["asset"]["id"]
    assert sum(bool(row["_finalizer_invoked"]) for row in results) == 1
    assert sum(bool(row["_adopted_existing_asset"]) for row in results) == 1
    assert calls == [True]


def test_durable_report_rediscovers_helper_when_pid_receipt_was_not_persisted(
    tmp_path, monkeypatch,
):
    import guanlan_v2.console.api as console_api

    pack = tmp_path / "pack.json"
    pack.write_text("{}", encoding="utf-8")
    reads = []
    discovered = []
    started = []

    def read_result(**kwargs):
        reads.append(kwargs)
        if len(reads) == 1:
            return None
        return {
            "ok": True,
            "content": "rejoined",
            "md_path": str(tmp_path / "already-produced.md"),
        }

    def find(**kwargs):
        discovered.append(kwargs)
        return 24680

    monkeypatch.setattr(console_api, "_company_report_result", read_result)
    monkeypatch.setattr(
        console_api, "_company_report_runner_alive",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(console_api, "_find_company_report_runner", find)
    monkeypatch.setattr(
        subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("must rejoin instead of respawn"),
    )

    result = console_api._call_durable_company_report(
        bg_id="bg_7878787878",
        code="SH603986",
        asof="2026-08-30",
        evidence_pack_path=str(pack),
        binding_sha256="b" * 64,
        job_root=tmp_path / "jobs",
        existing_pid=None,
        on_runner_started=lambda *args: started.append(args),
    )

    assert result["ok"] is True
    assert len(discovered) == 1
    assert started and started[0][0] == 24680
    assert reads[0]["report_out_dir"] == (
        tmp_path / "jobs" / "bg_7878787878" / "report_out"
    )


def test_durable_report_real_windows_lock_double_call_spawns_once(
    tmp_path, monkeypatch,
):
    """Both callers miss before locking; only the lock winner may spawn."""
    import guanlan_v2.console.api as console_api

    pack = tmp_path / "pack.json"
    pack.write_text("{}", encoding="utf-8")
    report = tmp_path / "finished.md"
    report.write_text("# once\n", encoding="utf-8")
    both_missed_before_lock = threading.Barrier(2)
    result_ready = threading.Event()
    find_guard = threading.Lock()
    popen_guard = threading.Lock()
    find_calls = 0
    popen_calls = 0

    def read_result(**_kwargs):
        if not result_ready.is_set():
            return None
        return {"ok": True, "content": "one helper", "md_path": str(report)}

    def find(**_kwargs):
        nonlocal find_calls
        with find_guard:
            find_calls += 1
            call_no = find_calls
        if call_no <= 2:
            both_missed_before_lock.wait(timeout=5)
        return None

    class _Proc:
        pid = 13579

    def popen(*_args, **_kwargs):
        nonlocal popen_calls
        with popen_guard:
            popen_calls += 1
        result_ready.set()
        return _Proc()

    monkeypatch.setattr(console_api, "_company_report_result", read_result)
    monkeypatch.setattr(
        console_api, "_company_report_runner_alive",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(console_api, "_find_company_report_runner", find)
    monkeypatch.setattr(subprocess, "Popen", popen)

    kwargs = {
        "bg_id": "bg_8989898989",
        "code": "SH603986",
        "asof": "2026-08-30",
        "evidence_pack_path": str(pack),
        "binding_sha256": "b" * 64,
        "job_root": tmp_path / "jobs",
        "existing_pid": None,
    }
    results = []
    errors = []

    def invoke():
        try:
            results.append(console_api._call_durable_company_report(**kwargs))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    callers = [threading.Thread(target=invoke) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5)

    assert all(not caller.is_alive() for caller in callers)
    assert errors == []
    assert len(results) == 2 and all(row["ok"] for row in results)
    assert popen_calls == 1
