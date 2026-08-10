# -*- coding: utf-8 -*-
"""L3 Task 1 — the preconditions gate (plan: ``docs/superpowers/plans/
2026-07-31-orchestration-L3-grants-refreeze.md`` Task 1).

Everything here is driven against the REAL tree — the real
``phase3_data_surface()``, the real ``_assemble_params`` / params models, the
real ``LiveClientSource`` behind a sentinel probe, the real catalog-chain
builders, the real ``build_phase3_catalog`` invariant. No fixture stands in
for a fact the gate claims.

The gate is also the executable half of its own ledger:
``docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md`` records every
measured value in machine-parsed tables, and :class:`TestGate4BaselineDigests`
/ :class:`TestGate3AdapterInventory` / :class:`TestGate5PointerSatisfiability`
re-measure the tree and assert the ledger equals it. A ledger that drifts from
the tree (or a tree that drifts from the frozen inventory) is RED here, which
is the whole point: Tasks 3-4 use that document as their allowed-red set and
Task 5 uses it as the regeneration checklist.

Gate items (Task 1 Step 1), one class each:

1. **L1 landed** — the ``dec.pm``/``verified_snapshot`` row is RUNNABLE from a
   committed ``RunSubject@1`` through the CLOSED projection, and the ``ff26c23``
   DECLARED-but-NOT-RUNNABLE pins were consciously flipped. If those pins still
   asserted not-runnable this class would be RED = STOP.
2. **L2-b landed** — a production ``DataRuntimeWorld`` construction exists
   outside ``tests/``, the zero-production-callers AST pin is flipped to the ONE
   named caller, both retired providers are gone repo-wide, the registered
   ``bridge.data_runtime.provider@1`` incumbent on the production bundle is the
   production provider, and the per-run view's override target is
   ``production_data_provider_factory(subject_params)``.
3. **Adapter inventory** — which of the seven data methods have a REAL
   registered backend, measured BEHAVIOURALLY (stage the recipe-frozen route on
   the real backend; a sentinel probe proves the transport was reached).
4. **Baseline digests** — the whole P2→P10 chain, re-derived live, equal the
   spec §1 values AND the ledger's recorded table; the ledger's 42-literal /
   16-file inventory equals what the tree carries, literal by literal.
5. **Pointer-set satisfiability** — for every D-1 candidate method, the real L1
   round-trip idiom decides satisfiable vs L1-module-movement.

Plus three items the brief could not know (recorded as such in the ledger):

6. **Grant site** (ledger §7.0) — the one-to-one invariant's ``granted`` set is
   derived over the end-of-Phase-2 worker set, which contains NEITHER candidate
   worker. The gate proves both consequences so D-1 is ruled with that fact in
   hand.
7. **The D-2 arithmetic + the two outage vocabularies** (ledger §8) — a data
   outage splits on the method's ``optional`` bit into ``bridge_execution_error``
   (core) vs ``tool_discipline_unmet`` (optional). Task 5 must pin both.
8. **§4's allowed-red set is executable** — every row in the ledger's
   allowed-red table is bound to a real file + a real in-file anchor, and the
   flip-classification column is a closed vocabulary. §4 is what Tasks 3-4
   enforce the red window against, so it may not be the one big table with no
   executable protection.

Run: ``python -m pytest tests/orchestration/test_l3_refreeze_gate.py -v``
"""
from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import guanlan_v2.orchestration.adapters.data_world as DW
import guanlan_v2.orchestration.data.catalog as DC
import guanlan_v2.orchestration.data.runtime as RT
import guanlan_v2.orchestration.pipeline.assembly as ASM
import guanlan_v2.orchestration.pipeline.deep_decide as DD
import guanlan_v2.orchestration.pipeline.live_decide as LD
from guanlan_v2.orchestration.adapters import chain
from guanlan_v2.orchestration.adapters.live_data import LiveClientSource
from guanlan_v2.orchestration.catalog import WorkerSpec
from guanlan_v2.orchestration.catalog_runtime import (
    TrustedFactoryRegistry,
    build_text_material,
)
from guanlan_v2.orchestration.data.catalog import (
    CatalogError,
    DataBridgePrefetchBinding,
    DataPrefetchOperation,
    ParamBinding,
    phase3_data_surface,
    serialize_bridge_descriptor,
    serialize_prefetch_binding,
)
from guanlan_v2.orchestration.data.errors import RoutingConfigurationError
from guanlan_v2.orchestration.data.source import (
    DataInvocationScope,
    RouteEntry,
)
from guanlan_v2.orchestration.pipeline.contracts import RunSubject
from guanlan_v2.orchestration.presets import load_compat_catalog
from guanlan_v2.orchestration.runtime_contracts import (
    ExecutionEvidenceOrdinalToken,
)
from guanlan_v2.orchestration.spec import PlanNode

UTC = timezone.utc
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER = (_REPO_ROOT / "docs" / "superpowers" / "ledgers"
           / "2026-07-31-L3-refreeze-inventory.md")

#: the gate's frozen as-of. 2026-07-31 07:00Z == 15:00 Asia/Shanghai (close).
_AS_OF = datetime(2026, 7, 31, 7, 0, tzinfo=UTC)
#: the campaign's subject code (the 五粮液 real-run stock), canonical bare form.
_CODE = "000858"

#: the seven reviewed data method ids.
_SEVEN = ("fundamentals", "indicators", "instrument_names", "news", "ohlcv",
          "signals", "verified_snapshot")

#: the two D-1 candidate workers and the methods their P8 allowlists hold.
_D1_CANDIDATES = {
    "pv.technical": ("indicators", "verified_snapshot"),
    "text.news": ("fundamentals", "news"),
}

#: the spec §1 chain, verbatim (the CROSS-CHECK; the measured tree governs).
_SPEC_CHAIN_PREFIXES = {
    "P2-static": "b41bf223", "P3-data": "ba708692", "P3-full": "c13294e5",
    "P4": "aefe0cf3", "P5": "42af2460", "P6": "42af2460", "P7": "c760df02",
    "P8": "7f00dde4", "P9": "0c48db78", "P10": "ff4cdc61",
}

#: the two retired provider families' names, spelled ONLY by concatenation so
#: this file satisfies neither its own scan nor the two sibling repo-wide
#: tombstone pins (``test_l2b_handoff.py`` / ``data/test_subject_projection.py``
#: scan for the contiguous stems, so every split falls INSIDE a stem).
_RETIRED_PROVIDER_NEEDLES = (
    "Structurally" + "DeadRowDataProvider",
    "_row_is_structurally" + "_dead",
    "structurally" + "_dead_row_fact",
    "register_structurally" + "_dead_row_data_provider",
    "Worldless" + "DataBridgeProvider",
    "worldless" + "_data_provider_factory",
    "register" + "_worldless_data_provider",
)


# --------------------------------------------------------------------------- #
# ledger parsing — the document is a machine-checked artifact, not prose        #
# --------------------------------------------------------------------------- #
_LEDGER_TEXT = _LEDGER.read_text(encoding="utf-8")

_CHAIN_ROW = re.compile(
    r"^\|\s*(P[0-9A-Za-z-]+)\s*\|\s*`([0-9a-f]{64})`\s*\|[^|]*\|\s*(FROZEN|MOVES)\s*\|",
    re.MULTILINE)
