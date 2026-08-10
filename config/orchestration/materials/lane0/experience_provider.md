# lane0.experience.provider — experience retrieval bridge provider

Trusted provider handler for the `experience.bridge` execution bridge. The
runtime binds this catalog identity to
`guanlan_v2.orchestration.bootstrap.ExperienceRetrievalBackend` — a READ-ONLY
wrapper over the Phase 5 Task 5 `retrieve_neighbours` pure core, closed over
the event-folded case views and the versioned point-in-time
`ExperienceScalerSnapshot`.

Confinement (machine-enforced elsewhere, restated here for review):
- Reachable ONLY through the Phase 2 `CapabilityGateway`, and only for a
  GRANTED worker — one whose `capability_allowlist` holds
  `experience.retrieve`. For such a worker the analyzer-bound summary is
  `max_capability_invocations=1` (a second begin fails BEFORE backend I/O).
  Never invoked directly by a worker or a prompt.
- Non-granted discrimination (controller ruling C3): a worker that reaches
  this bridge on the `experience_cases` read category ALONE, without the
  capability in its allowlist, was never granted retrieval. It arrives under
  the ruled ZERO-contribution summary (`max_capability_invocations=0`) and
  this provider does NOT invoke — zero invocations, zero ToolCallRecords, zero
  evidence writes, and NO untrusted block, not even the empty sentinel:
  retrieval never ran, so rendering a cold-start block would fabricate a read.
- Read-only: the backend holds no `ExperienceLog`, no store write handle, no
  grading or appending surface — case creation/maturation/review is never
  reachable from a worker.
- PIT: retrieval re-asserts `available_at <= as_of` and refuses a
  future-fitted scaler (`FutureDataRefused`); for a GRANTED worker an empty
  selection is honest (`cold_start:0_neighbours`) and still finalizes one
  successful call.
- Evidence: selection/scaler payloads and refs flow through
  `BridgeEvidenceWriter` only, under executor-minted ordinal tokens (the
  provider echoes tokens, never mints them).
