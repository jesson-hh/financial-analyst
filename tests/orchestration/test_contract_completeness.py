# -*- coding: utf-8 -*-
"""Task 13 — Phase-1 contract-completeness gate (``schema_registry`` population).

Written test-first (RED until ``PHASE1_PUBLIC_MODELS`` / ``INTERNAL_MODELS`` /
``default_registry`` exist). This is the *scope firewall* of the Phase-1 contract
package: it enumerates **every** public ``ContractModel`` subclass defined across
the frozen Phase-1 modules and asserts each one has been *reviewed* into exactly
one bucket —

* ``PHASE1_PUBLIC_MODELS`` — the reviewed tuple of standalone payload/fact schemas
  the sealed :func:`default_registry` resolves a :class:`SchemaRef` to; or
* ``INTERNAL_MODELS`` — an explicit ``model -> reason`` map of intentionally
  *unregistered* helper models (refs, envelopes, nested components, catalog /
  plan / migration governed models, abstract bases).

so that no future public contract model can silently escape registration review.
It also folds in the Task-11 / deferred-phase absence guards: no Trial/Holdout
type and no Phase 5/6/8 payload may appear anywhere in the sealed registry, and a
``WorkerCatalogSnapshot`` is governed by the catalog build/validate regime, never
treated as a schema-registry payload.

Run from repo root: ``pytest tests/orchestration/test_contract_completeness.py -v``
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Mapping

import pytest

import guanlan_v2.orchestration as _orchestration_pkg
from guanlan_v2.orchestration.digest import ContractModel, DigestModel
from guanlan_v2.orchestration.schema_registry import (
    INTERNAL_MODELS,
    PHASE1_PUBLIC_MODELS,
    UnknownSchemaError,
    default_registry,
)
from guanlan_v2.orchestration.refs import SchemaRef


# --------------------------------------------------------------------------- #
# The frozen Phase-1 module surface                                           #
# --------------------------------------------------------------------------- #
#: The complete set of Phase-1 modules whose public ContractModel subclasses the
#: completeness gate governs. A new public contract in ANY of these must be
#: reviewed into PHASE1_PUBLIC_MODELS or INTERNAL_MODELS or this gate fails.
PHASE1_MODULES: tuple[str, ...] = (
    "guanlan_v2.orchestration.digest",
    "guanlan_v2.orchestration.enums",
    "guanlan_v2.orchestration.refs",
    "guanlan_v2.orchestration.schema_registry",
    "guanlan_v2.orchestration.data.symbols",
    "guanlan_v2.orchestration.data.calendar",
    "guanlan_v2.orchestration.data.result",
    "guanlan_v2.orchestration.data.pit",
    "guanlan_v2.orchestration.data.source",
    "guanlan_v2.orchestration.data.snapshot",
    "guanlan_v2.orchestration.data.render",
    "guanlan_v2.orchestration.data.catalog",
    "guanlan_v2.orchestration.data.registry",
    "guanlan_v2.orchestration.schemas",
    "guanlan_v2.orchestration.context",
    "guanlan_v2.orchestration.events",
    "guanlan_v2.orchestration.catalog",
    "guanlan_v2.orchestration.spec",
    "guanlan_v2.orchestration.migration",
)

#: Deferred payloads frozen in later consumer phases. None exist yet; the guard
#: proves they stay out of Phase 1 (registry, classification and namespaces).
DEFERRED_PHASE_PAYLOADS: tuple[str, ...] = (
    # Phase 5 — market-factor / regime / rotation layer
    "MarketFactorValue",
    "MarketFactorReport",
    "RegimeReport",
    "RealizedRegime",
    "RotationReport",
    # Phase 6 — portfolio-target / schedule layer
    "TargetPosition",
    "PortfolioTargetProposal",
    "TargetPortfolioIntent",
    "DecisionSchedule",
    # Phase 8 — debate layer
    "DebateMessage",
)

#: Task-11 sealed-holdout evaluation types (Phase 4). Absent from Phase 1.
TRIAL_HOLDOUT_PREFIXES: tuple[str, ...] = ("Trial", "Holdout")


def _discover_public_contract_models() -> dict[str, type[ContractModel]]:
    """Every public ``ContractModel`` subclass DEFINED in a Phase-1 module.

    A class counts only under the module that defines it (``__module__`` match),
    so a model re-imported into another module is not double-counted; private
    (underscore-prefixed) helpers are excluded.
    """
    found: dict[str, type[ContractModel]] = {}
    for module_name in PHASE1_MODULES:
        module = importlib.import_module(module_name)
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, ContractModel)
                and obj.__module__ == module_name
                and not obj.__name__.startswith("_")
            ):
                found[f"{module_name}.{obj.__qualname__}"] = obj
    return found


DISCOVERED = _discover_public_contract_models()


def _modules_defining_public_contract_models() -> set[str]:
    """Walk the WHOLE ``guanlan_v2.orchestration`` package (not just the frozen
    ``PHASE1_MODULES`` list) and return every module name that *defines* a public
    ``ContractModel`` subclass — a class whose ``__module__`` is that module, that
    is a ``ContractModel`` subclass, excluding the ``ContractModel`` / ``DigestModel``
    bases and private ``_``-prefixed names.

    Uses :func:`pkgutil.walk_packages` + :func:`importlib.import_module` so a
    brand-new module added anywhere under the package is discovered on disk, not
    read from a hand-maintained list.
    """
    def _onerror(name: str) -> None:  # pragma: no cover - defensive, keeps failures loud
        raise AssertionError(f"pkgutil.walk_packages failed to import {name!r}")

    defining: set[str] = set()
    for mod_info in pkgutil.walk_packages(
        _orchestration_pkg.__path__,
        prefix=_orchestration_pkg.__name__ + ".",
        onerror=_onerror,
    ):
        module = importlib.import_module(mod_info.name)
        for obj in vars(module).values():
            if (
                inspect.isclass(obj)
                and issubclass(obj, ContractModel)
                and obj not in (ContractModel, DigestModel)
                and obj.__module__ == mod_info.name
                and not obj.__name__.startswith("_")
            ):
                defining.add(mod_info.name)
                break
    return defining


# --------------------------------------------------------------------------- #
# The discovery firewall — PHASE1_MODULES must enumerate every module that      #
# defines a public contract model (a new module cannot escape discovery)        #
# --------------------------------------------------------------------------- #
def test_phase1_modules_lists_every_module_defining_a_public_contract_model():
    """Close the "a new module with a public payload escapes discovery" hole.

    ``_discover_public_contract_models`` above only inspects the hand-maintained
    ``PHASE1_MODULES`` tuple, so a brand-new module carrying a public
    ``ContractModel`` subclass would never be seen by the completeness firewall.
    This test walks the entire package from disk and asserts every module that
    actually defines such a model is enumerated in ``PHASE1_MODULES``.
    """
    defining = _modules_defining_public_contract_models()
    # sanity: the walk found the known payload-bearing modules (not silently empty)
    assert "guanlan_v2.orchestration.schemas" in defining
    missing = defining - set(PHASE1_MODULES)
    assert not missing, (
        "these modules define a public ContractModel subclass but are absent from "
        "PHASE1_MODULES, so the completeness firewall would never review their "
        "contracts — add them to PHASE1_MODULES: " + ", ".join(sorted(missing))
    )


# --------------------------------------------------------------------------- #
# Shape of the two reviewed buckets                                            #
# --------------------------------------------------------------------------- #
def test_public_models_is_a_tuple_of_contract_models():
    assert isinstance(PHASE1_PUBLIC_MODELS, tuple)
    assert PHASE1_PUBLIC_MODELS, "the reviewed public tuple must be non-empty"
    for model in PHASE1_PUBLIC_MODELS:
        assert isinstance(model, type) and issubclass(model, ContractModel)


def test_internal_models_is_a_reason_map():
    assert isinstance(INTERNAL_MODELS, Mapping)
    for model, reason in INTERNAL_MODELS.items():
        assert isinstance(model, type) and issubclass(model, ContractModel)
        assert isinstance(reason, str) and reason.strip(), (
            f"{model.__name__} must carry a non-empty reviewed reason"
        )


def test_public_tuple_has_no_duplicates():
    assert len(set(PHASE1_PUBLIC_MODELS)) == len(PHASE1_PUBLIC_MODELS)


def test_public_and_internal_are_disjoint():
    overlap = set(PHASE1_PUBLIC_MODELS) & set(INTERNAL_MODELS)
    assert not overlap, f"a model cannot be both registered and internal: {overlap}"


# --------------------------------------------------------------------------- #
# The completeness firewall                                                    #
# --------------------------------------------------------------------------- #
def test_every_public_contract_model_is_classified():
    """Every discovered public contract model is reviewed into exactly one bucket."""
    classified = set(PHASE1_PUBLIC_MODELS) | set(INTERNAL_MODELS)
    discovered = set(DISCOVERED.values())
    unclassified = discovered - classified
    assert not unclassified, (
        "these public ContractModel subclasses are neither registered nor listed "
        "in INTERNAL_MODELS with a reviewed reason: "
        + ", ".join(sorted(f"{m.__module__}.{m.__name__}" for m in unclassified))
    )


def test_no_phantom_classifications():
    """Neither bucket lists a class that is not a discovered Phase-1 public model."""
    discovered = set(DISCOVERED.values())
    phantom = (set(PHASE1_PUBLIC_MODELS) | set(INTERNAL_MODELS)) - discovered
    assert not phantom, (
        "classified models that are not discovered public Phase-1 contracts: "
        + ", ".join(sorted(f"{m.__module__}.{m.__name__}" for m in phantom))
    )


def test_classification_partitions_the_whole_surface_exactly():
    """The two buckets together are exactly the discovered surface (a partition)."""
    classified = set(PHASE1_PUBLIC_MODELS) | set(INTERNAL_MODELS)
    assert classified == set(DISCOVERED.values())
    assert len(PHASE1_PUBLIC_MODELS) + len(INTERNAL_MODELS) == len(DISCOVERED)


# --------------------------------------------------------------------------- #
# The registry registers exactly the reviewed public tuple                     #
# --------------------------------------------------------------------------- #
def test_default_registry_registers_exactly_the_public_tuple():
    reg = default_registry()
    registered_keys = {e.key for e in reg.manifest()}
    expected_keys = {
        f"{m.__name__}@{m.model_fields['schema_version'].default}"
        for m in PHASE1_PUBLIC_MODELS
    }
    assert registered_keys == expected_keys


# --------------------------------------------------------------------------- #
# Task-11 absence guard — no Trial / Holdout type in the sealed registry        #
# --------------------------------------------------------------------------- #
def test_no_trial_or_holdout_type_registered():
    reg = default_registry()
    for entry in reg.manifest():
        name = entry.schema_ref.name
        assert not name.startswith(TRIAL_HOLDOUT_PREFIXES), (
            f"Trial/Holdout type {name!r} is a Phase-4 deferral and must not be "
            "in the Phase-1 registry"
        )


def test_no_trial_or_holdout_type_classified_or_defined():
    for model in set(PHASE1_PUBLIC_MODELS) | set(INTERNAL_MODELS):
        assert not model.__name__.startswith(TRIAL_HOLDOUT_PREFIXES)
    for module_name in PHASE1_MODULES:
        module = importlib.import_module(module_name)
        for prefix in TRIAL_HOLDOUT_PREFIXES:
            leaked = [n for n in vars(module) if n.startswith(prefix)]
            assert not leaked, f"{module_name} leaked Trial/Holdout names: {leaked}"


# --------------------------------------------------------------------------- #
# Deferred Phase 5 / 6 / 8 payloads never appear anywhere in Phase 1           #
# --------------------------------------------------------------------------- #
def test_deferred_phase_payloads_absent_from_registry():
    reg = default_registry()
    registered_names = {e.schema_ref.name for e in reg.manifest()}
    for name in DEFERRED_PHASE_PAYLOADS:
        assert name not in registered_names


def test_deferred_phase_payloads_absent_from_classification():
    classified_names = {
        m.__name__ for m in set(PHASE1_PUBLIC_MODELS) | set(INTERNAL_MODELS)
    }
    for name in DEFERRED_PHASE_PAYLOADS:
        assert name not in classified_names


def test_deferred_phase_payloads_absent_from_every_module():
    for name in DEFERRED_PHASE_PAYLOADS:
        for module_name in PHASE1_MODULES:
            module = importlib.import_module(module_name)
            assert not hasattr(module, name), (
                f"{name} is frozen in a later phase and must not be defined in "
                f"{module_name}"
            )


# --------------------------------------------------------------------------- #
# WorkerCatalogSnapshot is catalog-governed, never a registry payload          #
# --------------------------------------------------------------------------- #
def test_worker_catalog_snapshot_is_internal_not_registered():
    from guanlan_v2.orchestration.catalog import WorkerCatalogSnapshot

    assert WorkerCatalogSnapshot in INTERNAL_MODELS
    assert WorkerCatalogSnapshot not in PHASE1_PUBLIC_MODELS
    # its reason must name the catalog governance regime.
    reason = INTERNAL_MODELS[WorkerCatalogSnapshot].lower()
    assert "catalog" in reason


def test_worker_catalog_snapshot_not_resolvable_via_registry():
    """A WorkerCatalogSnapshot@1 SchemaRef must not resolve in the schema registry
    — it is validated by build/validate_catalog_snapshot, not the registry."""
    reg = default_registry()
    with pytest.raises(UnknownSchemaError):
        reg.resolve(SchemaRef(name="WorkerCatalogSnapshot", version="1"))


def test_catalog_snapshot_is_validated_by_catalog_rules():
    """A catalog snapshot round-trips through the catalog validator, proving it has
    its own governance regime distinct from the schema registry."""
    from guanlan_v2.orchestration.catalog import (
        build_catalog_snapshot,
        validate_catalog_snapshot,
    )

    snap = build_catalog_snapshot(
        catalog_version="cat-1",
        content_manifest=(),
        skill_manifest=(),
        capability_manifest=(),
        workers=(),
        resolved_material=(),
    )
    # a self-consistent snapshot re-validates without raising CatalogError.
    validate_catalog_snapshot(snap)
    assert snap.catalog_digest


# --------------------------------------------------------------------------- #
# Every INTERNAL reason cites a reviewed governance category                    #
# --------------------------------------------------------------------------- #
def test_internal_reasons_are_reviewed_categories():
    """Each internal reason must name why the model is intentionally unregistered
    (guards against an empty/placeholder reason slipping in)."""
    keywords = (
        "ref", "envelope", "component", "base", "catalog", "plan", "migration",
        "value", "record", "event", "budget", "accounting", "context",
    )
    for model, reason in INTERNAL_MODELS.items():
        low = reason.lower()
        assert any(k in low for k in keywords), (
            f"{model.__name__} reason does not cite a reviewed category: {reason!r}"
        )
