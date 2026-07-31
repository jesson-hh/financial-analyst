# -*- coding: utf-8 -*-
"""Phase 10 · Task 2 — the deterministic candidate workers (``cand.v4`` /
``cand.lane0`` / ``cand.model``).

Written test-first (RED until ``guanlan_v2.orchestration.pipeline.candidates``
exists).

**Handler-ABI binding (E2, read at source before writing code).** The reviewed
deterministic-handler calling convention is two-stage, exactly as the executor
invokes it at ``guanlan_v2/orchestration/worker.py:1730-1735``::

    handler_factory = runtime.factories.handler_factory(worker.execution.handler_ref)
    handler = handler_factory(worker=worker, resolved=resolved)
    model_result = handler(node=node, input_snapshot=input_snapshot,
                           contributions=contributions,
                           data_result_refs=merged.data_result_refs)

so a handler returns a ``worker.ModelResult`` whose ``.payload`` is the typed
product (here ``CandidateSlate@1``), NOT the slate itself. The production
precedent for a bound deterministic handler is
``bootstrap.bootstrap_market_factor_handler`` (bootstrap.py:1673-1700, registered
via ``lambda **_kw: handler`` at bootstrap.py:1891); registration itself is
``TrustedFactoryRegistry.register_handler`` (catalog_runtime.py:456) and belongs
to Task 11 — this module only ships handlers shaped to fit it.

What is pinned here (the Task-2 brief's matrix):

* the ``RankingReader`` port + the internal ``RankingArtifact`` / ``RankingRow``
  carriers (Task 11's ``PHASE10_INTERNAL_MODELS`` names them);
* ``cand.v4`` / ``cand.model``: ``top_n`` truncation, rank monotonicity + unique
  codes delegated to Task 1's validators, Phase 3 code normalization,
  full provenance (``source_artifact_digest`` / ``variant_id``);
* honest staleness — a ranking artifact whose Asia/Shanghai session date differs
  from the run's carries badge ``stale_ranking:<artifact-date>``; the slate is
  never re-dated to the artifact and never refused;
* ``cand.lane0`` v1 IS the honest typed refusal ``Lane0LeadersUnavailable``
  (ratified D4 — pinned by ``test_phase10_handoff.py``'s
  ``test_d4_rotation_report_carries_no_leader_codes``), carrying its full
  param/provenance surface, plus an ``xfail(strict=True)`` happy-path spec that
  reddens the day extraction is implemented;
* determinism: identical inputs → byte-equal slate digest and rendered line;
* red lines: zero network / zero LLM imports, no write call anywhere in the
  module (the v4 ranking surface is read-only — spec §8 v4 信号不动).

Markers: the single disk-touching test is marked ``realdata`` (registered in
``tests/conftest.py``) — the default run is hermetic in intent and that test can
be deselected with ``-m "not realdata"`` (its read-only ``(mtime_ns, size)``
assertion can false-RED if the daily producer refreshes the parquet mid-test).

Run from repo root:
``python -m pytest tests/orchestration/test_pipeline_candidates.py -v``
(hermetic subset: append ``-m "not realdata"``)
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration import worker as W
from guanlan_v2.orchestration.digest import ContractModel
from guanlan_v2.orchestration.market import factors as F
from guanlan_v2.orchestration.pipeline import candidates as M
from guanlan_v2.orchestration.pipeline import contracts as C
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef

# --------------------------------------------------------------------------- #
# constants + fixtures                                                         #
# --------------------------------------------------------------------------- #
_UTC = timezone.utc
_CST = timezone(timedelta(hours=8))
#: 2026-07-27 09:30 Asia/Shanghai — a normal session instant.
_RUN_AS_OF = datetime(2026, 7, 27, 1, 30, tzinfo=_UTC)
_HEX_A = "a" * 64
_HEX_B = "b" * 64
_HEX_C = "c" * 64

#: engine-form codes (the vendored v4 artifact's own grammar).
_CODES = (
    "SH600519", "SZ000001", "SH688115", "SZ301511", "SZ000002",
    "SH601318", "SZ300750", "SH603259",
)


def _rows(n: int = 6, *, codes=None, start_rank: int = 1):
    codes = codes if codes is not None else _CODES[:n]
    return tuple(
        M.RankingRow(code=c, rank=start_rank + i, score=round(5.0 - i * 0.25, 4))
        for i, c in enumerate(codes)
    )


def _artifact(*, date: str = "2026-07-27", rows=None, digest: str = _HEX_A):
    return M.RankingArtifact(
        as_of=M.session_date_to_utc(date),
        artifact_digest=digest,
        rows=_rows() if rows is None else rows,
    )


class _FixtureRankingReader:
    """A deterministic in-memory :class:`RankingReader` (zero IO, zero network).

    ``by_variant`` lets one reader serve the prod artifact (``None``) and a
    variant artifact from the same instance, so the ``model_id`` seam is
    observable.
    """

    def __init__(self, artifact=None, *, by_variant=None):
        self._artifact = artifact if artifact is not None else _artifact()
        self._by_variant = dict(by_variant or {})
        self.calls: list[tuple[str | None, datetime]] = []

    def read_ranking(self, *, variant_id, as_of_hint):
        self.calls.append((variant_id, as_of_hint))
        if variant_id in self._by_variant:
            return self._by_variant[variant_id]
        return self._artifact


def _ports(*, reader=None, as_of=_RUN_AS_OF, **over):
    fields = dict(as_of=as_of, ranking_reader=reader if reader is not None
                  else _FixtureRankingReader())
    fields.update(over)
    return M.CandidatePorts(**fields)


def _rotation_ref(digest: str = _HEX_B) -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name="RotationReport", version="1"),
        payload_ref=PayloadRef(namespace="main", object_id="rot-1",
                               content_digest=digest),
    )


def _node(**params):
    return SimpleNamespace(id="n-cand", worker_id="cand.v4", params=dict(params))


def _call(handler, node=None):
    return handler(node=node if node is not None else _node(),
                   input_snapshot=object(), contributions=(), data_result_refs=())


# =========================================================================== #
# 1. the RankingReader port + internal carriers                                #
# =========================================================================== #
def test_ranking_row_and_artifact_are_strict_internal_contract_models():
    assert issubclass(M.RankingRow, ContractModel)
    assert issubclass(M.RankingArtifact, ContractModel)
    # internal carriers: NO schema_version (never independently resolved).
    assert "schema_version" not in M.RankingRow.model_fields
    assert "schema_version" not in M.RankingArtifact.model_fields
    assert set(M.RankingRow.model_fields) == {"code", "rank", "score"}
    assert set(M.RankingArtifact.model_fields) == {
        "as_of", "artifact_digest", "rows"}
    with pytest.raises(ValidationError):
        M.RankingRow(code="600519", rank=1, score=0.5, extra="x")
    with pytest.raises(ValidationError):  # frozen
        _rows(1)[0].__setattr__("rank", 9)


def test_ranking_row_refuses_a_non_finite_or_non_positive_value():
    with pytest.raises(ValidationError):
        M.RankingRow(code="600519", rank=0, score=1.0)
    with pytest.raises(ValidationError):
        M.RankingRow(code="600519", rank=1, score=float("nan"))
    # an absent score is an explicit None, never a fabricated 0.0
    assert M.RankingRow(code="600519", rank=1, score=None).score is None


def test_the_fixture_reader_satisfies_the_ranking_reader_protocol():
    assert isinstance(_FixtureRankingReader(), M.RankingReader)
    params = inspect.signature(M.RankingReader.read_ranking).parameters
    assert set(params) == {"self", "variant_id", "as_of_hint"}
    for name in ("variant_id", "as_of_hint"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY


def test_session_date_helpers_reuse_the_reviewed_plus_eight_convention():
    """The Asia/Shanghai session-date helper is the Phase-5 one BY OBJECT — a
    second copy could drift from the reviewed +08:00 (CN has no DST) rule."""
    assert M.session_date_of is F._session_date
    assert M.session_date_of(M.session_date_to_utc("2026-07-24")) == "2026-07-24"
    # 2026-07-26T23:00Z is already 2026-07-27 07:00 CST — a UTC-date comparison
    # would call this "yesterday"; the session-date one does not.
    assert M.session_date_of(datetime(2026, 7, 26, 23, 0, tzinfo=_UTC)) == "2026-07-27"


def test_ports_refuse_a_naive_as_of():
    with pytest.raises(M.CandidateWorkerError):
        M.CandidatePorts(as_of=datetime(2026, 7, 27, 9, 30))


# =========================================================================== #
# 2. params surfaces                                                           #
# =========================================================================== #
def test_params_are_plain_frozen_dataclasses_not_contract_models():
    """Task 11's public/internal ContractModel partition names only
    RankingArtifact/RankingRow from this module — params ride as plain
    dataclasses (the DeepDecideBindings precedent).

    Each instance is built with its FULL argument set and the refusal is
    narrowed to ``FrozenInstanceError``: a bare ``pytest.raises(Exception)``
    over a partial construction would be satisfied by the missing-argument
    ``TypeError`` and stay green with ``frozen=True`` removed.
    """
    instances = (
        M.CandV4Params(top_n=1),
        M.CandLane0Params(top_n=1, mainline_limit=1),
        M.CandModelParams(top_n=1, variant_id="m_x"),
    )
    for instance in instances:
        assert not isinstance(instance, ContractModel)
        assert dataclasses.is_dataclass(instance)
        with pytest.raises(dataclasses.FrozenInstanceError):
            instance.top_n = 2


@pytest.mark.parametrize("bad", [0, 51, -3])
def test_top_n_out_of_range_is_a_typed_refusal(bad):
    with pytest.raises(M.CandidateParamsError):
        M.CandV4Params(top_n=bad)


@pytest.mark.parametrize("bad", [0, 6])
def test_lane0_mainline_limit_out_of_range_is_a_typed_refusal(bad):
    with pytest.raises(M.CandidateParamsError):
        M.CandLane0Params(top_n=10, mainline_limit=bad)


def test_model_params_require_a_named_variant():
    with pytest.raises(M.CandidateParamsError):
        M.CandModelParams(top_n=10, variant_id="   ")
    assert M.CandModelParams(top_n=10, variant_id="m_6dce0995a4").variant_id


@pytest.mark.parametrize("reserved", ["prod", "PROD", "  prod  "])
def test_the_reserved_production_variant_id_is_refused_by_name(reserved):
    """``load_v4_ranking(model_id="prod")`` reads the PRODUCTION artifact
    (ranking.py:59), so a cand.model slate named with it would label production
    v4 rows ``source_kind="model_variant"`` — a mislabel Task 1's biconditional
    cannot catch, because the field IS populated."""
    with pytest.raises(M.CandidateParamsError) as excinfo:
        M.CandModelParams(top_n=10, variant_id=reserved)
    assert "prod" in str(excinfo.value)
    assert "cand.v4" in str(excinfo.value)   # names the right worker instead
    assert "prod" in M.RESERVED_VARIANT_IDS


def test_params_from_mapping_refuses_an_unknown_key_and_a_wrong_type():
    assert M.CandV4Params.from_mapping({"top_n": 7}) == M.CandV4Params(top_n=7)
    with pytest.raises(M.CandidateParamsError):
        M.CandV4Params.from_mapping({"top_n": 7, "mainline_limit": 2})
    with pytest.raises(M.CandidateParamsError):
        M.CandV4Params.from_mapping({"top_n": "7"})
    with pytest.raises(M.CandidateParamsError):
        M.CandV4Params.from_mapping({"top_n": True})  # bool is not an int here


def test_node_params_override_the_factory_bound_params():
    reader = _FixtureRankingReader()
    handler = M.cand_v4_handler(params=M.CandV4Params(top_n=2), ports=_ports(reader=reader))
    assert len(_call(handler).payload.entries) == 2
    result = _call(handler, _node(top_n=4))
    assert len(result.payload.entries) == 4
    assert result.payload.top_n == 4
    # and a bad override is refused rather than silently ignored
    with pytest.raises(M.CandidateParamsError):
        _call(handler, _node(top_n=99))


# =========================================================================== #
# 3. cand.v4                                                                   #
# =========================================================================== #
def test_v4_slate_carries_full_provenance_and_normalized_codes():
    reader = _FixtureRankingReader(_artifact(digest=_HEX_C))
    slate = M.build_v4_slate(params=M.CandV4Params(top_n=4), ports=_ports(reader=reader))
    assert isinstance(slate, C.CandidateSlate)
    assert slate.source_kind == "v4"
    assert slate.as_of == _RUN_AS_OF          # the RUN's as-of, never the artifact's
    assert slate.top_n == 4
    assert slate.source_artifact_digest == _HEX_C
    assert slate.rotation_report_ref is None
    assert slate.variant_id is None
    assert [e.code for e in slate.entries] == ["600519", "000001", "688115", "301511"]
    assert [e.rank for e in slate.entries] == [1, 2, 3, 4]
    assert slate.entries[0].score == 5.0
    # the reader was consulted for the production artifact with the run as-of
    assert reader.calls == [(None, _RUN_AS_OF)]


def test_top_n_truncates_and_never_pads():
    reader = _FixtureRankingReader(_artifact(rows=_rows(3)))
    slate = M.build_v4_slate(params=M.CandV4Params(top_n=10), ports=_ports(reader=reader))
    assert len(slate.entries) == 3 and slate.top_n == 10
    short = M.build_v4_slate(params=M.CandV4Params(top_n=2), ports=_ports(reader=reader))
    assert [e.code for e in short.entries] == ["600519", "000001"]


def test_rows_are_ordered_by_source_rank_regardless_of_reader_order():
    shuffled = (
        M.RankingRow(code="SZ000001", rank=4, score=1.0),
        M.RankingRow(code="SH600519", rank=2, score=3.0),
        M.RankingRow(code="SH688115", rank=9, score=0.5),
    )
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=3),
        ports=_ports(reader=_FixtureRankingReader(_artifact(rows=shuffled))))
    assert [e.code for e in slate.entries] == ["600519", "000001", "688115"]
    assert [e.rank for e in slate.entries] == [1, 2, 3]     # slate rank = position
    assert [e.source_note for e in slate.entries] == [
        "source_rank=2", "source_rank=4", "source_rank=9"]  # upstream rank kept


def test_an_empty_ranking_yields_an_empty_but_legal_slate():
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=5),
        ports=_ports(reader=_FixtureRankingReader(_artifact(rows=()))))
    assert slate.entries == ()
    assert M.EMPTY_SLATE_BADGE in slate.badges


def test_an_empty_slate_is_reported_as_a_degraded_node_never_a_silent_success():
    handler = M.cand_v4_handler(
        params=M.CandV4Params(top_n=5),
        ports=_ports(reader=_FixtureRankingReader(_artifact(rows=()))))
    result = _call(handler)
    assert result.degraded is True
    assert M.EMPTY_SLATE_BADGE in result.degradation_reasons


def test_a_row_outside_the_phase3_grammar_is_skipped_with_an_honest_badge():
    """CONSCIOUSLY FLIPPED 2026-07-31 (merge c01d099, the great-meitner BJ-920
    fix): ``BJ920807`` now maps to BJ through the shared predicate and ENTERS
    the slate. A genuinely non-grammatical row (``白酒``) keeps the honesty
    rule live: EXCLUDED and COUNTED, never re-mapped by fiat."""
    # 白酒 sits at rank 1 so the scan MUST meet it while filling top_n
    # (the builder counts unmappables ENCOUNTERED, not all rows).
    rows = (
        M.RankingRow(code="白酒", rank=1, score=5.3),
        M.RankingRow(code="BJ920807", rank=2, score=5.2),
        M.RankingRow(code="SH600519", rank=3, score=5.1),
        M.RankingRow(code="SZ000001", rank=4, score=5.0),
    )
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=2),
        ports=_ports(reader=_FixtureRankingReader(_artifact(rows=rows))))
    assert [e.code for e in slate.entries] == ["920807", "600519"]
    assert f"{M.UNMAPPABLE_CODES_BADGE_PREFIX}1" in slate.badges


def test_a_duplicate_code_is_refused_by_task_1_never_silently_deduped():
    rows = (
        M.RankingRow(code="SH600519", rank=1, score=5.0),
        M.RankingRow(code="600519", rank=2, score=4.0),
    )
    with pytest.raises(ValidationError) as excinfo:
        M.build_v4_slate(
            params=M.CandV4Params(top_n=2),
            ports=_ports(reader=_FixtureRankingReader(_artifact(rows=rows))))
    assert "duplicate code" in str(excinfo.value)


def test_a_missing_ranking_port_is_a_typed_refusal_naming_it():
    with pytest.raises(M.RankingSourceUnavailable) as excinfo:
        M.build_v4_slate(params=M.CandV4Params(top_n=3),
                         ports=M.CandidatePorts(as_of=_RUN_AS_OF))
    assert "ranking_reader" in str(excinfo.value)


# =========================================================================== #
# 4. honest staleness                                                          #
# =========================================================================== #
def test_a_stale_ranking_is_badged_and_never_re_dated():
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=3),
        ports=_ports(reader=_FixtureRankingReader(_artifact(date="2026-07-24"))))
    assert f"{M.STALE_RANKING_BADGE_PREFIX}2026-07-24" in slate.badges
    assert slate.as_of == _RUN_AS_OF              # never silently re-dated
    assert slate.entries                          # never refused


def test_a_same_session_ranking_carries_no_stale_badge():
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=3),
        ports=_ports(reader=_FixtureRankingReader(_artifact(date="2026-07-27"))))
    assert not [b for b in slate.badges
                if b.startswith(M.STALE_RANKING_BADGE_PREFIX)]


@pytest.mark.parametrize("run_as_of", [
    datetime(2026, 7, 26, 22, 0, tzinfo=_UTC),   # 06:00 CST on 07-27 — pre-open
    datetime(2026, 7, 27, 1, 30, tzinfo=_UTC),   # 09:30 CST on 07-27 — the open
    datetime(2026, 7, 27, 7, 5, tzinfo=_UTC),    # 15:05 CST on 07-27 — the close
])
def test_staleness_is_judged_on_the_session_date_not_the_utc_date(run_as_of):
    """Every instant of the 07-27 Asia/Shanghai session — which spans TWO UTC
    dates — reads the 07-27 artifact as fresh. Comparing UTC dates instead
    (the artifact's own instant is CN midnight = 16:00Z the day before) falsely
    badges the session's own artifact stale."""
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=3),
        ports=_ports(reader=_FixtureRankingReader(_artifact(date="2026-07-27")),
                     as_of=run_as_of))
    assert M.session_date_of(run_as_of) == "2026-07-27"
    assert not [b for b in slate.badges
                if b.startswith(M.STALE_RANKING_BADGE_PREFIX)]


