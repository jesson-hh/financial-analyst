# Orchestration framework — the reasoning record

These files were written during the nine-phase, subagent-driven build of
`guanlan_v2/orchestration/` (2026-07-15 … 2026-07-26). They lived under
`.superpowers/sdd/`, which `.gitignore:75` excludes, so none of it was in the
repository. Archived here on 2026-07-26 because the code alone does not record
*why* it looks the way it does — the rulings, the retractions, and the four
false-green findings are only here.

| File | What it is |
| --- | --- |
| `orchestration-phase1-9-ledger.md` | The durable ledger. One entry per task: brief, implementer report, independent review verdict, controller ruling, carry. Also holds the R1–R26 carry list and the four "false green" findings. |
| `post-merge/*.md` | Implementation + review reports for the five pieces built *after* the merge to main, when a survey found the framework could not start at all. |

## How to read the ledger

Entries are append-only and chronological. Each task reads:

- **Brief** — what the implementer was told, with grounded file:line seams.
- **Report** — what they actually built, including refusals.
- **Review** — an independent opus reviewer, dispatched with cruxes chosen to
  attack the task's most likely failure, not to confirm it.
- **Ruling** — the controller's decision when implementer and reviewer disagreed.

Search for `RULING`, `CARRY`, or `R<n>` to navigate.

## Two things worth reading even if you skip the rest

**The four false greens.** Four times a suite was green for the wrong reason: a
metering test that passed *because* two date keys disagreed; a `/weiwo/start`
route that returned `200 {ok:true}` while recording nothing; an e2e gate marked
✅ on an artifact the test had fabricated; and a watcher registration half that
did not exist at all. Green is evidence about the test, not about the code.

**Downstream corrected upstream five times.** Implementers twice refused to ship
a claim they could not defend; an implementer corrected a reviewer's premise on
the real interpreter; a reviewer answered "they are right, I was wrong"; and a
reviewer found a stronger property than the implementer had claimed. Every one
of those made the result more correct than the instruction that produced it.

## What "the framework runs" means

As of the final entry: the launcher's own composition and gating code executed
for real against the real durable store, and a restarted server served the
persisted result. But every input that produces a *decision* — including the
plan runner and the approval gate — still comes from the Phase-9 test fixtures.
**The sequencing is proven; the decision-making half is not built.** The next
piece is named at the end of the ledger.
