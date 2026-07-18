# lane0.experience.renderer — experience-selection untrusted block renderer

Trusted renderer handler for the experience bridge. The runtime binds this
catalog identity to `guanlan_v2.orchestration.bootstrap.
render_experience_selection_for_prompt` — a pure function of the
`ExperienceSelection@1` payload: no clock, no I/O, byte-identical output for
the same selection.

Contract (machine-enforced by the renderer itself):
- Untrusted-data channel: the rendered text opens with the untrusted-data
  marker and embeds the selection's `content_digest`
  (`rendered_from_payload_digest` binding) so the rendered bytes are
  auditable against the exact payload.
- Bounded: rendered output above the reviewed byte bound raises
  `ExperienceRenderError` BEFORE prompt assembly — there is no truncation
  path.
- Sentinel-honest: the empty selection renders the explicit
  `无可用类比案例` sentinel — the model is told there is no precedent
  reference, never left room to fabricate one.
- Per-neighbour lines carry only folded, PIT-visible facts: a pending case
  shows no realized numbers and no lesson; matured adds realized numbers;
  reviewed adds the lesson.