def test_the_stale_badge_names_the_artifacts_session_date():
    """The badge carries the artifact's SESSION date (2026-07-24), not the UTC
    date of the instant that stamps it (2026-07-23T16:00Z)."""
    slate = M.build_v4_slate(
        params=M.CandV4Params(top_n=3),
        ports=_ports(reader=_FixtureRankingReader(_artifact(date="2026-07-24")),
                     as_of=datetime(2026, 7, 26, 22, 0, tzinfo=_UTC)))
    assert f"{M.STALE_RANKING_BADGE_PREFIX}2026-07-24" in slate.badges


# =========================================================================== #
# 5. cand.model (variant)                                                      #
# =========================================================================== #
def test_model_variant_slate_carries_its_variant_provenance():
    variant_rows = _rows(3, codes=("SZ300750", "SH603259", "SH600519"))
    reader = _FixtureRankingReader(
        _artifact(digest=_HEX_A),
        by_variant={"m_6dce0995a4": _artifact(rows=variant_rows, digest=_HEX_C)})
    slate = M.build_model_variant_slate(
        params=M.CandModelParams(top_n=2, variant_id="m_6dce0995a4"),
        ports=_ports(reader=reader))
    assert slate.source_kind == "model_variant"
    assert slate.variant_id == "m_6dce0995a4"
    assert slate.source_artifact_digest == _HEX_C
    assert slate.rotation_report_ref is None
    assert [e.code for e in slate.entries] == ["300750", "603259"]
    assert reader.calls == [("m_6dce0995a4", _RUN_AS_OF)]


