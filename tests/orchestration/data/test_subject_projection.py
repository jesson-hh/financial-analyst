# -*- coding: utf-8 -*-
"""L1 Task 1 — the closed subject->data param projection (``SubjectParams``).

The frozen L1 plan (docs/superpowers/plans/2026-07-31-orchestration-L1-subject-
projection.md, D-0 option (i)) carries the run's committed ``RunSubject@1`` into
the ``data.runtime`` bridge's param assembly OUT-OF-BAND: ``spec.py``'s
``params_not_allowed`` guard and the sealed preset's code-free rule are both
untouched; the projection serves ``_assemble_params`` as a second, CLOSED source
for ``node_param`` pointers.

Binding investigation (Task 1 Step 1, performed 2026-07-31 BEFORE these tests
were written — outcomes recorded here per the plan):

* The ONE reviewed code->Symbol constructor is
  ``guanlan_v2.orchestration.data.symbols.normalize_symbol``
  (``data/symbols.py:114-165``): three anchored grammars (bare ``600519`` /
  dotted ``600519.SH`` / engine ``SH600519``), exchange+board derived inside the
  reviewed constructor — board rules are NEVER re-derived here (the BJ-920
  lesson: a hand-rolled prefix table is a known defect class). The
  watcher-canonical code format is the BARE six-digit form: it is exactly what
  ``RunSubject.code`` stores after validation (``pipeline/contracts.py:91-96``,
  ``_normalized_code`` == ``normalize_symbol(raw).code``), i.e. grammar 1
  (``_BARE_RE``, ``data/symbols.py:109``).
* The sealed row's params class is ``InstrumentUniverseParams``
  (``data/source.py:148-159``): ``symbols: tuple[Symbol, ...]``,
  ``as_of: IsoAwareDateTime`` (``data/source.py:125-145``). Probed empirically
  2026-07-31 under DigestModel strict mode: ``model_validate`` accepts
  ``symbols`` as a TUPLE of ``Symbol`` instances (or of Symbol-shaped
  mappings); a LIST is rejected (``tuple_type``). So ``code_value`` is the
  ratified singleton instrument set ``(normalize_symbol(code),)`` and
  ``asof_value`` is the aware UTC ISO-8601 string.
* Neither Task-1 STOP trigger fired: the reviewed constructor exists, and
  ``InstrumentUniverseParams.model_validate`` accepts the honest projection of
  one code + one aware datetime.

Run: ``pytest tests/orchestration/data/test_subject_projection.py -v``
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from guanlan_v2.orchestration.data import runtime as RT
from guanlan_v2.orchestration.data.catalog import ParamBinding, phase3_data_surface
from guanlan_v2.orchestration.data.source import InstrumentUniverseParams
from guanlan_v2.orchestration.data.symbols import normalize_symbol
from guanlan_v2.orchestration.spec import PlanNode

UTC = timezone.utc

#: the run's session as-of used throughout — aware, non-UTC offsets also probed.
_AS_OF = datetime(2026, 7, 31, 7, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


@pytest.fixture(scope="module")
def real_row(surface):
    """The single REAL sealed prefetch row the reviewed grant produces."""
    rows = surface.prefetch_binding.operations
    assert [(r.worker_id, r.method_ref.id) for r in rows] == [
        ("dec.pm", "verified_snapshot")]
    return rows[0]


def _pm_node(params: dict | None = None) -> PlanNode:
    """A legally-shaped ``dec.pm`` node (params-less unless a test says otherwise)."""
    return PlanNode(
        id="pm", worker_id="dec.pm", writes_slot="slot-pm", params=params or {})


def _subject() -> "RT.SubjectParams":
    return RT.SubjectParams.project(code="600519", as_of=_AS_OF)


# --------------------------------------------------------------------------- #
# SubjectParams.project — the ONE reviewed projection recipe                    #
# --------------------------------------------------------------------------- #
class TestSubjectParamsProject:
    def test_happy_path_projects_the_singleton_instrument_set(self):
        sp = _subject()
        # the ratified singleton-universe semantic: /code -> (Symbol,) via the
        # EXISTING reviewed constructor, never a hand-rolled board table.
        assert sp.code_value == (normalize_symbol("600519"),)
        assert sp.asof_value == _AS_OF.astimezone(UTC).isoformat()
        assert datetime.fromisoformat(sp.asof_value) == _AS_OF

    def test_board_rules_come_from_the_reviewed_constructor(self):
        # BJ / chinext / star codes: the projection must equal the constructor's
        # own output bit-for-bit — this test carries NO prefix table of its own.
        for code in ("830799", "300750", "688111"):
            sp = RT.SubjectParams.project(code=code, as_of=_AS_OF)
            assert sp.code_value == (normalize_symbol(code),)

    def test_a_non_utc_aware_instant_projects_deterministically(self):
        from datetime import timedelta, timezone as tz

        cst = tz(timedelta(hours=8))
        sp = RT.SubjectParams.project(
            code="600519", as_of=datetime(2026, 7, 31, 15, 0, tzinfo=cst))
        assert sp.asof_value == datetime(2026, 7, 31, 7, 0, tzinfo=UTC).isoformat()

    def test_document_is_the_closed_two_key_shape(self):
        sp = _subject()
        doc = sp.as_document()
        assert doc == {"asof_date": sp.asof_value, "code": sp.code_value}
        assert sorted(doc) == ["asof_date", "code"]  # exactly two keys, closed

    def test_the_carrier_is_frozen(self):
        sp = _subject()
        with pytest.raises(dataclasses.FrozenInstanceError):
            sp.asof_value = "2020-01-01T00:00:00+00:00"  # type: ignore[misc]

    def test_refuses_an_empty_code(self):
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code="", as_of=_AS_OF)

    @pytest.mark.parametrize("bad", ["600519.SH", "SH600519", "白酒", "60051", "6005190"])
    def test_refuses_a_non_canonical_code(self, bad):
        """Only the watcher-canonical BARE six-digit form is accepted.

        A dotted / engine spelling parses under the constructor's wider grammar,
        but the committed ``RunSubject.code`` is always the bare form — accepting
        an alternate spelling here would open a second input grammar for the ONE
        recipe, so it is refused, never repaired.
        """
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code=bad, as_of=_AS_OF)

    def test_refuses_a_naive_as_of(self):
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code="600519", as_of=datetime(2026, 7, 31, 7, 0))

    def test_refuses_a_non_datetime_as_of(self):
        with pytest.raises(RT.DataRuntimeError):
            RT.SubjectParams.project(code="600519", as_of="2026-07-31T07:00:00+00:00")

    def test_the_pointer_set_is_closed(self):
        assert RT.SUBJECT_PARAM_POINTERS == frozenset({"/asof_date", "/code"})


# --------------------------------------------------------------------------- #
# the real-row round trip — the load-bearing test of the whole L1 plan          #
# --------------------------------------------------------------------------- #
class TestRealRowRoundTrip:
    def test_the_real_sealed_row_resolves_and_validates_from_the_subject(
            self, real_row):
        """The REAL sealed row + a legally-shaped params-less ``dec.pm`` node
        resolve through the REAL ``_assemble_params`` under the projection, and
        the assembled document validates as a real ``InstrumentUniverseParams``
        whose ``symbols[0]`` round-trips the subject code. No fixture row, no
        fabricated binding."""
        sp = _subject()
        doc = RT._assemble_params(real_row, _pm_node(), subject_params=sp)
        assert doc == {"as_of": sp.asof_value, "symbols": sp.code_value}
        params_cls = RT._BINDING_BY_METHOD["verified_snapshot"].params_cls
        assert params_cls is InstrumentUniverseParams
        params = params_cls.model_validate(doc)
        assert [s.dotted for s in params.symbols] == ["600519.SH"]
        assert params.symbols[0].code == "600519"
        assert datetime.fromisoformat(params.as_of) == _AS_OF

    def test_without_the_subject_the_same_real_row_still_refuses(self, real_row):
        """The refusal survives — but its cause is now the RUNNER SEAM, not an
        unbuilt projection (the projection exists as of this task)."""
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(real_row, _pm_node())
        msg = str(exc.value)
        assert "'/asof_date'" in msg
        assert "does not resolve" in msg
        assert "params_not_allowed" in msg
        assert "not bound at the runner seam" in msg
        assert "is built" not in msg  # the pre-L1 'until ... is built' story is gone

    def test_a_node_may_never_contradict_the_run_subject(self, real_row):
        """Conflict guard: node params carrying a DIFFERENT ``/code`` value than
        the subject document -> loud typed refusal, never a silent winner."""
        sp = _subject()
        node = _pm_node({"code": "000001"})
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(real_row, node, subject_params=sp)
        assert "contradict" in str(exc.value)
        assert "'/code'" in str(exc.value)

    def test_a_node_carrying_the_same_value_resolves(self, real_row):
        """Same-value node params are NOT a conflict (both arms).

        ``/asof_date`` arm rides a real PlanNode (an ISO string is JSON-shaped);
        the ``/code`` arm needs a stub node because ``PlanNode.params`` is
        JSON-shaped and cannot carry the projected ``(Symbol,)`` tuple — the
        guard compares exact document values, and ``_assemble_params``'s node
        contract is duck-typed (``.id`` + ``.params``)."""
        sp = _subject()
        # arm 1: real PlanNode supplies /asof_date equal to the subject's value.
        doc = RT._assemble_params(
            real_row, _pm_node({"asof_date": sp.asof_value}), subject_params=sp)
        assert doc == {"as_of": sp.asof_value, "symbols": sp.code_value}
        # arm 2: stub node supplies BOTH pointers with the exact subject values.
        stub = SimpleNamespace(
            id="pm", params={"asof_date": sp.asof_value, "code": sp.code_value})
        doc = RT._assemble_params(real_row, stub, subject_params=sp)
        assert doc == {"as_of": sp.asof_value, "symbols": sp.code_value}

    def test_a_pointer_outside_the_closed_set_is_still_refused(self, real_row):
        """Closure: a ``node_param`` pointer outside ``SUBJECT_PARAM_POINTERS``
        is never served by the subject document, even when a subject is bound.
        The refusal names the closure so a widened set (mutation m1) reddens
        this test."""
        binding = ParamBinding(
            target_pointer="/limit", source_kind="node_param",
            source_pointer="/limit")
        row = real_row.model_copy(update={"param_bindings": (binding,)})
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(row, _pm_node(), subject_params=_subject())
        msg = str(exc.value)
        assert "'/limit'" in msg
        assert "closed pointer set" in msg


# --------------------------------------------------------------------------- #
# SubjectParams stays a plain service object — never a registered model         #
# --------------------------------------------------------------------------- #
class TestNothingIsRegistered:
    def test_runtime_registers_no_models(self):
        assert RT.RUNTIME_PUBLIC_MODELS == ()
        assert RT.RUNTIME_INTERNAL_MODELS == {}

    def test_subject_params_is_a_plain_dataclass_not_a_model(self):
        assert dataclasses.is_dataclass(RT.SubjectParams)
        assert not issubclass(RT.SubjectParams, BaseModel)
