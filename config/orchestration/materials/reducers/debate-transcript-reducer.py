# -*- coding: utf-8 -*-
"""Trusted reducer material: ``debate.transcript_reducer`` (Phase 8 * Task 9).

This is the catalog ``kind="reducer"`` material a Plan's ``ReducerCfg.reducer_ref``
wires for a Lane-D debate slot. It is authorized by its content digest in the
catalog content manifest; the executable, byte-deterministic handler it names lives
in ``guanlan_v2.orchestration.debate`` (``fold_debate_messages`` /
``debate_transcript_reducer_handler``), registered under the frozen logical id
``DEBATE_TRANSCRIPT_REDUCER_ID = "debate.transcript_reducer"``.

Contract (the only behavior a Plan may rely on):

* Input  : the committed producer Artifacts of the debate slot's seat nodes — one
           Artifact per ``(debate_id, round, turn, role)`` seat turn.
* Output : a ``DebateTranscript@1`` payload.
* Order  : message order derives ONLY from the Plan node debate fields sorted by
           ``(debate_round, debate_turn)`` — NEVER thread / stage / commit completion
           order. The Task-8 ``pool.fold_reducer_producers`` seam pre-sorts the
           producers by ``producer_node_id``; this handler re-derives the transcript
           from Plan order and ignores that arrival order, so a shuffled completion
           order yields a byte-identical transcript (the exit-gate determinism
           property). ``artifact_id`` / ``created_at`` are read from each committed
           Artifact; ``created_at`` is audit-only and never perturbs the transcript
           semantic digest.

There is NO expression language and no configurable behavior: the fold is a fixed
pure function. A different reduction is a different reducer id, not a parameter of
this material.

REDUCER_ID = "debate.transcript_reducer"
OUTPUT_SCHEMA = "DebateTranscript@1"
MESSAGE_SCHEMA = "DebateMessage@1"
MAX_ROUNDS = 2  # spec §0 决定 2 — the reviewed Lane-D bounded-debate round cap.
"""

REDUCER_ID = "debate.transcript_reducer"
OUTPUT_SCHEMA = "DebateTranscript@1"
MESSAGE_SCHEMA = "DebateMessage@1"
MAX_ROUNDS = 2