def test_the_variant_and_production_slates_differ_only_by_their_source():
    reader = _FixtureRankingReader(
        _artifact(digest=_HEX_A),
        by_variant={"m_x": _artifact(digest=_HEX_A)})
    prod = M.build_v4_slate(params=M.CandV4Params(top_n=3), ports=_ports(reader=reader))
    var = M.build_model_variant_slate(
        params=M.CandModelParams(top_n=3, variant_id="m_x"), ports=_ports(reader=reader))
    assert [e.code for e in prod.entries] == [e.code for e in var.entries]
    assert prod.semantic_digest() != var.semantic_digest()   # provenance is identity


# =========================================================================== #
# 6. cand.lane0 — the ratified D4 honest refusal                                #
# =========================================================================== #
def _rotation_report():
    return SimpleNamespace(
        mainlines=(SimpleNamespace(name="AI算力", universe_key="ind:通信设备"),
                   SimpleNamespace(name="创新药", universe_key="ind:化学制药")),
        as_of=_RUN_AS_OF)


def test_lane0_always_refuses_with_the_typed_error_carrying_provenance():
    ref = _rotation_ref()
    with pytest.raises(M.Lane0LeadersUnavailable) as excinfo:
        M.build_lane0_slate(
            params=M.CandLane0Params(top_n=10, mainline_limit=3),
            ports=_ports(rotation_report=_rotation_report(), rotation_report_ref=ref))
    err = excinfo.value
    assert isinstance(err, M.CandidateWorkerError)
    assert err.rotation_report_ref is ref
    assert err.as_of == _RUN_AS_OF
    assert err.mainline_names == ("AI算力", "创新药")
    # the refusal names WHERE the codes would have come from, so the day upstream
    # grows them the reader of this message knows what to implement.
    assert "MainlineRead" in str(err)
    assert "D4" in str(err)


