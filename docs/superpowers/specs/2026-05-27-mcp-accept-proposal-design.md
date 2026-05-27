# MCP `accept_proposal` — close the dream-loop with audit + revert

**Status**: Approved (3-section brainstorm walkthrough complete)
**Date**: 2026-05-27
**Owner**: jesson-hh
**Scope size**: Medium (~3-5 hours: new module, 4 MCP tools, CLI retrofit, tests)

## Goal

Let any MCP client (Claude Code, Cursor, Codex, Claude Desktop) close the
dream-loop end-to-end without manual `fa dream accept`:

```
Claude → report → T+5d → dream (introspector writes _proposed/)
       → list_dream_proposals → accept_proposal → next report uses new rule
                              ↓ (if regret)
                              revert_proposal → audit shows full history
```

Today this loop breaks at the human-review gate (mcp_server.py docstring
line 116: "Does NOT have write access to memories/<agent>/ files directly").
The trust model that gates it (only a human can promote LLM proposals into
production memory) is preserved by:

1. **Audit**: every accept/reject/revert is appended to
   `~/.financial-analyst/audit.jsonl`, including which surface initiated
   the action (CLI, MCP, future UI).
2. **Revert**: any accept is reversible by a `revert_proposal` tool that
   moves the file back to `_proposed/`. No data destruction.
3. **Git stage**: every accept does `git add memories/<agent>/<slug>.md`
   so users see the change in `git status` / `git diff` and can git revert
   on top of audit.

## Context (what already exists)

| Piece | Path | Status |
|-------|------|--------|
| Dream pipeline | `src/financial_analyst/dream/` | ✅ Introspector + OutcomeTracker + ProposalWriter + Aggregator |
| Accept/reject CLI | `cli._dream_promote(target, action)` | ✅ Move logic complete (line 447-483), refuses overwrite |
| `fa dream accept <agent>/<slug>` | typer subcommand | ✅ Wraps `_dream_promote` |
| Buddy read-only view | `buddy.tools._tool_dream_review` (line 898) | ✅ Lists `_proposed/`, no write path |
| UI `dream_review` button | `ui/app.jsx:42` (cn: "反思") | ✅ Read-only via buddy |
| MCP read tools | `list_dream_proposals`, `memory_search` | ✅ Already exposed (16 tools currently) |
| `memories/<agent>/` git tracking | tracked (39 files), `_proposed/` + `_pending_introspections/` gitignored | ✅ |
| Audit log of any sort | ❌ does not exist | This spec adds |
| Memory write from MCP | ❌ disallowed by design | This spec relaxes, with audit + revert as safety net |

## Trust model decision

User chose option **C** (full auto-accept with audit + revert), not
B (allowlist by agent) or A (reject the feature). Implication: any
`_proposed/<agent>/<slug>.md` may be accepted by MCP, including changes
to `risk-officer/hard_rules.md` or other blast-radius-high files. The
safety net is observability (audit + git diff), not gatekeeping.

## Approach

### Shared module: `src/financial_analyst/memory_ops.py` (new)

Single source of truth for all proposal lifecycle operations. Three
surfaces (CLI, MCP, future UI) call into it; each passes a `source`
string so audit can attribute the action.

```python
def accept_proposal(target: str, *, source: str, dry_run: bool = False,
                    project_root: Path = Path.cwd()) -> dict
def reject_proposal(target: str, *, source: str,
                    project_root: Path = Path.cwd()) -> dict
def revert_proposal(target: str, *, source: str,
                    project_root: Path = Path.cwd()) -> dict
def list_audit(limit: int = 20) -> list[dict]
```

Each function:
1. Resolves `target = "<agent>/<slug>"` to source/destination paths under
   `project_root / memories /`.
2. Performs the file move (or copy-back for revert).
3. Appends one JSON line to `~/.financial-analyst/audit.jsonl`.
4. (accept / revert only) runs `git -C <project_root> add <path>`;
   failures degrade to a warning logged inside the audit entry (not fatal).

### Audit log format (`~/.financial-analyst/audit.jsonl`)

Append-only, one JSON object per line:

```json
{
  "id": "a-0042",
  "ts": "2026-05-27T10:30:00+08:00",
  "action": "accept",
  "source": "mcp",
  "target": "bear-advocate/F15_new_pitfall",
  "src": "memories/_proposed/bear-advocate/F15_new_pitfall.md",
  "dst": "memories/bear-advocate/F15_new_pitfall.md",
  "project_root": "G:/financial-analyst",
  "git_staged": true
}
```

