# MCP Server — Claude Desktop / Claude Code Integration

`financial-analyst-mcp` exposes **13 tools** to Claude Desktop / Claude Code / OpenClaw via the [Model Context Protocol](https://modelcontextprotocol.io/).

After this is set up, you can say in Claude Desktop / Claude Code:

> "Look at SH600519 — is it a buy?"

and Claude will autonomously call `ask`, `quick_quote`, `read_past_report`, etc. through your local `financial-analyst-mcp` subprocess.

## Setup

### 1. Install (or upgrade) financial-analyst

```bash
pip install -U financial-analyst
```

This installs both `financial-analyst` and `financial-analyst-mcp` console scripts.

### 2a. Configure Claude Desktop

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

### 2b. Configure Claude Code (CLI / VS Code)

Use the `claude mcp add` command:

```bash
claude mcp add financial-analyst -- financial-analyst-mcp
```

Or manually edit `~/.claude.json`:

```json
{
  "mcpServers": {
    "financial-analyst": {
      "type": "stdio",
      "command": "financial-analyst-mcp",
      "args": [],
      "env": {
        "DASHSCOPE_API_KEY": "your-key"
      }
    }
  }
}
```

Then in any Claude Code session, the tools auto-appear (verify with `/mcp`).

### 2c. Verify install

```bash
# 进任意 venv 后跑
python -m financial_analyst.mcp_server --help    # 不会有输出 (stdio mode), 应安静退出
financial-analyst-mcp                            # 同上
```

如果 `financial-analyst-mcp` 命令不存在: `pip install --force-reinstall financial-analyst` 重生成 console scripts.

### 3. Restart Claude Desktop / reload Claude Code

Tools should appear in the tool drawer / `/mcp` list.

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
| `dream` | ~30-60s | "Introspect my recent reports for biases" (T+5d outcome 反推) |
| `dream_aggregate` | <5s | "Cluster Tier-4 introspector pending proposals (重复 ≥3 → 升级 _proposed/)" |

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
| Claude Desktop doesn't show tools | Check the path: `which financial-analyst-mcp` — must be on PATH where Claude can find it. Use absolute path in config 若 PATH 没装 |
| `financial-analyst-mcp` 命令不存在 | `pip install --force-reinstall financial-analyst` 重新生成 console scripts. 验证 `ls ~/.venv/Scripts/` (Windows) 或 `ls .venv/bin/` (Unix) 应见 |
| Tool calls fail with "no Tushare token" | Add `TUSHARE_TOKEN` to the `env` block. **新版本不需要** — pytdx 直连默认 |
| `report` always times out | Use `ask` for short queries, run `report` locally |
| Unicode errors | Set `PYTHONIOENCODING=utf-8` in the `env` block |
| Claude Code `/mcp` 显示 disconnected | 检查 `claude_desktop_config.json` JSON 合法 + restart Claude Code 完整退出 (Ctrl+C twice + relaunch) |
| 想看 MCP 流量 debug | Claude Desktop: 启用 `MCP_DEBUG=1` 环境变量, log 到 `~/Library/Logs/Claude/mcp*.log` (mac) |

## 与 buddy SSE bridge 的差异

financial-analyst 同时支持两套通信协议:

| 维度 | MCP (financial-analyst-mcp) | SSE Bridge (financial-analyst serve) |
|------|---------|---------|
| 协议 | stdio JSON-RPC (MCP spec) | HTTP + Server-Sent Events |
| 客户端 | Claude Desktop / Claude Code / OpenClaw | GuanLan UI / 自定义 web client / curl |
| 启动 | Claude Desktop spawn subprocess | `financial-analyst serve --port 9999` |
| Tools | 13 (本文档) | 30 (含 update_data, alert_*, conversations 等) |
| Use case | LLM 自主调工具 (Claude 大模型决策) | UI 后端 (前端 React 调) |
| 长任务 (report 5+ min) | ⚠ 大多 client 默认 5 min timeout | ✓ SSE 流式 + /report-progress 轮询 |
