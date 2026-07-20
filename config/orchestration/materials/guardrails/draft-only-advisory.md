# Draft-only advisory guardrail

This guardrail is binding on every offline research / curator lane whose output is a
DRAFT proposal for human review (the #25 factor miner and the #27 pattern curator
同构): the seat proposes, a human decides, and the seat holds zero write or trading
authority.

Draft-only, never adopted
- The seat's only product is a DRAFT proposal (a `draft_only=True` payload). A proposal
  can never be self-adopted: adoption — a registry bump, a skill change, a battery
  revision — happens only downstream through human git-review, never inside this seat.

No write capability, no decision authority
- The seat has a FORBIDDEN tool policy and an empty capability allowlist: it can call no
  tool and therefore cannot write memory, skills, code or any registry.
- It never emits a decision-class schema and it can never emit a trading decision
  (`can_emit_decision=False`, `decision_authority="none"`). It rates no name, sizes no
  position and executes nothing.

Offline, never in the daily main DAG
- The seat is an OFFLINE research occupant. It is never selected into a live or daily
  main DAG and no preset graph references it — 真跑 stays deferred until the reviewing
  human wires it, if ever.

Scope
- This guardrail governs the advisory boundary. It grants no authority; it only affirms
  that everything this seat produces is a proposal a human must review before anything
  changes.
