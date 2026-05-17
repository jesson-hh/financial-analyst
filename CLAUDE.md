# Claude Code Working Instructions

Multi-agent A-share research workstation. 13 sub-agents in three trust tiers.

## Hard Rules
- Only `report-writer` has the `write` tool. All others are read-only.
- Untrusted sources (news, F10) only touch `news-reader` and `f10-reader`. Their output is pydantic-validated JSON.
- Memory is per-agent under `memories/<agent>/*.md`. Hot-reloadable: edit a markdown → next agent invocation picks it up.
- LLM is provider-abstracted via LiteLLM. Configure in `config/llm.yaml`.

## Code Style
- Python 3.11+, type hints, async-first (asyncio.gather for parallel tiers).
- pydantic v2 for all sub-agent IO.
- pytest for tests. Mock LLM in unit tests; real LLM only in integration tests under `tests/integration/`.

## Workflow
- Each task in `docs/superpowers/plans/*.md` should produce exactly one commit.
- Run `pytest tests/` before each commit.
