# Project Governance / 项目治理

## English

`financial-analyst` is a single-maintainer project at launch (v1.0.0, 2026-05-25), open to growing community-driven governance as the project matures.

### Roles

| Role | Current holder | Responsibilities |
|---|---|---|
| **Lead maintainer** | [@jesson-hh](https://github.com/jesson-hh) | Final decision on architecture, release timing, security disclosures, code of conduct enforcement |
| **Maintainers** | (open, recruited from sustained contributors) | Merge PRs in their owned area, triage issues, mentor newcomers |
| **Contributors** | Anyone with a merged PR | Review PRs, propose features, write docs |

### Decision Process

- **Trivial changes** (typos, doc updates, small bug fixes): one maintainer LGTM, merge.
- **New sub-agent / new swarm / new LLM provider**: lead maintainer review, 1-week public RFC in Discussions.
- **Breaking API change**: lead maintainer veto, must be in MAJOR release with deprecation period.
- **Security disclosure**: lead maintainer + reporter only, coordinated disclosure timeline.

### Becoming a Maintainer

After ~5 substantial merged PRs + sustained good standing (no CoC violations, helpful in Discussions / issues), lead maintainer may invite you to become a maintainer. Decline is fine.

### Conflicts of Interest

Maintainers must disclose financial stakes in tools / data sources / LLM providers the project depends on. This project depends on Tushare / Aliyun / DeepSeek / Anthropic / OpenAI APIs; current maintainer has no commercial relationship with any of them as of 2026-05-25.

---

## 中文

`financial-analyst` 在启动时 (v1.0.0, 2026-05-25) 是单维护者项目, 随社区发展逐步开放治理.

### 角色

| 角色 | 当前持有者 | 职责 |
|---|---|---|
| **首席维护者** | [@jesson-hh](https://github.com/jesson-hh) | 架构 / 发布节奏 / 安全披露 / 行为规范执行的最终决策 |
| **维护者** | (开放, 从持续贡献者中招募) | 在负责领域 merge PR, 分流 issue, 指导新人 |
| **贡献者** | 任何有 merged PR 的人 | review PR, 提建议, 写文档 |

### 决策流程

- **小改动** (typo / 文档 / 小 bug): 一个维护者 LGTM, 即合并.
- **新 sub-agent / 新 swarm / 新 LLM provider**: 首席维护者 review, 在 Discussions 公开 RFC 1 周.
- **破坏性 API 变更**: 首席维护者一票否决, 必须放 MAJOR release + 弃用周期.
- **安全披露**: 首席维护者 + 上报者私下沟通, 协调披露时间表.

### 成为维护者

累计约 5 个高质量 merged PR + 持续良好表现 (无 CoC 违规, Discussions / issue 里友善积极), 首席维护者可能邀请你成为维护者. 拒绝也行.

### 利益冲突

维护者须披露所依赖工具 / 数据源 / LLM 厂商的财务关系. 本项目依赖 Tushare / 阿里云 / DeepSeek / Anthropic / OpenAI; 当前维护者 2026-05-25 与这些厂商无商业关系.
