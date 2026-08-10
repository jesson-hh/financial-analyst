# lane0.experience.analyzer — experience bridge static support analyzer

Trusted pure support analyzer for the `experience.bridge` execution bridge.
The runtime binds this catalog identity to
`guanlan_v2.orchestration.bootstrap.ExperienceBridgeSupportAnalyzer` — a pure,
deterministic, clock/store/gateway-free `BridgeSupportAnalyzer` (no I/O; runs
before reservation/approval/freeze).

Reviewed bounds (closed; derived from the config rows, never trusted numbers):
- `max_capability_invocations = 1` — exactly one `experience.retrieve` call
  per node; the CapabilityGateway rejects the max+1 begin before backend I/O.
- `min_finalized_tool_calls_on_success = 1` — node success REQUIRES one
  finalized call (`always_invoke` + `success_requires_finalized_call=true`
  are Literal-pinned on the config binding). An EMPTY `ExperienceSelection`
  is still one successful finalized call, so a cold-start library satisfies
  the REQUIRED discipline honestly.
- The summary's allowed capability set is exactly the config row's
  `capability_ref`, cross-checked against the WorkerSpec allowlist — a row
  granting a capability outside the allowlist is an analyzer failure, not
  authority.
- One config row per Lane 0 LLM worker (`market.regime`, `market.rotation`).
  A worker that reaches this analyzer with ZERO rows is discriminated by its
  allowlist, never by default (controller ruling C3, Phase 10 · Task 11):
  - its `capability_allowlist` does NOT hold the activation capability — the
    bridge activated on the `experience_cases` read category alone, so this
    worker was never granted anything. It receives the honest
    ZERO-CONTRIBUTION summary (`min=0`, `max=0`, no capability refs — the same
    posture the Phase-3 data analyzer takes for a rowless worker) and
    `check_runtime_support` COMPLETES and reports the absence instead of
    crashing.
  - its `capability_allowlist` DOES hold it — a reviewed grant lost its row.
    That is LOUD (`CatalogError`) and must never degrade silently: "never
    granted" and "granted, row missing" are two different facts and this
    analyzer refuses to collapse them.
  More than one row for one worker is LOUD for the same reason — ambiguity is
  refused, never averaged.
