# -*- coding: utf-8 -*-
"""R22 — the pending approval cards a shadow replay actually needs.

The hole this closes
--------------------
:class:`~guanlan_v2.orchestration.adapters.api.ProductionReplayPlanCoordinator`
gates **both** replay lanes on a durable human decision: ``_require_approval``
looks up ``(request_id, <lane candidate digest>)`` on the Phase-7
:class:`~guanlan_v2.orchestration.approval.PlanApprovalCoordinator` and refuses the
lane when there is none. Before this module,
:meth:`PlanApprovalCoordinator.register_pending` had **zero** production callers —
so no card ever existed for either lane digest, and every replay refused at its
first decision point with ``ReplayCoordinatorApprovalRefused``. Meanwhile
``POST /orchestration/replay/start`` published a THIRD digest
(``derive_replay_start_candidate_digest``) that no gate ever consults, so a human
who "approved the card the door published" authorized nothing. Three digests where
there should have been a chain.

This module is the missing producer: it turns two *already prepared* lane plans
into two human-approvable cards on the real coordinator, and hands back the exact
two digests to construct the replay coordinator with.

Which digest a card binds — and why NOT the coordinator's default
-----------------------------------------------------------------
``ProductionReplayPlanCoordinator`` **defaults** its two lane digests to synthetic
identities — ``content_digest({"domain": "shadow-replay-<lane>-candidate-v1",
"plan_candidate_digest": …})`` — and accepts overrides through its
``bootstrap_candidate_plan_digest=`` / ``main_candidate_plan_digest=`` parameters.
The defaults cannot be used, and that is a fact of the production path, not a
preference:

* every ``PlanApprovalCoordinator.decide`` calls the Phase-2
  ``PlanAdmissionService.record_approval(candidate_id=…)``, which refuses with
  ``AdmissionRejected(code="unknown_candidate")`` for any digest that is not one of
  its own *prepared and reserved* candidates. A synthetic domain digest never is.
  So a card registered under a default lane digest can be **displayed but never
  decided** once the coordinator is bound to the real admission service;
* the same digest is what the eventual freeze checks
  (``freeze_and_admit_candidate`` → ``approval.authorizes_freeze(...,
  candidate_plan_digest=plan_digest)``), so binding the approval to the real
  candidate digest makes ONE human decision satisfy BOTH gates instead of two
  unrelated ones;
* a default digest binds ``(request, schedule, execution config)`` but **not the
  plan**, so an approval under it would authorize any plan at all. The real
  candidate digest binds the request, the whole executable draft and the
  ContextSnapshot content — the human approves what actually runs.

What the approval therefore does and does not bind is worth stating plainly. It
binds the plan; it does **not** bind the ``ShadowExecutionConfig``. The execution
config is enforced separately and structurally: the coordinator reserves each
point's Bootstrap node as a child of the driver's ONE interval plan reservation,
resolved by ``derive_replay_plan_candidate_digest(request_id, schedule_digest,
execution_config_digest)`` — a run under a different config finds no active
reservation and refuses before any node work. Both facts are returned together on
:class:`ReplayApprovalCards` so a caller can see the pair.

Ordering: the sealed config comes first, by type
------------------------------------------------
:func:`register_replay_approval_cards` **requires** the sealed
:class:`ShadowExecutionConfig` (it derives ``plan_candidate_digest`` from it), so
the approval moment structurally cannot precede the sealing of the config the run
will use. This module deliberately does NOT seal that config: five of its ten
fields (``init_cash``, the two snapshot digests, ``cost_model_digest``,
``intrabar_exit_priority``) are deployment configuration that neither the
``/replay/start`` request body nor ``AdaptersRouterDeps`` carries, and inventing
defaults for them would produce a card that approves a 口径 the run will not use —
worse than the current honest refusal.

Red lines
---------
* **It never approves anything.** It registers *pending* cards and stops: no
  ``decide``, no lease consume, no admission freeze, no ``AUTO`` (a draft whose
  ``approval_policy`` is not ``REQUIRED`` is refused, and the card model makes an
  ``AUTO`` card structurally unconstructible anyway).
* **A card can only be minted under a digest that recomputes from its own draft**
  (Phase-1 ``compute_candidate_plan_digest``), so a card can never authorize a plan
  the reviewer did not see.
* **Nothing is fabricated.** The lane drafts, their candidate digests and the
  ContextSnapshot digest are inputs from the caller's own admission preparation;
  this module derives identities and renders a diff, and refuses whatever it cannot
  derive honestly.

It defines no ``ContractModel`` (only frozen dataclasses and functions), so the
Phase-1 completeness walk and the Phase-9 classification firewall stay inert over
it — the ``adapters.identity`` precedent.
"""
from __future__ import annotations

