# Number-provenance guardrail

This guardrail is binding on every worker whose evidence policy sets
`require_number_anchors=True` and `allow_unsourced_numbers=False`.

Every number is anchored
- Every number, date, percentage, price, probability, count or temperature in
  your output must trace to a specific provided block (source_span discipline).
  If you cannot point to the block it came from, you may not state the figure.
- State the figure with enough of its context (which name, which as_of, which
  source) that a reader can find it in the evidence. A bare number with no anchor
  is a fabrication risk and must be dropped.

Known scan-boundary limit
- The desk's automated number-provenance scan reads the structured output
  surface. Numerals that appear only inside RENDERED-MARKDOWN prose within an
  evidence block are OUTSIDE that automated scan boundary — a known, accepted
  limitation. Because the machine check cannot see them, you remain personally
  responsible for anchoring every figure you restate; do not treat the scan's
  silence as permission to carry an unsourced number.

Policy note
- This guardrail pairs with `require_number_anchors=True` +
  `allow_unsourced_numbers=False`. The contradictory relaxation
  (`require_number_anchors=False` together with `allow_unsourced_numbers=False`)
  is never configured — it would demand anchors while disabling the requirement,
  a meaningless combination.

Scope
- This guardrail governs how you support figures. It grants no authority to act.
