"""Versioned, fail-closed control contract for ``company_research_v1``.

The Console path owns the side effects of company research: it resolves the
current session/workspace subject, freezes an approved and budgeted typed-v2
plan, prepares a private evidence bundle, launches the durable report process,
and finalises a gated workspace asset.  Public ``/gl-mcp`` deliberately does
not expose that scoped workflow.

This module deliberately does *not* create another runner.  It loads one strict
recipe and produces a read-only launch contract from a server-supplied binding
resolver.  A caller cannot provide a workspace id or subject, and this adapter
contains no approval or dispatch method.  The Console dispatcher revalidates
the same binding and passes through the human-confirmation surface.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from pydantic import field_validator, model_validator


RECIPE_SCHEMA_VERSION = "company-research-recipe@2"
RECIPE_ID = "company_research_v1"
LAUNCH_REQUEST_SCHEMA_VERSION = "company-research-launch-request@1"
LAUNCH_CONTRACT_SCHEMA_VERSION = "company-research-launch-contract@2"
TRUSTED_BINDING_SCHEMA_VERSION = "company-research-trusted-binding@1"

RECIPE_STAGE_ORDER: tuple[str, ...] = (
    "prepare",
    "research_report",
    "risk_gate",
    "report_gate",
    "workspace_asset_finalizer",
)

DEFAULT_COMPANY_RESEARCH_RECIPE = (
    Path(__file__).resolve().parents[2]
    / "config"
    / "research"
    / "recipes"
    / "company_research_v1.json"
)

_MAX_RECIPE_BYTES = 64 * 1024
_SID_RE = re.compile(r"^cs_[0-9a-f]{12}$")
_WORKSPACE_ID_RE = re.compile(r"^rw_[0-9a-f]{12}$")
_ASSET_ID_RE = re.compile(r"^wa_[0-9a-f]{16}$")
_A_SHARE_CODE_RE = re.compile(r"^(?:SH|SZ|BJ)[0-9]{6}$")


class CompanyResearchRecipeError(ValueError):
    """The recipe or launch contract is malformed or no longer trustworthy."""


class CompanyResearchBindingError(CompanyResearchRecipeError):
    """The server resolver did not return one active stock-workspace subject."""


class CompanyResearchBindingDrift(CompanyResearchBindingError):
    """The trusted workspace/subject changed after the launch preview."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecipeBindingPolicy(_StrictModel):
    workspace: Literal["server_current_session"]
    subject: Literal["server_workspace_subject"]
    workspace_kind: Literal["stock"]
    subject_kind: Literal["a_share"]
    client_workspace_id: Literal["forbidden"]
    client_subject: Literal["forbidden"]


class RecipeApprovalPolicy(_StrictModel):
    report_dispatch: Literal["explicit_confirmation"]
    auto_approve: StrictBool
    prepare_tool_confirmation: Literal["none_private_scoped"]
    downstream_effects: Literal["covered_by_confirmed_report_dispatch"]

    @model_validator(mode="after")
    def _never_auto_approve(self) -> "RecipeApprovalPolicy":
        if self.auto_approve:
            raise ValueError("company research must never auto-approve report dispatch")
        return self


class RecipeRestartPolicy(_StrictModel):
    resume_across_restart: Literal[True]
    stale_running: Literal["resume_same_job"]
    relaunch: Literal["explicit_new_request"]


class CompanyResearchRecipeStage(_StrictModel):
    id: str
    operation: str
    execution_owner: str
    depends_on: tuple[str, ...] = ()
    side_effect: Literal["none", "workspace_write", "background_process"]
    approval_scope: Literal[
        "none_private_scoped",
        "explicit_report_confirmation",
        "confirmed_report_dispatch",
    ]


class RecipeEntrypoint(_StrictModel):
    status: Literal["connected", "unavailable"]
    route: str
    connected_stages: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()


class RecipeEntrypoints(_StrictModel):
    console: RecipeEntrypoint
    glmcp: RecipeEntrypoint
    typed_orchestration_v2: RecipeEntrypoint


