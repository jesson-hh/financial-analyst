---
name: A-share sentiment read
description: |
  Turn pre-fetched A-share news, research and social blocks into a calibrated sentiment band.
  Perfect for: ["single-name A-share sentiment reads","event-driven news clusters","earnings and policy reaction windows"]
  Not ideal for: ["intraday tick scalping","markets without Chinese-language coverage"]
---
## ⚠️ CRITICAL: Data Source Priority
- pre-fetched verified-snapshot news block for the target name
- guanlan datafeed sentiment store (aggregated, month-rotated)
- official exchange announcements and regulatory filings
- curated research-report excerpts provided in-context

Only these blocks are trusted. If a claim is not in one of them, it does not
exist for this read. Never browse or call a live tool.

## Banding rubric
Map the balance of durable evidence to the six-level `overall_band`:
- Bullish / Bearish: corroborated, one-directional catalyst (earnings beat/miss,
  concrete policy tailwind/headwind, credible guidance change).
- Mildly Bullish / Mildly Bearish: a real but partial or single-source tilt.
- Mixed: strong signals genuinely pointing both ways.
- Neutral: thin, stale, or offsetting coverage.

Set `overall_score` in [0, 10] consistent with the band (Bearish 0-2, Mildly
Bearish 2-4, Mixed/Neutral 4-6, Mildly Bullish 6-8, Bullish 8-10).

## Confidence
Derive `confidence` from coverage breadth and source quality, not conviction:
- high: multiple independent, high-quality blocks agree.
- medium: partial corroboration or one strong source.
- low: single unverified source, or the block set is empty.

## Anti-fabrication
Never invent a quote, figure, issuer or date. Attribute every number in the
`narrative` to its block. If the blocks are empty, return Neutral / low
confidence and say the evidence was unavailable. You report sentiment only — you
do not rate, size, or recommend action.