import dataclasses
from types import MappingProxyType
from typing import Any, Mapping

from guanlan_v2.orchestration.digest import content_digest
from guanlan_v2.orchestration.enums import ApprovalPolicy, PlanSource
from guanlan_v2.orchestration.plan_diff import (
    PLAN_DIFF_SCHEMA_REF,
    PendingPlanApproval,
    build_pending_plan_approval,
    build_plan_diff,
)
from guanlan_v2.orchestration.refs import TypedPayloadRef
from guanlan_v2.orchestration.spec import compute_candidate_plan_digest

__all__ = [
    "REPLAY_LANES",
    "LANE_CANDIDATE_DOMAINS",
    "ReplayCardError",
    "ReplayLaneUnknown",
    "ReplayCardRefused",
    "ReplayLanePlan",
    "ReplayApprovalCards",
    "coordinator_default_lane_candidate_digests",
    "build_replay_lane_card",
    "register_replay_approval_cards",
]

#: the two lanes ``ProductionReplayPlanCoordinator`` gates, in the order it gates
#: them (``bootstrap_context`` then ``llm_proposal`` per decision point).
REPLAY_LANES: tuple[str, ...] = ("bootstrap", "main")

#: the domain strings the sealed coordinator uses for its FALLBACK lane digests,
#: transcribed from ``adapters/api.py`` (``ProductionReplayPlanCoordinator.__init__``).
#: Reproduced here only so a caller can *recognise* a default — never to card one
#: (see the module docstring: a default digest is undecidable on the real path).
LANE_CANDIDATE_DOMAINS: Mapping[str, str] = {
    "bootstrap": "shadow-replay-bootstrap-candidate-v1",
    "main": "shadow-replay-main-candidate-v1",
}

#: the payload namespace every plan-shaped payload in this framework uses.
_MAIN_NAMESPACE = "main"

#: the two card sources Phase 7 allows (``plan_diff.PendingPlanApproval._verify``).
_CARDABLE_SOURCES = (PlanSource.DYNAMIC, PlanSource.PRESET_FALLBACK)


class ReplayCardError(Exception):
    """Base: this module refused to produce or register a card."""


class ReplayLaneUnknown(ReplayCardError):
    """The lane set is not exactly the two lanes the coordinator gates.

    Both lanes are required together: the whole point of R22 is that ONE human
    moment authorizes exactly what will run, and a run whose LLM lane is approved
    but whose bootstrap lane is not simply dies at the first decision point.
    """


class ReplayCardRefused(ReplayCardError):
    """A lane plan cannot be carded honestly (never a silently weakened card).

    Raised when the supplied ``candidate_plan_digest`` does not recompute from the
    draft, when the draft belongs to a different request, when its approval policy
    is not ``REQUIRED``, when its ``PlanSource`` is one the Phase-7 reviewer card
    structurally does not cover, or when its preset provenance is inconsistent.
    """


# =========================================================================== #
# inputs / outputs                                                             #
# =========================================================================== #
@dataclasses.dataclass(frozen=True)
class ReplayLanePlan:
    """One lane's PREPARED admission candidate — the caller's own, not invented.

    ``candidate_plan_digest`` is what ``PlanAdmissionService.prepare_candidate``
    minted for ``draft`` (``PreparedAdmissionCandidate.candidate_plan_digest``) and
    ``context_content_digest`` the ``PreparedAdmissionCandidate``'s value for the
    same preparation (``None`` for a context-less draft). Both are re-verified here
    against the Phase-1 rule, so a mismatched pair is refused rather than carded.

    The baseline fields are the reviewer's frame of reference for the diff and
    follow ``PlanDiff``'s own matrix: ``none`` carries neither baseline identity,
    ``fallback_preset`` names ``baseline_preset_id``, ``prior_plan`` names
    ``baseline_plan_digest``. ``preset_id`` / ``preset_record_digest`` are the
    card's preset provenance and are required exactly when the draft's source is
    ``PRESET_FALLBACK`` (the Phase-7 both-direction rule, consumed by lease
    matching).
    """

    lane: str
    draft: Any
    candidate_plan_digest: str
    context_content_digest: str | None = None
    baseline: Any = None
    baseline_kind: str = "none"
    baseline_plan_digest: str | None = None
    baseline_preset_id: str | None = None
    planner_rationale: str | None = None
    preset_id: str | None = None
    preset_record_digest: str | None = None


