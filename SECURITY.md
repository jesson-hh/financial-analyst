# Security Policy / 安全策略

## Supported Versions / 支持版本

| Version | Supported |
|---------|-----------|
| 1.0.x   | ✅ |
| < 1.0   | ❌ (internal preview, please upgrade) |

详见 [VERSIONING.md](VERSIONING.md) for the LTS policy.

## Reporting a Vulnerability / 漏洞上报

**EN**: If you discover a security vulnerability (LLM API key leak, Tushare token leak, agent privilege escalation, SSRF via untrusted news feed, etc.), please **do not open a public issue**. Instead:

1. Use [GitHub Private Vulnerability Reporting](https://github.com/jesson-hh/financial-analyst/security/advisories/new) (preferred).
2. Or email **xuyi1030@proton.me** with details + reproduction steps.

We will respond within 72 hours, triage within 7 days, and disclose with a coordinated CVE if applicable.

**中文**: 发现安全漏洞 (LLM API key 泄露 / Tushare token 泄露 / agent 越权 / 不可信新闻源 SSRF 等) 请**不要开公开 issue**, 走以下渠道:

1. 使用 [GitHub Private Vulnerability Reporting](https://github.com/jesson-hh/financial-analyst/security/advisories/new) (推荐).
2. 或邮件 **xuyi1030@proton.me** 附详情 + 复现步骤.

我们 72 小时内回应, 7 天内分级, 如适用按 CVE 流程协调披露.

## Key Risk Areas / 重点风险

- **LLM provider keys** (DASHSCOPE_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY): never commit to repo. `.env` 在 `.gitignore`.
- **Tushare token**: 同上.
- **Untrusted news content** read by `news-reader` / `f10-reader` / `global-news-aggregator` — JSON schema 锁死输出, prompt injection 风险已隔离 (Tier-1 only reads, Tier-2/3 consume structured output).
- **MCP server** exposed via `financial-analyst-mcp` — local only, no remote exposure by default.

## Public Disclosure / 公开披露

Acknowledged reporters listed in [SECURITY-HALL-OF-FAME.md](SECURITY-HALL-OF-FAME.md) (created on first report).