def test_lane0_never_invents_a_universe():
    """No path returns a slate: the refusal is the only outcome in v1."""
    src = Path(M.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_lane0_slate")
    assert not [n for n in ast.walk(fn)
                if isinstance(n, ast.Return) and n.value is not None], (
        "build_lane0_slate must not return a value in v1 — it refuses")
    assert [n for n in ast.walk(fn) if isinstance(n, ast.Raise)]


def test_lane0_validates_its_full_param_surface_before_refusing():
    """Invariant 3: the worker ships its full param/provenance surface, so a
    malformed param is caught as a param error, not masked by the D4 refusal."""
    with pytest.raises(M.CandidateParamsError):
        M.cand_lane0_handler(
            params=M.CandLane0Params(top_n=10, mainline_limit=3),
            ports=_ports(rotation_report=_rotation_report(),
                         rotation_report_ref=_rotation_ref()),
        )(node=_node(mainline_limit=9), input_snapshot=None, contributions=(),
          data_result_refs=())


def test_lane0_refuses_when_the_rotation_port_itself_is_absent():
    with pytest.raises(M.Lane0LeadersUnavailable) as excinfo:
        M.build_lane0_slate(params=M.CandLane0Params(top_n=10, mainline_limit=3),
                            ports=_ports())
    assert excinfo.value.rotation_report_ref is None
    assert "rotation_report" in str(excinfo.value)


def test_the_lane0_handler_propagates_the_refusal_to_the_executor():
    handler = M.cand_lane0_handler(
        params=M.CandLane0Params(top_n=10, mainline_limit=2),
        ports=_ports(rotation_report=_rotation_report(),
                     rotation_report_ref=_rotation_ref()))
    with pytest.raises(M.Lane0LeadersUnavailable):
        _call(handler)


@pytest.mark.xfail(strict=True, reason=(
    "ratified D4: upstream carries no leader stock codes today "
    "(test_phase10_handoff.test_d4_rotation_report_carries_no_leader_codes pins "
    "the absence). This is the future happy-path spec — when the gate reddens "
    "and extraction lands, this XPASSes and forces D4 to be re-reviewed."))
def test_lane0_happy_path_spec_for_a_code_bearing_upstream():
    report = SimpleNamespace(
        mainlines=(
            SimpleNamespace(name="AI算力", universe_key="ind:通信设备",
                            leader_codes=("SZ300750", "SH688115")),
            SimpleNamespace(name="创新药", universe_key="ind:化学制药",
                            leader_codes=("SH600519",)),
        ),
        as_of=_RUN_AS_OF)
    slate = M.build_lane0_slate(
        params=M.CandLane0Params(top_n=3, mainline_limit=2),
        ports=_ports(rotation_report=report, rotation_report_ref=_rotation_ref()))
    assert slate.source_kind == "lane0"
    assert slate.rotation_report_ref is not None
    assert [e.code for e in slate.entries] == ["300750", "688115", "600519"]


# =========================================================================== #
# 7. the handler ABI + determinism                                             #
# =========================================================================== #
@pytest.mark.parametrize("builder", [
    lambda **kw: M.cand_v4_handler(params=M.CandV4Params(top_n=3), **kw),
    lambda **kw: M.cand_model_handler(
        params=M.CandModelParams(top_n=3, variant_id="m_x"), **kw),
    lambda **kw: M.cand_lane0_handler(
        params=M.CandLane0Params(top_n=3, mainline_limit=2), **kw),
])
def test_every_handler_matches_the_reviewed_executor_signature(builder):
    handler = builder(ports=_ports())
    sig = inspect.signature(handler)
    assert set(sig.parameters) == {
        "node", "input_snapshot", "contributions", "data_result_refs"}
    for p in sig.parameters.values():
        assert p.kind is inspect.Parameter.KEYWORD_ONLY


def test_a_handler_returns_a_model_result_carrying_the_slate():
    handler = M.cand_v4_handler(params=M.CandV4Params(top_n=3), ports=_ports())
    result = _call(handler)
    assert isinstance(result, W.ModelResult)
    assert isinstance(result.payload, C.CandidateSlate)
    assert result.number_anchors == ()
    assert result.input_tokens == 0 and result.output_tokens == 0
    assert result.provider is None and result.model is None
    assert result.degraded is False
    assert "cand.v4" in result.rendered_text


def test_the_registration_adapter_matches_the_trusted_factory_registry_abi():
    """``register_handler(ref, factory)`` calls ``factory(worker=…, resolved=…)``
    (worker.py:1730-1731); the adapter is the one place that shape is pinned."""
    handler = M.cand_v4_handler(params=M.CandV4Params(top_n=3), ports=_ports())
    factory = M.as_handler_factory(handler)
    assert factory(worker=object(), resolved=object()) is handler
    assert factory() is handler          # tolerant of a bare call, like bootstrap's


def test_handlers_ignore_contributions_and_data_result_refs():
    handler = M.cand_v4_handler(params=M.CandV4Params(top_n=3), ports=_ports())
    plain = _call(handler)
    noisy = handler(node=_node(), input_snapshot={"junk": 1},
                    contributions=("junk",), data_result_refs=("junk",))
    assert plain.payload.semantic_digest() == noisy.payload.semantic_digest()


def test_handler_determinism_is_byte_equal_across_instances():
    def _one():
        return M.cand_v4_handler(
            params=M.CandV4Params(top_n=4),
            ports=_ports(reader=_FixtureRankingReader(_artifact(date="2026-07-24"))))
    a, b = _call(_one()), _call(_one())
    assert a.payload.semantic_digest() == b.payload.semantic_digest()
    assert a.payload.model_dump() == b.payload.model_dump()
    assert a.rendered_text == b.rendered_text


def test_worker_ids_are_the_planned_three():
    assert (M.CAND_V4_WORKER_ID, M.CAND_LANE0_WORKER_ID, M.CAND_MODEL_WORKER_ID) == (
        "cand.v4", "cand.lane0", "cand.model")


# =========================================================================== #
# 8. the production ranking reader (D5)                                        #
# =========================================================================== #
class _FakeRankingModule(types.ModuleType):
    """A stand-in for ``guanlan_v2.strategy.ranking`` (no pandas, no artifact)."""

    def __init__(self, frame=None, date="2026-07-24", exc=None):
        super().__init__("guanlan_v2.strategy.ranking")
        self._frame, self._date, self._exc = frame, date, exc
        self.calls: list[tuple[str, object]] = []

    def load_v4_ranking(self, model_id=None):
        self.calls.append(("load", model_id))
        if self._exc is not None:
            raise self._exc
        return self._frame

    def ranking_date(self, model_id=None):
        self.calls.append(("date", model_id))
        return self._date


class _FakeFrame:
    """The minimal DataFrame surface the reader is allowed to use.

    Records carry their own ``date`` (as the real artifact does — ``V4_COLUMNS``
    guarantees the column), so the reader's anti-skew cross-check is exercised.
    """

    _DEFAULT_COLUMNS = ("code", "lgb_rank", "lgb_score", "date")

    def __init__(self, records, columns=None, row_date="2026-07-24"):
        self._records = [
            dict(r) if "date" in r or row_date is None else {**r, "date": row_date}
            for r in records
        ]
        self.columns = list(self._DEFAULT_COLUMNS if columns is None else columns)

    def to_dict(self, orient):
        assert orient == "records"
        return [dict(r) for r in self._records]


def _install_fake_ranking(monkeypatch, module):
    monkeypatch.setitem(sys.modules, "guanlan_v2.strategy.ranking", module)
    return module


def test_the_production_reader_binds_the_ratified_d5_surface(monkeypatch):
    fake = _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([
            {"code": "SH600519", "lgb_rank": 2, "lgb_score": 4.5},
            {"code": "SZ000001", "lgb_rank": 1, "lgb_score": 5.5},
        ]), date="2026-07-24"))
    artifact = M.build_production_ranking_reader().read_ranking(
        variant_id=None, as_of_hint=_RUN_AS_OF)
    assert [c for c in fake.calls] == [("load", None), ("date", None)]
    assert M.session_date_of(artifact.as_of) == "2026-07-24"
    assert [(r.code, r.rank, r.score) for r in artifact.rows] == [
        ("SZ000001", 1, 5.5), ("SH600519", 2, 4.5)]
    assert artifact.artifact_digest == M.compute_ranking_artifact_digest(
        artifact.rows, model_id=None, as_of_date="2026-07-24")


