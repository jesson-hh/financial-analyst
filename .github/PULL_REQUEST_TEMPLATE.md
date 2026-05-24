# Pull Request

## Summary / 概要

<!-- 一句话描述这个 PR 改了什么 -->

## Related issue / 相关 issue

Closes #

## Type / 类型

- [ ] 🐛 Bug fix
- [ ] ✨ New feature (non-breaking)
- [ ] 💥 Breaking change
- [ ] 📝 Documentation only
- [ ] 🔧 Refactor / cleanup
- [ ] 🧪 Tests only
- [ ] 🤖 New sub-agent
- [ ] 📊 New data source / LLM provider

## Checklist

- [ ] `pytest tests/` passes (currently 712 expected)
- [ ] `black src/ tests/` clean
- [ ] `ruff check src/` clean
- [ ] CHANGELOG.md updated (`[Unreleased]` section)
- [ ] README.md / README_zh.md updated if user-facing API changes
- [ ] New sub-agent: registered in `tui.py`, has memory dir, has `agent_override` in llm.yaml
- [ ] New data source: uses `net.py.domestic_session/intl_session` + `@rate_limited`
- [ ] No `*_API_KEY` / `TUSHARE_TOKEN` committed in code or test data
- [ ] Conventional Commit messages (feat: / fix: / chore: / docs:)

## Testing / 测试

<!-- How was this tested? Manual + automated -->

## Breaking changes / 破坏性变更

<!-- If breaking, list migration steps and which public API contracts change -->

## Reviewer notes / 审阅备注

<!-- Anything specific reviewers should focus on? -->
