# -*- coding: utf-8 -*-
"""Phase 3 · Task 9 — strict memory contract / state-matrix tests.

Locks the closed model matrices of ``guanlan_v2.orchestration.memory.models``:
pending/approved basis-evidence, the owner-receipt CAS matrix, cutover token
derivations, source-state present/absent + genesis lineage, snapshot lineage +
wall-clock exclusion, authority-narrowing query builders, the closed marker
grammar, the shared console normalizer and proposal grant/preparation binding.

Run: ``pytest tests/orchestration/memory/test_models.py -v``
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from guanlan_v2.orchestration.context import MemoryRecordRef
from guanlan_v2.orchestration.digest import ContractModel
from guanlan_v2.orchestration.memory import models as M
from guanlan_v2.orchestration.refs import PayloadRef, SchemaRef, TypedPayloadRef

NOW = datetime(2026, 7, 17, 10, 0, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone.utc)
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def typed_ref(schema: str, digest: str, *, namespace: str = "main",
              object_id: str = "obj-1") -> TypedPayloadRef:
    return TypedPayloadRef(
        schema_ref=SchemaRef(name=schema, version="1"),
        payload_ref=PayloadRef(namespace=namespace, object_id=object_id,
                               content_digest=digest),
    )


def make_policy(**overrides) -> M.MemoryCapturePolicy:
    fields = dict(
        policy_id="memory.policy.test",
        policy_version="1",
        source_mappings=(
            M.MemorySourcePolicyEntry(source_id="agent.own", kind="semantic",
                                      scope="agent_own", importance=0.5),
            M.MemorySourcePolicyEntry(source_id="agent.shared", kind="semantic",
                                      scope="agent_shared", importance=0.6),
            M.MemorySourcePolicyEntry(source_id="console.global.keyed", kind="procedural",
                                      scope="console_global", importance=0.8, mandatory=True),
        ),
        archive_tail_chars=4000,
        context_scopes=("console_global", "console_session"),
        worker_scopes=("agent_own", "agent_shared"),
        borrow_grants=(),
        allowed_kinds=("episodic", "procedural", "semantic"),
        top_k_non_mandatory=5,
        max_mandatory=64,
        max_record_bytes=4096,
        max_total_rendered_bytes=65536,
    )
    fields.update(overrides)
    return M.MemoryCapturePolicy.build(**fields)


def present_entry(*, source_id="agent.own", locator="dec.pm/lesson.md",
                  epoch=0, digest=HEX_A, marker=None) -> M.MemorySourceStateEntry:
    return M.MemorySourceStateEntry(
        source_id=source_id, locator=locator, presence="present",
        store_content_digest=digest, apply_marker_id=marker, continuity_epoch=epoch)


def build_record(**overrides):
    kw = dict(
        source_entry=present_entry(),
        owner_id="dec.pm", scope="agent_own", session_id=None,
        kind="semantic", text="lesson body",
        created_at=NOW, valid_from=NOW, valid_to=None, available_at=NOW,
        review_state="pending", review_basis=None,
        review_evidence=None, review_evidence_ref=None,
        importance=0.5, mandatory=False,
        identity={"store": "agent", "owner": "dec.pm", "locator": "dec.pm/lesson.md"},
    )
    kw.update(overrides)
    return M.build_memory_record(**kw)


# --------------------------------------------------------------------------- #
# namespace union + partitions                                                 #
# --------------------------------------------------------------------------- #
def test_state_cell_namespace_union_is_the_exact_seven_name_canonical_tuple():
    ns = M.PHASE3_MEMORY_STATE_CELL_NAMESPACES
    assert ns == (
        "memory.cutover_preparation.v1",
        "memory.proposal_preparation.v1",
        "memory.snapshot_head.v1",
        "memory.snapshot_operation.v1",
        "memory.snapshot_preparation.v1",
        "memory.source_head.v1",
        "memory.source_operation.v1",
    )
    assert list(ns) == sorted(ns) and len(set(ns)) == 7


def test_internal_carriers_are_not_contract_models_and_not_public():
    for model, reason in M.PHASE3_MEMORY_INTERNAL_MODELS.items():
        assert not issubclass(model, ContractModel), model.__name__
        assert model not in M.MEMORY_PUBLIC_MODELS
        assert isinstance(reason, str) and reason.strip()
    # the three recovery preparations are deliberately PUBLIC (registered).
    for prep in (M.MemoryCutoverPreparation, M.MemorySnapshotCapturePreparation,
                 M.MemoryProposalPreparation):
        assert prep in M.MEMORY_PUBLIC_MODELS
        assert prep not in M.PHASE3_MEMORY_INTERNAL_MODELS


# --------------------------------------------------------------------------- #
# policy                                                                       #
# --------------------------------------------------------------------------- #
def test_policy_self_seals_and_rejects_drifted_digest():
    pol = make_policy()
    assert pol.policy_digest == pol.semantic_digest()
    with pytest.raises(ValidationError):
        M.MemoryCapturePolicy(**{**{f: getattr(pol, f) for f in type(pol).model_fields
                                    if f != "policy_digest"}, "policy_digest": HEX_B})


def test_policy_setlike_grants_must_be_sorted_unique():
    with pytest.raises(ValidationError):
        make_policy(context_scopes=("console_session", "console_global"))
    with pytest.raises(ValidationError):
        make_policy(allowed_kinds=("semantic", "semantic"))


def test_a_structurally_valid_caller_policy_has_no_authority():
    catalog_bound = M.ResolvedMemoryPolicy(
        policy=make_policy(),
        material_ref=__import__(
            "guanlan_v2.orchestration.refs", fromlist=["ContentRef"]
        ).ContentRef(id="memory.facade.policy", version="1", content_digest=HEX_A),
    )
    caller_policy = make_policy(top_k_non_mandatory=999)
    with pytest.raises(M.MemoryAuthorityError):
        catalog_bound.require_digest(caller_policy.policy_digest)


# --------------------------------------------------------------------------- #
# cutover                                                                      #
# --------------------------------------------------------------------------- #
def test_source_revision_token_is_the_closed_derivation_and_drift_fails():
    token = M.derive_source_revision_token(
        cutover_operation_id="cutover.op1", source_id="agent.own",
        locator="dec.pm/lesson.md", store_content_digest=HEX_A)
    payload = M.MemoryCutoverSourcePayload(
        cutover_operation_id="cutover.op1", source_id="agent.own",
        locator="dec.pm/lesson.md", normalized_text="body",
        store_content_digest=HEX_A, source_revision_token=token)
    assert payload.source_revision_token == token
    with pytest.raises(ValidationError):
        M.MemoryCutoverSourcePayload(
            cutover_operation_id="cutover.op1", source_id="agent.own",
            locator="dec.pm/lesson.md", normalized_text="body",
            store_content_digest=HEX_A, source_revision_token=HEX_B)
    assert M.derive_cutover_put_idempotency_key(
        cutover_operation_id="cutover.op1", source_id="agent.own",
        locator="dec.pm/lesson.md", store_content_digest=HEX_A) == "memcut:" + token


def test_cutover_manifest_is_ordered_and_carries_no_downstream_fields():
    entry = M.MemoryCutoverManifestEntry(
        source_id="agent.own", locator="a.md", store_content_digest=HEX_A,
        payload_ref=typed_ref("MemoryCutoverSourcePayload", HEX_A))
    manifest = M.MemoryCutoverManifest.build(
        cutover_operation_id="cutover.op1", entries=(entry,))
    assert manifest.content_digest == manifest.semantic_digest()
    # deliberately absent: record revision/availability/attestation fields.
    for forbidden in ("available_at", "attestation_digest", "revision_id",
                      "review_evidence_digest"):
        assert forbidden not in type(manifest).model_fields
    e2 = M.MemoryCutoverManifestEntry(
        source_id="agent.own", locator="b.md", store_content_digest=HEX_B,
        payload_ref=typed_ref("MemoryCutoverSourcePayload", HEX_B))
    with pytest.raises(ValidationError):
        M.MemoryCutoverManifest.build(cutover_operation_id="cutover.op1",
                                      entries=(e2, entry))  # unsorted


def test_cutover_preparation_verifies_token_and_put_key_map():
    token = M.derive_source_revision_token(
        cutover_operation_id="cutover.op1", source_id="agent.own",
        locator="a.md", store_content_digest=HEX_A)
    good = M.MemoryCutoverPreparationEntry(
        source_id="agent.own", locator="a.md", store_content_digest=HEX_A,
        source_revision_token=token, put_idempotency_key="memcut:" + token)
    prep = M.MemoryCutoverPreparation(
        admin_operation_key="admin-op-1", cutover_operation_id="cutover.op1",
        entries=(good,))
    assert prep.entries[0].source_revision_token == token
    with pytest.raises(ValidationError):
        M.MemoryCutoverPreparation(
            admin_operation_key="admin-op-1", cutover_operation_id="cutover.op1",
            entries=(M.MemoryCutoverPreparationEntry(
                source_id="agent.own", locator="a.md", store_content_digest=HEX_A,
                source_revision_token=HEX_B, put_idempotency_key="memcut:" + HEX_B),))


def test_attestation_requires_the_exact_manifest_schema():
    with pytest.raises(ValidationError):
        M.MemoryCutoverAttestation(
            manifest_ref=typed_ref("MemoryRecord", HEX_A),
            cutover_policy_digest=HEX_A, root_state_digest=HEX_B,
            admin_actor="ops-admin", attested_at=NOW)


# --------------------------------------------------------------------------- #
# source state                                                                 #
# --------------------------------------------------------------------------- #
def test_source_state_entry_present_absent_matrix():
    assert present_entry().store_content_digest == HEX_A
    with pytest.raises(ValidationError):  # present requires digest
        M.MemorySourceStateEntry(source_id="agent.own", locator="a.md",
                                 presence="present", continuity_epoch=0)
    with pytest.raises(ValidationError):  # absent forbids digest
        M.MemorySourceStateEntry(source_id="agent.own", locator="a.md",
                                 presence="absent", store_content_digest=HEX_A,
                                 continuity_epoch=1)
    with pytest.raises(ValidationError):  # absent forbids marker
        M.MemorySourceStateEntry(source_id="agent.own", locator="a.md",
                                 presence="absent",
                                 apply_marker_id="apply." + "d" * 64,
                                 continuity_epoch=1)


def _genesis_state(**overrides) -> M.MemorySourceStateSnapshot:
    fields = dict(
        root_binding_digest=HEX_A,
        captured_at=NOW,
        entries=(present_entry(),),
        genesis_manifest_ref=typed_ref("MemoryCutoverManifest", HEX_B),
        genesis_attestation_ref=typed_ref("MemoryCutoverAttestation", HEX_C),
    )
    fields.update(overrides)
    return M.MemorySourceStateSnapshot.build(**fields)


def test_source_state_genesis_binds_manifest_and_attestation():
    state = _genesis_state()
    assert state.previous_source_state_ref is None
    with pytest.raises(ValidationError):
        _genesis_state(genesis_manifest_ref=None)
    with pytest.raises(ValidationError):
        _genesis_state(genesis_attestation_ref=None)


def test_source_state_successor_binds_predecessor_and_no_genesis():
    genesis = _genesis_state()
    succ = M.MemorySourceStateSnapshot.build(
        root_binding_digest=HEX_A, captured_at=LATER,
        entries=(present_entry(epoch=1, digest=HEX_B),),
        previous_source_state_ref=typed_ref("MemorySourceStateSnapshot",
                                            genesis.content_digest),
        previous_source_state_hash=genesis.content_digest,
    )
    assert succ.previous_source_state_hash == genesis.content_digest
    with pytest.raises(ValidationError):  # ref/hash mismatch
        M.MemorySourceStateSnapshot.build(
            root_binding_digest=HEX_A, captured_at=LATER,
            entries=(present_entry(epoch=1, digest=HEX_B),),
            previous_source_state_ref=typed_ref("MemorySourceStateSnapshot", HEX_A),
            previous_source_state_hash=genesis.content_digest)
    with pytest.raises(ValidationError):  # a successor binds no replacement genesis
        M.MemorySourceStateSnapshot.build(
            root_binding_digest=HEX_A, captured_at=LATER,
            entries=(present_entry(epoch=1, digest=HEX_B),),
            previous_source_state_ref=typed_ref("MemorySourceStateSnapshot",
                                                genesis.content_digest),
            previous_source_state_hash=genesis.content_digest,
            genesis_manifest_ref=typed_ref("MemoryCutoverManifest", HEX_B),
            genesis_attestation_ref=typed_ref("MemoryCutoverAttestation", HEX_C))


def test_source_state_capture_wall_clock_is_audit_only():
    a = _genesis_state(captured_at=NOW)
    b = _genesis_state(captured_at=LATER)
    assert a.content_digest == b.content_digest


# --------------------------------------------------------------------------- #
# record + revision identity                                                   #
# --------------------------------------------------------------------------- #
def test_record_builder_emits_matching_phase1_ref():
    record, ref = build_record()
    assert isinstance(ref, MemoryRecordRef)
    assert ref.record_id == record.record_id
    assert ref.revision_id == record.revision_id
    assert ref.available_at == record.available_at
    assert ref.content_digest == record.content_digest == record.semantic_digest()
    assert record.record_id.startswith("mem.") and len(record.record_id) == 68


def test_record_pending_forbids_and_approved_requires_basis_evidence():
    with pytest.raises(M.MemoryContractError):
        build_record(review_basis="legacy_cutover")
    with pytest.raises(M.MemoryContractError):
        build_record(review_state="approved")


def test_record_approved_via_legacy_cutover_evidence_round_trip():
    evidence = M.MemoryReviewEvidence(evidence=M.LegacyCutoverEvidence(
        attestation_ref=typed_ref("MemoryCutoverAttestation", HEX_B),
        source_payload_ref=typed_ref("MemoryCutoverSourcePayload", HEX_C),
        genesis_continuity_epoch=0))
    ev_ref = typed_ref("MemoryReviewEvidence", evidence.semantic_digest())
    record, ref = build_record(review_state="approved", review_basis="legacy_cutover",
                               review_evidence=evidence, review_evidence_ref=ev_ref)
    assert record.review_state == "approved"
    # a forged ref (digest not the evidence's) is rejected loudly.
    with pytest.raises(M.MemoryContractError):
        build_record(review_state="approved", review_basis="legacy_cutover",
                     review_evidence=evidence,
                     review_evidence_ref=typed_ref("MemoryReviewEvidence", HEX_A))
    # a basis that does not match the evidence variant is rejected.
    with pytest.raises(M.MemoryContractError):
        build_record(review_state="approved", review_basis="memory_ops_approval",
                     review_evidence=evidence, review_evidence_ref=ev_ref)


def test_revision_identity_covers_the_complete_immutable_tuple():
    _, ref1 = build_record()
    _, ref2 = build_record()
    assert ref1.revision_id == ref2.revision_id  # deterministic reuse
    _, ref3 = build_record(text="changed body")
    assert ref3.revision_id != ref1.revision_id
    _, ref4 = build_record(source_entry=present_entry(epoch=2, digest=HEX_B))
    assert ref4.revision_id != ref1.revision_id  # A->B->A epoch advance = new revision
    evidence = M.MemoryReviewEvidence(evidence=M.LegacyCutoverEvidence(
        attestation_ref=typed_ref("MemoryCutoverAttestation", HEX_B),
        source_payload_ref=typed_ref("MemoryCutoverSourcePayload", HEX_C),
        genesis_continuity_epoch=0))
    _, ref5 = build_record(
        review_state="approved", review_basis="legacy_cutover",
        review_evidence=evidence,
        review_evidence_ref=typed_ref("MemoryReviewEvidence", evidence.semantic_digest()))
    assert ref5.revision_id != ref1.revision_id  # changed review evidence = new revision


def test_record_session_and_validity_matrices():
    with pytest.raises(ValidationError):
        build_record(scope="console_session")  # session scope requires session_id
    rec, _ = build_record(scope="console_session", session_id="cs.abc",
                          owner_id="console",
                          source_entry=present_entry(source_id="console.session",
                                                     locator="cs.abc/notes.md"))
    assert rec.session_id == "cs.abc"
    with pytest.raises(ValidationError):
        build_record(session_id="cs.abc")  # non-session scope forbids session_id
    with pytest.raises(ValidationError):
        build_record(valid_to=NOW)  # valid_from must precede exclusive valid_to


def test_record_rejects_naive_availability_loudly():
    with pytest.raises((ValidationError, M.MemoryContractError, ValueError)):
        build_record(available_at=datetime(2026, 7, 17, 10, 0, 0))  # naive


def test_record_cannot_build_over_an_absent_source_entry():
    absent = M.MemorySourceStateEntry(source_id="agent.own", locator="a.md",
                                      presence="absent", continuity_epoch=3)
    with pytest.raises(M.MemoryContractError):
        build_record(source_entry=absent)


def test_evidence_epoch_and_locator_must_cross_match_for_applied_evidence():
    owner_receipt_ref = typed_ref("MemoryOwnerApplySemanticReceipt", HEX_B)
    ev = M.MemoryReviewEvidence(evidence=M.AgentAppliedEvidence(
        source_id="agent.own", locator="dec.pm/lesson.md", continuity_epoch=5,
        proposal_ref=typed_ref("MemoryProposal", HEX_A),
        receipt_ref=typed_ref("MemoryProposalReceipt", HEX_B),
        decision_ref=typed_ref("MemoryProposalDecision", HEX_C),
        owner_receipt_ref=owner_receipt_ref))
    ev_ref = typed_ref("MemoryReviewEvidence", ev.semantic_digest())
    with pytest.raises(M.MemoryContractError):  # entry epoch 0 != evidence epoch 5
        build_record(review_state="approved", review_basis="memory_ops_approval",
                     review_evidence=ev, review_evidence_ref=ev_ref)
    record, _ = build_record(
        source_entry=present_entry(epoch=5),
        review_state="approved", review_basis="memory_ops_approval",
        review_evidence=ev, review_evidence_ref=ev_ref)
    assert record.review_basis == "memory_ops_approval"


# --------------------------------------------------------------------------- #
# owner-receipt CAS matrix                                                     #
# --------------------------------------------------------------------------- #
def _receipt(**overrides) -> M.MemoryOwnerApplySemanticReceipt:
    fields = dict(
        receipt_kind="agent", operation="create", apply_operation_id="apply-op.1",
        decision_ref=typed_ref("MemoryProposalDecision", HEX_A),
        proposal_ref=typed_ref("MemoryProposal", HEX_B),
        source_id="agent.own", locator="dec.pm/lesson.md",
        marker_id="apply." + "d" * 64,
        expected_before_target_store_digest="absent",
        actual_before_target_store_digest="absent",
        intended_after_target_store_digest=HEX_C,
        actual_after_target_store_digest=HEX_C,
    )
    fields.update(overrides)
    return M.MemoryOwnerApplySemanticReceipt(**fields)


def test_owner_receipt_create_requires_absent_before_digests():
    assert _receipt().operation == "create"
    with pytest.raises(ValidationError):
        _receipt(expected_before_target_store_digest=HEX_A)
    with pytest.raises(ValidationError):
        _receipt(actual_before_target_store_digest=HEX_A)


def test_owner_receipt_update_requires_equal_concrete_before_digests():
    ok = _receipt(operation="update",
                  expected_before_target_store_digest=HEX_A,
                  actual_before_target_store_digest=HEX_A)
    assert ok.operation == "update"
    with pytest.raises(ValidationError):
        _receipt(operation="update",
                 expected_before_target_store_digest=HEX_A,
                 actual_before_target_store_digest=HEX_B)
    with pytest.raises(ValidationError):
        _receipt(operation="update",
                 expected_before_target_store_digest="absent",
                 actual_before_target_store_digest="absent")


def test_owner_receipt_actual_after_must_equal_intended_after():
    with pytest.raises(ValidationError):
        _receipt(actual_after_target_store_digest=HEX_B)


def test_owner_receipt_console_container_matrix():
    console = _receipt(
        receipt_kind="console", source_id="console.global.keyed", locator="memory.md",
        expected_before_container_digest=HEX_A, actual_before_container_digest=HEX_A,
        intended_after_container_digest=HEX_B, actual_after_container_digest=HEX_B,
        journal_session_id="cs_abcdef123456", journal_event_id=7)
    assert console.journal_event_id == 7
    with pytest.raises(ValidationError):  # console requires all container digests
        _receipt(receipt_kind="console", journal_session_id="cs_x", journal_event_id=1)
    with pytest.raises(ValidationError):  # container after drift fails
        _receipt(receipt_kind="console",
                 expected_before_container_digest=HEX_A,
                 actual_before_container_digest=HEX_A,
                 intended_after_container_digest=HEX_B,
                 actual_after_container_digest=HEX_C,
                 journal_session_id="cs_x", journal_event_id=1)
    with pytest.raises(ValidationError):  # agent receipts forbid container digests
        _receipt(expected_before_container_digest=HEX_A,
                 actual_before_container_digest=HEX_A,
                 intended_after_container_digest=HEX_B,
                 actual_after_container_digest=HEX_B)


def test_decision_model_has_no_actual_fields():
    assert not [f for f in M.MemoryProposalDecision.model_fields if f.startswith("actual")]


# --------------------------------------------------------------------------- #
# snapshot + selection                                                         #
# --------------------------------------------------------------------------- #
def _snapshot_fields(records=(), **overrides) -> dict:
    entries = []
    for record, ref in records:
        entries.append(M.MemorySnapshotEntry(
            record_ref=ref,
            record_payload_ref=typed_ref("MemoryRecord", record.content_digest)))
    entries.sort(key=lambda e: (e.record_ref.record_id, e.record_ref.revision_id,
                                e.record_ref.content_digest))
    fields = dict(
        as_of=NOW, capture_completed_at=NOW, memory_session_id=None,
        scope_binding_digest=HEX_A,
        cutover_attestation_ref=typed_ref("MemoryCutoverAttestation", HEX_B),
        source_state_ref=typed_ref("MemorySourceStateSnapshot", HEX_C),
        policy_digest=HEX_A, entries=tuple(entries),
    )
    fields.update(overrides)
    return fields


def test_snapshot_entry_binds_the_exact_record_digest():
    record, ref = build_record()
    entry = M.MemorySnapshotEntry(
        record_ref=ref, record_payload_ref=typed_ref("MemoryRecord", record.content_digest))
    assert entry.record_ref.content_digest == record.content_digest
    with pytest.raises(ValidationError):
        M.MemorySnapshotEntry(record_ref=ref,
                              record_payload_ref=typed_ref("MemoryRecord", HEX_B))
    with pytest.raises(ValidationError):
        M.MemorySnapshotEntry(record_ref=ref,
                              record_payload_ref=typed_ref("MemoryQuery",
                                                           record.content_digest))


def test_snapshot_excludes_capture_wall_clock_and_object_ids_from_digest():
    record, ref = build_record()
    a = M.MemorySnapshot.build(**_snapshot_fields([(record, ref)]))
    b = M.MemorySnapshot.build(**_snapshot_fields(
        [(record, ref)], capture_completed_at=LATER))
    assert a.content_digest == b.content_digest
    relocated = M.MemorySnapshot.build(**_snapshot_fields(
        [(record, ref)],
        source_state_ref=typed_ref("MemorySourceStateSnapshot", HEX_C,
                                   object_id="moved-elsewhere")))
    assert relocated.content_digest == a.content_digest  # object_id is audit-only


def test_snapshot_lineage_pairing_and_predecessor_binding():
    first = M.MemorySnapshot.build(**_snapshot_fields())
    assert first.previous_snapshot_ref is None
    succ = M.MemorySnapshot.build(**_snapshot_fields(
        previous_snapshot_ref=typed_ref("MemorySnapshot", first.content_digest),
        previous_snapshot_hash=first.content_digest, as_of=LATER))
    assert succ.previous_snapshot_hash == first.content_digest
    with pytest.raises(ValidationError):  # hash without ref
        M.MemorySnapshot.build(**_snapshot_fields(previous_snapshot_hash=HEX_A))
    with pytest.raises(ValidationError):  # ref/hash mismatch
        M.MemorySnapshot.build(**_snapshot_fields(
            previous_snapshot_ref=typed_ref("MemorySnapshot", HEX_A),
            previous_snapshot_hash=HEX_B))


def test_a_future_addition_cannot_alter_an_old_snapshot_hash():
    r1 = build_record()
    old = M.MemorySnapshot.build(**_snapshot_fields([r1]))
    r2 = build_record(text="a later record",
                      source_entry=present_entry(locator="dec.pm/later.md",
                                                 digest=HEX_B))
    new = M.MemorySnapshot.build(**_snapshot_fields(
        [r1, r2], as_of=LATER,
        previous_snapshot_ref=typed_ref("MemorySnapshot", old.content_digest),
        previous_snapshot_hash=old.content_digest))
    again = M.MemorySnapshot.build(**_snapshot_fields([r1]))
    assert again.content_digest == old.content_digest
    assert new.content_digest != old.content_digest


def test_selection_binds_query_digest_and_explicit_ranks():
    record, ref = build_record()
    sel = M.MemorySelection.build(
        snapshot_digest=HEX_A, query_ref=typed_ref("MemoryQuery", HEX_B),
        query_digest=HEX_B,
        entries=(M.MemorySelectionEntry(rank=0, record_ref=ref),))
    assert sel.content_digest == sel.semantic_digest()
    with pytest.raises(ValidationError):  # query digest must equal the ref digest
        M.MemorySelection.build(
            snapshot_digest=HEX_A, query_ref=typed_ref("MemoryQuery", HEX_B),
            query_digest=HEX_C, entries=())
    with pytest.raises(ValidationError):  # ranks must be explicit 0..n-1
        M.MemorySelection.build(
            snapshot_digest=HEX_A, query_ref=typed_ref("MemoryQuery", HEX_B),
            query_digest=HEX_B,
            entries=(M.MemorySelectionEntry(rank=1, record_ref=ref),))


def test_selection_query_object_relocation_is_audit_only_but_drift_is_semantic():
    record, ref = build_record()
    base = M.MemorySelection.build(
        snapshot_digest=HEX_A, query_ref=typed_ref("MemoryQuery", HEX_B),
        query_digest=HEX_B,
        entries=(M.MemorySelectionEntry(rank=0, record_ref=ref),))
    relocated = M.MemorySelection.build(
        snapshot_digest=HEX_A,
        query_ref=typed_ref("MemoryQuery", HEX_B, object_id="moved"),
        query_digest=HEX_B,
        entries=(M.MemorySelectionEntry(rank=0, record_ref=ref),))
    assert relocated.content_digest == base.content_digest
    drifted = M.MemorySelection.build(
        snapshot_digest=HEX_A, query_ref=typed_ref("MemoryQuery", HEX_C),
        query_digest=HEX_C,
        entries=(M.MemorySelectionEntry(rank=0, record_ref=ref),))
    assert drifted.content_digest != base.content_digest


# --------------------------------------------------------------------------- #
# query builders — authority ∩ policy, never widening                          #
# --------------------------------------------------------------------------- #
def _resolved_policy(**overrides) -> M.ResolvedMemoryPolicy:
    from guanlan_v2.orchestration.refs import ContentRef

    pol = make_policy(**overrides)
    return M.ResolvedMemoryPolicy(
        policy=pol,
        material_ref=ContentRef(id="memory.facade.policy", version="1",
                                content_digest=pol.policy_digest))


def test_context_query_is_authority_intersect_policy():
    policy = _resolved_policy()
    authority = M.MemoryContextAuthority(
        memory_session_id="cs.abc",
        granted_scopes=("console_global",),  # narrower than policy
        authenticated_by="orchestrator-entry")
    q = M.build_context_memory_query("recent lessons", 3,
                                     authority=authority, policy=policy)
    assert q.role == "context" and q.reader_id is None
    assert q.allowed_scopes == ("console_global",)
    assert q.memory_session_id == "cs.abc"
    assert q.policy_digest == policy.policy.policy_digest


def test_context_query_cannot_exceed_the_policy_top_k_and_rejects_bool():
    policy = _resolved_policy(top_k_non_mandatory=5)
    authority = M.MemoryContextAuthority(
        memory_session_id=None, granted_scopes=("console_global",),
        authenticated_by="orchestrator-entry")
    with pytest.raises(M.MemoryAuthorityError):
        M.build_context_memory_query("q", 6, authority=authority, policy=policy)
    with pytest.raises(M.MemoryContractError):
        M.build_context_memory_query("q", True, authority=authority, policy=policy)
    with pytest.raises(M.MemoryContractError):
        M.build_context_memory_query("q", 0, authority=authority, policy=policy)


def _worker_and_context(session_id: str | None = "cs.abc",
                        read_categories=("memory",), borrows=()):
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

    base = phase3_data_catalog_snapshot().workers[0]
    fields = {name: getattr(base, name) for name in type(base).model_fields}
    fields["read_categories"] = tuple(read_categories)
    fields["borrowed_from"] = tuple(borrows)
    worker = type(base)(**fields)
    context = SimpleNamespace(memory_session_id=session_id)
    snapshot = SimpleNamespace(memory_session_id=session_id)
    return worker, context, snapshot


def test_worker_query_requires_the_memory_read_category():
    policy = _resolved_policy()
    worker, context, snapshot = _worker_and_context(read_categories=("context",))
    with pytest.raises(M.MemoryAuthorityError):
        M.build_worker_memory_query("q", 2, worker=worker, context=context,
                                    snapshot=snapshot, policy=policy)


def test_worker_query_verifies_snapshot_session_and_derives_own_scope():
    policy = _resolved_policy()
    worker, context, snapshot = _worker_and_context()
    q = M.build_worker_memory_query("q", 2, worker=worker, context=context,
                                    snapshot=snapshot, policy=policy)
    assert q.role == "worker" and q.reader_id == worker.id
    assert q.allowed_scopes == ("agent_own", "agent_shared")
    assert q.memory_session_id == "cs.abc"
    drifted = SimpleNamespace(memory_session_id="cs.other")
    with pytest.raises(M.MemoryAuthorityError):
        M.build_worker_memory_query("q", 2, worker=worker, context=context,
                                    snapshot=drifted, policy=policy)


def test_worker_borrowed_scope_requires_policy_and_worker_grant_together():
    worker, context, snapshot = _worker_and_context(borrows=("mkt.macro",))
    # policy grants nothing -> borrowed empty even though the worker declares it.
    q1 = M.build_worker_memory_query("q", 2, worker=worker, context=context,
                                     snapshot=snapshot, policy=_resolved_policy())
    assert q1.borrowed_owners == ()
    # policy grants it AND the worker declares it -> granted.
    granting = _resolved_policy(borrow_grants=(
        M.MemoryBorrowGrant(borrower_id=worker.id, owner_id="mkt.macro"),))
    q2 = M.build_worker_memory_query("q", 2, worker=worker, context=context,
                                     snapshot=snapshot, policy=granting)
    assert q2.borrowed_owners == ("mkt.macro",)
    # policy grants it but the worker does not declare it -> still nothing.
    worker2, context2, snapshot2 = _worker_and_context(borrows=())
    q3 = M.build_worker_memory_query("q", 2, worker=worker2, context=context2,
                                     snapshot=snapshot2, policy=granting)
    assert q3.borrowed_owners == ()


def test_query_model_role_matrices():
    with pytest.raises(ValidationError):  # context query carries no reader
        M.MemoryQuery(query_text="q", role="context", reader_id="dec.pm",
                      allowed_kinds=("semantic",), allowed_scopes=("agent_own",),
                      top_k=1, policy_digest=HEX_A)
    with pytest.raises(ValidationError):  # worker query requires reader
        M.MemoryQuery(query_text="q", role="worker",
                      allowed_kinds=("semantic",), allowed_scopes=("agent_own",),
                      top_k=1, policy_digest=HEX_A)


# --------------------------------------------------------------------------- #
# marker + normalizer + proposal boundary                                       #
# --------------------------------------------------------------------------- #
def _agent_request() -> M.MemoryProposalRequest:
    return M.MemoryProposalRequest(target=M.AgentMemoryProposalTarget(
        target_agent="dec.pm", topic_slug="lesson-x", title="t",
        confidence=0.7, reasoning="r", lesson_md="body"))


def test_marker_grammar_and_independence():
    req = _agent_request()
    marker = M.build_apply_marker_id(request=req, proposal_id="prop.abc",
                                     intended_target_payload_digest=HEX_A)
    assert marker.startswith("apply.") and len(marker) == 6 + 64
    assert marker == marker.lower()
    # a LogicalId — no path/uppercase/'@'.
    import re
    from guanlan_v2.orchestration.refs import LOGICAL_ID_PATTERN

    assert re.fullmatch(LOGICAL_ID_PATTERN, marker)
    # deterministic; changes with the intended payload digest, not with refs.
    assert marker == M.build_apply_marker_id(
        request=req, proposal_id="prop.abc", intended_target_payload_digest=HEX_A)
    assert marker != M.build_apply_marker_id(
        request=req, proposal_id="prop.abc", intended_target_payload_digest=HEX_B)


def test_console_normalizer_shared_rejections():
    n = M.normalize_console_memory_write
    assert n(scope="global", key="", text="ok")["text"] == "ok"
    for bad in (
        dict(scope="other", key="", text="x"),
        dict(scope="global", key="", text=""),
        dict(scope="global", key="", text="a\nb"),
        dict(scope="global", key="", text="x" * 281),
        dict(scope="global", key="a/b", text="x"),
        dict(scope="global", key="a\\b", text="x"),
        dict(scope="global", key="a..b", text="x"),
        dict(scope="global", key="(k)", text="x"),
        dict(scope="global", key=" k ", text="x"),
    ):
        with pytest.raises(M.MemoryContractError):
            n(**bad)


def test_console_target_must_be_pre_normalized():
    with pytest.raises(ValidationError):
        M.ConsoleMemoryProposalTarget(scope="global", key="a/b", text="x")
    ok = M.ConsoleMemoryProposalTarget(scope="session", key="pref", text="value")
    assert ok.target_kind == "console"


def test_agent_pending_locator_rejects_absolute_unc_and_traversal():
    ok = M.AgentPendingLocator(target_agent="dec.pm", proposal_id="prop.abc",
                               relative_locator="_proposed/dec.pm/2026-07-17_lesson-x.md")
    assert ok.locator_kind == "agent_pending"
    for bad in ("/abs/path.md", "C:/x.md", "..\\up.md", "a/../b.md", "a//b.md",
                "\\\\unc\\share\\f.md"):
        with pytest.raises(ValidationError):
            M.AgentPendingLocator(target_agent="dec.pm", proposal_id="prop.abc",
                                  relative_locator=bad)


def _facade(grants=()):
    from guanlan_v2.orchestration.memory.catalog import phase3_memory_surface

    surface = phase3_memory_surface()
    if not grants:
        return surface.facade_descriptor
    fields = {f: getattr(surface.facade_descriptor, f)
              for f in type(surface.facade_descriptor).model_fields}
    fields["proposal_grants"] = tuple(grants)
    return M.MemoryFacadeDescriptor(**fields)


def _granted_worker(cap_ref):
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

    base = phase3_data_catalog_snapshot().workers[0]
    fields = {name: getattr(base, name) for name in type(base).model_fields}
    fields["capability_allowlist"] = tuple(sorted(
        tuple(base.capability_allowlist) + (cap_ref,), key=lambda c: (c.id, c.version)))
    return type(base)(**fields)


def _proposal_env(target=None, grants=None, session="cs.abc"):
    from guanlan_v2.orchestration.digest import content_digest as cd

    request = M.MemoryProposalRequest(target=target or _agent_request().target)
    facade_nogruniverse = _facade()
    cap = facade_nogruniverse.proposal_capability_ref
    worker = _granted_worker(cap)
    if grants is None:
        grants = (M.MemoryProposalGrant(worker_id=worker.id,
                                        allowed_agent_owners=("dec.pm",),
                                        allowed_console_scopes=("global", "session")),)
    facade = _facade(grants)
    request_ref = typed_ref("MemoryProposalRequest", request.semantic_digest())
    is_console = request.target.target_kind == "console"
    prep = M.MemoryProposalPreparation(
        preparation_key="prep-key-1",
        request_ref=request_ref,
        request_digest=request.semantic_digest(),
        proposer_worker_id=worker.id,
        run_id="run-1",
        plan_digest=HEX_A,
        memory_session_id=session,
        proposal_id="prop.abc",
        proposed_at=NOW,
        effective_date="2026-07-17",
        target_kind=request.target.target_kind,
        target_identity_digest=cd(M._target_logical_identity(request.target)),
        marker_id=M.build_apply_marker_id(request=request, proposal_id="prop.abc",
                                          intended_target_payload_digest=HEX_B),
        intended_target_payload_digest=HEX_B,
        expected_before_target_store_digest="absent",
        intended_after_target_store_digest=HEX_C,
        expected_before_container_digest=HEX_A if is_console else None,
        intended_after_container_digest=HEX_B if is_console else None,
    )
    prep_ref = typed_ref("MemoryProposalPreparation", prep.semantic_digest())
    plan = SimpleNamespace(plan_digest=HEX_A)
    context = SimpleNamespace(memory_session_id=session)
    return request, prep, prep_ref, worker, plan, context, facade


def test_build_memory_proposal_copies_only_frozen_values():
    request, prep, prep_ref, worker, plan, context, facade = _proposal_env()
    proposal = M.build_memory_proposal(
        request, preparation=prep, preparation_ref=prep_ref, worker=worker,
        plan=plan, run_id="run-1", context=context, facade=facade)
    assert proposal.proposal_id == prep.proposal_id
    assert proposal.marker_id == prep.marker_id
    assert proposal.request_ref == prep.request_ref
    assert proposal.preparation_ref == prep_ref
    assert proposal.expected_before_target_store_digest == "absent"


def test_proposal_denied_without_capability_or_grant_or_cross_agent():
    request, prep, prep_ref, worker, plan, context, facade = _proposal_env()
    # (a) worker without the capability.
    from guanlan_v2.orchestration.data.catalog import phase3_data_catalog_snapshot

    plain = phase3_data_catalog_snapshot().workers[0]
    with pytest.raises(M.MemoryAuthorityError):
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=plain, plan=plan, run_id="run-1",
                                context=context, facade=facade)
    # (b) no grant row at all.
    with pytest.raises(M.MemoryAuthorityError):
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=worker, plan=plan, run_id="run-1",
                                context=context, facade=_facade())
    # (c) cross-agent target denied before any pending side effect.
    other = M.MemoryProposalGrant(worker_id=worker.id,
                                  allowed_agent_owners=("mkt.macro",))
    with pytest.raises(M.MemoryAuthorityError):
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=worker, plan=plan, run_id="run-1",
                                context=context, facade=_facade((other,)))


def test_console_proposal_requires_scope_grant_and_a_session_context():
    target = M.ConsoleMemoryProposalTarget(scope="global", key="", text="note")
    request, prep, prep_ref, worker, plan, context, facade = _proposal_env(target=target)
    ok = M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                 worker=worker, plan=plan, run_id="run-1",
                                 context=context, facade=facade)
    assert ok.target.target_kind == "console"
    # session->global widening: grant only 'session', target 'global'.
    session_only = M.MemoryProposalGrant(worker_id=worker.id,
                                         allowed_console_scopes=("session",))
    with pytest.raises(M.MemoryAuthorityError):
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=worker, plan=plan, run_id="run-1",
                                context=context, facade=_facade((session_only,)))
    # a console target from a context with no session fails.
    request2, prep2, prep_ref2, worker2, plan2, _ctx, facade2 = _proposal_env(
        target=target, session=None)
    with pytest.raises(M.MemoryAuthorityError):
        M.build_memory_proposal(request2, preparation=prep2, preparation_ref=prep_ref2,
                                worker=worker2, plan=plan2, run_id="run-1",
                                context=SimpleNamespace(memory_session_id=None),
                                facade=facade2)


def test_proposal_preparation_semantic_drift_conflicts():
    request, prep, prep_ref, worker, plan, context, facade = _proposal_env()
    with pytest.raises(M.MemoryConflictError):  # wrong run
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=worker, plan=plan, run_id="run-2",
                                context=context, facade=facade)
    with pytest.raises(M.MemoryConflictError):  # wrong plan digest
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=worker, plan=SimpleNamespace(plan_digest=HEX_B),
                                run_id="run-1", context=context, facade=facade)
    with pytest.raises(M.MemoryConflictError):  # foreign session
        M.build_memory_proposal(request, preparation=prep, preparation_ref=prep_ref,
                                worker=worker, plan=plan, run_id="run-1",
                                context=SimpleNamespace(memory_session_id="cs.other"),
                                facade=facade)
    with pytest.raises(M.MemoryConflictError):  # forged preparation ref
        M.build_memory_proposal(request, preparation=prep,
                                preparation_ref=typed_ref("MemoryProposalPreparation", HEX_A),
                                worker=worker, plan=plan, run_id="run-1",
                                context=context, facade=facade)


def test_rendered_block_verifies_total_bytes():
    from guanlan_v2.orchestration.refs import ContentRef

    ref = ContentRef(id="memory.render.deterministic", version="1", content_digest=HEX_A)
    ok = M.RenderedMemoryBlock.build(
        selection_digest=HEX_A, snapshot_digest=HEX_B, renderer_ref=ref,
        text="hello", per_record_bytes=(5,), total_bytes=5)
    assert ok.trust == "untrusted_data"
    with pytest.raises(ValidationError):
        M.RenderedMemoryBlock.build(
            selection_digest=HEX_A, snapshot_digest=HEX_B, renderer_ref=ref,
            text="hello", per_record_bytes=(5,), total_bytes=6)