@dataclasses.dataclass(frozen=True)
class ReplayApprovalCards:
    """The registered cards plus every identity the replay run must be built with.

    ``coordinator_kwargs()`` is the whole point: feed it straight into
    ``ProductionReplayPlanCoordinator(...)`` so the digests the human decided are
    the digests ``_require_approval`` looks up. Omit it and the coordinator falls
    back to its synthetic defaults, which nothing can ever approve — a loud,
    fail-closed mistake rather than a silent one.

    ``already_decided`` maps a lane to the terminal decision value (``"approved"`` /
    ``"rejected"``) that was ALREADY on the journal when
    :func:`register_replay_approval_cards` ran, i.e. the lanes it deliberately did
    not re-register (a decided candidate can never be re-carded — see that
    function's docstring). Empty on a first call. A caller that wants to know
    whether a human still has to act reads :meth:`awaiting_human`.
    """

    request_id: str
    schedule_digest: str
    execution_config_digest: str
    plan_candidate_digest: str
    cards: Mapping[str, PendingPlanApproval]
    already_decided: Mapping[str, str] = dataclasses.field(
        default_factory=lambda: MappingProxyType({}))

    def candidate_plan_digest(self, lane: str) -> str:
        """The digest the human decided for ``lane`` (== the admission candidate)."""
        if lane not in self.cards:
            raise ReplayLaneUnknown(f"no card was registered for lane {lane!r}")
        return self.cards[lane].candidate_plan_digest

    def awaiting_human(self) -> tuple[str, ...]:
        """The lanes still pending a decision, in lane order (``()`` ⇒ all decided).

        Honest about direction only: a lane absent from this tuple was *decided*,
        which includes ``rejected``. Read ``already_decided`` for the verdict; the
        coordinator's own gate is what actually refuses a rejected lane.
        """
        return tuple(l for l in REPLAY_LANES if l not in self.already_decided)

    def coordinator_kwargs(self) -> dict[str, str]:
        """The two ``ProductionReplayPlanCoordinator`` constructor overrides."""
        return {
            "bootstrap_candidate_plan_digest": self.candidate_plan_digest("bootstrap"),
            "main_candidate_plan_digest": self.candidate_plan_digest("main"),
        }


# =========================================================================== #
# the coordinator's fallback identities (recognition only)                      #
# =========================================================================== #
def coordinator_default_lane_candidate_digests(
    *, plan_candidate_digest: str
) -> dict[str, str]:
    """Reproduce the sealed coordinator's DEFAULT lane digests, byte-identically.

    Useful for exactly one thing: telling a caller "you are about to run with the
    fallback identities, which no human can ever have approved". It is deliberately
    NOT what :func:`register_replay_approval_cards` cards — see the module
    docstring. ``plan_candidate_digest`` is
    :func:`~guanlan_v2.orchestration.adapters.api.derive_replay_plan_candidate_digest`'s
    interval identity.
    """
    return {
        lane: content_digest({"domain": domain,
                              "plan_candidate_digest": plan_candidate_digest})
        for lane, domain in LANE_CANDIDATE_DOMAINS.items()
    }


