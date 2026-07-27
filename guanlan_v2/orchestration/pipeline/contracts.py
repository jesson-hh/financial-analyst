# -*- coding: utf-8 -*-
"""Phase 10 · Task 1 — the public product-pipeline contracts.

This module is the **contract layer only** — every class is a strict, frozen,
``extra="forbid"`` :class:`~guanlan_v2.orchestration.digest.DigestModel`, carries
no runtime logic, and re-uses the frozen Phase 1-9 primitives verbatim
(``TypedPayloadRef`` / the strict ``digest`` types / the Phase 3
``normalize_symbol`` grammar). It defines **no** upstream payload
(``RotationReport`` / ``ResearchPlan`` / ``PortfolioDecision`` are imported to
pin their schema identity, never re-declared here).

Five registered public models plus three internal carriers:

* ``CandidateSlate@1`` (+ ``CandidateEntry``) — the deterministic candidate
  output of the three ``cand.*`` workers. The source-kind biconditionals
  (``lane0`` ⇔ a pinned ``RotationReport@1`` ref; ``model_variant`` ⇔ a named
  ``variant_id``) are structural, as are ``len(entries) <= top_n``, strictly
  increasing ranks and unique codes. An empty slate is legal (无候选=诚实).
* ``RecommendationSlate@1`` (+ ``RecommendationEntry``) — the advisory 选股
  product. ``portfolio_decision_ref`` is v1-always-``None`` with the mandatory
  ``no_cross_sectional_summary_v1`` badge (the ``dec.pm`` 全场裁决 is deferred
  per ratified D3's single-code context convention; the field stays so a future
  cross-sectional summary lands without a schema bump).
* ``EscalationReport@1`` (+ ``EscalationTrigger``) — the zero-LLM 落子 escalation
  verdict. ``escalate`` ⇔ ``triggers_hit`` non-empty is structural; an absent
  context port is named in ``inert_ports`` rather than guessed.
* ``RunSubject@1`` — Amendment 2's minimal subject carrier
  (``{schema_version, code, as_of}``): committed at request creation and bound
  to a plan via the Phase 1 ``InputArtifactBinding`` machinery. It is the ONLY
  sanctioned way a Phase 10 plan names its stock — the handoff gate pinned that
  no upstream structural carrier exists.
* ``TaSubmission@1`` — the D7 external-TA inbox receipt. ``author`` is mandatory
  (source attribution); the submitted text itself is untrusted data and never
  lives here — only its digest.

Registration is per-consumer: the cumulative Phase-10 registry / catalog chain
node (and its goldens) is Task 11's, not this module's.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import field_validator, model_validator

from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.digest import (
    ContractModel,
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
)
from guanlan_v2.orchestration.market.factors import RotationReport
from guanlan_v2.orchestration.refs import TypedPayloadRef
from guanlan_v2.orchestration.schemas import PortfolioDecision, ResearchPlan

__all__ = [
    "CandidateEntry",
    "CandidateSlate",
    "RecommendationEntry",
    "RecommendationSlate",
    "EscalationTrigger",
    "EscalationReport",
    "RunSubject",
    "TaSubmission",
]

#: the closed candidate-source vocabulary (the three ``cand.*`` workers).
CandidateSourceKind = Literal["v4", "lane0", "model_variant"]
#: the closed escalation-trigger vocabulary (ruling R-C; zero-LLM judge).
EscalationTriggerKind = Literal[
    "direction_flip",
    "stop_take_proximity",
    "event_wordlist",
    "pattern_hit",
    "opt_in",
]
#: the mandatory v1 badge naming the deferred cross-sectional ``dec.pm`` summary.
NO_CROSS_SECTIONAL_SUMMARY_BADGE = "no_cross_sectional_summary_v1"


# --------------------------------------------------------------------------- #
# shared field/ref helpers                                                     #
# --------------------------------------------------------------------------- #
def _normalized_code(raw: str) -> str:
    # Every ``code`` field in this module is validated through the Phase 3
    # syntactic grammar and stored in its canonical six-digit form, so a slate
    # can never carry the same stock twice under two input grammars and an
    # industry / concept term (e.g. 白酒) is refused rather than guessed.
    return normalize_symbol(raw).code


def _schema_key(model: type[ContractModel]) -> str:
    return f"{model.__name__}@{model.model_fields['schema_version'].default}"


def _require_pinned_schema(
    ref: TypedPayloadRef, model: type[ContractModel], *, field_name: str
) -> None:
    # A pinned typed ref may only name the exact registered schema@version of
    # ``model`` — a consumer re-validates the payload against it instead of
    # guessing a schema from the object id.
    expected_name = model.__name__
    expected_version = model.model_fields["schema_version"].default
    if (
        ref.schema_ref.name != expected_name
        or ref.schema_ref.version != expected_version
    ):
        raise ValueError(
            f"{field_name}.schema_ref must name "
            f"{expected_name}@{expected_version}, got {ref.schema_ref.key}"
        )


# =========================================================================== #
# CandidateEntry / CandidateSlate                                              #
# =========================================================================== #
class CandidateEntry(DigestModel):
    # One ranked candidate of a slate (internal carrier; nested, no
    # schema_version — it is never independently resolved by a SchemaRef).
    # ``score`` is the producing surface's raw score when it has one and an
    # explicit ``None`` when it does not (never a fabricated 0.0);
    # ``source_note`` is the optional human-readable provenance line.
    code: NonEmptyStr
    rank: PositiveInt
    score: FiniteFloat | None = None
    source_note: NonEmptyStr | None = None

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return _normalized_code(v)


class CandidateSlate(DigestModel):
    # The registered ``CandidateSlate@1`` product of a ``cand.*`` worker.
    #
    # ``entries`` is at most ``top_n`` long, strictly increasing in ``rank`` and
    # duplicate-code-free; an EMPTY slate is legal and honest (无候选 is a real
    # result, not an error). ``source_artifact_digest`` binds the upstream
    # ranking artifact when there is one.
    #
    # Two structural biconditionals encode ruling R-A's source discipline:
    # a ``lane0`` slate MUST name the ``RotationReport@1`` it was derived from
    # (and no other kind may), and a ``model_variant`` slate MUST name its
    # ``variant_id`` (and no other kind may) — so a slate can never claim a
    # provenance it does not carry, nor carry evidence its kind cannot explain.
    schema_version: Literal["1"] = "1"
    source_kind: CandidateSourceKind
    as_of: UtcDateTime
    top_n: PositiveInt
    entries: tuple[CandidateEntry, ...]
    source_artifact_digest: DigestHex | None = None
    rotation_report_ref: TypedPayloadRef | None = None
    variant_id: NonEmptyStr | None = None
    badges: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _verify(self) -> "CandidateSlate":
        if len(self.entries) > self.top_n:
            raise ValueError(
                f"entries must be at most top_n long (got {len(self.entries)} "
                f"entries for top_n={self.top_n})"
            )
        for prev, cur in zip(self.entries, self.entries[1:]):
            if cur.rank <= prev.rank:
                raise ValueError(
                    "entries must be strictly increasing in rank "
                    f"(got {cur.rank} <= {prev.rank})"
                )
        codes = [e.code for e in self.entries]
        if len(set(codes)) != len(codes):
            raise ValueError("entries must not contain a duplicate code")

        has_rotation = self.rotation_report_ref is not None
        if self.source_kind == "lane0":
            if not has_rotation:
                raise ValueError(
                    "source_kind 'lane0' requires a rotation_report_ref"
                )
        elif has_rotation:
            raise ValueError(
                f"source_kind {self.source_kind!r} must not carry a "
                "rotation_report_ref"
            )
        if self.rotation_report_ref is not None:
            _require_pinned_schema(
                self.rotation_report_ref,
                RotationReport,
                field_name="rotation_report_ref",
            )

        has_variant = self.variant_id is not None
        if self.source_kind == "model_variant":
            if not has_variant:
                raise ValueError(
                    "source_kind 'model_variant' requires a variant_id"
                )
        elif has_variant:
            raise ValueError(
                f"source_kind {self.source_kind!r} must not carry a variant_id"
            )
        return self


# =========================================================================== #
# RecommendationEntry / RecommendationSlate                                    #
# =========================================================================== #
class RecommendationEntry(DigestModel):
    # One per-stock recommendation (internal carrier; nested, no schema_version).
    # ``rating`` is COPIED VERBATIM from the lane's committed ``ResearchPlan``
    # and never re-derived here; ``research_plan_ref`` pins that exact plan so a
    # reader can re-validate the rating against its source.
    code: NonEmptyStr
    lane_index: NonNegativeInt
    rating: NonEmptyStr
    research_plan_ref: TypedPayloadRef

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return _normalized_code(v)

    @model_validator(mode="after")
    def _verify(self) -> "RecommendationEntry":
        _require_pinned_schema(
            self.research_plan_ref, ResearchPlan, field_name="research_plan_ref"
        )
        return self


class RecommendationSlate(DigestModel):
    # The registered ``RecommendationSlate@1`` — the advisory 选股 product of one
    # screening batch. ``candidate_slate_ref`` pins the ``CandidateSlate@1`` the
    # batch was built from; ``degraded_lanes`` names the candidate lane indexes
    # that produced no committed plan (honest degradation, never a silent drop).
    #
    # ``portfolio_decision_ref`` is the deferred cross-sectional ``dec.pm``
    # 全场裁决 slot. In v1 it is ALWAYS ``None`` and the
    # ``no_cross_sectional_summary_v1`` badge is mandatory (ratified D3: the
    # single-code context convention makes a whole-field decision structurally
    # unavailable). The field is retained — pinned to ``PortfolioDecision@1`` —
    # so a future version can open it without a schema bump; until then the
    # stricter ``is None`` rule subsumes the pin, so no unreachable pin check is
    # written here (the pinned key is named in the refusal message instead).
    #
    # ``badges`` deliberately has NO default: the only valid value must contain
    # the mandatory badge, so an empty default would be a trap that can never
    # validate.
    schema_version: Literal["1"] = "1"
    as_of: UtcDateTime
    batch_id: NonEmptyStr
    candidate_slate_ref: TypedPayloadRef
    entries: tuple[RecommendationEntry, ...]
    portfolio_decision_ref: TypedPayloadRef | None = None
    degraded_lanes: tuple[NonNegativeInt, ...] = ()
    badges: tuple[NonEmptyStr, ...]

    @model_validator(mode="after")
    def _verify(self) -> "RecommendationSlate":
        _require_pinned_schema(
            self.candidate_slate_ref,
            CandidateSlate,
            field_name="candidate_slate_ref",
        )
        if self.portfolio_decision_ref is not None:
            raise ValueError(
                "portfolio_decision_ref must be None in v1: the cross-sectional "
                f"{_schema_key(PortfolioDecision)} 全场裁决 is deferred "
                f"(badge {NO_CROSS_SECTIONAL_SUMMARY_BADGE!r}); the field is "
                "retained so a future summary lands without a schema bump"
            )
        if NO_CROSS_SECTIONAL_SUMMARY_BADGE not in self.badges:
            raise ValueError(
                f"badges must contain {NO_CROSS_SECTIONAL_SUMMARY_BADGE!r} — a v1 "
                "slate always declares that the cross-sectional summary is deferred"
            )
        return self


# =========================================================================== #
# EscalationTrigger / EscalationReport                                         #
# =========================================================================== #
class EscalationTrigger(DigestModel):
    # One fired escalation trigger (internal carrier; nested, no schema_version).
    # ``detail`` is the deterministic, human-readable reason the judge computed
    # — never an LLM narrative.
    kind: EscalationTriggerKind
    detail: NonEmptyStr


class EscalationReport(DigestModel):
    # The registered ``EscalationReport@1`` — the pure, zero-LLM verdict of the
    # 落子 escalation judge for one stock at one instant.
    #
    # ``escalate`` ⇔ ``triggers_hit`` non-empty is structural: the report can
    # never promote a judgment without naming why, nor list reasons it then
    # ignores. ``thresholds_version`` closes the reviewed threshold vocabulary
    # so a verdict always states which thresholds produced it. ``inert_ports``
    # names each absent context port whose trigger therefore could not fire
    # (ruling R-C: an absent port is a badge, never a guess).
    schema_version: Literal["1"] = "1"
    code: NonEmptyStr
    as_of: UtcDateTime
    thresholds_version: Literal["escalation-v1"] = "escalation-v1"
    triggers_hit: tuple[EscalationTrigger, ...]
    escalate: bool
    inert_ports: tuple[NonEmptyStr, ...] = ()

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return _normalized_code(v)

    @model_validator(mode="after")
    def _verify(self) -> "EscalationReport":
        if self.escalate and not self.triggers_hit:
            raise ValueError(
                "escalate=True requires a non-empty triggers_hit (a promotion "
                "must name why)"
            )
        if not self.escalate and self.triggers_hit:
            raise ValueError(
                "escalate=False must carry an empty triggers_hit (a fired "
                "trigger is never ignored)"
            )
        return self


# =========================================================================== #
# RunSubject (Amendment 2)                                                     #
# =========================================================================== #
class RunSubject(DigestModel):
    # The registered ``RunSubject@1`` — the pipeline's minimal subject carrier.
    #
    # Committed as an input artifact at request creation and bound to a plan via
    # the Phase 1 ``InputArtifactBinding`` machinery; it is the ONLY sanctioned
    # way a Phase 10 plan names its stock (the handoff gate pinned that the
    # kernel carries no structural subject-code field anywhere, and free-text
    # goal parsing is forbidden). Kept EXACTLY minimal — ``{schema_version,
    # code, as_of}`` — so one code-agnostic sealed preset serves every stock and
    # the subject's own digest is the whole of its identity.
    schema_version: Literal["1"] = "1"
    code: NonEmptyStr
    as_of: UtcDateTime

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return _normalized_code(v)


# =========================================================================== #
# TaSubmission (D7)                                                            #
# =========================================================================== #
class TaSubmission(DigestModel):
    # The registered ``TaSubmission@1`` — the receipt of one external technical-
    # analysis inbox submission.
    #
    # ``author`` is MANDATORY (D7 source attribution: an unattributed submission
    # is refused, never anonymized). The submitted text is untrusted data and
    # never lives on this contract — only its ``text_digest``, so the receipt can
    # be compared and audited without re-exposing the text. ``status`` is a
    # closed single-valued literal: this phase only ever RECEIVES (pv.curator
    # consumes the inbox in its own later phase; no LLM, no processing here).
    #
    # ``submitted_at`` is the audit wall-clock (``SEMANTIC_EXCLUDE``), matching
    # the Phase 1 ``created_at`` / ``issued_at`` / ``committed_at`` precedent: the
    # semantic identity of a submission is its author + title + content, so the
    # same text submitted twice by the same author is the same submission.
    schema_version: Literal["1"] = "1"
    author: NonEmptyStr
    title: NonEmptyStr | None = None
    submitted_at: UtcDateTime
    text_digest: DigestHex
    status: Literal["received"] = "received"

    SEMANTIC_EXCLUDE: ClassVar[frozenset[str]] = frozenset({"submitted_at"})
