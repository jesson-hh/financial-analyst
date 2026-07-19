# -*- coding: utf-8 -*-
"""Phase 6 · Task 3 — runtime-only ``TargetPortfolioIntent`` envelope + the three
shadow idempotency key families.

Written test-first (RED until ``TargetPortfolioIntent`` / ``ShadowEnvelopeError`` /
``wrap_proposal_as_intent`` and the three ``*_key`` / ``*_id`` builders exist in
``guanlan_v2.orchestration.shadow``).

These pin, per the Task-3 brief:

* the intent field surface + the "same portfolio matrix PLUS the Task-1b band
  check" model validator, and the three time-ordering validators
  (``decision_as_of < eligible_execution_at``, ``scheduled_for <= eligible_execution_at``,
  ``valid_until is None or eligible_execution_at <= valid_until``);
* invariant 1 (the model can never self-report the envelope): closed single-value
  ``origin``/``authority``/``execution_scope`` Literals reject every non-default;
  ``wrap_proposal_as_intent`` accepts NO caller-supplied ``scheduled_for`` /
  ``eligible_execution_at`` (both computed inside);
* invariant 2 — the five ``ShadowEnvelopeError`` refusal paths (no schedule ref /
  unregistered / stale digest / non-decision-point / ``decision_as_of`` before
  cutoff) each with distinct reason text, and no intent produced;
* invariant 3 — frozen intents (attribute assignment raises);
* invariant 4 — key determinism + single-component discrimination + JSON
  round-trip survival for all three families;
* invariant 5 — semantic digest excludes exactly ``intent_id`` / ``created_at``
  (equal ``semantic_digest`` but distinct apply keys).

Run from repo root: ``pytest tests/orchestration/test_shadow_envelope.py -v``
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import shadow
from guanlan_v2.orchestration.data.calendar import build_trading_calendar
from guanlan_v2.orchestration.data.symbols import Symbol
from guanlan_v2.orchestration.enums import Confidence, DataMode
from guanlan_v2.orchestration.refs import ContentRef, SchemaRef
from guanlan_v2.orchestration.schemas import Artifact, Provenance
from guanlan_v2.orchestration.spec import OrchestrationRequest

UTC = timezone.utc
_CST = ZoneInfo("Asia/Shanghai")
_CAL_ID = "ashare.xshg"
_WEEK = ("2026-07-20", "2026-07-21", "2026-07-22", "2026-07-23", "2026-07-24")  # Mon..Fri

# the three ruled instants used across the wrap tests (Asia/Shanghai, daily @09:30,
# cutoff @09:00, next_open execution on the strictly-following session):
_SCHED_UTC = datetime(2026, 7, 20, 1, 30, tzinfo=UTC)   # 09:30 CST 07-20
_CUTOFF_UTC = datetime(2026, 7, 20, 1, 0, tzinfo=UTC)    # 09:00 CST 07-20
_ELIGIBLE_UTC = datetime(2026, 7, 21, 1, 30, tzinfo=UTC)  # 09:30 CST 07-21 (next session open)


# --------------------------------------------------------------------------- #
# builders                                                                    #
# --------------------------------------------------------------------------- #
def _sym(code: str = "600519", exchange: str = "SH", board: str = "main") -> Symbol:
    return Symbol(code=code, exchange=exchange, board=board)


def _pos(weight: float, *, code: str = "600519", exchange: str = "SH",
         board: str = "main", **kw):
    return shadow.TargetPosition(symbol=_sym(code, exchange, board), target_weight=weight, **kw)


def _proposal(positions=None, cash_weight: float = 0.5, *,
              rationale: str = "fully-invested thesis",
              confidence: Confidence = Confidence.MEDIUM):
    if positions is None:
        positions = [_pos(0.5)]
    return shadow.PortfolioTargetProposal(
        positions=tuple(positions), cash_weight=cash_weight,
        rationale=rationale, confidence=confidence,
    )


def _calendar(sessions_iso=_WEEK, *, calendar_id=_CAL_ID):
    return build_trading_calendar(
        calendar_id=calendar_id,
        sessions=[date.fromisoformat(s) for s in sessions_iso],
        material_id="cal.ashare.2026",
        material_version="1",
    )


def _schedule(**overrides):
    base = dict(
        id="shadow.daily.ashare",
        version="1",
        calendar_id=_CAL_ID,
        timezone="Asia/Shanghai",
        kind="daily",
        decision_local_time="09:30",
        cutoff_local_time="09:00",
        bar_frequency="1d",
        execution_policy="next_open",
        execution_price_field="open",
        matching_engine_version=shadow.SHADOW_MATCHING_ENGINE_VERSION,
    )
    base.update(overrides)
    return shadow.DecisionSchedule.build(**base)


class _FixedClock:
    """A deterministic :class:`AuthoritativeClock` returning one fixed aware-UTC instant."""

    def __init__(self, instant: datetime = datetime(2026, 7, 20, 2, 0, tzinfo=UTC)) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant


def _provenance() -> Provenance:
    return Provenance(
        plan_digest="a" * 64,
        code_version="git:abc123",
        as_of=datetime(2026, 7, 20, 7, 0, tzinfo=UTC),
        pit_mode=DataMode.ONLINE,
    )


def _proposal_artifact(payload=None, *, schema_name="PortfolioTargetProposal",
                       schema_version="1", artifact_id="art-prop-1"):
    if payload is None:
        payload = _proposal([_pos(0.5)], 0.5)
    return Artifact.build(
        artifact_id=artifact_id,
        run_id="run-1",
        created_at=datetime(2026, 7, 20, 7, 30, tzinfo=UTC),
        producer_node_id="node.shadow_decision",
        slot="decision",
        output_key="proposal",
        kind="portfolio_target_proposal",
        payload_schema_ref=SchemaRef(name=schema_name, version=schema_version),
        payload=payload,
        rendered_md="# proposal",
        provenance=_provenance(),
    )


def _registry_with(schedule):
    reg = shadow.DecisionScheduleRegistry()
    ref = reg.register(schedule)
    return reg, ref


def _request(ref=None, **over):
    base = dict(
        request_id="req-1",
        goal="shadow the book",
        workflow="orchestrate_only",
        decision_schedule_ref=ref,
    )
    base.update(over)
    return OrchestrationRequest(**base)


def _wrap(*, proposal_artifact=None, schedule=None, registry=None, request=None,
          calendar=None, session_date="2026-07-20",
          decision_as_of=_SCHED_UTC, target_version=1, intent_id="intent-1",
          clock=None, valid_until=None):
    """Drive the real ``wrap_proposal_as_intent`` with the happy-path defaults."""
    schedule = schedule if schedule is not None else _schedule()
    if registry is None and request is None:
        registry, ref = _registry_with(schedule)
        request = _request(ref=ref)
    elif registry is None:
        registry, _ = _registry_with(schedule)
    elif request is None:
        ref = ContentRef(id=schedule.id, version=schedule.version,
                         content_digest=schedule.content_digest)
        request = _request(ref=ref)
    return shadow.wrap_proposal_as_intent(
        proposal_artifact=proposal_artifact or _proposal_artifact(),
        source_decision_artifact_id="dec-art-1",
        request=request,
        schedule_registry=registry,
        calendar=calendar or _calendar(),
        session_date=session_date,
        decision_as_of=decision_as_of,
        target_version=target_version,
        intent_id=intent_id,
        clock=clock or _FixedClock(),
        valid_until=valid_until,
    )


def _intent(**over):
    """A hand-built, self-consistent :class:`TargetPortfolioIntent` for the model-level tests."""
    base = dict(
        intent_id="intent-1",
        target_version=1,
        proposal_artifact_id="art-prop-1",
        proposal_digest="a" * 64,
        source_decision_artifact_id="dec-art-1",
        decision_schedule_id="shadow.daily.ashare",
        decision_schedule_version="1",
        decision_schedule_digest="b" * 64,
        scheduled_for=_SCHED_UTC,
        decision_as_of=_SCHED_UTC,
        eligible_execution_at=_ELIGIBLE_UTC,
        positions=(_pos(0.5),),
        cash_weight=0.5,
        rationale="thesis",
        confidence=Confidence.MEDIUM,
        created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC),
    )
    base.update(over)
    return shadow.TargetPortfolioIntent(**base)


# --------------------------------------------------------------------------- #
# module surface — exports + error hierarchy                                   #
# --------------------------------------------------------------------------- #
def test_new_names_exported_and_defined_in_shadow():
    for name in (
        "TargetPortfolioIntent", "ShadowEnvelopeError", "wrap_proposal_as_intent",
        "SHADOW_APPLY_KEY_DOMAIN", "target_apply_key",
        "SHADOW_ORDER_ID_DOMAIN", "shadow_order_id", "ShadowOrderKind",
        "SHADOW_FILL_ID_DOMAIN", "shadow_fill_id",
    ):
        assert name in shadow.__all__, f"{name} missing from shadow.__all__"
    assert shadow.TargetPortfolioIntent.__module__ == "guanlan_v2.orchestration.shadow"


def test_shadow_envelope_error_extends_shadow_contract_error():
    assert issubclass(shadow.ShadowEnvelopeError, shadow.ShadowContractError)
    assert issubclass(shadow.ShadowEnvelopeError, ValueError)


def test_key_family_domain_constants():
    assert shadow.SHADOW_APPLY_KEY_DOMAIN == "shadow-apply-key-v1"
    assert shadow.SHADOW_ORDER_ID_DOMAIN == "shadow-order-id-v1"
    assert shadow.SHADOW_FILL_ID_DOMAIN == "shadow-fill-id-v1"


# --------------------------------------------------------------------------- #
# field surface + closed default Literals (invariant 1)                        #
# --------------------------------------------------------------------------- #
def test_intent_field_surface_and_schema_version():
    fields = set(shadow.TargetPortfolioIntent.model_fields)
    assert fields == {
        "schema_version", "intent_id", "target_version", "proposal_artifact_id",
        "proposal_digest", "source_decision_artifact_id", "decision_schedule_id",
        "decision_schedule_version", "decision_schedule_digest", "scheduled_for",
        "decision_as_of", "eligible_execution_at", "valid_until", "positions",
        "cash_weight", "origin", "authority", "execution_scope", "rationale",
        "confidence", "created_at",
    }
    assert shadow.TargetPortfolioIntent.model_fields["schema_version"].default == "1"


def test_intent_zero_trading_defaults():
    i = _intent()
    assert i.origin == "LLM"
    assert i.authority == "ADVISORY_ONLY"
    assert i.execution_scope == "SHADOW_ONLY"
    assert i.valid_until is None


@pytest.mark.parametrize(
    "field,bad",
    [
        ("origin", "HUMAN"),
        ("authority", "LIVE"),
        ("execution_scope", "LIVE_ONLY"),
        ("schema_version", "2"),
    ],
)
def test_intent_closed_literals_reject_non_default(field, bad):
    # invariant 1: the structural Literals reject every non-default value at
    # construction — the model can never self-report a live/human envelope.
    with pytest.raises(ValidationError):
        _intent(**{field: bad})


def test_intent_forbids_extra_fields():
    with pytest.raises(ValidationError):
        _intent(not_a_real_field="x")


def test_intent_semantic_exclude_is_exactly_id_and_created_at():
    assert shadow.TargetPortfolioIntent.SEMANTIC_EXCLUDE == frozenset({"intent_id", "created_at"})
    assert shadow.TargetPortfolioIntent.SELF_DIGEST_FIELDS == frozenset()


# --------------------------------------------------------------------------- #
# model validators — portfolio matrix + time ordering                          #
# --------------------------------------------------------------------------- #
def _error_types(exc: ValidationError) -> set[str]:
    return {e["type"] for e in exc.errors()}


def test_intent_applies_the_same_portfolio_matrix_duplicate():
    with pytest.raises(ValidationError) as ei:
        _intent(positions=(_pos(0.25), _pos(0.25)), cash_weight=0.5)  # dup 600519.SH
    assert "duplicate_symbol" in _error_types(ei.value)


def test_intent_applies_the_same_portfolio_matrix_weight_sum():
    with pytest.raises(ValidationError) as ei:
        _intent(positions=(_pos(0.5),), cash_weight=0.4)  # sum 0.9
    assert "weight_sum_violation" in _error_types(ei.value)


def test_intent_applies_the_task1b_band_check():
    # an intent can never be laxer than a proposal: an off-band leg is rejected.
    with pytest.raises(ValidationError) as ei:
        _intent(positions=(_pos(0.3),), cash_weight=0.7)
    assert "non_band_weight" in _error_types(ei.value)


def test_intent_band_check_reads_the_same_exported_constant(monkeypatch):
    # widening the module band constant admits 0.3 through the INTENT validator too,
    # proving both proposal and intent consume the single-source TARGET_WEIGHT_BANDS.
    monkeypatch.setattr(shadow, "TARGET_WEIGHT_BANDS", (0.0, 0.25, 0.3, 0.5, 0.75, 1.0))
    i = _intent(positions=(_pos(0.3),), cash_weight=0.7)
    assert i.positions[0].target_weight == 0.3


def test_intent_requires_decision_as_of_strictly_before_eligible():
    with pytest.raises(ValidationError):
        _intent(decision_as_of=_ELIGIBLE_UTC, eligible_execution_at=_ELIGIBLE_UTC)  # equal, not <
    with pytest.raises(ValidationError):
        _intent(decision_as_of=_ELIGIBLE_UTC + timedelta(seconds=1),
                eligible_execution_at=_ELIGIBLE_UTC)


def test_intent_requires_scheduled_for_le_eligible():
    with pytest.raises(ValidationError):
        _intent(scheduled_for=_ELIGIBLE_UTC + timedelta(seconds=1))


def test_intent_valid_until_must_be_at_or_after_eligible():
    # valid_until before eligible → rejected; at/after eligible → accepted.
    with pytest.raises(ValidationError):
        _intent(valid_until=_ELIGIBLE_UTC - timedelta(seconds=1))
    ok = _intent(valid_until=_ELIGIBLE_UTC + timedelta(days=1))
    assert ok.valid_until == _ELIGIBLE_UTC + timedelta(days=1)


# --------------------------------------------------------------------------- #
# invariant 3 — frozen                                                         #
# --------------------------------------------------------------------------- #
def test_intent_is_frozen():
    i = _intent()
    with pytest.raises(ValidationError):
        i.decision_as_of = _ELIGIBLE_UTC
    with pytest.raises(ValidationError):
        i.positions = ()


# --------------------------------------------------------------------------- #
# invariant 5 — semantic digest excludes exactly intent_id / created_at         #
# --------------------------------------------------------------------------- #
def test_semantic_digest_excludes_exactly_the_two_audit_facts():
    a = _intent(intent_id="intent-1", created_at=datetime(2026, 7, 20, 2, 0, tzinfo=UTC))
    b = _intent(intent_id="intent-2", created_at=datetime(2026, 7, 20, 9, 9, tzinfo=UTC))
    # differ ONLY in the two excluded fields → identical semantic identity ...
    assert a.semantic_digest() == b.semantic_digest()
    # ... but DISTINCT apply keys (apply identity is operational, keyed on intent_id).
    assert shadow.target_apply_key(a) != shadow.target_apply_key(b)


@pytest.mark.parametrize(
    "over",
    [
        {"scheduled_for": _SCHED_UTC + timedelta(seconds=1)},
        {"decision_as_of": _CUTOFF_UTC},
        {"eligible_execution_at": _ELIGIBLE_UTC + timedelta(days=1)},
        {"target_version": 2},
        {"decision_schedule_digest": "c" * 64},
        {"proposal_digest": "d" * 64},
        {"positions": (_pos(0.75),), "cash_weight": 0.25},
        {"rationale": "different"},
        {"confidence": Confidence.HIGH},
        {"valid_until": _ELIGIBLE_UTC + timedelta(days=2)},
    ],
)
def test_every_business_field_is_semantic(over):
    base = _intent()
    variant = _intent(**over)
    assert variant.semantic_digest() != base.semantic_digest()


# --------------------------------------------------------------------------- #
# invariant 4 — apply-key family                                               #
# --------------------------------------------------------------------------- #
def test_apply_key_is_deterministic_digesthex():
    a, b = _intent(), _intent()
    k = shadow.target_apply_key(a)
    assert k == shadow.target_apply_key(b)          # same inputs → identical key
    assert isinstance(k, str) and len(k) == 64 and all(c in "0123456789abcdef" for c in k)


@pytest.mark.parametrize(
    "over",
    [
        {"intent_id": "intent-2"},
        {"scheduled_for": _SCHED_UTC + timedelta(seconds=1)},
        {"target_version": 2},
    ],
)
def test_apply_key_discriminates_each_component(over):
    assert shadow.target_apply_key(_intent(**over)) != shadow.target_apply_key(_intent())


def test_apply_key_ignores_non_component_fields():
    # the apply key is over exactly {domain, intent_id, scheduled_for, target_version}
    # — changing another semantic field (rationale) does NOT change the apply key.
    assert shadow.target_apply_key(_intent(rationale="other")) == shadow.target_apply_key(_intent())


# --------------------------------------------------------------------------- #
# invariant 4 — order-id family                                                #
# --------------------------------------------------------------------------- #
def _order_id(**over):
    base = dict(
        apply_key=shadow.target_apply_key(_intent()),
        symbol=_sym(),
        order_kind="target_buy",
        trigger_bar="2026-07-21",
        ordinal=0,
    )
    base.update(over)
    return shadow.shadow_order_id(**base)


def test_shadow_order_kind_vocabulary():
    import typing
    assert set(typing.get_args(shadow.ShadowOrderKind)) == {
        "target_buy", "target_sell", "stop_loss", "take_profit", "max_hold_exit",
    }


def test_order_id_is_deterministic_digesthex():
    assert _order_id() == _order_id()
    oid = _order_id()
    assert isinstance(oid, str) and len(oid) == 64


@pytest.mark.parametrize(
    "over",
    [
        {"apply_key": shadow.target_apply_key(_intent(intent_id="intent-2"))},
        {"symbol": _sym("000001", exchange="SZ", board="main")},
        {"order_kind": "target_sell"},
        {"trigger_bar": "2026-07-22"},
        {"ordinal": 1},
    ],
)
def test_order_id_discriminates_each_component(over):
    assert _order_id(**over) != _order_id()


def test_order_id_keys_on_symbol_dotted():
    # the symbol enters the key as Symbol.dotted — two symbols with the same dotted
    # string but a different board still collide (board is not part of dotted); a
    # different code/exchange (hence dotted) discriminates.
    same_dotted = _sym("600519", exchange="SH", board="main")
    assert _order_id(symbol=same_dotted) == _order_id()


# --------------------------------------------------------------------------- #
# invariant 4 — fill-id family                                                 #
# --------------------------------------------------------------------------- #
def _fill_id(**over):
    base = dict(order_id=_order_id(), fill_seq=1)
    base.update(over)
    return shadow.shadow_fill_id(**base)


def test_fill_id_is_deterministic_digesthex():
    assert _fill_id() == _fill_id()
    fid = _fill_id()
    assert isinstance(fid, str) and len(fid) == 64


@pytest.mark.parametrize(
    "over",
    [
        {"order_id": _order_id(ordinal=1)},
        {"fill_seq": 2},
    ],
)
def test_fill_id_discriminates_each_component(over):
    assert _fill_id(**over) != _fill_id()


# --------------------------------------------------------------------------- #
# invariant 4 — JSON round-trip survival across all three families              #
# --------------------------------------------------------------------------- #
def test_keys_survive_json_roundtrip():
    i = _intent()
    reloaded = shadow.TargetPortfolioIntent.model_validate_json(i.model_dump_json())
    assert reloaded.semantic_digest() == i.semantic_digest()

    ak, ak2 = shadow.target_apply_key(i), shadow.target_apply_key(reloaded)
    assert ak == ak2
    oid = shadow.shadow_order_id(apply_key=ak, symbol=_sym(), order_kind="target_buy",
                                 trigger_bar="2026-07-21", ordinal=0)
    oid2 = shadow.shadow_order_id(apply_key=ak2, symbol=_sym(), order_kind="target_buy",
                                  trigger_bar="2026-07-21", ordinal=0)
    assert oid == oid2
    assert shadow.shadow_fill_id(order_id=oid, fill_seq=1) == \
        shadow.shadow_fill_id(order_id=oid2, fill_seq=1)


# --------------------------------------------------------------------------- #
# wrap_proposal_as_intent — invariant 1 no caller times + happy path            #
# --------------------------------------------------------------------------- #
def test_wrap_accepts_no_caller_supplied_times():
    # invariant 1: there is no public constructor path that accepts caller-supplied
    # scheduled_for / eligible_execution_at — both are computed inside wrap.
    params = inspect.signature(shadow.wrap_proposal_as_intent).parameters
    assert "scheduled_for" not in params
    assert "eligible_execution_at" not in params
    # and the wrap function is keyword-only (the sole disciplined constructor path).
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in params.values())


def test_wrap_happy_path_assembles_full_envelope():
    art = _proposal_artifact()
    sched = _schedule()
    reg, ref = _registry_with(sched)
    req = _request(ref=ref)
    clock = _FixedClock(datetime(2026, 7, 20, 2, 0, tzinfo=UTC))
    intent = shadow.wrap_proposal_as_intent(
        proposal_artifact=art,
        source_decision_artifact_id="dec-art-1",
        request=req,
        schedule_registry=reg,
        calendar=_calendar(),
        session_date="2026-07-20",
        decision_as_of=_SCHED_UTC,
        target_version=3,
        intent_id="intent-xyz",
        clock=clock,
    )
    assert isinstance(intent, shadow.TargetPortfolioIntent)
    # structural zero-trading envelope
    assert (intent.origin, intent.authority, intent.execution_scope) == \
        ("LLM", "ADVISORY_ONLY", "SHADOW_ONLY")
    # runtime-argument provenance
    assert intent.intent_id == "intent-xyz"
    assert intent.target_version == 3
    assert intent.source_decision_artifact_id == "dec-art-1"
    assert intent.proposal_artifact_id == art.artifact_id
    assert intent.proposal_digest == art.content_digest
    # schedule triple from the resolved schedule
    assert intent.decision_schedule_id == sched.id
    assert intent.decision_schedule_version == sched.version
    assert intent.decision_schedule_digest == sched.content_digest
    # computed instants (never caller-supplied)
    assert intent.scheduled_for == _SCHED_UTC
    assert intent.eligible_execution_at == _ELIGIBLE_UTC
    assert intent.decision_as_of == _SCHED_UTC
    # created_at from the clock
    assert intent.created_at == clock.now()


def test_wrap_copies_positions_cash_rationale_confidence_verbatim():
    payload = _proposal([_pos(0.75, code="000001", exchange="SZ")], 0.25,
                        rationale="single-name thesis", confidence=Confidence.HIGH)
    intent = _wrap(proposal_artifact=_proposal_artifact(payload=payload))
    assert intent.positions == payload.positions
    assert intent.cash_weight == payload.cash_weight
    assert intent.rationale == payload.rationale
    assert intent.confidence == payload.confidence


def test_wrap_copies_entry_tranches_verbatim():
    # verbatim includes each position's entry_tranches (Task 1b) — the intent layer
    # can never become a bypass around the band/tranche constraints.
    tranche = shadow.TrancheTrigger(price_low=10.0, price_high=12.0, fraction=0.5)
    payload = _proposal([_pos(0.5, entry_tranches=(tranche,))], 0.5)
    intent = _wrap(proposal_artifact=_proposal_artifact(payload=payload))
    assert intent.positions[0].entry_tranches == (tranche,)


def test_wrap_next_bar_close_execution_anchor():
    sched = _schedule(execution_policy="next_bar_close", execution_price_field="close")
    intent = _wrap(schedule=sched)
    assert intent.eligible_execution_at == datetime(2026, 7, 21, 7, 0, tzinfo=UTC)  # 15:00 CST


# --------------------------------------------------------------------------- #
# wrap — invariant 2 refusal matrix (each distinct ShadowEnvelopeError text)     #
# --------------------------------------------------------------------------- #
def test_wrap_refuses_request_without_schedule_ref():
    req = _request(ref=None)  # no decision_schedule_ref
    with pytest.raises(shadow.ShadowEnvelopeError) as ei:
        _wrap(schedule=_schedule(), registry=_registry_with(_schedule())[0], request=req)
    assert "schedule" in str(ei.value).lower()


def test_wrap_refuses_unregistered_schedule():
    sched = _schedule()
    empty_reg = shadow.DecisionScheduleRegistry()  # never registered
    ref = ContentRef(id=sched.id, version=sched.version, content_digest=sched.content_digest)
    with pytest.raises(shadow.ShadowEnvelopeError) as ei:
        _wrap(registry=empty_reg, request=_request(ref=ref))
    assert "no schedule registered" in str(ei.value)


def test_wrap_refuses_stale_schedule_digest():
    sched = _schedule()
    reg, _ = _registry_with(sched)
    stale = ContentRef(id=sched.id, version=sched.version, content_digest="0" * 64)
    with pytest.raises(shadow.ShadowEnvelopeError) as ei:
        _wrap(registry=reg, request=_request(ref=stale))
    assert "stale" in str(ei.value).lower() or "does not match" in str(ei.value).lower()


def test_wrap_refuses_non_decision_point_session_date():
    sched = _schedule(kind="weekly", weekdays=(3,))  # Wednesday only
    reg, ref = _registry_with(sched)
    with pytest.raises(shadow.ShadowEnvelopeError) as ei:
        _wrap(schedule=sched, registry=reg, request=_request(ref=ref),
              session_date="2026-07-20")  # Monday
    assert "decision point" in str(ei.value).lower()
    assert "no schedule registered" not in str(ei.value)


def test_wrap_refuses_decision_as_of_before_cutoff():
    with pytest.raises(shadow.ShadowEnvelopeError) as ei:
        _wrap(decision_as_of=_CUTOFF_UTC - timedelta(seconds=1))
    assert "cutoff" in str(ei.value).lower()


def test_wrap_accepts_decision_as_of_exactly_at_cutoff():
    # the ruled ordering is cutoff <= decision_as_of, so equality passes.
    intent = _wrap(decision_as_of=_CUTOFF_UTC)
    assert intent.decision_as_of == _CUTOFF_UTC


def test_wrap_refuses_wrong_payload_schema_ref():
    art = _proposal_artifact(schema_version="2")  # PortfolioTargetProposal@2, wrong key
    with pytest.raises(shadow.ShadowEnvelopeError) as ei:
        _wrap(proposal_artifact=art)
    assert "PortfolioTargetProposal@1" in str(ei.value)


def test_wrap_refuses_payload_that_does_not_revalidate_as_proposal():
    # schema ref names PortfolioTargetProposal@1 but the payload is NOT a proposal.
    art = _proposal_artifact(payload=_pos(0.5))  # a TargetPosition, not a proposal
    with pytest.raises(shadow.ShadowEnvelopeError):
        _wrap(proposal_artifact=art)


def test_wrap_refusal_reason_texts_are_pairwise_distinct():
    # invariant 2: each refusal path carries distinct reason text.
    sched = _schedule()
    weekly = _schedule(kind="weekly", weekdays=(3,))
    messages: list[str] = []

    def _capture(fn):
        with pytest.raises(shadow.ShadowEnvelopeError) as ei:
            fn()
        messages.append(str(ei.value))

    # no schedule ref
    _capture(lambda: _wrap(registry=_registry_with(sched)[0], request=_request(ref=None)))
    # unregistered
    _capture(lambda: _wrap(
        registry=shadow.DecisionScheduleRegistry(),
        request=_request(ref=ContentRef(id=sched.id, version=sched.version,
                                        content_digest=sched.content_digest))))
    # stale digest
    reg, _ = _registry_with(sched)
    _capture(lambda: _wrap(
        registry=reg,
        request=_request(ref=ContentRef(id=sched.id, version=sched.version,
                                        content_digest="0" * 64))))
    # non-decision-point
    wreg, wref = _registry_with(weekly)
    _capture(lambda: _wrap(schedule=weekly, registry=wreg, request=_request(ref=wref),
                           session_date="2026-07-20"))
    # decision_as_of before cutoff
    _capture(lambda: _wrap(decision_as_of=_CUTOFF_UTC - timedelta(seconds=1)))

    assert len(set(messages)) == len(messages) == 5


# --------------------------------------------------------------------------- #
# invariant 1 tail — a proposal payload can never carry an envelope key          #
# --------------------------------------------------------------------------- #
def test_proposal_payload_cannot_smuggle_an_envelope_key():
    with pytest.raises(ValidationError) as ei:
        shadow.PortfolioTargetProposal(
            positions=(_pos(0.5),), cash_weight=0.5, rationale="x",
            confidence=Confidence.LOW, scheduled_for="smuggled",
        )
    assert "extra_forbidden" in _error_types(ei.value)