# =========================================================================== #
# one card                                                                     #
# =========================================================================== #
def _refuse_uncardable(plan: ReplayLanePlan, request: Any) -> None:
    """Every honesty precondition, checked BEFORE anything is written or stored."""
    draft = plan.draft
    draft_request_id = getattr(draft, "request_id", None)
    if draft_request_id != getattr(request, "request_id", None):
        raise ReplayCardRefused(
            f"the {plan.lane} lane draft belongs to request {draft_request_id!r}, not "
            f"{getattr(request, 'request_id', None)!r}; a card is only ever registered "
            "for the request whose run it authorizes")
    policy = getattr(draft, "approval_policy", None)
    if policy is not ApprovalPolicy.REQUIRED:
        raise ReplayCardRefused(
            f"the {plan.lane} lane draft carries approval_policy={policy!r}; a human "
            "card exists only under REQUIRED — an auto-approved plan is refused at "
            "the door, never carded")
    source = getattr(draft, "source", None)
    if source not in _CARDABLE_SOURCES:
        raise ReplayCardRefused(
            f"the {plan.lane} lane draft has source={source!r}, which the Phase-7 "
            "reviewer card (PendingPlanApproval) structurally does not cover — it "
            "accepts only DYNAMIC and PRESET_FALLBACK. A PlanSource.PRESET plan (the "
            "Phase-5 Lane-0 bootstrap draft is one) therefore cannot be carded; "
            "relabelling a preset as a 'fallback' to make it fit would put a false "
            "provenance in front of the reviewer, so it is refused instead.")
    provenance = (plan.preset_id, plan.preset_record_digest)
    if source is PlanSource.PRESET_FALLBACK and any(x is None for x in provenance):
        raise ReplayCardRefused(
            f"the {plan.lane} lane draft is PRESET_FALLBACK, so the card must name "
            "both preset_id and preset_record_digest (the Phase-7 both-direction "
            "provenance rule) — the reviewer has to see WHICH reviewed preset ran")
    if source is PlanSource.DYNAMIC and any(x is not None for x in provenance):
        raise ReplayCardRefused(
            f"the {plan.lane} lane draft is DYNAMIC, so it must carry no preset "
            "provenance; a dynamic candidate that names a preset is a false claim")

    recomputed = compute_candidate_plan_digest(
        request=request, draft=draft,
        context_content_digest=plan.context_content_digest)
    if recomputed != plan.candidate_plan_digest:
        raise ReplayCardRefused(
            f"the {plan.lane} lane candidate digest does not recompute from its own "
            f"draft (declared {plan.candidate_plan_digest!r}, computed {recomputed!r}); "
            "an approval that does not bind the plan the reviewer saw is worthless, "
            "so the card is refused")


def build_replay_lane_card(
    *,
    plan: ReplayLanePlan,
    request: Any,
    payloads: Any,
    registry_digest: str,
    requested_at: Any,
    namespace: str = _MAIN_NAMESPACE,
) -> PendingPlanApproval:
    """Build ONE lane's reviewer card, committing its ``PlanDiff@1`` payload.

    The diff payload is really ``put`` into ``payloads`` (a
    ``RuntimeStores.payloads`` surface) under ``registry_digest`` before the card is
    built, so the card's ``plan_diff_ref`` resolves for real and a consumer can
    re-derive ``rendered_md`` from the stored payload. Both writes are keyed by
    deterministic idempotency keys, so re-building the same card is a no-op.

    ``requested_at`` must come from an authoritative clock (the caller's — this
    module never reads the wall clock); ``register_replay_approval_cards`` supplies
    the coordinator's own ``now()``.
    """
    if plan.lane not in REPLAY_LANES:
        raise ReplayLaneUnknown(
            f"unknown replay lane {plan.lane!r}; the coordinator gates exactly "
            f"{REPLAY_LANES}")
    _refuse_uncardable(plan, request)

    diff = build_plan_diff(
        plan.draft, request=request,
        candidate_plan_digest=plan.candidate_plan_digest,
        baseline=plan.baseline, baseline_kind=plan.baseline_kind,
        baseline_plan_digest=plan.baseline_plan_digest,
        baseline_preset_id=plan.baseline_preset_id)
    payload_ref = payloads.put(
        PLAN_DIFF_SCHEMA_REF, diff,
        registry_digest=registry_digest, namespace=namespace,
        idempotency_key=_diff_idempotency_key(plan))
    return build_pending_plan_approval(
        draft=plan.draft, request=request,
        candidate_plan_digest=plan.candidate_plan_digest,
        diff=diff,
        plan_diff_ref=TypedPayloadRef(
            schema_ref=PLAN_DIFF_SCHEMA_REF, payload_ref=payload_ref),
        planner_rationale=plan.planner_rationale,
        candidate_id=_candidate_id(plan),
        requested_at=requested_at,
        preset_id=plan.preset_id,
        preset_record_digest=plan.preset_record_digest)


def _candidate_id(plan: ReplayLanePlan) -> str:
    """The card's audit id — deterministic, so a rebuild is byte-stable."""
    return f"replay-card.{plan.lane}.{plan.candidate_plan_digest[:16]}"


