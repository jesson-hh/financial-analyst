# -*- coding: utf-8 -*-
"""Phase 10 · Task 1 — the public pipeline contracts (candidate slate /
recommendation slate / escalation report / run subject / TA submission).

Written test-first (RED until ``guanlan_v2.orchestration.pipeline.contracts``
and its classes exist).

These pin, per the Task-1 brief:

* the field surface + validation matrix of every Phase-10 public contract —
  the ``lane0 ⇔ rotation_report_ref`` and ``model_variant ⇔ variant_id``
  biconditionals, ``len(entries) <= top_n`` + strictly-increasing ranks +
  unique codes, the ``escalate ⇔ triggers_hit`` biconditional, the mandatory
  D7 ``author``, the v1 ``portfolio_decision_ref is None`` +
  ``no_cross_sectional_summary_v1`` badge invariant, and the
  ``TypedPayloadRef`` schema pins;
* Phase 3 ``normalize_symbol`` validation of every ``code`` field (a canonical
  six-digit code out, an industry/concept name or partial code refused);
* digest determinism (two identical constructions → equal semantic digest);
* projection exclusions (``TaSubmission.submitted_at`` is the only wall-clock
  field — moving it never moves the semantic digest; every ``as_of`` IS
  semantic);
* registration round-trip through a FRESH :class:`SchemaRegistry` (the
  cumulative Phase-10 chain node is Task 11, not this task).

Run from repo root:
``python -m pytest tests/orchestration/test_pipeline_contracts.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.pipeline import contracts as C
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef
from guanlan_v2.orchestration.schema_registry import (
    SchemaRegistry,
    SchemaRegistryError,
    UnknownSchemaError,
)


# --------------------------------------------------------------------------- #
# small typed constructors                                                    #
# --------------------------------------------------------------------------- #
_UTC = timezone.utc
_T0 = datetime(2026, 7, 27, 1, 30, 0, tzinfo=_UTC)
_T1 = datetime(2026, 7, 28, 1, 30, 0, tzinfo=_UTC)
_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64
_HEX_D = "d" * 64

#: the five registered Phase-10 public models and their expected registry keys.
_PUBLIC_MODELS_AND_KEYS = (
    ("CandidateSlate", "1"),
    ("RecommendationSlate", "1"),
    ("EscalationReport", "1"),
    ("RunSubject", "1"),
    ("TaSubmission", "1"),
)


def _payload_ref(digest: str = _HEX_A, *, namespace: str = "main",
                 object_id: str = "obj-1") -> PayloadRef:
    return PayloadRef(namespace=namespace, object_id=object_id,
                      content_digest=digest)


def _typed_ref(name: str, *, version: str = "1", digest: str = _HEX_A,
               object_id: str = "obj-1") -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=name, version=version),
        payload_ref=_payload_ref(digest, object_id=object_id),
    )


def _entry(code: str = "600519", rank: int = 1, **over):
    fields = dict(code=code, rank=rank, score=0.91, source_note="v4 rank 1")
    fields.update(over)
    return C.CandidateEntry(**fields)


def _slate(**over):
    fields = dict(
        source_kind="v4",
        as_of=_T0,
        top_n=3,
        entries=(_entry("600519", 1), _entry("000001", 2)),
        source_artifact_digest=_HEX_A,
        rotation_report_ref=None,
        variant_id=None,
        badges=(),
    )
    fields.update(over)
    return C.CandidateSlate(**fields)


def _rec_entry(code: str = "600519", lane_index: int = 0, **over):
    fields = dict(
        code=code,
        lane_index=lane_index,
        rating="BUY",
        research_plan_ref=_typed_ref("ResearchPlan", digest=_HEX_B),
    )
    fields.update(over)
    return C.RecommendationEntry(**fields)


def _rec_slate(**over):
    fields = dict(
        as_of=_T0,
        batch_id="batch-" + _HEX_A[:8],
        candidate_slate_ref=_typed_ref("CandidateSlate", digest=_HEX_C),
        entries=(_rec_entry("600519", 0), _rec_entry("000001", 1)),
        portfolio_decision_ref=None,
        degraded_lanes=(),
        badges=("no_cross_sectional_summary_v1",),
    )
    fields.update(over)
    return C.RecommendationSlate(**fields)


def _trigger(kind: str = "direction_flip", detail: str = "fast=buy deep=sell"):
    return C.EscalationTrigger(kind=kind, detail=detail)


def _report(**over):
    fields = dict(
        code="600519",
        as_of=_T0,
        triggers_hit=(_trigger(),),
        escalate=True,
        inert_ports=(),
    )
    fields.update(over)
    return C.EscalationReport(**fields)


def _subject(**over):
    fields = dict(code="600519", as_of=_T0)
    fields.update(over)
    return C.RunSubject(**fields)


def _submission(**over):
    fields = dict(
        author="张三",
        title="龙虎榜形态复盘",
        submitted_at=_T0,
        text_digest=_HEX_D,
    )
    fields.update(over)
    return C.TaSubmission(**fields)


# =========================================================================== #
# CandidateEntry / CandidateSlate                                              #
# =========================================================================== #
class TestCandidateSlate:
    def test_valid_v4_slate_roundtrips(self):
        s = _slate()
        assert s.schema_version == "1"
        assert s.source_kind == "v4"
        assert s.as_of == _T0
        assert s.top_n == 3
        assert len(s.entries) == 2
        assert s.entries[0].code == "600519"
        assert s.entries[0].rank == 1
        assert s.entries[0].score == pytest.approx(0.91)
        assert s.entries[0].source_note == "v4 rank 1"
        assert s.source_artifact_digest == _HEX_A
        assert s.rotation_report_ref is None
        assert s.variant_id is None
        assert s.badges == ()

    def test_slate_is_frozen_and_closed(self):
        s = _slate()
        with pytest.raises(ValidationError):
            s.top_n = 9
        with pytest.raises(ValidationError):
            _slate(bogus_field=1)

    def test_entry_is_frozen_and_closed(self):
        e = _entry()
        with pytest.raises(ValidationError):
            e.rank = 7
        with pytest.raises(ValidationError):
            C.CandidateEntry(code="600519", rank=1, score=None,
                             source_note=None, bogus=1)

    def test_entry_optional_fields_may_be_none(self):
        e = C.CandidateEntry(code="600519", rank=1, score=None, source_note=None)
        assert e.score is None and e.source_note is None

    def test_entry_code_is_normalized_through_phase3(self):
        assert _entry("600519.SH").code == "600519"
        assert _entry("SH600519").code == "600519"
        assert _entry("300750").code == "300750"

    @pytest.mark.parametrize("bad", ["白酒", "60051", "600519X", "600519.SZ", ""])
    def test_entry_refuses_a_non_symbol_code(self, bad):
        with pytest.raises(ValidationError):
            _entry(bad)

    def test_entry_rejects_a_non_positive_rank(self):
        with pytest.raises(ValidationError):
            _entry("600519", 0)

    def test_empty_entries_is_allowed(self):
        assert _slate(entries=()).entries == ()

    def test_entries_may_equal_top_n(self):
        s = _slate(top_n=2, entries=(_entry("600519", 1), _entry("000001", 2)))
        assert len(s.entries) == s.top_n

    def test_entries_longer_than_top_n_refused(self):
        with pytest.raises(ValidationError, match="top_n"):
            _slate(top_n=1, entries=(_entry("600519", 1), _entry("000001", 2)))

    @pytest.mark.parametrize("ranks", [(1, 1), (2, 1), (3, 3)])
    def test_non_increasing_ranks_refused(self, ranks):
        entries = (_entry("600519", ranks[0]), _entry("000001", ranks[1]))
        with pytest.raises(ValidationError, match="increasing"):
            _slate(entries=entries)

    def test_strictly_increasing_non_contiguous_ranks_allowed(self):
        s = _slate(entries=(_entry("600519", 1), _entry("000001", 5)))
        assert [e.rank for e in s.entries] == [1, 5]

    def test_duplicate_codes_refused(self):
        with pytest.raises(ValidationError, match="duplicate"):
            _slate(entries=(_entry("600519", 1), _entry("600519", 2)))

    def test_duplicate_codes_refused_across_symbol_grammars(self):
        # both normalize to 600519 — the same stock in two input forms.
        with pytest.raises(ValidationError, match="duplicate"):
            _slate(entries=(_entry("600519.SH", 1), _entry("SH600519", 2)))

    # -- source_kind biconditionals ---------------------------------------- #
    def test_lane0_requires_a_rotation_report_ref(self):
        with pytest.raises(ValidationError, match="rotation_report_ref"):
            _slate(source_kind="lane0")

    def test_lane0_with_rotation_report_ref_ok(self):
        s = _slate(source_kind="lane0",
                   rotation_report_ref=_typed_ref("RotationReport"))
        assert s.rotation_report_ref is not None

    def test_non_lane0_must_not_carry_a_rotation_report_ref(self):
        with pytest.raises(ValidationError, match="rotation_report_ref"):
            _slate(source_kind="v4",
                   rotation_report_ref=_typed_ref("RotationReport"))

    def test_model_variant_requires_a_variant_id(self):
        with pytest.raises(ValidationError, match="variant_id"):
            _slate(source_kind="model_variant")

    def test_model_variant_with_variant_id_ok(self):
        s = _slate(source_kind="model_variant", variant_id="m_6dce0995a4")
        assert s.variant_id == "m_6dce0995a4"

    def test_non_model_variant_must_not_carry_a_variant_id(self):
        with pytest.raises(ValidationError, match="variant_id"):
            _slate(source_kind="v4", variant_id="m_6dce0995a4")

    def test_source_kind_vocabulary_is_closed(self):
        with pytest.raises(ValidationError):
            _slate(source_kind="v5")

    # -- typed ref pin ------------------------------------------------------ #
    def test_rotation_report_ref_must_name_rotation_report_v1(self):
        with pytest.raises(ValidationError, match="RotationReport@1"):
            _slate(source_kind="lane0",
                   rotation_report_ref=_typed_ref("RegimeReport"))

    def test_rotation_report_ref_wrong_version_refused(self):
        with pytest.raises(ValidationError, match="RotationReport@1"):
            _slate(source_kind="lane0",
                   rotation_report_ref=_typed_ref("RotationReport", version="2"))

    def test_source_artifact_digest_may_be_none(self):
        assert _slate(source_artifact_digest=None).source_artifact_digest is None

    def test_naive_as_of_refused(self):
        with pytest.raises(ValidationError):
            _slate(as_of=datetime(2026, 7, 27, 1, 30, 0))


# =========================================================================== #
# RecommendationEntry / RecommendationSlate                                    #
# =========================================================================== #
class TestRecommendationSlate:
    def test_valid_slate_roundtrips(self):
        s = _rec_slate()
        assert s.schema_version == "1"
        assert s.as_of == _T0
        assert s.batch_id.startswith("batch-")
        assert s.candidate_slate_ref.schema_ref.key == "CandidateSlate@1"
        assert len(s.entries) == 2
        assert s.entries[0].code == "600519"
        assert s.entries[0].lane_index == 0
        assert s.entries[0].rating == "BUY"
        assert s.entries[0].research_plan_ref.schema_ref.key == "ResearchPlan@1"
        assert s.portfolio_decision_ref is None
        assert s.degraded_lanes == ()
        assert s.badges == ("no_cross_sectional_summary_v1",)

    def test_slate_is_frozen_and_closed(self):
        s = _rec_slate()
        with pytest.raises(ValidationError):
            s.batch_id = "other"
        with pytest.raises(ValidationError):
            _rec_slate(bogus_field=1)

    def test_entry_code_is_normalized_through_phase3(self):
        assert _rec_entry("600519.SH").code == "600519"

    @pytest.mark.parametrize("bad", ["白酒", "12345", "600519 600520"])
    def test_entry_refuses_a_non_symbol_code(self, bad):
        with pytest.raises(ValidationError):
            _rec_entry(bad)

    def test_entry_rating_must_be_non_blank(self):
        with pytest.raises(ValidationError):
            _rec_entry(rating="   ")

    def test_entry_research_plan_ref_must_name_research_plan_v1(self):
        with pytest.raises(ValidationError, match="ResearchPlan@1"):
            _rec_entry(research_plan_ref=_typed_ref("PortfolioDecision"))

    def test_entry_research_plan_ref_is_mandatory(self):
        with pytest.raises(ValidationError):
            C.RecommendationEntry(code="600519", lane_index=0, rating="BUY")

    def test_candidate_slate_ref_must_name_candidate_slate_v1(self):
        with pytest.raises(ValidationError, match="CandidateSlate@1"):
            _rec_slate(candidate_slate_ref=_typed_ref("RotationReport"))

    def test_empty_entries_and_degraded_lanes_allowed(self):
        s = _rec_slate(entries=(), degraded_lanes=(0, 1))
        assert s.entries == () and s.degraded_lanes == (0, 1)

    def test_negative_degraded_lane_refused(self):
        with pytest.raises(ValidationError):
            _rec_slate(degraded_lanes=(-1,))

    # -- the v1 None + mandatory-badge invariant ---------------------------- #
    def test_v1_portfolio_decision_ref_must_be_none(self):
        with pytest.raises(ValidationError, match="portfolio_decision_ref"):
            _rec_slate(portfolio_decision_ref=_typed_ref("PortfolioDecision"))

    def test_v1_refuses_even_a_correctly_pinned_portfolio_decision_ref(self):
        with pytest.raises(ValidationError,
                           match="no_cross_sectional_summary_v1|deferred"):
            _rec_slate(portfolio_decision_ref=_typed_ref("PortfolioDecision",
                                                         version="1"))

    def test_mandatory_badge_missing_refused(self):
        with pytest.raises(ValidationError,
                           match="no_cross_sectional_summary_v1"):
            _rec_slate(badges=())

    def test_mandatory_badge_alongside_other_badges_ok(self):
        s = _rec_slate(badges=("stale_snapshot", "no_cross_sectional_summary_v1"))
        assert "no_cross_sectional_summary_v1" in s.badges


# =========================================================================== #
# EscalationTrigger / EscalationReport                                         #
# =========================================================================== #
class TestEscalationReport:
    def test_valid_escalating_report_roundtrips(self):
        r = _report()
        assert r.schema_version == "1"
        assert r.code == "600519"
        assert r.as_of == _T0
        assert r.thresholds_version == "escalation-v1"
        assert r.escalate is True
        assert len(r.triggers_hit) == 1
        assert r.triggers_hit[0].kind == "direction_flip"
        assert r.inert_ports == ()

    def test_valid_non_escalating_report_roundtrips(self):
        r = _report(triggers_hit=(), escalate=False,
                    inert_ports=("event_wordlist", "pattern_hits"))
        assert r.escalate is False
        assert r.triggers_hit == ()
        assert r.inert_ports == ("event_wordlist", "pattern_hits")

    def test_escalate_true_without_triggers_refused(self):
        with pytest.raises(ValidationError, match="triggers_hit"):
            _report(triggers_hit=(), escalate=True)

    def test_escalate_false_with_triggers_refused(self):
        with pytest.raises(ValidationError, match="triggers_hit"):
            _report(triggers_hit=(_trigger(),), escalate=False)

    @pytest.mark.parametrize("kind", ["direction_flip", "stop_take_proximity",
                                      "event_wordlist", "pattern_hit", "opt_in"])
    def test_every_reviewed_trigger_kind_is_accepted(self, kind):
        r = _report(triggers_hit=(_trigger(kind=kind),))
        assert r.triggers_hit[0].kind == kind

    def test_trigger_kind_vocabulary_is_closed(self):
        with pytest.raises(ValidationError):
            _trigger(kind="vibes")

    def test_trigger_detail_must_be_non_blank(self):
        with pytest.raises(ValidationError):
            _trigger(detail="  ")

    def test_thresholds_version_defaults_and_is_closed(self):
        assert _report().thresholds_version == "escalation-v1"
        with pytest.raises(ValidationError):
            _report(thresholds_version="escalation-v2")

    def test_code_is_normalized_through_phase3(self):
        assert _report(code="SH600519").code == "600519"

    @pytest.mark.parametrize("bad", ["白酒", "6005190"])
    def test_refuses_a_non_symbol_code(self, bad):
        with pytest.raises(ValidationError):
            _report(code=bad)

    def test_report_is_frozen_and_closed(self):
        r = _report()
        with pytest.raises(ValidationError):
            r.escalate = False
        with pytest.raises(ValidationError):
            _report(bogus_field=1)


# =========================================================================== #
# RunSubject (Amendment 2)                                                     #
# =========================================================================== #
class TestRunSubject:
    def test_valid_subject_roundtrips(self):
        s = _subject()
        assert s.schema_version == "1"
        assert s.code == "600519"
        assert s.as_of == _T0

    def test_field_surface_stays_minimal(self):
        # Amendment 2: the subject carrier is EXACTLY {schema_version, code, as_of}.
        assert set(C.RunSubject.model_fields) == {"schema_version", "code", "as_of"}

    def test_code_is_normalized_through_phase3(self):
        assert _subject(code="600519.SH").code == "600519"
        assert _subject(code="SH600519").code == "600519"

    @pytest.mark.parametrize("bad", ["白酒", "宁德时代", "60051", "600519X",
                                     "600519,000001"])
    def test_normalize_symbol_refusal_is_surfaced(self, bad):
        with pytest.raises(ValidationError):
            _subject(code=bad)

    def test_naive_as_of_refused(self):
        with pytest.raises(ValidationError):
            _subject(as_of=datetime(2026, 7, 27, 1, 30, 0))

    def test_subject_is_frozen_and_closed(self):
        s = _subject()
        with pytest.raises(ValidationError):
            s.code = "000001"
        with pytest.raises(ValidationError):
            _subject(bogus_field=1)


# =========================================================================== #
# TaSubmission (D7)                                                            #
# =========================================================================== #
class TestTaSubmission:
    def test_valid_submission_roundtrips(self):
        t = _submission()
        assert t.schema_version == "1"
        assert t.author == "张三"
        assert t.title == "龙虎榜形态复盘"
        assert t.submitted_at == _T0
        assert t.text_digest == _HEX_D
        assert t.status == "received"

    def test_title_may_be_none(self):
        assert _submission(title=None).title is None

    def test_author_is_mandatory(self):
        with pytest.raises(ValidationError):
            C.TaSubmission(submitted_at=_T0, text_digest=_HEX_D)

    @pytest.mark.parametrize("bad", ["", "   ", "\n"])
    def test_blank_author_refused(self, bad):
        with pytest.raises(ValidationError):
            _submission(author=bad)

    def test_status_defaults_to_received_and_is_closed(self):
        assert _submission().status == "received"
        with pytest.raises(ValidationError):
            _submission(status="processed")

    def test_text_digest_must_be_sha256_hex(self):
        with pytest.raises(ValidationError):
            _submission(text_digest="not-a-digest")

    def test_naive_submitted_at_refused(self):
        with pytest.raises(ValidationError):
            _submission(submitted_at=datetime(2026, 7, 27, 1, 30, 0))

    def test_submission_is_frozen_and_closed(self):
        t = _submission()
        with pytest.raises(ValidationError):
            t.author = "李四"
        with pytest.raises(ValidationError):
            _submission(bogus_field=1)


# =========================================================================== #
# digest determinism                                                           #
# =========================================================================== #
class TestDigestDeterminism:
    @pytest.mark.parametrize("build", [_slate, _rec_slate, _report, _subject,
                                       _submission])
    def test_two_identical_constructions_share_a_semantic_digest(self, build):
        assert build().semantic_digest() == build().semantic_digest()

    def test_a_changed_semantic_field_moves_the_digest(self):
        assert _slate().semantic_digest() != _slate(top_n=4).semantic_digest()
        assert (_rec_slate().semantic_digest()
                != _rec_slate(batch_id="other").semantic_digest())
        assert (_report().semantic_digest()
                != _report(code="000001").semantic_digest())
        assert _subject().semantic_digest() != _subject(code="000001").semantic_digest()
        assert (_submission().semantic_digest()
                != _submission(author="李四").semantic_digest())

    def test_nested_entry_changes_move_the_slate_digest(self):
        other = _slate(entries=(_entry("600519", 1), _entry("000002", 2)))
        assert _slate().semantic_digest() != other.semantic_digest()

    def test_typed_ref_object_id_is_audit_only_inside_a_slate(self):
        # PayloadRef.object_id is audit identity (Phase 1): re-storing identical
        # content under a new object id must NOT move the parent semantic digest.
        a = _rec_slate()
        b = _rec_slate(candidate_slate_ref=_typed_ref("CandidateSlate",
                                                      digest=_HEX_C,
                                                      object_id="obj-999"))
        assert a.semantic_digest() == b.semantic_digest()
        assert a.audit_digest_value() != b.audit_digest_value()


# =========================================================================== #
# projection exclusions                                                        #
# =========================================================================== #
class TestProjectionExclusions:
    def test_ta_submission_submitted_at_is_audit_only(self):
        a = _submission()
        b = _submission(submitted_at=_T1)
        assert a.semantic_digest() == b.semantic_digest()
        assert a.audit_digest_value() != b.audit_digest_value()

    def test_ta_submission_declares_the_reviewed_exclude_set(self):
        assert C.TaSubmission.SEMANTIC_EXCLUDE == frozenset({"submitted_at"})

    @pytest.mark.parametrize("model", ["CandidateSlate", "RecommendationSlate",
                                       "EscalationReport", "RunSubject"])
    def test_the_other_publics_exclude_nothing(self, model):
        assert getattr(C, model).SEMANTIC_EXCLUDE == frozenset()

    @pytest.mark.parametrize("build", [_slate, _rec_slate, _report, _subject])
    def test_as_of_is_semantic_not_wall_clock(self, build):
        # as_of is the PIT boundary — it must NEVER be projected out.
        assert build().semantic_digest() != build(as_of=_T1).semantic_digest()

    @pytest.mark.parametrize("build", [_slate, _rec_slate, _report, _subject,
                                       _submission])
    def test_no_public_declares_a_self_digest_field(self, build):
        assert type(build()).SELF_DIGEST_FIELDS == frozenset()


# =========================================================================== #
# fresh-registry registration round-trip (the cumulative chain node is Task 11) #
# =========================================================================== #
class TestFreshRegistryRoundTrip:
    @staticmethod
    def _fresh() -> SchemaRegistry:
        reg = SchemaRegistry()
        for name, _version in _PUBLIC_MODELS_AND_KEYS:
            reg.register(getattr(C, name))
        return reg

    def test_all_five_publics_register_under_their_own_keys(self):
        reg = self._fresh()
        keys = tuple(e.key for e in reg.manifest())
        assert keys == tuple(sorted(f"{n}@{v}" for n, v in _PUBLIC_MODELS_AND_KEYS))

    @pytest.mark.parametrize("name,version", _PUBLIC_MODELS_AND_KEYS)
    def test_resolution_returns_the_registered_model(self, name, version):
        reg = self._fresh()
        assert reg.resolve(SchemaRef(name=name, version=version)) is getattr(C, name)

    @pytest.mark.parametrize("name,build", [
        ("CandidateSlate", _slate),
        ("RecommendationSlate", _rec_slate),
        ("EscalationReport", _report),
        ("RunSubject", _subject),
        ("TaSubmission", _submission),
    ])
    def test_payload_round_trips_through_the_registry(self, name, build):
        reg = self._fresh()
        original = build()
        revalidated = reg.validate_payload(SchemaRef(name=name, version="1"),
                                           original.model_dump())
        assert revalidated == original
        assert revalidated.semantic_digest() == original.semantic_digest()

    def test_unknown_version_is_refused(self):
        reg = self._fresh()
        with pytest.raises(UnknownSchemaError):
            reg.resolve(SchemaRef(name="CandidateSlate", version="2"))

    def test_registry_digest_is_registration_order_independent(self):
        a = SchemaRegistry()
        for name, _v in _PUBLIC_MODELS_AND_KEYS:
            a.register(getattr(C, name))
        b = SchemaRegistry()
        for name, _v in reversed(_PUBLIC_MODELS_AND_KEYS):
            b.register(getattr(C, name))
        assert a.registry_digest == b.registry_digest

    @pytest.mark.parametrize("name", ["CandidateEntry", "RecommendationEntry",
                                      "EscalationTrigger"])
    def test_internal_carriers_are_structurally_unregistrable(self, name):
        # they declare no schema_version, so they can never be resolved by a
        # SchemaRef — the structural evidence that they are internal carriers.
        assert "schema_version" not in getattr(C, name).model_fields
        with pytest.raises(SchemaRegistryError):
            SchemaRegistry().register(getattr(C, name))


# =========================================================================== #
# export surface                                                               #
# =========================================================================== #
def test_contracts_module_exports_the_full_surface():
    assert set(C.__all__) == {
        "CandidateEntry", "CandidateSlate",
        "RecommendationEntry", "RecommendationSlate",
        "EscalationTrigger", "EscalationReport",
        "RunSubject", "TaSubmission",
        # the mandatory v1 badge travels with the contracts: the producing surface
        # (pipeline.screening) imports it rather than re-typing the literal.
        "NO_CROSS_SECTIONAL_SUMMARY_BADGE",
    }
    assert C.NO_CROSS_SECTIONAL_SUMMARY_BADGE == "no_cross_sectional_summary_v1"


def test_package_reexports_the_five_publics():
    from guanlan_v2.orchestration import pipeline

    for name, _v in _PUBLIC_MODELS_AND_KEYS:
        assert getattr(pipeline, name) is getattr(C, name)
