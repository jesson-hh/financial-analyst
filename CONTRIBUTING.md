# Contributing to financial-analyst / 贡献指南

[English](#english) | [中文](#中文)

---

## English

Thanks for your interest in contributing!

### Quick Start

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env  # add your TUSHARE_TOKEN + DASHSCOPE_API_KEY at minimum
pytest tests/         # should report 712 passed
```

### Project Structure

- `src/financial_analyst/agent/` — 25 sub-agents (Tier 1-4 + market + meta)
- `config/swarm/*.yaml` — DAG presets (stock-deep-dive, morning-brief, overseas-radar, etc.)
- `src/financial_analyst/llm/client.py` — multi-provider LLM routing (qwen / deepseek / openai / anthropic)
- `src/financial_analyst/data/` — loaders / collectors / quote_fallback
- `memories/<agent>/*.md` — pluggable per-agent memory (edit markdown, next run picks up)
- `tests/` — 712 tests, all mocked (real-LLM E2E opt-in via `FA_E2E=1`)

### Development Loop

1. **Branch**: `git checkout -b feat/your-feature` from `main`
2. **Code**: follow PEP 8, line length 120, black-compatible
3. **Test**: `pytest tests/your_change_test.py` + full `pytest tests/`
4. **Lint**: `black src/ tests/` + `ruff check src/`
5. **CHANGELOG**: add entry to `[Unreleased]` section in [CHANGELOG.md](CHANGELOG.md)
6. **Docs**: update README/README_zh if user-facing API changes
7. **PR**: use the [PR template](.github/PULL_REQUEST_TEMPLATE.md), reference issue number

### Adding a New Sub-Agent

1. Inherit `SubAgent[<OutputSchema>]` (`agent/base.py`), implement `_execute(inputs) -> dict`
2. Register in `tui.py::_ensure_registered`
3. Add to relevant swarm yaml (`config/swarm/*.yaml`) with `deps` + `input_keys`
4. Create memory dir `memories/<your-agent>/` + at least one `.md`
5. Add `agent_overrides` in `config/llm.yaml` (default qwen3.5-plus)
6. Write smoke test mocking LLM call

### Adding a New Data Source

1. New file under `src/financial_analyst/data/collectors/<your_source>.py`
2. Use `net.py.domestic_session()` for 国内站 / `intl_session()` for 国外站
3. Register with `@rate_limited("source_name", cache_key=...)` to respect upstream rate limits
4. Update `/diag` endpoint in `buddy/server.py` to include health probe

### Commit Style

Use [Conventional Commits](https://www.conventionalcommits.org/):
- `feat: add X`
- `fix: resolve Y in Z`
- `chore(data): update tushare adapter`
- `docs: improve install guide`

This drives automatic semver bump via `python-semantic-release` (planned for v1.1+).

---

## 中文

感谢有兴趣贡献!

### 快速开始

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
python -m venv .venv && .venv\Scripts\activate  # Linux/Mac: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env  # 至少填 TUSHARE_TOKEN + DASHSCOPE_API_KEY
pytest tests/         # 期望 712 passed
```

### 项目结构

- `src/financial_analyst/agent/` — 25 个 sub-agent (Tier 1-4 + market + meta)
- `config/swarm/*.yaml` — DAG 预设 (stock-deep-dive / morning-brief / overseas-radar 等)
- `src/financial_analyst/llm/client.py` — 多 provider LLM 路由 (qwen / deepseek / openai / anthropic)
- `src/financial_analyst/data/` — loader / collector / quote_fallback
- `memories/<agent>/*.md` — 每个 agent 的可插拔记忆 (改 markdown 下次运行立即生效)
- `tests/` — 712 测试, 全 mock (真 LLM E2E 通过 `FA_E2E=1` 启)

### 开发循环

1. **分支**: `git checkout -b feat/你的功能` 从 `main` 拉
2. **代码**: PEP 8, 行长 120, black 兼容
3. **测试**: `pytest tests/你的测试.py` + 全量 `pytest tests/`
4. **格式**: `black src/ tests/` + `ruff check src/`
5. **CHANGELOG**: 加条目到 [CHANGELOG.md](CHANGELOG.md) 的 `[Unreleased]` 段
6. **文档**: 用户可见 API 变更同步改 README / README_zh
7. **PR**: 用 [PR 模板](.github/PULL_REQUEST_TEMPLATE.md), 引用 issue 编号

### 新增 sub-agent

1. 继承 `SubAgent[<OutputSchema>]` (`agent/base.py`), 实现 `_execute(inputs) -> dict`
2. 在 `tui.py::_ensure_registered` 注册
3. 加到相关 swarm yaml (`config/swarm/*.yaml`), 配 `deps` + `input_keys`
4. 创建 memory 目录 `memories/<新agent>/` + 至少一个 `.md`
5. 在 `config/llm.yaml` 加 `agent_overrides` (默认 qwen3.5-plus)
6. 写 smoke test, mock LLM call

### 新增数据源

1. 新文件 `src/financial_analyst/data/collectors/<新源>.py`
2. 用 `net.py.domestic_session()` 给国内站, `intl_session()` 给国外站
3. 用 `@rate_limited("source_name", cache_key=...)` 装饰, 尊重对端限速
4. 更新 `buddy/server.py` 的 `/diag` 端点加健康探活

### Commit 风格

用 [Conventional Commits](https://www.conventionalcommits.org/):
- `feat: 新增 X`
- `fix: 修 Z 里的 Y`
- `chore(data): 更新 tushare 适配`
- `docs: 改进安装指南`

后续 v1.1+ 会接入 `python-semantic-release` 自动 bump 版本.

### 文档语言策略

- README 双语 (README.md 英 / README_zh.md 中)
- CHANGELOG 主英文, 关键 entry 加中文 callout
- 内部模块 docstring 主中文 (本项目核心受众是中文 quant 开发者)
- agent prompt 主中文 (LLM 输入)
