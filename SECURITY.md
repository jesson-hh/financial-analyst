# Security Policy / 安全策略

> 中英文都接受 (English / 中文 both accepted).
> Project: `financial-analyst` ([觀瀾](https://github.com/jesson-hh/financial-analyst))

## Supported Versions / 支持的版本

We currently support security fixes on the latest minor release line.

只对最新的 minor 版本提供安全修复.

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | :white_check_mark: |
| < 1.0   | :x:                |

Upgrade via / 升级方式: `pip install -U financial-analyst`.

---

## Reporting a Vulnerability / 报告漏洞

**Please do NOT open a public GitHub issue for security bugs.**

**请勿** 在 public issue 里报告漏洞 (容易被恶意利用).

### Preferred channel / 首选渠道

Email **xuyi1030@proton.me** (Proton Mail, E2E encrypted) with:

发邮件到 **xuyi1030@proton.me** (Proton Mail, 端到端加密), 内容包含:

- A clear description of the issue / 清晰的问题描述
- Steps to reproduce (or PoC) / 复现步骤或 PoC
- Affected version(s) / 受影响的版本
- Impact assessment (what an attacker could do) / 影响评估
- Your suggested fix if you have one / 修复建议 (如果有)

Subject line prefix: `[SECURITY] financial-analyst — <short summary>` so it doesn't get buried.

邮件标题前缀: `[SECURITY] financial-analyst — <简述>`.

### Alternative / 备用渠道

If email is inconvenient, use [GitHub's private security advisory](https://github.com/jesson-hh/financial-analyst/security/advisories/new) on this repo. Only repo maintainers see the report.

也可以用 GitHub 私密漏洞报告 (上面链接), 只有维护者能看到.

---

## What to expect / 处理流程

| Step / 步骤 | Timeline / 时间 |
|------------|---------------|
| Acknowledgement / 确认收到 | within 72 hours / 72 小时内 |
| Initial assessment / 初步评估 | within 7 days / 7 天内 |
| Fix + release / 修复并发版 | depends on severity (critical: ASAP; high: ≤14 days; medium: ≤30 days) / 视严重程度而定 |
| Public disclosure / 公开披露 | after fix is released and users have a reasonable upgrade window (typically 7 days post-release) / 修复发布且用户有合理升级窗口后 (通常发版后 7 天) |

We follow **coordinated disclosure**: please give us a chance to fix before going public.

我们采用 **协调披露** 流程: 请给我们修复的时间窗口再公开.

---

## Scope / 受理范围

### In scope / 受理

- Code execution / injection vulnerabilities in `src/financial_analyst/` / `src/` 下的代码执行/注入漏洞
- Authentication / API key handling bugs / 认证或 API key 处理 bug
- Path traversal, unsafe deserialization / 路径穿越、反序列化
- Prompt injection vectors that bypass tier-1 JSON schema lock-down on **untrusted** agent inputs (news, F10) / 绕过 Tier-1 JSON schema 锁的 prompt injection
- Vulnerabilities in our PyPI package or Docker image / PyPI 包或 Docker 镜像漏洞
- Sensitive info leakage in logs / reports / 日志或研报里泄露敏感信息

### Out of scope / 不受理

- **Investment advice quality / 投资建议质量** — this is a research tool, not a recommendation. Bad-stock-pick ≠ vulnerability. 选股错 ≠ 漏洞.
- **LLM hallucination** in narrative output (we mitigate via JSON-schema lock, RAG, and audit agents — but cannot guarantee zero hallucination) / LLM 在叙述部分的幻觉 (我们靠 JSON schema、RAG、审计 agent 缓解, 但无法保证零幻觉)
- **Third-party data accuracy** (Tushare / pytdx / 雪球 / akshare upstream issues) — report those upstream / 第三方数据源的准确性问题, 请向上游报告
- **Rate-limit bypasses against third-party providers** — we don't want fixes that abuse upstream services / 绕过第三方限流, 我们不接受这类「修复」
- Social engineering, physical attacks, attacks requiring local OS access already / 社工、物理攻击、需要本机访问权的攻击
- Vulnerabilities in `qlib`, `lightgbm`, `pytdx`, or other upstream dependencies — report those upstream / 上游依赖的漏洞, 请向上游报告

---

## Hall of Fame / 致谢墙

Security researchers who responsibly disclose vulnerabilities are listed here (with permission) once a fix is released.

负责任披露漏洞的研究者, 在修复发版后会列在这里 (经本人同意).

*(currently empty — be the first!)*

---

## Thank you / 致谢

Security research keeps the community safe. We appreciate your time and effort.

安全研究让社区更安全, 感谢你的时间与精力.
