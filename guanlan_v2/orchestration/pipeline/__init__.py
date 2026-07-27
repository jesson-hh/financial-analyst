# -*- coding: utf-8 -*-
"""Phase 10 — product pipelines over the sealed Phase 1-9 orchestration kernel.

Every module in this package is ADDITIVE composition: it imports the implemented
kernel surfaces (catalog runtime, execution runtime, artifact pool, admission,
launcher, durable stores) and assembles them for production — it never forks,
shadows or re-implements any of them, and it never bypasses admission/approval.

The package ``__init__`` re-exports only the five registered public contracts of
:mod:`.contracts` (a pure, dependency-light contract module), so a consumer can
name a Phase 10 payload without reaching into a submodule. The runtime modules
(:mod:`.assembly` and later task modules) are deliberately NOT re-exported —
importing this package must never eagerly pull the production assembly (and, with
it, the engine LLM client) onto the import path. Import those directly.
"""
from __future__ import annotations

from guanlan_v2.orchestration.pipeline.contracts import (
    CandidateSlate,
    EscalationReport,
    RecommendationSlate,
    RunSubject,
    TaSubmission,
)

__all__ = [
    "CandidateSlate",
    "EscalationReport",
    "RecommendationSlate",
    "RunSubject",
    "TaSubmission",
]
