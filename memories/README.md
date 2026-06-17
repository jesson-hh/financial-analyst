# memories/ — Agent 可插拔记忆体系

[English](#english) | [中文](#中文)

---

## 中文

每个 sub-agent 在 `memories/<agent-name>/` 下有一组 markdown 文件, runtime 被
追加到该 agent 的 `SYSTEM_PROMPT`. **改 markdown, 下次跑 agent 立即生效, 不
需要重启服务**.

### 设计原则

1. **可插拔**: 经验存 markdown 不存代码, 用户可以 fork / 编辑 / 加自己的规则.
2. **per-agent 隔离**: agent 之间默认不共享记忆 (隔离视角), 必要共享走 `_shared/`.
3. **FTS5 检索**: 标 `memory_mode: retrieval` 的 agent 用 FTS5 按 prompt 主题
   检索, 节约 token (~60%). 其它 agent 全量加载.
4. **白名单关键文件**: `memories/<agent>/always_include.txt` 列必读文件,
   无条件加载 (绕过 retrieval).
5. **不进 source 的 staging**: `_pending_introspections/`, `_proposed/`,
   `_buddy/conversation_lessons.md` (buddy /lesson 沉淀) 是 runtime 生成的, 已 `.gitignore`.

### 目录结构

```
memories/
├── README.md                            ← 本文
├── <agent-name>/                        ← 24 个, 每个对应一个 sub-agent
│   ├── *.md                             ← 经验规则 / 阈值 / 历史教训
│   └── always_include.txt (可选)         ← 必读文件白名单
├── _shared/                             ← 跨 agent 共享 (etf_context 等; 九视角 playbook 已归 bull-advocate/)
│   └── etf_context.md
├── _buddy/                   (ignored)  ← buddy /lesson 对话经验 (个人, 不入公开 fork)
├── _pending_introspections/  (ignored)  ← Tier-4 introspector 输出 review queue
└── _proposed/                (ignored)  ← dream-loop aggregator 待审提案
```

### 24 个 agent memory 目录用途

| Agent | Tier | 用途 | 建议改动场景 |
|---|---|---|---|
| **quote-fetcher** | 1 | 行情拉取, 无需 LLM, memory 占位 | 一般不动 |
| **factor-computer** | 1 | 因子计算, 无需 LLM, memory 占位 | 一般不动 |
| **model-predictor** | 1 | LGB/FM 模型预测, 无需 LLM, memory 占位 | 一般不动 |
| **news-reader** | 1 | 不可信新闻读取 (JSON-schema 锁死), 占位 | prompt-injection 防御规则 |
| **f10-reader** | 1 | TDX F10 读取 (JSON-schema 锁死), 占位 | F10 事件分类阈值 |
| **overseas-market-scanner** | 1 (v1.9.7) | 国际指数拉取 + risk_tone 判读 | 阈值调整 / 传导经验 |
| **sector-rotation-analyzer** | 1 (v1.9.7) | 板块轮动聚合, 无 LLM | 轮动规则 / 行业分类源 |
| **fundamental-analyst** | 2 | 基本面分析 | 市值分层规则 / 估值阈值 / 红旗判定 |
| **technical-analyst** | 2 | 技术面分析 | 因子组合 / RSI/MACD 阈值 / 海外联动 |
| **whale-analyst** | 2 | 主力 / 情绪分析 | 涨停规则 / 量价异动经验 |
| **quant-analyst** | 2 | 量化模型分析 | 因子打分 / 板块轮动加权 |
| **catalyst-extractor** | (market, v1.9.7) | A 股催化提取 | 催化类型优先级 / 输出约束 |
| **global-news-aggregator** | (market, v1.9.7) | 海外格局判读 | 6 大 channel 速查 / 经验规则 |
| **bull-advocate** | 3 | 多头辩护 | 看多视角 / 经验案例 |
| **bear-advocate** | 3 | 空头辩护 | 看空视角 / 历史 pitfalls (memory_mode: retrieval) |
| **risk-officer** | 3 | 风险官 | 硬规则 / veto 条件 (memory_mode: retrieval) |
| **report-writer** | 3 | 报告撰写 | 输出格式 / rating system |
| **introspector** | 4 | 复盘自审 | quality_flags 规则 |
| **macro-impact-analyzer** | (overseas-radar, v1.9.7) | 宏观 → A 股传导 | 4 种场景 playbook |
| **mainline-classifier** | (mainline-radar) | 主线状态分类, 无 LLM | 状态阈值 |
| **mainline-writer** | (mainline-radar) | 主线月报撰写 | 月报模板 |
| **market-scanner** | (morning-brief) | 异动扫描, 无 LLM | 异动阈值 |
| **morning-brief-writer** | (morning-brief) | 晨会简报 | 输出 section 模板 |
| **intraday-reviewer** | (intraday-review) | 午休复盘 | 复盘三档判读 |

### 怎么改 memory

```bash
# 1. 直接编辑
vim memories/risk-officer/hard_rules.md
# 加一条 "Veto 6: 上市不足 3 个月, 自动否定" 之类

# 2. 下次跑 fa report 立即生效
fa report SH600519

# 3. 通过 buddy slash command 写
financial-analyst
> /lesson 大盘股 PE>50 + 涨幅 60d>30% 通常是博弈票, 模型信号失效

# 4. 通过 dream loop 自迭代 (人工 review)
fa dream --since 30        # 跑 introspector
fa memory                  # 列 _proposed/ 待审
# 满意 → 接受 / 不满意 → 拒绝
```

### 不要做的事

- ❌ **不要在 memory 里写代码**: memory 是文本规则, 不是 Python.
- ❌ **不要 hardcode 真实股票数据**: 用 "示例: SH600519" 不要 "SH600519 PE 2026-05-15 = 19.5". 实测数据存 strategy/ 不在 memory.
- ❌ **不要把 memory 当 cache 用**: cache 走 `~/.financial-analyst/cache/`, memory 是规则.
- ❌ **不要把私人 personal data push 上去**: `_buddy/conversation_lessons.md` 已 ignore, 个人 /lesson 不出现在公开 fork.

---

## English

Each sub-agent has a set of markdown files in `memories/<agent-name>/`, which are appended to that agent's `SYSTEM_PROMPT` at runtime. **Edit a markdown, the next agent run picks it up — no service restart needed**.

### Design Principles

1. **Pluggable**: knowledge stored as markdown not code; users can fork / edit / add custom rules.
2. **Per-agent isolation**: agents don't share memory by default (preserves their distinct perspectives); cross-agent sharing goes through `_shared/`.
3. **FTS5 retrieval**: agents marked `memory_mode: retrieval` use FTS5 to fetch memory chunks per-prompt theme, saving ~60% tokens. Others load all.
4. **Always-include whitelist**: `memories/<agent>/always_include.txt` lists files loaded unconditionally (bypasses retrieval).
5. **Runtime staging not committed**: `_pending_introspections/`, `_proposed/`, `_buddy/conversation_lessons.md` (buddy /lesson) are runtime-generated, `.gitignore`'d.

See the table above for the role of each of 24 agent memory directories.

### How to edit

```bash
# Edit markdown directly
vim memories/risk-officer/hard_rules.md

# Next agent run picks it up
fa report SH600519

# Via slash command in TUI
financial-analyst
> /lesson Mega-cap PE>50 + 60d return>30% usually means liquidity-game stock, model signals fail

# Via dream loop (human review)
fa dream --since 30
fa memory  # list _proposed/ for review
```

### Don'ts

- ❌ Don't write code in memory — markdown rules only.
- ❌ Don't hardcode live stock data — use "example: SH600519" not "SH600519 PE on 2026-05-15 = 19.5". Real data lives in `strategy/`.
- ❌ Don't use memory as cache — `~/.financial-analyst/cache/` is for that.
- ❌ Don't push personal data — `_buddy/conversation_lessons.md` is gitignored; personal `/lesson` stays local.
