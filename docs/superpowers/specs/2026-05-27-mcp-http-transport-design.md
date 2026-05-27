# MCP Streamable HTTP transport — mount at `/mcp` on the buddy backend

**Status**: Approved (3-section brainstorm walkthrough complete)
**Date**: 2026-05-27
**Owner**: jesson-hh
**Scope size**: Small-to-medium (~3-5 hours: 1 new module, 1 mount line, 5 tests, docs)

## Goal

Expose the same 20 fa MCP tools over **Streamable HTTP** (in addition to the
existing stdio transport), so AI IDEs that don't speak stdio MCP — primarily
JetBrains plugins and any client that prefers HTTP — can connect to fa without
extra binaries or background processes beyond what `fa start` already runs.

Endpoint: `http://127.0.0.1:9999/mcp`. Localhost only, no auth.

## Context

| Piece | Status |
|-------|--------|
| `mcp>=1.0` SDK | ✅ 1.26.0 installed; ships `StreamableHTTPSessionManager`, `StreamableHTTPServerTransport` |
| stdio MCP server | ✅ `src/financial_analyst/mcp_server.py` — 20 tools registered, server build via `_build_server()` |
| buddy backend | ✅ FastAPI on 0.0.0.0:9999 via `build_app()` (buddy/server.py line 146); uvicorn launch line 918 |
| `fa start` | ✅ Spawns buddy automatically as part of the wizard / launch flow |
| stdio docs | ✅ `docs/mcp.md` §1-§2 covers Claude Desktop / Code / Cursor / Codex (stdio) |
| HTTP MCP docs | ❌ None — this spec adds §3 |
| HTTP MCP impl | ❌ Does not exist — this spec adds |

## Trust model decision (chosen during brainstorm)

User picked **option A — localhost only, no auth**. Binding stays
`127.0.0.1` via buddy's existing host parameter; the firewall is the
only protection layer. Use cases B (LAN + token) and C (public + OAuth)
are explicitly deferred.

## Long-task strategy (chosen during brainstorm)

User picked **option 1 — YAGNI, don't change long-task semantics**.
HTTP transport inherits the same client-side 5-minute tool-call timeout
as stdio. Docs continue to direct users to `ask` + `read_past_report`
for long-running operations. Adding MCP `notifications/progress` is a
separate spec for the future.

## Architecture

```
buddy/server.py build_app()
└── FastAPI on 127.0.0.1:9999  (existing — UI + ~30 buddy routes)
    └── app.mount("/mcp", build_mcp_http_app())   ← new line
                         └── Starlette Mount
                             └── StreamableHTTPSessionManager.handle_request
                                 └── reuses mcp_server._build_server()
                                     └── 20 tools from mcp_server.TOOLS
```

Single source of tools: both transports read the same `mcp_server.TOOLS`
dict. Adding a tool there auto-exposes it to both stdio and HTTP. The
locked-in `EXPECTED_TOOLS` set in `tests/test_mcp_server.py` remains
the drift detector for both transports.

### New module: `src/financial_analyst/mcp_http.py`

```python
def build_mcp_http_app() -> ASGIApp:
    """Build a Starlette app exposing financial-analyst MCP over Streamable HTTP.

    Reuses the existing mcp_server._build_server() — same 20 tools, same
    handlers, same TOOLS dict. Intended to be mounted at /mcp on the
    buddy FastAPI app.
    """
```

Inside:
- Import `StreamableHTTPSessionManager` from `mcp.server.streamable_http_manager`
- Call `_build_server()` from `mcp_server.py`
- Construct manager `StreamableHTTPSessionManager(app=server)`
- Wrap `manager.handle_request` as a Starlette `Mount("/", app=handle)`
- Expose the manager's lifespan context so buddy can await it on startup/shutdown

### `buddy/server.py` integration

Two edits to `build_app()`:

1. Mount the MCP sub-app at `/mcp`:
   ```python
   from financial_analyst.mcp_http import build_mcp_http_app
   mcp_app = build_mcp_http_app()
   app.mount("/mcp", mcp_app)
   ```

2. Merge the MCP session manager's lifespan into FastAPI's lifespan so the
   manager's session table is started/torn down with the parent app. If
   the SDK manager exposes its own lifespan, use `contextlib.asynccontextmanager`
   to combine. If buddy's `build_app()` has no current lifespan parameter,
   add one and pass via `FastAPI(lifespan=combined)`.

No changes to buddy routes or behavior.

## Tests (new `tests/test_mcp_http.py`)

| Test | Asserts |
|------|---------|
| `test_http_initialize_handshake` | POST `/mcp` with initialize → response has `protocolVersion` + `serverInfo.name == "financial-analyst"` |
| `test_http_tools_list_matches_expected_set` | tools/list returns exactly `EXPECTED_TOOLS` (imported from `tests.test_mcp_server`) — drift detector across both transports |
| `test_http_chain_lookup_roundtrip` | tools/call `chain_lookup("SH688256")` returns primary_product or clean error (mirror of stdio smoke) |
| `test_http_quick_quote_roundtrip` | tools/call `quick_quote("SH600519")` returns OHLCV dict |
| `test_http_session_isolation` | Two concurrent `AsyncClient`s each get distinct session ids; one's `tools/call` doesn't bleed into the other |

