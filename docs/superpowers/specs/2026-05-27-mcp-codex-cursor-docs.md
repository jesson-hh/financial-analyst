# MCP integration — smoke-test current stdio server + add Codex & Cursor client setup

**Status**: Approved (3-section design)
**Date**: 2026-05-27
**Owner**: jesson-hh
**Scope size**: Small (~1-2 hours, docs-only + read-only smoke test)

## Goal

1. **Prove the existing `financial-analyst-mcp` stdio server actually works end-to-end** — handshake + tool discovery + 2 fast tool calls — so we can stop guessing whether it's broken.
2. **Extend `docs/mcp.md` to cover two more AI IDE clients**: Cursor and Codex CLI. The current doc only covers Claude Desktop and Claude Code.

Both deliverables together let an external user `pip install financial-analyst` and wire the MCP into whatever AI IDE they use without trial-and-error.

## Context

What already exists (was discovered during exploration, not built in this spec):

| Piece | Path | Status |
|------|------|--------|
| Stdio MCP server | `src/financial_analyst/mcp_server.py` | ✅ 395 lines, 13 tools registered, uses `mcp>=1.0` SDK |
| Console script | `pyproject.toml:94` `financial-analyst-mcp = "financial_analyst.mcp_server:main"` | ✅ |
| MCP dep | `pyproject.toml:62` `mcp>=1.0` | ✅ |
| Setup docs | `docs/mcp.md` (147 lines) | ✅ for Claude Desktop + Claude Code; ❌ no Cursor / Codex |

The 13 registered tools are: `ask`, `quick_quote`, `quick_factors`, `memory_search`, `list_past_reports`, `read_past_report`, `list_dream_proposals`, `report`, `mainline`, `brief`, `intraday`, `dream`, `dream_aggregate`.

What has **never been verified**: that the server actually starts, returns tools, and answers a `tools/call` request. No tests exist. This spec adds that proof.

## Approach

### Verification — spawn subprocess + raw JSON-RPC

Instead of trying to plumb the server into a live Claude Code session (slow feedback loop), drive it directly with `Bash` from the stocks cwd:

```
financial-analyst-mcp  ← stdin: 4 JSON-RPC frames, stdout: 4 responses
```

Sent frames (in order):

1. `initialize` — protocol handshake, expect server capabilities + `protocolVersion`.
2. `notifications/initialized` — fire-and-forget per MCP spec.
3. `tools/list` — expect array of 13 `Tool` objects with `name`, `description`, `inputSchema`.
4. `tools/call` × 2:
   - `quick_quote(code="SH600519")` — <1s, no LLM, exercises the data layer.
   - `memory_search(query="game-capital", top_k=3)` — <1s, exercises FTS5.

**Why not these tools**: `report` / `brief` / `mainline` take minutes and may time out our Bash call. `ask` requires `DASHSCOPE_API_KEY` and burns LLM tokens. `dream*` writes files. `quick_quote` + `memory_search` are read-only, fast, and exercise two different code paths.

**Acceptance**: all 4 frames return well-formed JSON-RPC responses; no Python tracebacks; tool calls return result dicts (content may be empty if no past report exists for SH600519 — empty is still pass).

### Docs — add §2c Cursor + §2d Codex to `docs/mcp.md`

Insert after the existing `2b. Configure Claude Code` block, before `2c. Verify install`. Renumber the existing `2c` to `2e`. New content:

**§2c Cursor**: JSON config at `~/.cursor/mcp.json` (global) or `<project>/.cursor/mcp.json` (per-project). Same `mcpServers` shape as Claude Desktop:

```json
{
  "mcpServers": {
    "financial-analyst": {
      "command": "financial-analyst-mcp",
      "args": [],
      "env": { "DASHSCOPE_API_KEY": "your-key" }
    }
  }
}
```

Restart Cursor; Composer / Chat picks up tools automatically. `@financial-analyst` mentions them explicitly.

**§2d Codex CLI**: TOML config at `~/.codex/config.toml`:

```toml
[mcp_servers.financial-analyst]
command = "financial-analyst-mcp"
args = []

[mcp_servers.financial-analyst.env]
DASHSCOPE_API_KEY = "your-key"
```

Restart codex; `/mcp` shows the tools.

**One uncertainty**: the TOML schema (`mcp_servers` singular vs plural, `env` as sub-table vs inline) is from memory. Implementation step 2 below WebFetches the OpenAI codex repo's MCP doc to verify before writing.

## Out of scope

- `fa mcp install --client <name>` one-shot installer command (would belong to medium scope).
- HTTP / SSE MCP transport (large scope; current stdio works for local AI IDE which is the audience).
- Refactoring `mcp_server.py` itself — only patch if smoke test reveals a real bug.
- New PyPI release, `v1.0.7` retagging, or any tag operation.
- Pushing `main` to origin — local commit only; user decides push after seeing the diff (same pattern as previous turns in this session).

## Implementation steps

| # | Action | Done when |
|---|--------|-----------|
| 1 | Activate `financial-analyst` env and confirm `financial-analyst-mcp` is on PATH | `where financial-analyst-mcp` returns a path |
| 2 | WebFetch `https://github.com/openai/codex` MCP docs; capture exact TOML schema | TOML snippet matches official |
| 3 | Run the 4-frame smoke test via Bash heredoc + report the responses inline | 4 valid JSON-RPC responses captured |
| 4 | Edit `docs/mcp.md`: insert §2c Cursor and §2d Codex; renumber existing §2c → §2e | File still parses as well-formed Markdown; existing sections intact |
| 5 | `git add docs/mcp.md && git commit -m "docs(mcp): add Cursor + Codex CLI client setup"` (no Co-Author trailer) | Commit lands on local `main`, working tree clean |
| 6 | If smoke test failed in step 3: open follow-up fix commit for `mcp_server.py`; otherwise stop | N/A unless triggered |

Step 6 is conditional. The other 5 always run.

## Risks

| Risk | Mitigation |
|------|-----------|
| `financial-analyst-mcp` not on PATH (wrong env activated) | Step 1 checks; abort early with clear message if missing |
| Smoke test fails because of an import-time exception (BYOM plugin / data path) | Report the traceback verbatim, don't try to fix in this scope unless the fix is one-line obvious |
| WebFetch returns a stale or generic doc | Cross-reference at least one example in the codex repo's `examples/` dir; if still unclear, write `# TODO: verify against your codex version` in the Markdown |
| User opens new Claude Code session after the commit and tools still don't show | Out of scope — that's a per-client install issue covered by the troubleshooting table in `docs/mcp.md` |

## Acceptance criteria

1. Bash output in the chat showing 4 successful JSON-RPC round-trips with `financial-analyst-mcp`.
2. `docs/mcp.md` contains a new §2c Cursor block and a new §2d Codex block, with the existing Verify section renumbered to §2e.
3. One git commit on local `main` with message `docs(mcp): add Cursor + Codex CLI client setup`, no `Co-Authored-By:` trailer, working tree clean except for pre-existing untracked `docs/superpowers/plans/`.

## Out-of-band follow-ups (not blockers)

After this scope lands, candidate next pieces (each its own brainstorm):

- `fa mcp install --client <claude-desktop|claude-code|cursor|codex>` — auto-patch the user's config JSON / TOML. Eliminates the manual JSON-editing step in the docs.
- HTTP `streamable_http` MCP transport so the server can run remotely / inside Docker / behind a corp proxy.
- A `tests/test_mcp_server.py` that runs the 4-frame smoke test as part of CI.

None of these are in this spec.