Fields:
- `id`: monotonic `a-<N>`, derived from file line count at write time.
- `action`: `"accept" | "reject" | "revert"`.
- `source`: `"cli" | "mcp" | "buddy"` (future).
- `src` / `dst`: relative to `project_root`. For `revert`, src/dst are swapped.
- `git_staged`: `true` if `git add` succeeded; `false` + `git_error: "..."`
  field if not (e.g. not a git repo).
- `reverted_id` (revert only): the `id` of the original accept being undone.

### MCP tool surface (4 new tools, 16 → 20)

| Tool | Signature | Behavior |
|------|-----------|----------|
| `accept_proposal` | `(target: str, dry_run: bool = false)` | Calls `memory_ops.accept_proposal(..., source="mcp")`. `dry_run=true` returns `{would_move: {src, dst}, dry_run: true}` without touching files or audit. |
| `reject_proposal` | `(target: str)` | Calls `memory_ops.reject_proposal(..., source="mcp")`. Deletes the `_proposed/` file, logs audit. |
| `revert_proposal` | `(target: str)` | Calls `memory_ops.revert_proposal(..., source="mcp")`. Moves `memories/<agent>/<slug>.md` back to `_proposed/`. |
| `list_audit` | `(limit: int = 20)` | Returns last N entries from `~/.financial-analyst/audit.jsonl` reversed (newest first). |

Each handler catches exceptions and returns `{"error": "..."}` JSON dict;
no Python tracebacks propagate to the MCP client.

### CLI retrofit

`cli._dream_promote(target, action)` becomes a thin wrapper:

```python
def _dream_promote(target: str, action: str) -> None:
    from financial_analyst.memory_ops import accept_proposal, reject_proposal
    fn = accept_proposal if action == "accept" else reject_proposal
    result = fn(target, source="cli", project_root=Path.cwd())
    if "error" in result:
        typer.echo(result["error"])
        raise typer.Exit(1)
    if action == "accept":
        typer.echo(f"Accepted: {result['src']} → {result['dst']}")
        typer.echo(f"Audit id: {result['id']}")
    else:
        typer.echo(f"Rejected and deleted: {result['src']}")
        typer.echo(f"Audit id: {result['id']}")
```

This ensures **every** CLI accept also writes audit — closing the gap
where user-driven accepts would otherwise leave no trace.

### Security model update (mcp_server.py docstring + docs/mcp.md)

Replace:
> Does NOT have write access to `memories/<agent>/` files directly

With:
> Writes to `memories/<agent>/` ONLY via `accept_proposal` (promotes from
> `_proposed/`) and `revert_proposal` (demotes back to `_proposed/`).
> Every accept / reject / revert is logged to
> `~/.financial-analyst/audit.jsonl` with source attribution. Cannot
> create arbitrary memory content — only promote LLM-vetted proposals
> from the dream loop.

## Error handling

| Condition | Behavior | Audit written? |
|-----------|----------|----------------|
| `target` not in `_proposed/<agent>/` | `{"error": "no proposal matching ..."}` | No |
| accept dst already exists (refuse overwrite) | `{"error": "refusing to overwrite ..."}` | No |
| revert: `memories/<agent>/<slug>.md` doesn't exist | `{"error": "nothing to revert"}` | No |
| `target` malformed (no `/`) | `{"error": "target must be <agent>/<slug>"}` | No |
| `git add` fails (not a repo, ignored path, ...) | accept succeeds; audit entry has `git_staged: false` + `git_error: "..."` | Yes |
| `audit.jsonl` write fails (disk full, permission) | abort accept BEFORE file move; `{"error": "audit write failed: ..."}` | n/a |
| `dry_run=true` | Returns `{would_move, dry_run: true}` without touching anything | No |

## Compatibility check (verified during brainstorm)

- ✅ Buddy `_tool_dream_review` only **reads** `_proposed/`; no write path conflicts with the new MCP write tools.
- ✅ UI `dream_review` button (ui/app.jsx) is a thin wrapper around buddy's read tool; no contention.
- ✅ `memories/<agent>/` is git-tracked (39 files); git stage works.
- ✅ `memories/_proposed/` and `_pending_introspections/` are `.gitignore`'d; rejecting a proposal does not pollute git history.

