# -*- coding: utf-8 -*-
"""Phase 10 — product pipelines over the sealed Phase 1-9 orchestration kernel.

Every module in this package is ADDITIVE composition: it imports the implemented
kernel surfaces (catalog runtime, execution runtime, artifact pool, admission,
launcher, durable stores) and assembles them for production — it never forks,
shadows or re-implements any of them, and it never bypasses admission/approval.
"""