def test_the_production_reader_passes_a_variant_id_through_as_model_id(monkeypatch):
    fake = _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"code": "SZ000001", "lgb_rank": 1, "lgb_score": 1.0}])))
    M.build_production_ranking_reader().read_ranking(
        variant_id="m_6dce0995a4", as_of_hint=_RUN_AS_OF)
    assert fake.calls == [("load", "m_6dce0995a4"), ("date", "m_6dce0995a4")]


def test_the_production_reader_passes_a_missing_artifact_through_honestly(monkeypatch):
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        exc=FileNotFoundError("v4 排名产物缺失")))
    with pytest.raises(FileNotFoundError):
        M.build_production_ranking_reader().read_ranking(
            variant_id=None, as_of_hint=_RUN_AS_OF)


def test_the_production_reader_refuses_a_dateless_or_columnless_artifact(monkeypatch):
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"code": "SZ000001", "lgb_rank": 1, "lgb_score": 1.0}]),
        date=""))
    with pytest.raises(M.RankingSourceUnavailable):
        M.build_production_ranking_reader().read_ranking(
            variant_id=None, as_of_hint=_RUN_AS_OF)
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"lgb_pct": 0.5}], columns=("lgb_pct",))))
    with pytest.raises(M.RankingSourceUnavailable) as excinfo:
        M.build_production_ranking_reader().read_ranking(
            variant_id=None, as_of_hint=_RUN_AS_OF)
    assert "code" in str(excinfo.value)