def _diff_idempotency_key(plan: ReplayLanePlan) -> str:
    return f"replay-plan-diff:{plan.lane}:{plan.candidate_plan_digest}"


def _register_idempotency_key(request_id: str, plan: ReplayLanePlan) -> str:
    return f"replay-card:{plan.lane}:{request_id}:{plan.candidate_plan_digest}"


# =========================================================================== #
# both cards, on the real coordinator                                          #
# =========================================================================== #
def register_replay_approval_cards(
    *,
    coordinator: Any,
    request: Any,
    schedule: Any,
    execution_config: Any,
    lane_plans: Mapping[str, ReplayLanePlan],
    payloads: Any,
    registry_digest: str,
    requested_at: Any = None,
) -> ReplayApprovalCards:
    """Register the two pending cards one human moment must decide.

    Requires the SEALED :class:`ShadowExecutionConfig` the run will use (see the
    module docstring: this is the ordering guarantee, expressed as a parameter
    rather than a comment) and the resolved ``DecisionSchedule``. Both lanes must be
    supplied together, each keyed by its own lane name.

    **Re-callable, with a precise idempotence contract** (the launcher will call
    this on every replay start and after every restart, so the boundaries matter):

    * *lane still pending* — re-registering the semantically identical card appends
      no journal row and returns the stored card (``register_pending``'s own
      identity rule);
    * *lane still pending, but the card content differs* — refused **before** any
      lane is registered, by a pre-flight comparison of semantic digests, so a
      partial write cannot happen (see the atomicity note below);
    * *lane already DECIDED* — the lane is **skipped**, not re-registered, and named
      in ``already_decided``. ``register_pending`` raises ``ApprovalDecisionConflict``
      for a decided key (``approval.py:467-471``), so re-registering would crash the
      caller exactly when a human had already done the right thing. The decision is
      the authority; the card is only its request form. Honest limit: a decided card
      is consumed out of the pending fold, so the non-digest-bound framing
      (``planner_rationale``, the baseline choice) of what the human actually read
      can no longer be compared — everything the *digest* binds (request, whole
      executable draft, ContextSnapshot) is identical by construction, because the
      digest is re-derived and checked above.

    **Atomicity is build-time and pre-flight, not transactional.** Every lane is
    built and pre-flight-checked before ANY lane is registered, so a refusal leaves
    the journal untouched. It is not a transaction: `register_pending` fsyncs per
    call and this module cannot roll one back (``approval.py`` is review-sealed and
    the journal is append-only), so an I/O failure part-way through the registration
    loop can still leave the first lane's card pending alone. That state stays
    fail-closed — the unregistered lane has no decision, so the run refuses — and a
    re-call heals it, because the already-pending lane is idempotent.

    Returns everything the caller needs to build the run:
    ``coordinator_kwargs()`` for ``ProductionReplayPlanCoordinator`` and
    ``plan_candidate_digest`` — the interval reservation identity the driver mints,
    which is where the execution config is structurally enforced.
    """
    supplied = set(lane_plans)
    if supplied != set(REPLAY_LANES):
        raise ReplayLaneUnknown(
            f"expected exactly the lanes {sorted(REPLAY_LANES)}, got "
            f"{sorted(supplied)}; one human moment authorizes BOTH lanes or the run "
            "dies at its first decision point")
    for lane, plan in lane_plans.items():
        if plan.lane != lane:
            raise ReplayCardRefused(
                f"lane plan keyed {lane!r} declares lane {plan.lane!r}; a crossed "
                "lane mapping would card one lane's plan under the other's identity")
    digests = {lane: lane_plans[lane].candidate_plan_digest for lane in REPLAY_LANES}
    if len(set(digests.values())) != len(digests):
        raise ReplayCardRefused(
            "both lanes name the SAME candidate digest "
            f"({sorted(set(digests.values()))}); the card identity is "
            "(request_id, candidate_plan_digest), so the second registration would "
            "silently collapse into the first and ONE decision would authorize both "
            "lanes while the reviewer believed they were deciding one")
    if getattr(execution_config, "schedule_digest", None) != schedule.content_digest:
        raise ReplayCardRefused(
            "the sealed ShadowExecutionConfig names schedule_digest "
            f"{getattr(execution_config, 'schedule_digest', None)!r} but the supplied "
            f"schedule is {schedule.content_digest!r}; the 口径 a reviewer authorizes "
            "must be the one bound to the schedule that will actually be walked")
    # …and the REQUEST must bind that same schedule. Without this, a request bound to
    # schedule A could be carded against schedule B and still pass the config check
    # above (the config would simply be B's) — the drift this door exists to catch.
    # ``shadow.wrap_proposal_as_intent`` requires the ref anyway, so an absent one is
    # refused here rather than three layers deeper, mid-run.
    request_ref = getattr(request, "decision_schedule_ref", None)
    if request_ref is None:
        raise ReplayCardRefused(
            "the request binds no decision_schedule_ref; a replay request must name "
            "the registered schedule it will be walked against (the shadow envelope "
            "requires it), so there is nothing to card it against")
    if (request_ref.id, request_ref.version, request_ref.content_digest) != (
            schedule.id, schedule.version, schedule.content_digest):
        raise ReplayCardRefused(
            f"the request binds schedule {request_ref.id}@{request_ref.version} "
            f"({request_ref.content_digest}) but the supplied schedule is "
            f"{schedule.id}@{schedule.version} ({schedule.content_digest}); a card "
            "must authorize the schedule the request will actually run")

    # imported here (not at module import time) purely to keep this module's import
    # graph free of the adapters router; the derivation itself is the router's.
    from guanlan_v2.orchestration.adapters.api import derive_replay_plan_candidate_digest

    request_id = str(getattr(request, "request_id", ""))
    schedule_digest = str(schedule.content_digest)
    execution_config_digest = str(execution_config.semantic_digest())
    plan_candidate_digest = derive_replay_plan_candidate_digest(
        request_id=request_id, schedule_digest=schedule_digest,
        execution_config_digest=execution_config_digest)

    at = requested_at if requested_at is not None else coordinator.now()

    # ── phase 1: BUILD every lane. Each honesty precondition runs here, so a bad
    # lane refuses before the journal is touched at all.
    #
    # Building does write: the PlanDiff `put` necessarily precedes the card, because
    # the card needs the ref the put returns — so a later refusal can orphan a diff
    # payload. That orphan is inert, not a leak: the payload store is
    # content-addressed and idempotent, so the row references nothing, is referenced
    # by nothing, is reachable only by recomputing its own content digest, and a
    # retry of the identical build reuses it rather than adding a second.
    built: dict[str, PendingPlanApproval] = {
        lane: build_replay_lane_card(
            plan=lane_plans[lane], request=request, payloads=payloads,
            registry_digest=registry_digest, requested_at=at)
        for lane in REPLAY_LANES                   # deterministic order
    }

    # ── phase 2: PRE-FLIGHT the journal for both lanes at once, so the two states
    # register_pending can refuse (a decided key, a semantically different pending
    # card) are known before the first append rather than discovered halfway.
    stored_pending = {
        (c.request_id, c.candidate_plan_digest): c for c in coordinator.list_pending()
    }
    decided: dict[str, str] = {}
    drifted: list[str] = []
    for lane in REPLAY_LANES:
        digest = lane_plans[lane].candidate_plan_digest
        prior = coordinator.load_decision(request_id, digest)
        if prior is not None:
            decided[lane] = prior.decision.value
            continue
        stored = stored_pending.get((request_id, digest))
        if stored is not None and stored.semantic_digest() != built[lane].semantic_digest():
            drifted.append(lane)
    if drifted:
        raise ReplayCardRefused(
            f"a semantically different pending card already exists for lane(s) "
            f"{sorted(drifted)} under the same identity; refusing before ANY lane is "
            "registered rather than half-writing, and never silently replacing the "
            "card a reviewer may be reading right now")

    # ── phase 3: register only the lanes that are still undecided.
    cards: dict[str, PendingPlanApproval] = {}
    for lane in REPLAY_LANES:
        if lane in decided:
            cards[lane] = built[lane]      # the decision is the authority; see docstring
            continue
        cards[lane] = coordinator.register_pending(
            built[lane],
            idempotency_key=_register_idempotency_key(request_id, lane_plans[lane]))
    return ReplayApprovalCards(
        request_id=request_id,
        schedule_digest=schedule_digest,
        execution_config_digest=execution_config_digest,
        plan_candidate_digest=plan_candidate_digest,
        cards=MappingProxyType(cards),
        already_decided=MappingProxyType(decided),
    )
