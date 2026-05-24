# Versioning Policy / 版本策略

[English](#english) | [中文](#中文)

---

## English

`financial-analyst` follows [Semantic Versioning 2.0.0](https://semver.org/) starting from **v1.0.0** (public release, 2026-05-25).

### Semver in this project

- **MAJOR** (`1.x.x` → `2.0.0`): backward-incompatible API change. Plugin authors / BYOM users must adapt.
- **MINOR** (`1.0.x` → `1.1.0`): backward-compatible new feature (new sub-agent / new swarm / new LLM provider).
- **PATCH** (`1.0.0` → `1.0.1`): backward-compatible bug fix.

### Support Policy (N-2 LTS)

We support the **current minor** + **2 prior minors**. Older versions get no patches.

| Version | Status | Support window |
|---|---|---|
| 1.0.x | ✅ Active | until 2027-05-25 (12 months minimum) |
| 0.x.x | ❌ Pre-1.0 internal preview | unsupported, please upgrade |

### Breaking Change Policy

Breaking changes are deferred to MAJOR releases (`2.0`, `3.0`). Within a MAJOR:
- **Deprecation warning** in the previous MINOR (at least 1 release cycle of warning before removal).
- **Migration guide** in CHANGELOG + dedicated `docs/migration_v<N>.md`.
- **Compat shims** for at least 1 MINOR cycle.

### What counts as "public API"?

Stable contract (SemVer applies):
- `financial_analyst.agent.SubAgent` base class + concrete agent classes' `NAME` / `OUTPUT_SCHEMA`
- `financial_analyst.llm.client.LLMClient` public methods (`for_agent`, `with_overrides`, `chat`)
- `financial_analyst.data.loaders.*` loader interfaces
- CLI commands (`financial-analyst report` / `ask` / `dream` / etc.)
- MCP tool schemas
- swarm YAML schema in `config/swarm/`
- `config/llm.yaml` / `config/loaders.yaml` schema

NOT public API (may change between minor versions):
- Internal helpers under `_*` prefix
- `memories/<agent>/*.md` content (these are runtime data, not API)
- Default LLM model assignments in `agent_overrides`
- Test fixtures

### Versioning Lineage

Internal preview series `v1.9.x` is collapsed into public `v1.0.0` GA. See [CHANGELOG.md](CHANGELOG.md#pre-10-history-internal-preview) for full pre-1.0 entries. This is **not a semver regression** — it's a new public versioning baseline. PyPI project was wiped and re-registered to establish a clean 1.0 baseline.

---

## 中文

`financial-analyst` 从 **v1.0.0** (公开发布, 2026-05-25) 起遵循 [Semantic Versioning 2.0.0](https://semver.org/).

### 本项目的 semver 实践

- **MAJOR** (`1.x.x` → `2.0.0`): 不向后兼容的 API 变更. plugin 作者 / BYOM 用户需要适配.
- **MINOR** (`1.0.x` → `1.1.0`): 向后兼容的新功能 (新 sub-agent / 新 swarm / 新 LLM provider).
- **PATCH** (`1.0.0` → `1.0.1`): 向后兼容的 bug 修.

### 支持政策 (N-2 LTS)

支持**当前 minor** + **前 2 个 minor**. 更老版本不再发 patch.

| 版本 | 状态 | 支持窗口 |
|---|---|---|
| 1.0.x | ✅ Active | 至少到 2027-05-25 (12 个月) |
| 0.x.x | ❌ Pre-1.0 内部预览 | 不再支持, 请升级 |

### 破坏性变更政策

破坏性变更推到 MAJOR 版本 (2.0, 3.0). 同 MAJOR 内:
- 前一个 MINOR 加 **deprecation warning** (至少 1 个 release cycle 警告期).
- CHANGELOG 写 **migration guide** + 单独 `docs/migration_v<N>.md`.
- **Compat shim** 至少保留 1 个 MINOR 周期.

### 什么算"公开 API"?

稳定契约 (适用 SemVer):
- `financial_analyst.agent.SubAgent` 基类 + 具体 agent 的 `NAME` / `OUTPUT_SCHEMA`
- `financial_analyst.llm.client.LLMClient` 公开方法 (`for_agent`, `with_overrides`, `chat`)
- `financial_analyst.data.loaders.*` loader 接口
- CLI 命令 (`financial-analyst report` / `ask` / `dream` 等)
- MCP tool schema
- `config/swarm/` 的 swarm YAML schema
- `config/llm.yaml` / `config/loaders.yaml` schema

**不是公开 API** (可能在 minor 间变):
- `_*` 前缀的内部 helper
- `memories/<agent>/*.md` 内容 (runtime 数据, 不是 API)
- `agent_overrides` 里默认 LLM 模型分配
- 测试 fixture

### 版本号 lineage

内部预览 `v1.9.x` 收口到公开 `v1.0.0` GA. 完整 pre-1.0 entries 见 [CHANGELOG.md](CHANGELOG.md#pre-10-history-internal-preview). **不是 semver 倒退** — 这是新的公开版本基线. PyPI project 被清空重新注册以建立干净的 1.0 起点.
