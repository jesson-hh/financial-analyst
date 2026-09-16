from __future__ import annotations

import json
from pathlib import Path

import pytest

from guanlan_v2.research.company_recipe import (
    CompanyResearchBindingDrift,
    CompanyResearchBindingError,
    CompanyResearchRecipeAdapter,
    CompanyResearchRecipeError,
    DEFAULT_COMPANY_RESEARCH_RECIPE,
    RECIPE_STAGE_ORDER,
    load_company_research_recipe,
)


def _binding(*, code: str = "SH603986", workspace_id: str = "rw_abcdef123456") -> dict:
    return {
        "binding_source": "server_current_session",
        "session_id": "cs_123456789abc",
        "workspace_id": workspace_id,
        "workspace_kind": "stock",
        "active": True,
        "subject": {"kind": "stock", "code": code, "name": "兆易创新"},
    }


def _adapter(binding_state: dict, *, recipe_path: Path = DEFAULT_COMPANY_RESEARCH_RECIPE):
    return CompanyResearchRecipeAdapter(
        loaded_recipe=load_company_research_recipe(recipe_path),
        resolve_current_binding=lambda: dict(binding_state),
    )


def _copy_recipe(tmp_path: Path) -> tuple[Path, dict]:
    payload = json.loads(DEFAULT_COMPANY_RESEARCH_RECIPE.read_text(encoding="utf-8"))
    target = tmp_path / "company_research_v1.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target, payload


def test_default_recipe_is_strict_versioned_and_kept_out_of_v2_presets() -> None:
    loaded = load_company_research_recipe()

    assert loaded.recipe.recipe_id == "company_research_v1"
    assert loaded.recipe.revision == 2
    assert tuple(stage.id for stage in loaded.recipe.stages) == RECIPE_STAGE_ORDER
    assert loaded.recipe.entrypoints.console.status == "connected"
    assert loaded.recipe.entrypoints.glmcp.status == "unavailable"
    assert loaded.recipe.entrypoints.typed_orchestration_v2.status == "connected"
    assert "config/research/recipes" in loaded.path.as_posix()
    assert "config/orchestration/presets/v2" not in loaded.path.as_posix()
    assert len(loaded.file_sha256) == len(loaded.semantic_sha256) == 64


def test_public_launch_request_cannot_select_workspace_or_subject() -> None:
    adapter = _adapter(_binding())

    with pytest.raises(CompanyResearchRecipeError, match="invalid launch request"):
        adapter.build_launch_contract({
            "as_of": "2026-09-03",
            "workspace_id": "rw_000000000000",
        })
    with pytest.raises(CompanyResearchRecipeError, match="invalid launch request"):
        adapter.build_launch_contract({"code": "SZ300750"})


def test_console_preview_is_bound_and_pending_but_never_dispatches() -> None:
    calls = 0

    def resolver():
        nonlocal calls
        calls += 1
        return _binding()

    adapter = CompanyResearchRecipeAdapter(
        loaded_recipe=load_company_research_recipe(),
        resolve_current_binding=resolver,
    )
    contract = adapter.build_launch_contract({"as_of": "2026-09-03"})

    assert calls == 1
    assert contract.status == "awaiting_confirmation"
    assert contract.approval_required is True
    assert contract.approval_state == "pending"
    assert contract.auto_approved is False
    assert contract.resume_supported is True
    assert contract.restart_behavior == "resume_same_job"
    assert contract.binding.workspace_id == "rw_abcdef123456"
    assert contract.binding.subject.code == "SH603986"
    assert [stage.state for stage in contract.stages] == [
        "ready",
        "awaiting_confirmation",
        "blocked_by_dependency",
        "blocked_by_dependency",
        "blocked_by_dependency",
    ]
    assert not hasattr(adapter, "approve")
    assert not hasattr(adapter, "dispatch")

    rebound = adapter.revalidate_binding(contract)
    assert calls == 2
    assert rebound == contract.binding