_EXPECTED_STAGES: tuple[dict[str, Any], ...] = (
    {
        "id": "prepare",
        "operation": "ww_workspace_company_research_prepare",
        "execution_owner": "console.workspace_tools",
        "depends_on": (),
        "side_effect": "workspace_write",
        "approval_scope": "none_private_scoped",
    },
    {
        "id": "research_report",
        "operation": "ww_report_run",
        "execution_owner": "console.report_background",
        "depends_on": ("prepare",),
        "side_effect": "background_process",
        "approval_scope": "explicit_report_confirmation",
    },
    {
        "id": "risk_gate",
        "operation": "build_company_risk_contract",
        "execution_owner": "console.workspace_report_finalizer",
        "depends_on": ("research_report",),
        "side_effect": "none",
        "approval_scope": "confirmed_report_dispatch",
    },
    {
        "id": "report_gate",
        "operation": "validate_company_research_report_strict",
        "execution_owner": "console.workspace_report_finalizer",
        "depends_on": ("risk_gate",),
        "side_effect": "none",
        "approval_scope": "confirmed_report_dispatch",
    },
    {
        "id": "workspace_asset_finalizer",
        "operation": "finalize_company_research_report",
        "execution_owner": "console.workspace_report_finalizer",
        "depends_on": ("report_gate",),
        "side_effect": "workspace_write",
        "approval_scope": "confirmed_report_dispatch",
    },
)

_EXPECTED_ENTRYPOINTS: Mapping[str, dict[str, Any]] = {
    "console": {
        "status": "connected",
        "route": "console_confirmed_typed_v2_background",
        "connected_stages": RECIPE_STAGE_ORDER,
        "missing_capabilities": (),
    },
    "glmcp": {
        "status": "unavailable",
        "route": "glmcp_detached_report",
        "connected_stages": (),
        "missing_capabilities": (
            "trusted_workspace_subject_binding",
            "company_prepare_bundle",
            "strict_workspace_report_finalizer",
        ),
    },
    "typed_orchestration_v2": {
        "status": "connected",
        "route": "console_company_typed_v2_frozen_plan",
        "connected_stages": RECIPE_STAGE_ORDER,
        "missing_capabilities": (),
    },
}


class CompanyResearchRecipe(_StrictModel):
    schema_version: Literal[RECIPE_SCHEMA_VERSION]
    recipe_id: Literal[RECIPE_ID]
    revision: Literal[2]
    integration_audit_date: Literal["2026-09-04"]
    binding: RecipeBindingPolicy
    approval: RecipeApprovalPolicy
    restart: RecipeRestartPolicy
    stages: tuple[CompanyResearchRecipeStage, ...]
    entrypoints: RecipeEntrypoints

    @model_validator(mode="after")
    def _fixed_v1_contract(self) -> "CompanyResearchRecipe":
        observed = tuple(stage.model_dump(mode="python") for stage in self.stages)
        if observed != _EXPECTED_STAGES:
            raise ValueError(
                "company_research_v1 stage identities, order, dependencies and "
                "effect ownership are immutable; publish a new recipe revision"
            )
        for name, expected in _EXPECTED_ENTRYPOINTS.items():
            actual = getattr(self.entrypoints, name).model_dump(mode="python")
            if actual != expected:
                raise ValueError(
                    f"company_research_v1 entrypoint {name!r} no longer matches "
                    "the audited integration state; publish a new recipe revision"
                )
        return self


@dataclass(frozen=True)
class LoadedCompanyResearchRecipe:
    """Validated recipe plus exact-file and semantic digests."""

    recipe: CompanyResearchRecipe
    path: Path
    file_sha256: str
    semantic_sha256: str


class CompanyResearchSubject(_StrictModel):
    kind: Literal["stock"]
    code: str
    name: str = Field(default="", max_length=160)

    @field_validator("code")
    @classmethod
    def _valid_code(cls, value: str) -> str:
        if not isinstance(value, str) or _A_SHARE_CODE_RE.fullmatch(value) is None:
            raise ValueError("trusted company subject must be an SH/SZ/BJ A-share code")
        return value


class TrustedCompanyResearchBinding(_StrictModel):
    """A snapshot minted by a no-argument server context resolver."""

    schema_version: Literal[TRUSTED_BINDING_SCHEMA_VERSION] = TRUSTED_BINDING_SCHEMA_VERSION
    binding_source: Literal["server_current_session"]
    session_id: str
    workspace_id: str
    workspace_kind: Literal["stock"]
    active: Literal[True]
    subject: CompanyResearchSubject

    @field_validator("session_id")
    @classmethod
    def _valid_session_id(cls, value: str) -> str:
        if not isinstance(value, str) or _SID_RE.fullmatch(value) is None:
            raise ValueError("trusted binding has an invalid Console session id")
        return value

    @field_validator("workspace_id")
    @classmethod
    def _valid_workspace_id(cls, value: str) -> str:
        if not isinstance(value, str) or _WORKSPACE_ID_RE.fullmatch(value) is None:
            raise ValueError("trusted binding must name a non-general research workspace")
        return value