def test_a_non_finite_score_becomes_an_explicit_none(monkeypatch):
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"code": "SZ000001", "lgb_rank": 1,
                           "lgb_score": float("nan")}])))
    artifact = M.build_production_ranking_reader().read_ranking(
        variant_id=None, as_of_hint=_RUN_AS_OF)
    assert artifact.rows[0].score is None


def test_the_production_reader_is_a_ranking_reader():
    assert isinstance(M.build_production_ranking_reader(), M.RankingReader)


@pytest.mark.parametrize("hint", [_RUN_AS_OF, datetime(2020, 1, 1, tzinfo=_UTC)])
def test_the_as_of_hint_never_re_dates_the_artifact(monkeypatch, hint):
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"code": "SZ000001", "lgb_rank": 1, "lgb_score": 1.0}]),
        date="2026-07-24"))
    artifact = M.build_production_ranking_reader().read_ranking(
        variant_id=None, as_of_hint=hint)
    assert M.session_date_of(artifact.as_of) == "2026-07-24"


def test_a_mid_read_refresh_is_refused_naming_both_dates(monkeypatch):
    """``ranking_date()`` performs its OWN load (ranking.py:73-81) and the daily
    producer overwrites ``v4_ranking_latest.parquet`` IN PLACE. A refresh landing
    between the two loads would bind yesterday's rows to today's date and
    silently suppress the ``stale_ranking`` badge — invariant 2's exact
    prohibition. The rows' own date is cross-checked and the skew refused."""
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"code": "SZ000001", "lgb_rank": 1, "lgb_score": 1.0}],
                         row_date="2026-07-24"),          # the rows we hold
        date="2026-07-27"))                               # the refreshed artifact
    with pytest.raises(M.RankingSourceUnavailable) as excinfo:
        M.build_production_ranking_reader().read_ranking(
            variant_id=None, as_of_hint=_RUN_AS_OF)
    message = str(excinfo.value)
    assert "2026-07-24" in message and "2026-07-27" in message
    assert "stale_ranking" in message


