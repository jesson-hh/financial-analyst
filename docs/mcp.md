# MCP Server — Claude Desktop Integration

`financial-analyst-mcp` exposes 12 tools to Claude Desktop / Claude Code / OpenClaw via the [Model Context Protocol](https://modelcontextprotocol.io/).

After this is set up, you can say in Claude Desktop:

> "Look at SH600519 — is it a buy?"

and Claude will autonomously call `ask`, `quick_quote`, `read_past_report`, etc. through your local `financial-analyst-mcp` subprocess.

## Setup

### 1. Install (or upgrade) financial-analyst

```bash
pip install -U financial-analyst
```

This installs both `financial-analyst` and `financial-analyst-mcp` console scripts.

### 2. Configure Claude Desktop

Edit `~/.config/claude/claude_desktop_config.json` (Linux/Mac) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "financial-analyst": {
      "command": "financial-analyst-mcp",
      "args": [],
      "env": {
        "TUSHARE_TOKEN": "your-tushare-token",
        "DASHSCOPE_API_KEY": "your-aliyun-key"
      }
    }
  }
}
```

(The env block is optional if your `.env` is set up; `financial-analyst-mcp` also loads `.env` at startup.)

### 3. Restart Claude Desktop

Tools should appear in the tool drawer.

## Available Tools

| Tool | Speed | Use case |
|---|---|---|
| `ask` | ~10-30s | "What did the last report say about SH600519?" |
| `quick_quote` | <1s | "Current PE of SH600519" |
| `quick_factors` | ~1s | "Show me the rev_20 factor for SZ002594" |
| `memory_search` | <1s | "Are there pitfalls about game-capital tickers?" |
| `list_past_reports` | <1s | "What stocks have I researched recently?" |
| `read_past_report` | <1s | "Show me my full report on SH600519" |
| `list_dream_proposals` | <1s | "Are there any pending memory updates?" |
| `report` | 5-10min ⚠ | "Run a full deep-dive on SH600519" — may time out in MCP client |
| `mainline` | ~30s | "Which sectors are mainline this month?" |
| `brief` | 1-3min | "Generate today's morning brief" |
| `intraday` | ~30-60s | "Lunch-break review of my positions" |
| `dream` | ~30-60s | "Introspect my recent reports for biases" |

### About `report` timeout

The full 13-agent deep-dive can take 5-10 minutes. Most MCP clients (including Claude Desktop) default to ~5-min tool-call timeout. If `report` times out:

1. Use Claude Desktop to call `ask` instead for quick questions
2. Run `report` locally: `financial-analyst report SH600519`
3. Then in Claude Desktop, call `read_past_report` to get the result

## Security model

`financial-analyst-mcp` runs as a local subprocess. It:
- Reads/writes files under your project's `out/`, `memories/_proposed/`, `~/.financial-analyst/`
- Calls Tushare / LLM provider APIs using YOUR keys
- Honors `config/plugins.yaml` (loads your private BYOM models)
- Does NOT expose arbitrary shell command execution to Claude Desktop
- Does NOT have write access to `memories/<agent>/` files directly (only via `accept` if user pre-stages a proposal)

Each tool is a Python function with a strict JSON schema for input — Claude cannot pass arbitrary code.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Claude Desktop doesn't show tools | Check the path: `which financial-analyst-mcp` — must be on PATH where Claude can find it |
| Tool calls fail with "no Tushare token" | Add `TUSHARE_TOKEN` to the `env` block in `claude_desktop_config.json` |
| `report` always times out | Use `ask` for short queries, run `report` locally |
| Unicode errors | Set `PYTHONIOENCODING=utf-8` in the `env` block |