_LITERAL_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`(tests/[^`:]+)`\s*\|\s*(\d+)\s*\|\s*(P[0-9A-Za-z-]+)\s*\|"
    r"\s*([0-9a-f]{8})\s*\|\s*(FROZEN|MOVES)\s*\|\s*(\w+)\s*\|",
    re.MULTILINE)
_METHOD_ROW = re.compile(
    r"^\|\s*([a-z_]+)\s*\|\s*([a-z0-9_.]+)\s*\|\s*(YES|NO)\s*\|\s*(YES|NO)\s*\|"
    r"\s*(\w+)\s*\|\s*(YES|NO)\s*\|",
    re.MULTILINE)
_MATERIAL_ROW = re.compile(
    r"^\|\s*`(bridge\.data_runtime\.\w+@1)`\s*\|\s*`([0-9a-f]{64})`\s*\|"
    r"\s*\*\*(不动|移动)\*\*",
    re.MULTILINE)
#: §4's allowed-red rows: | # | `file` | line(s) | `anchor` | verdict | notes |
#: The backtick groups forbid ``|`` so a malformed row can never swallow a column
#: and masquerade as a well-formed one.
_PIN_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*`([^`|]+)`\s*\|([^|]*)\|\s*`([^`|]+)`\s*\|([^|]*)\|",
    re.MULTILINE)
_NUMBERED_ROW = re.compile(r"^\|\s*\d+\s*\|", re.MULTILINE)

#: the CLOSED verdict vocabulary of §4. A fourth spelling is a gate failure: the
#: red-window discipline is "red only on FLIP / FLIP-IF rows", which is
#: meaningless if the column can carry free text.
_PIN_VERDICTS = frozenset({"FLIP", "FLIP-IF", "GREEN-VERIFY"})
_RECIPE_DIGEST = re.compile(
    r"`(source_registry|routing|source_config) = ([0-9a-f]{64})`")


def _ledger_chain() -> dict[str, tuple[str, str]]:
    """node -> (digest64, FROZEN|MOVES) from the ledger's §2 table."""
    rows = {m.group(1): (m.group(2), m.group(3))
            for m in _CHAIN_ROW.finditer(_LEDGER_TEXT)}
    assert rows, "the ledger's chain-digest table did not parse"
    return rows


def _ledger_literals() -> list[tuple[str, int, str, str, str, str]]:
    """(path, line, node, digest8, class, family) from the ledger's §3 table."""
    rows = [(m.group(2), int(m.group(3)), m.group(4), m.group(5),
             m.group(6), m.group(7))
            for m in _LITERAL_ROW.finditer(_LEDGER_TEXT)]
    assert rows, "the ledger's literal-inventory table did not parse"
    return rows


def _ledger_section(number: str) -> str:
    """The text of one top-level ledger section, so a table's parser can never
    accidentally reach across into a neighbouring section's rows."""
    start = _LEDGER_TEXT.index(f"\n## {number}.")
    nxt = _LEDGER_TEXT.find("\n## ", start + 1)
    return _LEDGER_TEXT[start:nxt if nxt != -1 else len(_LEDGER_TEXT)]


def _ledger_pins() -> list[tuple[int, str, str, str, str]]:
    """(#, path, lines, anchor, verdict) from the ledger's §4 allowed-red table.

    Also asserts NO numbered row was silently skipped by the regex — without
    that, a row that fails to parse would quietly leave the allowed-red set and
    the red-window discipline would be enforced against an incomplete list.
    """
    section = _ledger_section("4")
    rows = [(int(m.group(1)), m.group(2), m.group(3).strip(), m.group(4),
             m.group(5).strip())
            for m in _PIN_ROW.finditer(section)]
    numbered = _NUMBERED_ROW.findall(section)
    assert len(rows) == len(numbered), (
        f"§4 has {len(numbered)} numbered rows but only {len(rows)} parsed — a "
        "row fell out of the allowed-red set; restructure the row, never "
        "weaken the parser")
    # …and the count check alone is not enough: it keys BOTH sides on a leading
    # "| <digits> |" cell, so a row that simply omits its number escapes both.
    # Every table line must therefore be the header, the separator, or a row
    # that fully parses.
    unparsed = [line for line in section.splitlines()
                if line.lstrip().startswith("|")
                and not line.startswith("| # |")
                and not re.fullmatch(r"\|(\s*:?-+:?\s*\|)+", line.strip())
                and not _PIN_ROW.match(line)]
    assert unparsed == [], (
        f"§4 table lines that are neither header, separator nor a parseable "
        f"row (an unnumbered row escapes both counts): {unparsed}")
    assert rows, "the ledger's §4 allowed-red table did not parse"
    return rows


def _ledger_methods() -> dict[str, tuple[str, bool, bool, str, bool]]:
    """method -> (source_id, real_backend, optional, params_cls, satisfiable)."""
    rows = {m.group(1): (m.group(2), m.group(3) == "YES", m.group(4) == "YES",
                         m.group(5), m.group(6) == "YES")
            for m in _METHOD_ROW.finditer(_LEDGER_TEXT)}
    assert rows, "the ledger's adapter-inventory table did not parse"
    return rows


# --------------------------------------------------------------------------- #
# small real drivers                                                            #
# --------------------------------------------------------------------------- #
class _ProbeReached(Exception):
    """Sentinel: the real adapter accepted the method and reached transport."""


def _token(evidence: int) -> ExecutionEvidenceOrdinalToken:
    return ExecutionEvidenceOrdinalToken(
        attempt=1, call_ordinal=1, evidence_ordinal=evidence)


def _scope(route) -> DataInvocationScope:
    return DataInvocationScope(
        plan_digest="a" * 64, node_id="n", worker_id="w",
        operation_token=_token(1), attempt_tokens=(_token(2),),
        frozen_route=route, invocation_mode="always_invoke",
        catalog_digest="b" * 64, schema_registry_digest="c" * 64)


def _candidate_row(surface, method_id: str, worker_id: str) -> DataPrefetchOperation:
    """A candidate-shaped row for ``method_id``: the sealed row's shape with the
    candidate method's identity, its recipe-frozen route, and the closed
    projection's bindings mapped onto the params class's REQUIRED fields.

    Nothing here is a fixture standing in for a fact: the method ref, the
    capability ref and the route entry all come from the real reviewed surface.
    """
    spec = surface.spec_by_method[method_id]
    params_cls = RT._BINDING_BY_METHOD[method_id].params_cls
    required = sorted(
        n for n, f in params_cls.model_fields.items()
        if f.is_required() and n != "schema_version")
    pointer_for = {"as_of": "/asof_date", "start": "/asof_date",
                   "end": "/asof_date", "symbols": "/code", "symbol": "/code"}
    targets = list(required)
    if "symbols" in params_cls.model_fields and "symbols" not in targets:
        targets.append("symbols")   # bind the universe even where it defaults
    bindings = tuple(sorted(
        (ParamBinding(target_pointer=f"/{name}", source_kind="node_param",
                      source_pointer=pointer_for[name]) for name in targets),
        key=lambda b: b.target_pointer))
    return DataPrefetchOperation(
        worker_id=worker_id, method_ref=spec.method_ref,
        capability_ref=spec.capability_ref,
        frozen_route=(RouteEntry(source_ref=surface.source_ref,
                                 capability_ref=spec.capability_ref),),
        invocation_mode="always_invoke", success_requires_finalized_call=True,
        param_bindings=bindings)


def _row_augmented_materials(surface):
    """The sealed data-bridge materials REBUILT over a binding that carries one
    extra candidate row — the exact edit D-1/D-2 will make.

    Returns ``(binding, config_ref, config_material, descriptor, desc_ref,
    desc_material)``. This is the one place the "which material digests move"
    question is answered by construction rather than by assertion: the caller
    compares these refs against the sealed surface's.
    """
    row = _candidate_row(surface, "verified_snapshot", "pv.technical")
    binding = DataBridgePrefetchBinding.build(
        bridge_id=DC.DATA_BRIDGE_ID, bridge_version="1",
        operations=tuple(surface.prefetch_binding.operations) + (row,))
    config_ref, config_material = build_text_material(
        id="bridge.data_runtime.prefetch", version="1", kind="guardrail",
        raw=serialize_prefetch_binding(binding))
    descriptor = surface.bridge_descriptor.model_copy(
        update={"config_ref": config_ref})
    desc_ref, desc_material = build_text_material(
        id="bridge.data_runtime.descriptor", version="1", kind="guardrail",
        raw=serialize_bridge_descriptor(descriptor))
    return binding, config_ref, config_material, descriptor, desc_ref, desc_material


def _measured_material_verdicts(surface) -> dict[str, tuple[str, str]]:
    """key -> (sealed digest, MEASURED 不动/移动) for the four bridge materials.

    Both halves take the binding as a real input, so both are falsifiable:

    * **移动 (prefetch / descriptor)** — rebuilt over the row-augmented binding
      and compared against the sealed refs; the digests genuinely differ.
    * **不动 (provider / analyzer)** — **INDEPENDENCE**: neither the sealed nor
      the row-augmented binding serialization occurs anywhere in the material's
      bytes, so no edit to the binding can reach them.

    Deliberately NOT how the 不动 half is measured: rebuilding a material from
    its OWN bytes and observing the digest is unchanged. That is a tautology
    (``build_text_material`` is a pure function of its input) — the binding never
    enters the computation, so no falsification could ever flip it. It was the
    first shape of this helper and it proved nothing.
    """
    binding, config_ref, _cm, _desc, desc_ref, _dm = _row_augmented_materials(surface)
    sealed_bytes = serialize_prefetch_binding(surface.prefetch_binding)
    augmented_bytes = serialize_prefetch_binding(binding)
    verdicts: dict[str, tuple[str, str]] = {}
    for key, ref, material in (
        ("bridge.data_runtime.provider@1", surface.provider_ref,
         surface.provider_material),
        ("bridge.data_runtime.analyzer@1", surface.analyzer_ref,
         surface.analyzer_material),
    ):
        independent = (sealed_bytes not in material.raw_utf8
                       and augmented_bytes not in material.raw_utf8)
        verdicts[key] = (ref.content_digest, "不动" if independent else "移动")
    verdicts["bridge.data_runtime.prefetch@1"] = (
        surface.config_ref.content_digest,
        "移动" if config_ref.content_digest != surface.config_ref.content_digest
        else "不动")
    verdicts["bridge.data_runtime.descriptor@1"] = (
        surface.descriptor_ref.content_digest,
        "移动" if desc_ref.content_digest != surface.descriptor_ref.content_digest
        else "不动")
    return verdicts


# --------------------------------------------------------------------------- #
# module fixtures (the real sealed artifacts, built once)                       #
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def surface():
    return phase3_data_surface()


@pytest.fixture(scope="module")
def recipe():
    return DW.production_data_recipe()


@pytest.fixture(scope="module")
def subject_params():
    """The production projection recipe, from a REAL committed ``RunSubject@1``."""
    subject = RunSubject(code=_CODE, as_of=_AS_OF)
    return RT.SubjectParams.project(code=subject.code, as_of=subject.as_of)


@pytest.fixture(scope="module")
def bundle():
    """The RAW production catalog bundle — exactly ``live_decide``'s construction."""
    return ASM.build_production_catalog_runtime(chain.phase9_catalog_snapshot())


# =========================================================================== #
# GATE 1 — L1 landed                                                           #
# =========================================================================== #
class TestGate1L1Landed:
    def test_the_closed_pointer_set_is_the_two_pointer_l1_closure(self):
        assert RT.SUBJECT_PARAM_POINTERS == frozenset({"/asof_date", "/code"})

    def test_the_one_reviewed_grant_is_still_the_single_pm_row(self, surface):
        assert DC._REVIEWED_INTEGRATION_GRANTS == {"dec.pm": ("verified_snapshot",)}
        rows = surface.prefetch_binding.operations
        assert [(r.worker_id, r.method_ref.id) for r in rows] == [
            ("dec.pm", "verified_snapshot")]

    def test_the_real_sealed_row_is_runnable_from_a_committed_run_subject(
            self, surface, subject_params):
        """The whole of gate item 1: a committed ``RunSubject@1`` -> the ONE
        reviewed projection -> the REAL ``_assemble_params`` over the REAL
        sealed row and a legally-shaped params-LESS ``dec.pm`` node -> the
        row's REAL params class validates. Zero ``param source pointer … does
        not resolve``.

        If L1 had not landed this raises, and the gate STOPs the plan."""
        row = surface.prefetch_binding.operations[0]
        node = PlanNode(id="pm", worker_id="dec.pm", writes_slot="slot-pm")
        assert node.params == {}, "the pm node must stay structurally params-less"
        doc = RT._assemble_params(row, node, subject_params=subject_params)
        params_cls = RT._BINDING_BY_METHOD["verified_snapshot"].params_cls
        params = params_cls.model_validate(doc)
        assert [s.code for s in params.symbols] == [_CODE]
        assert datetime.fromisoformat(params.as_of) == _AS_OF

    def test_the_projection_call_site_is_the_production_deep_lane_one(self):
        """The gate drives the same recipe production drives — pinned at the
        source so this file can never test a private re-implementation."""
        src = re.sub(r"\s+", " ", inspect.getsource(DD))
        assert ("subject_params = SubjectParams.project( code=subject.code, "
                "as_of=subject.as_of)") in src

    def test_the_ff26c23_declared_not_runnable_pins_were_consciously_flipped(self):
        """STOP condition, executable. ``ff26c23`` pinned the row as
        DECLARED-but-NOT-RUNNABLE; L1's conscious flip renamed the class and
        both of its load-bearing tests. If the pre-flip names are still there,
        L1 has not landed and no later L3 task may start.

        The pre-flip names are spelled by concatenation for symmetry with the
        repo's tombstone idiom — an absence assertion must not be satisfiable
        by this file's own bytes if the scan ever widens."""
        text = (_REPO_ROOT / "tests" / "orchestration" / "data"
                / "test_data_catalog.py").read_text(encoding="utf-8")
        for pre in (
            "TestVerifiedSnapshotRowIs" + "DeclaredNotRunnable",
            "test_the_real_runtime_projection" + "_raises_on_the_first_binding",
            "test_the_data_bridge_provider" + "_has_no_production_caller",
        ):
            assert pre not in text, (
                f"the ff26c23 DECLARED-but-NOT-RUNNABLE pin {pre!r} is still in "
                "the tree: L1 has NOT landed — STOP")
        for post in (
            "TestVerifiedSnapshotRowRunnableOnlyUnderSubjectProjection",
            "test_with_the_subject_the_same_real_row_resolves_and_validates",
            "test_the_data_bridge_provider_has_exactly_one_production_caller",
        ):
            assert post in text, f"L1's flipped pin is missing: {post!r}"


# =========================================================================== #
# GATE 2 — L2-b landed                                                         #
# =========================================================================== #
class TestGate2L2bLanded:
    def test_exactly_one_production_data_runtime_world_construction(self):
        """A production ``DataRuntimeWorld`` is constructed OUTSIDE ``tests/``,
        and exactly once: ``ProductionDataWorldResolver.world_for``. Zero
        constructions = L2-b has not landed (STOP); a second one = the per-run
        world grew a competing builder and the gate must be re-pinned
        consciously."""
        pkg = _REPO_ROOT / "guanlan_v2"
        sites: list[str] = []
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for n in ast.walk(tree):
                if not isinstance(n, ast.Call):
                    continue
                fn = n.func
                named = (fn.id if isinstance(fn, ast.Name)
                         else fn.attr if isinstance(fn, ast.Attribute) else None)
                if named == "DataRuntimeWorld":
                    sites.append(
                        f"{path.relative_to(_REPO_ROOT).as_posix()}:{n.lineno}")
        assert len(sites) == 1, (
            f"expected exactly one production DataRuntimeWorld construction, "
            f"found {sites}")
        assert sites[0].startswith(
            "guanlan_v2/orchestration/adapters/data_world.py:")

    def test_the_zero_production_callers_pin_is_flipped_to_the_one_caller(self):
        """L2-b Task 4's conscious flip, re-measured here rather than trusted:
        ``data_runtime_provider_factory``'s occurrences in the orchestration
        package are its own ``def`` plus the ONE delegation in
        ``adapters/data_world.py``. Still zero callers = L2-b has not landed."""
        pkg = Path(RT.__file__).resolve().parent.parent
        assert pkg.name == "orchestration"
        refs: dict[str, list[str]] = {}
        for path in sorted(pkg.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for n in ast.walk(tree):
                named = (n.name if isinstance(n, (ast.FunctionDef,
                                                  ast.AsyncFunctionDef))
                         else n.id if isinstance(n, ast.Name)
                         else n.attr if isinstance(n, ast.Attribute)
                         else n.name if isinstance(n, ast.alias) else None)
                if named == "data_runtime_provider_factory":
                    refs.setdefault(
                        path.relative_to(pkg).as_posix(), []).append(
                            type(n).__name__)
        assert sorted(refs) == ["adapters/data_world.py", "data/runtime.py"]
        assert refs["data/runtime.py"] == ["FunctionDef"]

    def test_both_retired_provider_families_are_gone_repo_wide(self):
        """The dead-row provider (L1) AND its worldless successor (L2-b Task 5)
        are retired per their tombstones: neither family's names exist in any
        ``.py`` under ``guanlan_v2/`` or ``tests/``. L3 grants rows; a surviving
        provider that fabricates emptiness or refuses worldlessly would route
        those real reads into a lie."""
        offenders: list[str] = []
        for base in ("guanlan_v2", "tests"):
            for path in sorted((_REPO_ROOT / base).rglob("*.py")):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for needle in _RETIRED_PROVIDER_NEEDLES:
                    if needle in text:
                        offenders.append(
                            f"{path.relative_to(_REPO_ROOT).as_posix()}: {needle}")
        assert offenders == [], (
            f"a retired data provider came back: {offenders}")

    def test_the_registered_incumbent_on_the_production_bundle_is_production(
            self, bundle, surface):
        """The ONE production registration recipe, driven for real: after
        ``register_production_data_provider`` the factory resolved at the sealed
        ``bridge.data_runtime.provider@1`` identity constructs a
        ``ProductionDataProvider``. Poisoned stores/resolver prove registration
        touches neither (they are carried into the per-run resolver)."""
        poison = SimpleNamespace()
        factories = TrustedFactoryRegistry(bundle.runtime)
        DW.register_production_data_provider(
            factories=factories, stores=poison, schema_resolver=poison,
            clock=SimpleNamespace(now=lambda: _AS_OF),
            catalog_runtime=bundle.runtime)
        provider = factories.handler_factory(surface.provider_ref)(
            bridge=SimpleNamespace(bridge_id="data.runtime", priority=100),
            summary=SimpleNamespace(summary_digest="s" * 64))
        assert isinstance(provider, DW.ProductionDataProvider)
        # …and the production caller is the ONE-recipe seam.
        assert "register_production_data_provider(" in inspect.getsource(LD)

    def test_the_per_run_view_override_target_is_the_production_factory(self):
        """The L1<->L2-b seam: the per-run ``_SubjectScopedFactories`` view is
        built over ``production_data_provider_factory(subject_params, …)``,
        keyed on the sealed provider ref. Both halves are asserted so a
        half-wired revert reddens here."""
        src = re.sub(r"\s+", " ", inspect.getsource(ASM.build_production_plan_runner))
        assert "_SubjectScopedFactories( bundle.factories," in src
        assert "provider_ref=phase3_data_surface().provider_ref," in src
        assert "factory=production_data_provider_factory( subject_params," in src
        assert ASM._SubjectScopedFactories is not None


# =========================================================================== #
# GATE 3 — the adapter inventory (feeds D-1)                                   #
# =========================================================================== #
class TestGate3AdapterInventory:
    def test_the_world_registers_exactly_one_adapter_under_the_sealed_identity(
            self, surface):
        adapters = DW.production_data_adapters()
        assert sorted(adapters) == [surface.source_ref.id]
        assert isinstance(adapters[surface.source_ref.id], LiveClientSource)

    @pytest.mark.parametrize("method_id", _SEVEN)
    def test_each_method_is_classified_real_backend_or_no_backend(
            self, method_id, surface, recipe):
        """BEHAVIOURAL, not a private-constant read: stage the recipe-frozen
        route on the REAL thread-confined backend whose adapter is a REAL
        ``LiveClientSource``, with a sentinel ``probe_fn``.

        * sentinel raised  -> the adapter accepted the method and reached the
          transport = a REAL registered backend;
        * ``RoutingConfigurationError('… not bound to this source')`` before any
          probe -> NO backend.

        The ledger's §5 table is the frozen answer; this asserts equality."""
        spec = surface.spec_by_method[method_id]
        route = recipe.registry.default_route(method_id)
        backend = DW.ThreadConfinedDataBackend({
            surface.source_ref.id: LiveClientSource(
                probe_fn=lambda *a, **k: (_ for _ in ()).throw(_ProbeReached()),
                resolve_source_fn=lambda s: s)})
        backend.stage(route.entries[0].source_ref, _scope(route))
        request = SimpleNamespace(method_spec_ref=spec.method_ref, params={})
        try:
            backend.invoke(capability_ref=spec.capability_ref, request=request)
        except _ProbeReached:
            real_backend = True
        except RoutingConfigurationError as exc:
            assert "is not bound to this source" in str(exc)
            real_backend = False
        else:  # pragma: no cover - defensive
            pytest.fail("the staged invoke neither reached transport nor refused")

        recorded = _ledger_methods()[method_id]
        assert recorded[0] == route.entries[0].source_ref.id
        assert recorded[1] is real_backend, (
            f"ledger says real_backend={recorded[1]} for {method_id!r}; the "
            f"tree measures {real_backend}")
        assert recorded[2] is spec.optional
        assert recorded[3] == RT._BINDING_BY_METHOD[method_id].params_cls.__name__

    def test_the_measured_real_backend_set_is_the_three_supported_methods(
            self):
        real = {m for m, r in _ledger_methods().items() if r[1]}
        assert real == {"verified_snapshot", "news", "ohlcv"}
        # the 07-29 spec statement, re-verified rather than assumed:
        assert not _ledger_methods()["indicators"][1]
        assert not _ledger_methods()["fundamentals"][1]


# =========================================================================== #
# GATE 4 — the baseline chain digests + the frozen literal inventory            #
# =========================================================================== #
def _measured_chain() -> dict[str, str]:
    from guanlan_v2.orchestration.bootstrap import phase5_catalog_snapshot
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot
    from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot
    from guanlan_v2.orchestration.memory.catalog import phase3_full_catalog_snapshot
    from guanlan_v2.orchestration.phase7_registry import phase7_catalog_snapshot
    from guanlan_v2.orchestration.pipeline.chain import phase10_catalog_snapshot
    from guanlan_v2.orchestration.shadow import phase6_catalog_snapshot
    from guanlan_v2.orchestration.trial import phase4_catalog_snapshot

    return {
        "P2-static": load_compat_catalog().snapshot.catalog_digest,
        "P3-data": phase3_data_catalog_snapshot().catalog_digest,
        "P3-full": phase3_full_catalog_snapshot().catalog_digest,
        "P4": phase4_catalog_snapshot().catalog_digest,
        "P5": phase5_catalog_snapshot().catalog_digest,
        "P6": phase6_catalog_snapshot().catalog_digest,
        "P7": phase7_catalog_snapshot().catalog_digest,
        "P8": phase8_catalog_snapshot().catalog_digest,
        "P9": chain.phase9_catalog_snapshot().catalog_digest,
        "P10": phase10_catalog_snapshot().catalog_digest,
    }


#: text suffixes scanned for chain-digest literals. Deliberately wider than the
#: golden/handoff family's ``.py``/``.json``: a chain digest pasted into a README,
#: a fixture manifest or a config under ``tests/`` is just as much a literal
#: Task 5 must re-freeze, and a narrow scan would hide it from the
#: both-directions equality below.
_TEXT_SUFFIXES = (".py", ".json", ".md", ".txt", ".yaml", ".yml", ".cfg",
                  ".ini", ".toml")


def _tree_literals() -> list[tuple[str, int, str]]:
    """Every chain-digest literal under ``tests/`` — (path, line, digest64)."""
    by_digest = {d: node for node, (d, _) in _ledger_chain().items()}
    hits: list[tuple[str, int, str]] = []
    root = _REPO_ROOT / "tests"
    for path in sorted(root.rglob("*")):
        if path.suffix not in _TEXT_SUFFIXES or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for i, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for digest in by_digest:
                if digest in line:
                    hits.append((rel, i, digest))
    return sorted(hits)


class TestGate4BaselineDigests:
    def test_the_measured_chain_equals_the_spec_section_1_values(self):
        """The plan's riskiest assumption, made executable: if ANY chain digest
        differs from spec §1, the 07-29 blast-radius inventory is stale and every
        count in the plan must be re-derived before proceeding."""
        measured = _measured_chain()
        drifted = {node: (digest, _SPEC_CHAIN_PREFIXES[node])
                   for node, digest in measured.items()
                   if not digest.startswith(_SPEC_CHAIN_PREFIXES[node])}
        assert drifted == {}, (
            "sealed chain drift vs spec §1 — the 07-29 blast-radius inventory "
            f"is stale and the plan's counts must be re-derived: {drifted}")
        assert measured["P6"] == measured["P5"], "P6 is the identity node"

    def test_the_ledger_records_exactly_the_measured_chain(self):
        recorded = _ledger_chain()
        measured = _measured_chain()
        assert {n: d for n, (d, _) in recorded.items()} == measured
        frozen = {n for n, (_, cls) in recorded.items() if cls == "FROZEN"}
        assert frozen == {"P2-static"}, (
            "exactly one chain node does not move this phase (P2); the ledger "
            f"marks {sorted(frozen)}")

    def test_the_bridge_material_movement_is_measured_not_declared(self, surface):
        """The 移动 half, derived by construction: rebuild the prefetch config +
        descriptor over a binding carrying one extra candidate row — exactly the
        edit D-1/D-2 makes — and the digests genuinely differ from the sealed
        ones.

        The plan header said all FOUR move; this is the measurement that says
        two."""
        _b, config_ref, _cm, _d, desc_ref, _dm = _row_augmented_materials(surface)
        assert config_ref.content_digest != surface.config_ref.content_digest, (
            "adding a prefetch row did not change the prefetch material bytes — "
            "the whole 'the binding edit moves this digest' claim is wrong")
        assert desc_ref.content_digest != surface.descriptor_ref.content_digest, (
            "the descriptor embeds config_ref, so it must move with it")

    def test_the_provider_and_analyzer_materials_are_binding_INDEPENDENT(
            self, surface):
        """The 不动 half, with the binding as a real input.

        Rebuilding a material from its own bytes and finding the digest unchanged
        proves nothing — ``build_text_material`` is a pure function of its input,
        so the binding never enters the computation. The falsifiable statement is
        INDEPENDENCE: neither the sealed nor the row-augmented binding
        serialization occurs anywhere in the provider/analyzer bytes, so no edit
        to the binding can reach them.

        The prefetch material is the POSITIVE CONTROL: it does embed the sealed
        serialization, which is what proves the "contains" probe is sensitive
        rather than vacuously true for every input."""
        binding, *_ = _row_augmented_materials(surface)
        sealed_bytes = serialize_prefetch_binding(surface.prefetch_binding)
        augmented_bytes = serialize_prefetch_binding(binding)
        assert sealed_bytes != augmented_bytes

        # positive control — the probe CAN find a binding inside a material.
        assert sealed_bytes in surface.config_material.raw_utf8, (
            "the prefetch material no longer embeds its binding; the "
            "independence probe below would then be vacuous")

        for ref, material in ((surface.provider_ref, surface.provider_material),
                              (surface.analyzer_ref, surface.analyzer_material)):
            assert sealed_bytes not in material.raw_utf8, (
                f"{ref.id} embeds the sealed prefetch binding — it is NOT "
                "binding-independent and would move with a grant")
            assert augmented_bytes not in material.raw_utf8, ref.id

    def test_the_provider_and_analyzer_builders_take_no_binding_argument(self):
        """Independence pinned at the source too, so a future refactor that
        starts deriving these materials from the binding reddens here instead of
        silently invalidating the 不动 verdict.

        The contrast is the point: provider/analyzer are built from MODULE
        CONSTANTS, the prefetch material from ``serialize_prefetch_binding``."""
        src = re.sub(r"\s+", " ", inspect.getsource(DC._Phase3DataSurface))
        assert ('build_text_material( id="bridge.data_runtime.provider", '
                'version="1", kind="handler", raw=_PROVIDER_BYTES)') in src
        assert ('build_text_material( id="bridge.data_runtime.analyzer", '
                'version="1", kind="handler", raw=_ANALYZER_BYTES)') in src
        # …and the prefetch material IS binding-derived (the contrast half).
        assert "config_bytes = serialize_prefetch_binding(self.prefetch_binding)" in src
        assert 'kind="guardrail", raw=config_bytes)' in src
        # the two raw= arguments resolve to module-level byte constants, and the
        # sealed materials really carry them byte-for-byte (so nothing
        # per-binding can have flowed in).
        surface = phase3_data_surface()
        assert isinstance(DC._PROVIDER_BYTES, bytes)
        assert isinstance(DC._ANALYZER_BYTES, bytes)
        assert surface.provider_material.raw_utf8 == DC._PROVIDER_BYTES
        assert surface.analyzer_material.raw_utf8 == DC._ANALYZER_BYTES

    def test_the_ledger_records_the_measured_bridge_material_verdicts(
            self, surface):
        recorded = {m.group(1): (m.group(2), m.group(3))
                    for m in _MATERIAL_ROW.finditer(_LEDGER_TEXT)}
        measured = _measured_material_verdicts(surface)
        assert sorted(recorded) == sorted(measured)
        assert recorded == measured, (
            f"ledger vs measured bridge-material verdicts: {recorded} != {measured}")
        # the ledger's "11 digests" reconciliation now rests on a MEASURED
        # verdict: 9 distinct chain values + the materials measured to move.
        distinct_chain = {d for d, _ in _ledger_chain().values()}
        moving_materials = {k for k, v in measured.items() if v[1] == "移动"}
        assert len(distinct_chain) == 9
        assert len(moving_materials) == 2
        assert len(distinct_chain) + len(moving_materials) == 11

    def test_the_ledger_records_the_l2b_refrozen_recipe_digests(self, recipe):
        recorded = {m.group(1): m.group(2)
                    for m in _RECIPE_DIGEST.finditer(_LEDGER_TEXT)}
        assert recorded == {
            "source_registry": recipe.source_registry_digest,
            "routing": recipe.routing_snapshot_digest,
            "source_config": recipe.source_config_digest,
        }
        # they are NOT chain digests — attribution, not drift.
        assert recorded["source_registry"] not in {
            d for d, _ in _ledger_chain().values()}

    def test_the_frozen_literal_inventory_equals_what_the_tree_carries(self):
        """Both directions, literal by literal: every row in the ledger's §3
        table still names a real occurrence at that exact file:line with that
        exact digest, and every occurrence in the tree is in the table. This is
        what makes the document Task 5's regeneration checklist rather than a
        snapshot that silently rots."""
        by_node = {n: d for n, (d, _) in _ledger_chain().items()}
        # the literal table labels the identity node "P5-P6" (one value, two
        # chain positions); the chain table lists P5 and P6 separately.
        assert by_node["P5"] == by_node["P6"]
        by_node["P5-P6"] = by_node["P5"]
        recorded = {(p, ln, by_node[node]) for p, ln, node, _d8, _c, _f
                    in _ledger_literals()}
        measured = set(_tree_literals())
        assert recorded == measured, (
            f"inventory drift — only in ledger: {sorted(recorded - measured)}; "
            f"only in tree: {sorted(measured - recorded)}")

    def test_every_recorded_literal_carries_the_right_moves_verdict(self):
        """The ``class`` column is load-bearing, not decoration: Task 5 rewrites
        every MOVES row and must leave every FROZEN row alone. Each row's
        verdict is re-derived from the chain table's own FROZEN/MOVES column."""
        chain = _ledger_chain()
        aliases = {"P5-P6": "P5"}
        for path, line, node, _d8, cls, _fam in _ledger_literals():
            expected = chain[aliases.get(node, node)][1]
            assert cls == expected, (
                f"{path}:{line} is recorded {cls} but chain node {node} is "
                f"{expected}")

    def test_the_inventory_counts_are_the_spec_16_42_11(self):
        rows = _ledger_literals()
        family = [r for r in rows if r[5] in ("golden", "handoff")]
        moving_files = {r[0] for r in rows if r[4] == "MOVES"}
        assert len(family) == 42, (
            "spec §1 counted 42 chain-digest literals across the golden + "
            f"handoff family; measured {len(family)}")
        assert len(moving_files) == 16, (
            "spec §1 counted 16 files carrying a moving literal (9 golden + 7 "
            f"handoff); measured {len(moving_files)}")
        assert sum(1 for f in moving_files if "/golden/" in f) == 9
        assert sum(1 for f in moving_files if "handoff" in f) == 7
        # the two out-of-family / non-moving carriers must stay untouched by
        # Task 5 — they pin P2, the one chain node this phase may not move.
        frozen_carriers = {r[0] for r in rows if r[4] == "FROZEN"}
        assert "tests/orchestration/data/test_phase_handoff.py" in frozen_carriers
        assert ("tests/orchestration/fixtures/stock_deep_dive_equivalence_v1.json"
                in frozen_carriers)


# =========================================================================== #
# GATE 5 — pointer-set satisfiability (feeds D-1, seam-review finding 4)        #
# =========================================================================== #
class TestGate5PointerSatisfiability:
    @pytest.mark.parametrize("worker_id,method_id", sorted(
        (w, m) for w, ms in _D1_CANDIDATES.items() for m in ms))
    def test_each_d1_candidate_method_is_classified_by_the_real_round_trip(
            self, worker_id, method_id, surface, subject_params):
        """The L1 real-row round-trip idiom applied to every D-1 candidate:
        real ``_assemble_params`` over a candidate-shaped row + a projected
        subject, then the method's REAL ``params_cls.model_validate``.

        Satisfiable = the closed ``SUBJECT_PARAM_POINTERS`` can serve the row.
        Unsatisfiable = L1-MODULE MOVEMENT (widen the pointer set + the
        projection recipe), which is a named cost in the D-1 packet and is never
        improvised at Task 3."""
        row = _candidate_row(surface, method_id, worker_id)
        node = PlanNode(id="n", worker_id=worker_id, writes_slot="slot-n")
        params_cls = RT._BINDING_BY_METHOD[method_id].params_cls
        try:
            doc = RT._assemble_params(row, node, subject_params=subject_params)
            params_cls.model_validate(doc)
        except Exception:
            satisfiable = False
        else:
            satisfiable = True
        assert _ledger_methods()[method_id][4] is satisfiable, (
            f"ledger disagrees with the tree on {method_id!r} satisfiability")

    def test_the_universe_scoped_candidates_validate_for_real(
            self, surface, subject_params):
        for worker_id, method_id in (("pv.technical", "verified_snapshot"),
                                     ("text.news", "news")):
            row = _candidate_row(surface, method_id, worker_id)
            node = PlanNode(id="n", worker_id=worker_id, writes_slot="slot-n")
            doc = RT._assemble_params(row, node, subject_params=subject_params)
            params = RT._BINDING_BY_METHOD[method_id].params_cls.model_validate(doc)
            assert [s.code for s in params.symbols] == [_CODE]
            assert datetime.fromisoformat(params.as_of) == _AS_OF

    def test_the_series_scoped_candidates_fail_on_the_element_vs_set_mismatch(
            self, surface, subject_params):
        """The exact cause, not just the verdict: ``InstrumentSeriesParams.symbol``
        is a SINGULAR ``Symbol`` while the closed ``/code`` pointer serves the
        singleton TUPLE. Assembly succeeds; validation refuses."""
        from pydantic import ValidationError

        for worker_id, method_id in (("pv.technical", "indicators"),
                                     ("text.news", "fundamentals")):
            row = _candidate_row(surface, method_id, worker_id)
            node = PlanNode(id="n", worker_id=worker_id, writes_slot="slot-n")
            doc = RT._assemble_params(row, node, subject_params=subject_params)
            assert sorted(doc) == ["end", "start", "symbol"]
            with pytest.raises(ValidationError) as exc:
                RT._BINDING_BY_METHOD[method_id].params_cls.model_validate(doc)
            assert "symbol" in str(exc.value)
            assert "instance of Symbol" in str(exc.value)

    def test_the_element_pointer_is_outside_the_closure_and_is_refused(
            self, surface, subject_params):
        """The obvious workaround is closed too: ``/code/0`` would index the
        singleton tuple, but the closure is an EXACT membership test, so
        ``_assemble_params`` refuses it by name. Serving it = L1-module
        movement, priced separately in the D-1 packet."""
        spec = surface.spec_by_method["indicators"]
        row = surface.prefetch_binding.operations[0].model_copy(update={
            "worker_id": "pv.technical",
            "method_ref": spec.method_ref,
            "capability_ref": spec.capability_ref,
            "frozen_route": (RouteEntry(source_ref=surface.source_ref,
                                        capability_ref=spec.capability_ref),),
            "param_bindings": (
                ParamBinding(target_pointer="/end", source_kind="node_param",
                             source_pointer="/asof_date"),
                ParamBinding(target_pointer="/start", source_kind="node_param",
                             source_pointer="/asof_date"),
                ParamBinding(target_pointer="/symbol", source_kind="node_param",
                             source_pointer="/code/0"),
            )})
        node = PlanNode(id="n", worker_id="pv.technical", writes_slot="slot-n")
        with pytest.raises(RT.DataRuntimeError) as exc:
            RT._assemble_params(row, node, subject_params=subject_params)
        msg = str(exc.value)
        assert "'/code/0'" in msg
        assert "closed pointer set" in msg
        assert "refused, never guessed" in msg

    def test_the_series_params_need_a_second_temporal_pointer(self):
        """The second, independent unsatisfiability: the closed set has ONE time
        pointer (``/asof_date``) while ``InstrumentSeriesParams`` needs both
        ``start`` and ``end``. Binding both to the one pointer is structurally
        valid but is a ZERO-LENGTH window — satisfiability in appearance only."""
        cls = RT._BINDING_BY_METHOD["indicators"].params_cls
        required = {n for n, f in cls.model_fields.items()
                    if f.is_required() and n != "schema_version"}
        assert {"start", "end"} <= required
        temporal_pointers = {p for p in RT.SUBJECT_PARAM_POINTERS
                             if "date" in p or "time" in p}
        assert temporal_pointers == {"/asof_date"}


# =========================================================================== #
# GATE 6 — the grant site (Task-1 discovery; ledger §7.0)                       #
# =========================================================================== #
class TestGate6GrantSiteReality:
    def test_the_one_to_one_invariant_scopes_granted_to_the_phase2_worker_set(
            self):
        """The measured scope of ``catalog.py``'s ``granted != rows`` check: it
        derives ``granted`` from the end-of-Phase-2 snapshot's workers, and
        NEITHER D-1 candidate worker is in that set (they are Phase-8 workers).
        So the plan's "literal one-to-one forces FOUR rows" does not follow —
        D-1 must first rule WHERE the grant lives."""
        base_ids = {w.id for w in load_compat_catalog().snapshot.workers}
        for worker_id in _D1_CANDIDATES:
            assert worker_id not in base_ids, (
                f"{worker_id!r} unexpectedly entered the Phase-3 base worker "
                "set; ledger §7.0 must be re-measured")
        from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot
        p8_ids = {w.id for w in phase8_catalog_snapshot().workers}
        assert set(_D1_CANDIDATES) <= p8_ids
        # …and the check really is derived over that snapshot's workers.
        src = re.sub(r"\s+", " ", inspect.getsource(DC.build_phase3_catalog))
        assert "for w in phase2_snapshot.workers" in src
        assert "if granted != rows:" in src

    def test_the_grant_machinery_cannot_name_a_post_phase2_worker(self):
        """``phase3_data_catalog_snapshot`` resolves each granted worker id out
        of the Phase-3 BASE catalog with a bare ``next(...)`` — so adding a
        Phase-8 worker to the reviewed grants raises ``StopIteration`` rather
        than granting anything. Asserted structurally (the global dict is never
        mutated by this gate)."""
        src = re.sub(r"\s+", " ", inspect.getsource(DC.phase3_data_catalog_snapshot))
        assert ("base_worker = next(w for w in base.workers if w.id == worker_id)"
                in src)
        base_ids = {w.id for w in load_compat_catalog().snapshot.workers}
        assert not (set(_D1_CANDIDATES) & base_ids)

    def test_a_candidate_row_alone_trips_the_invariant_in_the_rows_direction(
            self, surface):
        """The other direction, driven for real through the REAL
        ``build_phase3_catalog``: adding ONLY a prefetch row for a Phase-8
        worker (materials rebuilt so the row actually reaches the check) is
        refused as a row without a grant. This is the executable cost every D-1
        option inherits."""
        (binding, _config_ref, config_material, descriptor, _desc_ref,
         desc_material) = _row_augmented_materials(surface)

        runtime = load_compat_catalog()
        base = runtime.snapshot
        p_text, p_caps = runtime.resolved_materials()
        updates = []
        for worker_id, method_ids in DC._REVIEWED_INTEGRATION_GRANTS.items():
            base_worker = next(w for w in base.workers if w.id == worker_id)
            grants = tuple(surface.spec_by_method[m].capability_ref
                           for m in method_ids)
            fields = {n: getattr(base_worker, n)
                      for n in type(base_worker).model_fields}
            fields["capability_allowlist"] = tuple(sorted(
                tuple(base_worker.capability_allowlist) + grants,
                key=lambda c: (c.id, c.version)))
            updates.append(WorkerSpec(**fields))
        kept = tuple(m for m in surface.text_materials()
                     if m.ref.id not in ("bridge.data_runtime.prefetch",
                                         "bridge.data_runtime.descriptor"))
        with pytest.raises(CatalogError) as exc:
            DC.build_phase3_catalog(
                base, reviewed_worker_updates=tuple(updates),
                reviewed_source_descriptors=(surface.source_descriptor,),
                reviewed_method_specs=surface.method_specs,
                data_bridge_descriptor=descriptor,
                data_bridge_prefetch_binding=binding,
                resolved_materials=tuple(p_text) + tuple(p_caps) + kept
                + (config_material, desc_material) + surface.capability_materials)
        msg = str(exc.value)
        assert "one-to-one" in msg
        assert "rows without a grant" in msg
        assert "pv.technical" in msg

    def test_the_seventeen_p8_grants_stand_against_one_sealed_row(self, surface):
        """The scope fact in numbers, so D-1(0)'s cost is not a guess: the
        Phase-10 catalog carries SEVENTEEN (worker, data-capability) grants and
        the sealed binding carries ONE row. The invariant sees 1 vs 1 because it
        only ever looks at the five Phase-2 workers."""
        from guanlan_v2.orchestration.data.catalog import data_capability_refs
        from guanlan_v2.orchestration.pipeline.chain import phase10_catalog_snapshot

        method_by_cap = {(c.id, c.version): mid
                         for mid, c in data_capability_refs().items()}
        grants = {(w.id, c.id) for w in phase10_catalog_snapshot().workers
                  for c in w.capability_allowlist if (c.id, c.version) in method_by_cap}
        assert len(grants) == 17, sorted(grants)
        assert len(surface.prefetch_binding.operations) == 1

        # MINOR #4 — the frozen candidate table must EQUAL the live allowlist ∩
        # data capabilities, not merely be contained in it. A grown allowlist
        # would otherwise silently stale §5/§6's per-method pricing (a method
        # nobody measured for backend or pointer satisfiability).
        live: dict[str, set[str]] = {}
        for worker in phase10_catalog_snapshot().workers:
            methods = {method_by_cap[(c.id, c.version)]
                       for c in worker.capability_allowlist
                       if (c.id, c.version) in method_by_cap}
            if methods:
                live[worker.id] = methods
        for worker_id, methods in _D1_CANDIDATES.items():
            assert live[worker_id] == set(methods), (
                f"{worker_id!r}'s live data-capability allowlist is "
                f"{sorted(live[worker_id])} but the gate prices "
                f"{sorted(methods)}; re-measure §5/§6 before ruling D-1")


# =========================================================================== #
# GATE 8 — §4's allowed-red set is REAL (not just labelled)                     #
# =========================================================================== #
class TestGate8TheAllowedRedSetIsExecutable:
    """§4 governs Tasks 3-4's red-window discipline ("red ONLY on these rows")
    and Task 5's flip checklist. A checklist nobody can execute is worse than
    none, so every row is bound to the tree here.

    The contract is the ANCHOR column, not the line column: the gate asserts the
    file exists and the anchor string occurs somewhere in it. That is
    deliberately line-drift tolerant — upstream code moving up or down must not
    redden this table — while a pin that is DELETED or RENAMED reddens
    immediately, which is the failure mode that would actually silently shrink
    the allowed-red set.
    """

    def test_every_pin_row_names_a_real_file_and_a_real_anchor_inside_it(self):
        missing_files: list[str] = []
        missing_anchors: list[str] = []
        for num, rel, _lines, anchor, _verdict in _ledger_pins():
            path = _REPO_ROOT / rel
            if not path.is_file():
                missing_files.append(f"#{num} {rel}")
                continue
            if anchor not in path.read_text(encoding="utf-8", errors="replace"):
                missing_anchors.append(f"#{num} {rel} <- {anchor!r}")
        assert missing_files == [], (
            f"§4 rows name files that do not exist: {missing_files}")
        assert missing_anchors == [], (
            "§4 rows name pins that no longer exist in their file — the pin was "
            f"deleted or renamed, so the allowed-red set is stale: {missing_anchors}")

    def test_the_verdict_column_is_a_closed_vocabulary(self):
        """A fourth spelling makes "red only on FLIP / FLIP-IF rows" unenforceable,
        so the column is closed by assertion, not by convention."""
        offenders = [(num, verdict) for num, _rel, _l, _a, verdict
                     in _ledger_pins() if verdict not in _PIN_VERDICTS]
        assert offenders == [], (
            f"§4 verdicts outside the closed set {sorted(_PIN_VERDICTS)}: "
            f"{offenders}")

    def test_the_row_numbering_is_contiguous_from_one(self):
        """Task 5 works this table by row number; a gap or a duplicate silently
        drops a flip from the checklist."""
        numbers = [num for num, *_ in _ledger_pins()]
        assert numbers == list(range(1, len(numbers) + 1)), numbers

    def test_the_allowed_red_set_covers_every_family_the_plan_enumerates(self):
        """The plan's Step 2 names the families by hand; this asserts each one is
        actually in the table, so a future edit cannot quietly drop a whole
        family and still look complete."""
        pins = {(rel, anchor) for _n, rel, _l, anchor, _v in _ledger_pins()}
        required = {
            # the two support-refusal pins Task 5 flips
            ("tests/orchestration/test_pipeline_deep_preset.py",
             "test_the_report_is_honestly_unsupported_naming_the_data_grant_gap"),
            ("tests/orchestration/test_pipeline_reduced_deep_preset.py",
             "test_the_untouched_ten_node_baseline_is_still_honestly_unsupported"),
            # L2-b's pv-aux typed-refusal pins (seam-review finding 5)
            ("tests/orchestration/test_pv_aux_nodes.py", "AuxDataUngranted"),
            ("tests/orchestration/test_pv_aux_nodes.py", "aux_data_ungranted"),
            # L2-b's supported-methods guard
            ("tests/orchestration/test_data_world_recipe.py",
             "test_every_granted_prefetch_row_is_within_the_supported_set"),
            # L2-b's route-coverage pins (every new row enters their iteration)
            ("tests/orchestration/test_data_world_recipe.py",
             "test_every_sealed_prefetch_row_route_equality"),
            ("tests/orchestration/test_data_world_recipe.py",
             "test_all_seven_methods_have_the_single_surface_route"),
            # the api.py:1187-adjacent doc pin
            ("guanlan_v2/orchestration/pipeline/api.py",
             "permanent-honest grant-gap ruling"),
        }
        assert required <= pins, f"missing from §4: {sorted(required - pins)}"

    def test_the_pv_aux_pins_are_conditional_not_unconditional_flips(self):
        """The measured classification from the report: the aux pins belong to
        ``pv.price_action``/``pv.microstructure``, NOT to the D-1 candidates, so
        they are FLIP-IF. Recording it as an unconditional FLIP would licence a
        red that nothing in D-1 justifies."""
        verdicts = {anchor: verdict for _n, rel, _l, anchor, verdict
                    in _ledger_pins()
                    if rel == "tests/orchestration/test_pv_aux_nodes.py"}
        assert verdicts == {"AuxDataUngranted": "FLIP-IF",
                            "aux_data_ungranted": "FLIP-IF"}

    def test_every_section4_cross_reference_resolves(self):
        """Prose elsewhere in the ledger points AT §4 rows by number. Row numbers
        move whenever a row is inserted, and §3.1's first version shipped with
        every number off by +5 — a drift no human reading caught and no existing
        gate could see, because each number was individually plausible.

        So the cross-reference carries its own witness: every reference is
        written ``§4 #NN(`token`)`` and ``token`` must occur in row NN's ``file``
        or ``anchor`` cell. A renumbering slides the token off its row and this
        reddens; a token that is merely a filename shared by neighbouring rows
        still catches the multi-row shifts that actually happen.
        """
        rows = {num: f"{rel} {anchor}"
                for num, rel, _l, anchor, _v in _ledger_pins()}
        refs = re.findall(r"§4 #(\d+)\(`([^`]+)`\)", _LEDGER_TEXT)
        assert refs, ("no §4 cross-reference parsed — either the ledger stopped "
                      "citing §4 rows or the citation format drifted")
        broken = [
            f"§4 #{num}(`{token}`) -> row {num} is "
            + (f"{rows[int(num)]!r}" if int(num) in rows else "MISSING")
            for num, token in refs
            if int(num) not in rows or token not in rows[int(num)]
        ]
        assert broken == [], (
            "§4 cross-references that do not resolve to the row they name "
            f"(the numbering drifted under them): {broken}")

    def test_the_supported_methods_guard_is_an_unconditional_flip(self):
        """It reddens under ANY grant (its literal ``granted == {…}`` arm), so it
        must not be filed as conditional."""
        verdict = next(
            v for _n, rel, _l, a, v in _ledger_pins()
            if rel == "tests/orchestration/test_data_world_recipe.py"
            and a == "test_every_granted_prefetch_row_is_within_the_supported_set")
        assert verdict == "FLIP"


# =========================================================================== #
# GATE 7 — the D-2 arithmetic + the measured outage vocabulary                  #
# =========================================================================== #
class TestGate7D2Arithmetic:
    def test_both_candidate_workers_are_tool_calls_required(self):
        from guanlan_v2.orchestration.enums import ToolCallRequirement
        from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot

        workers = {w.id: w for w in phase8_catalog_snapshot().workers}
        for worker_id in _D1_CANDIDATES:
            assert (workers[worker_id].evidence_policy.tool_calls
                    is ToolCallRequirement.REQUIRED)
            assert workers[worker_id].params_schema_ref is None

    def test_only_always_invoke_plus_required_finalized_yields_min_one(
            self, surface):
        """``catalog.py``'s arithmetic, driven on real rows — never inferred
        from ``tool_calls=REQUIRED``."""
        sealed = surface.prefetch_binding.operations[0]
        assert sealed.invocation_mode == "cache_or_invoke"
        assert sealed.success_requires_finalized_call is False
        assert sealed.row_min_finalized == 0
        candidate = _candidate_row(surface, "verified_snapshot", "pv.technical")
        assert candidate.invocation_mode == "always_invoke"
        assert candidate.success_requires_finalized_call is True
        assert candidate.row_min_finalized == 1

    def test_the_sealed_pm_row_clears_nothing_and_pm_is_optional(self):
        """The same-family precedent, read precisely: ``dec.pm`` does not report
        ``tool_calls_required_unmet`` because its ``tool_calls`` is OPTIONAL —
        NOT because its row clears the arithmetic (it does not; min=0). So D-2's
        precedent covers the CORE-method hard-fail shape, not ``always_invoke``."""
        from guanlan_v2.orchestration.enums import ToolCallRequirement
        from guanlan_v2.orchestration.lane_catalog import phase8_catalog_snapshot

        workers = {w.id: w for w in phase8_catalog_snapshot().workers}
        assert (workers["dec.pm"].evidence_policy.tool_calls
                is ToolCallRequirement.OPTIONAL)

    def test_the_two_outage_vocabularies_are_both_real_at_head(self, surface):
        """The measured correction to the brief's D-2 wording: a data outage
        does NOT land in one failure vocabulary.

        * a CORE method (``verified_snapshot``) exhausts its route by RAISING
          (``data/registry.py::_exhaustion``), the exception escapes the bridge
          stage and the executor maps it to ``bridge_execution_error``;
        * an OPTIONAL method (``news``) builds an honest UNAVAILABLE result with
          NO finalized ToolCallRecord, so the node reaches the tool-discipline
          check and fails ``tool_discipline_unmet`` on ``finalized 0 < min 1``.

        Task 5 must pin BOTH; pinning one leaves half the degradation path
        unproven."""
        import guanlan_v2.orchestration.worker as W
        from guanlan_v2.orchestration.data import registry as DR

        assert surface.spec_by_method["verified_snapshot"].optional is False
        assert surface.spec_by_method["news"].optional is True

        exhaustion = re.sub(r"\s+", " ", inspect.getsource(DR._exhaustion))
        assert "if not method_spec.optional:" in exhaustion
        assert "raise first_retryable" in exhaustion
        assert "status=DataStatus.UNAVAILABLE" in exhaustion
        assert "tool_call_record=None" in exhaustion

        executor = re.sub(r"\s+", " ", inspect.getsource(W))
        assert 'NodeStatus.FAILED, "bridge_execution_error"' in executor
        discipline = re.sub(
            r"\s+", " ", inspect.getsource(W._check_tool_discipline))
        assert "charge.finalized < summary.min_finalized_tool_calls_on_success" \
            in discipline
        assert 'reason_code="tool_discipline_unmet"' in executor