@pytest.mark.parametrize("channel", ["glmcp"])
def test_unwired_entrypoints_are_honestly_unavailable(channel: str) -> None:
    contract = _adapter(_binding()).build_launch_contract(channel=channel)  # type: ignore[arg-type]

    assert contract.status == "unavailable"
    assert contract.approval_state == "not_applicable"
    assert contract.unavailable_reason_codes
    assert all(stage.state == "unavailable" for stage in contract.stages)


def test_typed_v2_entrypoint_is_connected_but_still_needs_confirmation() -> None:
    contract = _adapter(_binding()).build_launch_contract(
        channel="typed_orchestration_v2",
    )

    assert contract.status == "awaiting_confirmation"
    assert contract.approval_state == "pending"
    assert contract.approval_required is True
    assert contract.resume_supported is True
    assert [stage.state for stage in contract.stages] == [
        "ready",
        "awaiting_confirmation",
        "blocked_by_dependency",
        "blocked_by_dependency",
        "blocked_by_dependency",
    ]


def test_binding_drift_fails_closed_before_any_future_dispatch() -> None:
    binding_state = _binding()
    adapter = _adapter(binding_state)
    contract = adapter.build_launch_contract()
    binding_state["workspace_id"] = "rw_111111111111"
    binding_state["subject"] = {
        "kind": "stock", "code": "SZ300750", "name": "宁德时代",
    }

    with pytest.raises(CompanyResearchBindingDrift, match="differs"):
        adapter.revalidate_binding(contract)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"workspace_id": "rw_general"}, "non-general"),
        ({"workspace_kind": "etf"}, "workspace_kind"),
        ({"active": False}, "active"),
        ({"subject": {"kind": "stock", "code": "AAPL", "name": "Apple"}}, "A-share"),
    ],
)
def test_invalid_server_binding_is_refused(update: dict, message: str) -> None:
    raw = _binding()
    raw.update(update)

    with pytest.raises(CompanyResearchBindingError, match=message):
        _adapter(raw).build_launch_contract()


def test_recipe_byte_change_invalidates_an_existing_preview(tmp_path: Path) -> None:
    target, _payload = _copy_recipe(tmp_path)
    adapter = _adapter(_binding(), recipe_path=target)
    contract = adapter.build_launch_contract()
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(CompanyResearchRecipeError, match="changed after launch preview"):
        adapter.revalidate_binding(contract)


def test_launch_contract_tampering_is_detected() -> None:
    adapter = _adapter(_binding())
    contract = adapter.build_launch_contract()
    tampered = contract.model_copy(update={"contract_sha256": "0" * 64})

    with pytest.raises(CompanyResearchRecipeError, match="digest mismatch"):
        adapter.revalidate_binding(tampered)


@pytest.mark.parametrize("mutation", ["stage_order", "auto_approve", "resume", "fake_mcp"])
def test_recipe_semantics_are_immutable_within_revision_two(
    tmp_path: Path, mutation: str,
) -> None:
    target, payload = _copy_recipe(tmp_path)
    if mutation == "stage_order":
        payload["stages"][0], payload["stages"][1] = (
            payload["stages"][1], payload["stages"][0],
        )
    elif mutation == "auto_approve":
        payload["approval"]["auto_approve"] = True
    elif mutation == "resume":
        payload["restart"]["resume_across_restart"] = False
    else:
        payload["entrypoints"]["glmcp"]["status"] = "connected"
        payload["entrypoints"]["glmcp"]["missing_capabilities"] = []
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CompanyResearchRecipeError, match="contract failed"):
        load_company_research_recipe(target)


def test_duplicate_recipe_keys_are_rejected(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.json"
    target.write_text(
        '{"schema_version":"company-research-recipe@2",'
        '"schema_version":"company-research-recipe@2"}',
        encoding="utf-8",
    )

    with pytest.raises(CompanyResearchRecipeError, match="duplicate JSON key"):
        load_company_research_recipe(target)