## Testing

| File | New / changed | Coverage |
|------|---------------|----------|
| `tests/test_memory_ops.py` | **new** | accept happy path, refuse-overwrite, reject delete, revert round-trip (accept → revert → accept again), audit jsonl format check, git-stage success + git-stage failure fallback, malformed-target error, dry_run no-side-effect |
| `tests/test_mcp_server.py` | extend | Update `EXPECTED_TOOLS` set to 20. Add smoke test for `accept_proposal(dry_run=true)` (subprocess MCP round-trip, asserts no file mutation). Add unit test that the 4 new handlers exist + have schemas. |
| `tests/test_memory_cli.py` | extend | After retrofit, assert `fa dream accept` writes one audit entry with `source: "cli"`. |

## Out of scope

- Bulk operations (`accept_all`, `reject_all_with_confidence < X`) — YAGNI.
- LLM session correlation (audit only records client name, not Claude session id).
- Audit log rotation / compression — defer until performance signals it.
- Adding UI accept button — module is designed to plug in, but no UI change in this spec.
- Auto-commit (vs just `git add`) — user chose stage-only.
- Allowlist by agent name (option B from brainstorm) — explicitly rejected in favor of full-access + audit.

## Implementation order

1. Build `memory_ops.py` (new module) with all 4 functions + audit writer.
2. `tests/test_memory_ops.py` — write tests against the module, get green.
3. Retrofit `cli._dream_promote` to call into `memory_ops`. Run existing `tests/test_memory_cli.py` for no regression.
4. Add 4 MCP tool handlers + TOOLS entries in `mcp_server.py`. Update module docstring tool list.
5. Update `tests/test_mcp_server.py`: bump `EXPECTED_TOOLS` to 20, add `accept_proposal(dry_run=true)` smoke test.
6. Update `docs/mcp.md`: tool table (13 → 20 — already noted 16 → 20 here; the 13→16 jump landed earlier this session), security model paragraph.
7. Single commit `feat(mcp): close dream loop — accept/reject/revert/list_audit + memory_ops module`.
8. Run full memory test suite (`pytest tests/test_memory*.py tests/test_mcp_server.py`) — must be green.

## Acceptance criteria

1. `pytest tests/test_memory_ops.py tests/test_mcp_server.py tests/test_memory_cli.py` all green.
2. `EXPECTED_TOOLS` in `test_mcp_server.py` equals 20 and matches live tool list.
3. End-to-end smoke (manual or via subprocess in test): MCP `accept_proposal("<some test target>", dry_run=true)` returns `{would_move: ..., dry_run: true}` without touching files or audit.
4. CLI `fa dream accept ...` (after retrofit) writes an audit entry with `source: "cli"`.
5. `docs/mcp.md` security model paragraph reflects the new write capability via accept/revert with audit safety net.
6. No `Co-Authored-By:` trailers on any commit.

## Risks

| Risk | Mitigation |
|------|-----------|
| LLM-chain (Introspector → Claude accept) accepts a hallucinated rule into hard_rules.md, contaminating future reports | (1) audit log lets user spot it, (2) `git status` shows the change before any commit, (3) `revert_proposal` provides easy undo, (4) the proposal had to pass Introspector self-judgment first (already a check) |
| Audit log growth (no rotation) | Append-only jsonl, ~300 bytes per entry; 10k actions ≈ 3 MB. Defer rotation until real pain. |
| Git stage races with concurrent edits (user editing file while MCP is moving it) | Git's own locking + `_dream_promote`'s refuse-overwrite guard cover this; in worst case, audit shows what was attempted and user resolves manually. |
| Audit log on a network-mount user home (slow, can fail) | If write fails, accept aborts (not silent). User sees clear error. Mitigation: future option to redirect audit to local-only path via env var. |

## Follow-ups (not blockers)

After this spec lands:

- Wire buddy `/memory/accept` HTTP endpoint into the same `memory_ops` module → enables UI accept button later with zero new audit code.
- Add an `audit query` filter (by agent, by source, by date range) to `list_audit` if 20-most-recent becomes too coarse.
- Per-session Claude attribution: if MCP client passes session metadata, capture it in audit entries (cleaner than just `source: "mcp"`).
