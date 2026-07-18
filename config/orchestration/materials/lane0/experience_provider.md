# lane0.experience.provider — experience retrieval bridge provider

Trusted provider handler for the `experience.bridge` execution bridge. The
runtime binds this catalog identity to
`guanlan_v2.orchestration.bootstrap.ExperienceRetrievalBackend` — a READ-ONLY
wrapper over the Phase 5 Task 5 `retrieve_neighbours` pure core, closed over
the event-folded case views and the versioned point-in-time
`ExperienceScalerSnapshot`.

Confinement (machine-enforced elsewhere, restated here for review):
- Reachable ONLY through the Phase 2 `CapabilityGateway` under the
  analyzer-bound summary (`max_capability_invocations=1` — a second begin
  fails BEFORE backend I/O). Never invoked directly by a worker or a prompt.
- Read-only: the backend holds no `ExperienceLog`, no store write handle, no
  grading or appending surface — case creation/maturation/review is never
  reachable from a worker.
- PIT: retrieval re-asserts `available_at <= as_of` and refuses a
  future-fitted scaler (`FutureDataRefused`); an empty selection is honest
  (`cold_start:0_neighbours`) and still finalizes one successful call.
- Evidence: selection/scaler payloads and refs flow through
  `BridgeEvidenceWriter` only, under executor-minted ordinal tokens (the
  provider echoes tokens, never mints them).