All tests use `httpx.ASGITransport(app=build_app())` — no real TCP listen,
runs entirely in-process. Fast. No PATH dependency on `financial-analyst-mcp`
console script either.

## Docs (`docs/mcp.md` add §3)

New top-level section after the existing stdio §1-§2:

```
## 3. Streamable HTTP transport (remote IDEs / JetBrains / cross-process)

Prereq: `fa start` is running (buddy on 127.0.0.1:9999). MCP is then
available at http://127.0.0.1:9999/mcp without any extra steps.
```

Followed by client config blocks for:
- Claude Desktop / Claude Code (JSON `url` field)
- Cursor (`~/.cursor/mcp.json` with `url`)
- Codex CLI (`~/.codex/config.toml` with `url` — verify field name via WebFetch during implementation)
- JetBrains IDE plugin note

Plus a long-task warning paragraph reusing the existing stdio § wording.

Update the "Available Tools" table preamble — note both transports expose
the same 20 tools.

## Error handling

| Condition | Behavior |
|-----------|----------|
| Non JSON-RPC request body | MCP SDK returns -32700 parse error |
| Tool handler raises Python exception | Reuses `mcp_server.call_tool` wrapper, returns `{"error": "..."}` text content with `isError: true` |
| buddy not running | TCP connect refused; same UX as buddy itself being down — out of scope for MCP layer |
| Duplicate initialize on same session | SDK resets session state |
| Invalid `Accept` header (HTTP streamable requires `application/json, text/event-stream`) | SDK returns 406; docs flag this in troubleshooting |

## Implementation steps

1. New module `src/financial_analyst/mcp_http.py` with `build_mcp_http_app()` (~40 lines).
2. Edit `buddy/server.py build_app()` — import + mount + merge lifespan (~10 lines).
3. New `tests/test_mcp_http.py` — 5 tests via `httpx.ASGITransport`.
4. WebFetch OpenAI Codex MCP docs to confirm HTTP `url` field name in TOML; update docs accordingly.
5. Edit `docs/mcp.md` — add §3 HTTP transport block.
6. Run full memory + MCP test suite: `pytest tests/test_mcp_*.py tests/test_memory_*.py` — must be green.
7. Single commit: `feat(mcp): HTTP streamable transport mounted at /mcp on buddy backend`.
8. 实机验证: `fa start`, then `curl -N -X POST http://127.0.0.1:9999/mcp -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" -d '<initialize frame>'` — assert real response, then clean up.

## Acceptance criteria

1. `tests/test_mcp_http.py` 5/5 green, `tests/test_mcp_server.py` 15/15 unchanged.
2. `curl` against a live `fa start` returns a valid MCP initialize response from `/mcp`.
3. `docs/mcp.md` §3 documents the URL endpoint, 4 client configs (Claude / Cursor / Codex / JetBrains), and the long-task workaround.
4. Tool count unchanged at 20 — HTTP and stdio share `mcp_server.TOOLS`.
5. Mounting MCP does not break any existing buddy route (run a quick `curl /health` after).
6. No `Co-Authored-By:` trailers on the commit.

## Risks

| Risk | Mitigation |
|------|-----------|
| `StreamableHTTPSessionManager` API differs across mcp SDK versions | Pin behavior to `mcp>=1.26` and read the SDK source during implementation, not docs (docs lag) |
| Buddy lifespan merge bug — MCP session table not torn down properly | Add a teardown test (start → stop → assert manager state clean); use SDK examples as reference |
| `Accept` header pickiness blocks some clients | Document the required headers in troubleshooting; the SDK enforces them per MCP spec |
| User binds buddy to 0.0.0.0 (`fa serve --host 0.0.0.0`) and exposes /mcp to LAN | Out of scope to police; if user wants LAN, they want the auth that use case B brings (separate spec) |
| Concurrent stdio + HTTP clients fight over same project state (e.g. both accept the same proposal) | The accept_proposal "refuse overwrite" guard catches the race; the second one gets a clean error |

## Out of scope (deferred to follow-ups)

- Auth — token / OAuth / TLS (use cases B/C).
- Long-task progress notifications via `notifications/progress`.
- Async job pattern (job_id + poll).
- Standalone `fa mcp-http --port 9998` command (embed in buddy is sufficient).
- Dockerfile / docker-compose example.
- Env var to disable HTTP MCP (always-on for now; if users complain, add `FA_DISABLE_MCP_HTTP`).
- Per-client session metrics / logging dashboard.

## Follow-ups (separate brainstorms)

After this lands:

- **Spec**: Long-task progress notifications — wire `notifications/progress` into orchestrator hooks so `report` / `brief` / `data_update` can keep MCP clients alive past their default 5-min timeout.
- **Spec**: Optional token auth — add `FA_MCP_TOKEN` env var; if set, `/mcp` requires `Authorization: Bearer <token>` header. Enables use case B (LAN sharing).
- **Spec**: Dockerfile + compose for containerized deployment (use case D).