def test_rows_carrying_more_than_one_ranking_date_are_refused(monkeypatch):
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([
            {"code": "SZ000001", "lgb_rank": 1, "lgb_score": 1.0, "date": "2026-07-24"},
            {"code": "SH600519", "lgb_rank": 2, "lgb_score": 0.5, "date": "2026-07-23"},
        ]), date="2026-07-24"))
    with pytest.raises(M.RankingSourceUnavailable) as excinfo:
        M.build_production_ranking_reader().read_ranking(
            variant_id=None, as_of_hint=_RUN_AS_OF)
    assert "2026-07-23" in str(excinfo.value)


def test_the_row_date_and_the_ratified_surface_agreeing_is_the_happy_path(monkeypatch):
    """The cross-check is a skew detector, not a second date source: when the two
    agree the artifact is stamped with that one date."""
    _install_fake_ranking(monkeypatch, _FakeRankingModule(
        frame=_FakeFrame([{"code": "SZ000001", "lgb_rank": 1, "lgb_score": 1.0}],
                         row_date="2026-07-24"),
        date="2026-07-24"))
    artifact = M.build_production_ranking_reader().read_ranking(
        variant_id=None, as_of_hint=_RUN_AS_OF)
    assert M.session_date_of(artifact.as_of) == "2026-07-24"


