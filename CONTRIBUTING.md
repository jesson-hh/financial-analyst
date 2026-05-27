# Contributing to financial-analyst / 贡献指南

> 中英文都接受 (English / 中文 both accepted).
> 觀瀾 · `financial-analyst` — A-share research workstation, 24 AI agents.

Thanks for your interest in contributing! This doc covers how to set up a dev environment, the code conventions we enforce in CI, and how to submit a good PR.

感谢愿意参与! 本文档说明开发环境配置、代码规范、PR 流程.

---

## Quick links / 快速跳转

- [Dev setup / 开发环境](#-dev-setup--开发环境)
- [Running tests / 跑测试](#-running-tests--跑测试)
- [Code style / 代码风格](#-code-style--代码风格)
- [Commit messages / 提交信息](#-commit-messages--提交信息)
- [Submitting a PR / 提交 PR](#-submitting-a-pr--提交-pr)
- [Adding a new sub-agent / 新增 sub-agent](#-adding-a-new-sub-agent--新增-sub-agent)
- [Adding a new data source / 新增数据源](#-adding-a-new-data-source--新增数据源)
- [Security / 安全](#-security--安全)

---

## 🛠 Dev setup / 开发环境

**Requirements / 要求**:
- Python ≥ 3.11
- Git
- (Optional) Tushare token + 阿里云百炼 API key for live data + LLM (零 token 也能跑大部分功能, 走 pytdx 直连)

```bash
# Clone
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst

# Editable install (recommended for dev)
pip install -e ".[dev]"

# Or with all extras
pip install -e ".[dev,serve,mcp]"

# Copy env template
cp .env.example .env
# Edit .env to add ALIYUN_BAILIAN_API_KEY / TUSHARE_TOKEN if needed
```

> **Note / 注意**: This is an **editable install** — code changes in `src/` take effect immediately, no need to reinstall.
> 改 `src/` 立即生效, 不用重新装.

Verify the install / 验证安装:

```bash
fa --version            # should print 1.0.x
pytest tests/ -x        # should pass (712+ tests)
```

---

## 🧪 Running tests / 跑测试

```bash
# Full suite (~5 min)
pytest tests/

# Single file
pytest tests/test_agents.py -v

# Skip slow tests
pytest -m "not slow"

# Coverage report
pytest --cov=src/financial_analyst --cov-report=html
```

**Test data lives in `test_data/`** — small fixtures only. **Do not commit real API responses, real stock data dumps, or anything containing tokens.** 不要提交真实 API 响应、真实数据导出、含 token 的文件.

---

## 🎨 Code style / 代码风格

Enforced in CI:

```bash
black src/ tests/           # auto-format, line length 120
ruff check src/             # lint
ruff check --fix src/       # auto-fix where possible
```

**Conventions**:
- Line length: 120
- Type hints required for public functions / 公开函数必须有类型注解
- Docstrings: one-line where the name isn't self-explanatory. Don't write `Args:/Returns:` blocks unless the function is complex.
- No `print()` in library code — use `logger` (`logging.getLogger(__name__)`)
- No global state in `src/` — use config objects passed explicitly

**Don't / 不要**:
- 不要为了规避 lint 加 `# noqa` — 先看根因
- 不要 import 全模块再用一个函数 (`from foo import bar` 而不是 `import foo`)
- 不要把研究脚本扔进 `src/` — 放 `scripts/` 或 `examples/`

---

## 📝 Commit messages / 提交信息

We use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary>

<optional body>

<optional footer>
```

**Types**:
- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — docs only
- `refactor:` — code change that's neither feat nor fix
- `test:` — adding/fixing tests
- `chore:` — build/tooling/deps
- `perf:` — performance improvement

**Examples**:
```
feat(agents): add tier-4 introspector for self-audit
fix(tushare): bypass system proxy on Windows (no_proxy=*)
docs(zh): update README_zh quick-start for v1.0.7
chore(deps): bump pytdx 1.72 -> 1.73
```

**Don't / 不要**:
- 一个 commit 不要混 feat + refactor + 改无关文件的格式
- 不要写 `update`, `fix bug`, `wip`  之类无信息量的 message

---

## 🚀 Submitting a PR / 提交 PR

1. **Fork** the repo, create a branch off `main`:
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Code, test, commit**. Make sure:
   - [ ] `pytest tests/` passes (712+ expected)
   - [ ] `black src/ tests/` clean
   - [ ] `ruff check src/` clean
   - [ ] `CHANGELOG.md` updated under `[Unreleased]`
   - [ ] `README.md` + `README_zh.md` updated if user-facing API changed
   - [ ] No `*_API_KEY` / `TUSHARE_TOKEN` in code or test data

3. **Push** and open a PR. The PR template will prompt for the rest. 模板会引导你填剩下的项.

4. **Address review feedback** — reviewers may ask for changes or clarifications. Don't force-push to existing PRs unless asked; push new commits and we'll squash on merge.

5. **Merge** — maintainers merge with "Squash and merge", commit message follows Conventional Commits.

---

## 🤖 Adding a new sub-agent / 新增 sub-agent

If you're adding a new agent (we have 24 already across 4 trust tiers), you need:

1. **Agent definition** in `src/financial_analyst/agents/<tier>/<name>.py`
2. **Memory dir** in `memories/agents/<name>/` (markdown only)
3. **LLM override** in `config/llm.yaml` under `agent_override.<name>` if it needs a specific model
4. **TUI registration** in `src/financial_analyst/tui.py` (so users can see it in `fa agents list`)
5. **Tests** in `tests/agents/test_<name>.py`

Trust tiers:
- **Tier 1**: untrusted input (news, F10) — must use JSON-schema-locked outputs
- **Tier 2**: structured data parsing
- **Tier 3**: synthesis / analysis (bull, bear, risk)
- **Tier 4**: introspection / audit (only `report-writer` and `introspector`)

Only `report-writer` is allowed to **write files**. Other agents return text/JSON that gets piped to `report-writer`. 这条规则不可破坏.

---

## 📊 Adding a new data source / 新增数据源

If adding a new data provider (e.g. another quote/F10 source):

1. Put the client in `src/financial_analyst/data/<source>.py`
2. **Use the shared session helpers**:
   - Domestic (CN) data: `net.py:domestic_session()` (handles `NO_PROXY=*`, retries)
   - International data: `net.py:intl_session()` (Clash proxy or direct, profile-aware)
3. Decorate API calls with `@rate_limited(...)` to respect provider limits
4. **No raw `requests.get()`** in data modules — use the session helpers
5. Update `config/loaders.yaml` with the new source key
6. Add a contract entry in `docs/data_contract.md` (units, field names, frequency)
7. Add tests with mocked responses in `test_data/`

---

## 🔒 Security / 安全

**Don't open public issues for security bugs.** See [SECURITY.md](SECURITY.md) for private disclosure.

**漏洞不要发 public issue**, 见 [SECURITY.md](SECURITY.md).

---

## ❓ Questions / 问题

- General questions / 一般问题: [GitHub Discussions](https://github.com/jesson-hh/financial-analyst/discussions)
- Bug report / Bug 报告: open an issue with the bug template
- Feature request / 功能建议: open an issue with the feature template
- Sub-agent proposal / 新 agent 提议: open an issue with the `agent_request` template

By contributing, you agree your contributions are licensed under Apache 2.0 (same as the project).

提交即代表同意以 Apache 2.0 协议授权.
