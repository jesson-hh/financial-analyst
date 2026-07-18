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
- One config row per Lane 0 LLM worker (`market.regime`, `market.rotation`);
  a worker without a reviewed row fails analysis (never a default grant).