# --- one real-artifact read -------------------------------------------------- #
# Marked ``realdata`` (registered in tests/conftest.py): it is the only test in
# this module that touches the disk artifact, and its (mtime_ns, size) read-only
# assertion can false-RED if the daily producer refreshes the parquet mid-test
# on a production machine. The DEFAULT run stays hermetic in intent — deselect
# with ``-m "not realdata"``.
def _v4_artifact_path():
    from guanlan_v2.strategy.paths import V4_RANKING_PARQUET
    return V4_RANKING_PARQUET


@pytest.mark.realdata
@pytest.mark.skipif(not _v4_artifact_path().exists(),
                    reason="vendored v4 ranking artifact absent (see the restore recipe)")
def test_the_real_vendored_artifact_produces_a_slate_without_touching_it():
    path = _v4_artifact_path()
    before = (path.stat().st_mtime_ns, path.stat().st_size)
    ports = M.CandidatePorts(as_of=_RUN_AS_OF,
                             ranking_reader=M.build_production_ranking_reader())
    slate = M.build_v4_slate(params=M.CandV4Params(top_n=10), ports=ports)
    assert len(slate.entries) == 10
    assert all(len(e.code) == 6 and e.code.isdigit() for e in slate.entries)
    assert slate.source_artifact_digest
    assert (path.stat().st_mtime_ns, path.stat().st_size) == before  # read-only
    for badge in slate.badges:
        assert badge.startswith((M.STALE_RANKING_BADGE_PREFIX,
                                 M.UNMAPPABLE_CODES_BADGE_PREFIX)) or (
            badge == M.EMPTY_SLATE_BADGE), badge


# =========================================================================== #
# 9. red lines                                                                 #
# =========================================================================== #
def _module_imports():
    """(module-level imports, in-function imports) of ``candidates.py``."""
    tree = ast.parse(Path(M.__file__).read_text(encoding="utf-8"))
    top_level_ids = {id(node) for node in tree.body}
    top: set[str] = set()
    nested: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = {node.module}
        else:
            continue
        (top if id(node) in top_level_ids else nested).update(names)
    return top, nested


def test_no_network_or_llm_import_anywhere():
    top, nested = _module_imports()
    forbidden = ("httpx", "requests", "urllib", "socket", "aiohttp", "openai",
                 "anthropic", "financial_analyst", "engine")
    for module in top | nested:
        assert not module.startswith(forbidden), module
        assert "llm" not in module.split("."), module


def test_screen_and_strategy_imports_are_lazy_only():
    top, nested = _module_imports()
    for module in top:
        if module.startswith("guanlan_v2"):
            assert module.startswith("guanlan_v2.orchestration"), (
                f"{module} must be a lazy (in-function) import")
    assert any(m.startswith("guanlan_v2.strategy") for m in nested)


def test_the_module_contains_no_write_call():
    """Invariant 5: the v4 ranking surface is read-only (spec §8 v4 信号不动)."""
    tree = ast.parse(Path(M.__file__).read_text(encoding="utf-8"))
    banned = {"open", "write", "write_text", "write_bytes", "to_parquet",
              "to_csv", "mkdir", "unlink", "rename", "replace", "dump", "remove"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "attr", None) or getattr(func, "id", None)
            assert name not in banned, f"write-shaped call {name!r} in candidates.py"


def test_importing_the_module_pulls_in_no_pandas_or_strategy_module():
    """The module is import-cheap: the heavy read surfaces arrive lazily, inside
    the production reader only."""
    src = Path(M.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            name = getattr(node, "module", None) or node.names[0].name
            assert not name.startswith(("pandas", "numpy", "guanlan_v2.strategy",
                                        "guanlan_v2.screen")), name