class CompanyResearchLaunchRequest(_StrictModel):
    """Public request surface; workspace and subject fields do not exist here."""

    schema_version: Literal[LAUNCH_REQUEST_SCHEMA_VERSION] = LAUNCH_REQUEST_SCHEMA_VERSION
    as_of: str | None = None

    @field_validator("as_of")
    @classmethod
    def _valid_as_of(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or value != value.strip():
            raise ValueError("as_of must be an ISO date string")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of must be a real ISO date") from exc
        if parsed.isoformat() != value:
            raise ValueError("as_of must use YYYY-MM-DD")
        return value


class CompanyResearchLaunchStage(_StrictModel):
    id: str
    state: Literal[
        "ready",
        "awaiting_confirmation",
        "blocked_by_dependency",
        "unavailable",
    ]
    operation: str
    execution_owner: str


class CompanyResearchLaunchContract(_StrictModel):
    """Read-only preview.  It is neither an approval nor a dispatch token."""

    schema_version: Literal[LAUNCH_CONTRACT_SCHEMA_VERSION]
    recipe_id: Literal[RECIPE_ID]
    recipe_revision: Literal[2]
    recipe_file_sha256: str
    recipe_semantic_sha256: str
    request_sha256: str
    binding_sha256: str
    contract_sha256: str
    channel: Literal["console", "glmcp", "typed_orchestration_v2"]
    status: Literal["awaiting_confirmation", "unavailable"]
    approval_required: Literal[True]
    approval_state: Literal["pending", "not_applicable"]
    auto_approved: Literal[False]
    resume_supported: Literal[True]
    restart_behavior: Literal["resume_same_job"]
    binding: TrustedCompanyResearchBinding
    request: CompanyResearchLaunchRequest
    stages: tuple[CompanyResearchLaunchStage, ...]
    unavailable_reason_codes: tuple[str, ...] = ()


BindingResolver = Callable[[], TrustedCompanyResearchBinding | Mapping[str, Any]]


class CompanyResearchRecipeAdapter:
    """Build and revalidate launch previews without approving or dispatching.

    The resolver intentionally takes no arguments.  Consequently a request body
    can neither select another workspace nor substitute another issuer.
    """

    def __init__(
        self,
        *,
        loaded_recipe: LoadedCompanyResearchRecipe,
        resolve_current_binding: BindingResolver,
    ) -> None:
        if not callable(resolve_current_binding):
            raise TypeError("resolve_current_binding must be callable")
        self._loaded = loaded_recipe
        self._resolve_current_binding = resolve_current_binding

    @property
    def loaded_recipe(self) -> LoadedCompanyResearchRecipe:
        return self._loaded

    def build_launch_contract(
        self,
        request: CompanyResearchLaunchRequest | Mapping[str, Any] | None = None,
        *,
        channel: Literal["console", "glmcp", "typed_orchestration_v2"] = "console",
    ) -> CompanyResearchLaunchContract:
        if channel not in {"console", "glmcp", "typed_orchestration_v2"}:
            raise CompanyResearchRecipeError(
                f"unknown company-research channel: {channel!r}"
            )
        try:
            request_record = (
                request
                if isinstance(request, CompanyResearchLaunchRequest)
                else CompanyResearchLaunchRequest.model_validate(request or {})
            )
        except ValidationError as exc:
            raise CompanyResearchRecipeError(f"invalid launch request: {exc}") from exc

        binding = self._resolve_binding()
        entrypoint = getattr(self._loaded.recipe.entrypoints, channel)

        connected = entrypoint.status == "connected"
        if connected:
            states = {
                "prepare": "ready",
                "research_report": "awaiting_confirmation",
            }
            default_state = "blocked_by_dependency"
            status = "awaiting_confirmation"
            approval_state = "pending"
            reasons: tuple[str, ...] = ()
        else:
            states = {}
            default_state = "unavailable"
            status = "unavailable"
            approval_state = "not_applicable"
            reasons = entrypoint.missing_capabilities

        stages = tuple(
            CompanyResearchLaunchStage(
                id=stage.id,
                state=states.get(stage.id, default_state),
                operation=stage.operation,
                execution_owner=stage.execution_owner,
            )
            for stage in self._loaded.recipe.stages
        )
        payload: dict[str, Any] = {
            "schema_version": LAUNCH_CONTRACT_SCHEMA_VERSION,
            "recipe_id": RECIPE_ID,
            "recipe_revision": self._loaded.recipe.revision,
            "recipe_file_sha256": self._loaded.file_sha256,
            "recipe_semantic_sha256": self._loaded.semantic_sha256,
            "request_sha256": _semantic_sha256(request_record),
            "binding_sha256": _semantic_sha256(binding),
            "channel": channel,
            "status": status,
            "approval_required": True,
            "approval_state": approval_state,
            "auto_approved": False,
            "resume_supported": True,
            "restart_behavior": "resume_same_job",
            "binding": binding,
            "request": request_record,
            "stages": stages,
            "unavailable_reason_codes": reasons,
        }
        payload["contract_sha256"] = _canonical_sha256(payload)
        return CompanyResearchLaunchContract.model_validate(payload)

    def revalidate_binding(
        self, contract: CompanyResearchLaunchContract,
    ) -> TrustedCompanyResearchBinding:
        """Fail closed if recipe bytes, contract content or trusted binding drifted."""
        if not isinstance(contract, CompanyResearchLaunchContract):
            raise CompanyResearchRecipeError("a validated launch contract is required")

        current_recipe = load_company_research_recipe(self._loaded.path)
        if (
            contract.recipe_file_sha256 != self._loaded.file_sha256
            or contract.recipe_semantic_sha256 != self._loaded.semantic_sha256
            or current_recipe.file_sha256 != self._loaded.file_sha256
            or current_recipe.semantic_sha256 != self._loaded.semantic_sha256
        ):
            raise CompanyResearchRecipeError(
                "company research recipe changed after launch preview; create a new preview"
            )

        contract_payload = contract.model_dump(
            mode="json", exclude={"contract_sha256"},
        )
        if _canonical_sha256(contract_payload) != contract.contract_sha256:
            raise CompanyResearchRecipeError("company research launch contract digest mismatch")
        if _semantic_sha256(contract.binding) != contract.binding_sha256:
            raise CompanyResearchRecipeError("company research binding digest mismatch")

        current = self._resolve_binding()
        if _semantic_sha256(current) != contract.binding_sha256:
            raise CompanyResearchBindingDrift(
                "current session/workspace/subject differs from the launch preview"
            )
        return current

    def _resolve_binding(self) -> TrustedCompanyResearchBinding:
        try:
            raw = self._resolve_current_binding()
            return (
                raw
                if isinstance(raw, TrustedCompanyResearchBinding)
                else TrustedCompanyResearchBinding.model_validate(raw)
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise CompanyResearchBindingError(
                f"trusted current-workspace resolver refused the binding: {exc}"
            ) from exc


def load_company_research_recipe(
    path: str | Path = DEFAULT_COMPANY_RESEARCH_RECIPE,
) -> LoadedCompanyResearchRecipe:
    """Load the exact v1 JSON recipe with duplicate-key and size rejection."""
    source = Path(path)
    try:
        if not source.is_file():
            raise CompanyResearchRecipeError(f"company research recipe is absent: {source}")
        raw = source.read_bytes()
    except OSError as exc:
        raise CompanyResearchRecipeError(f"company research recipe is unreadable: {exc}") from exc
    if not raw or len(raw) > _MAX_RECIPE_BYTES or b"\x00" in raw:
        raise CompanyResearchRecipeError("company research recipe is empty, oversized or binary")
    try:
        text = raw.decode("utf-8")
        payload = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompanyResearchRecipeError(f"company research recipe is not strict UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CompanyResearchRecipeError("company research recipe root must be an object")
    try:
        recipe = CompanyResearchRecipe.model_validate(payload)
    except ValidationError as exc:
        raise CompanyResearchRecipeError(f"company research recipe contract failed: {exc}") from exc
    return LoadedCompanyResearchRecipe(
        recipe=recipe,
        path=source.resolve(),
        file_sha256=hashlib.sha256(raw).hexdigest(),
        semantic_sha256=_semantic_sha256(recipe),
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {}
    for key, value in pairs:
        if key in row:
            raise CompanyResearchRecipeError(f"duplicate JSON key in company recipe: {key!r}")
        row[key] = value
    return row


def _semantic_sha256(record: BaseModel) -> str:
    return _canonical_sha256(record.model_dump(mode="json"))


def _canonical_sha256(value: Any) -> str:
    body = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


__all__ = [
    "CompanyResearchBindingDrift",
    "CompanyResearchBindingError",
    "CompanyResearchLaunchContract",
    "CompanyResearchLaunchRequest",
    "CompanyResearchRecipe",
    "CompanyResearchRecipeAdapter",
    "CompanyResearchRecipeError",
    "CompanyResearchSubject",
    "DEFAULT_COMPANY_RESEARCH_RECIPE",
    "LAUNCH_CONTRACT_SCHEMA_VERSION",
    "LAUNCH_REQUEST_SCHEMA_VERSION",
    "LoadedCompanyResearchRecipe",
    "RECIPE_ID",
    "RECIPE_SCHEMA_VERSION",
    "RECIPE_STAGE_ORDER",
    "TRUSTED_BINDING_SCHEMA_VERSION",
    "TrustedCompanyResearchBinding",
    "load_company_research_recipe",
]
