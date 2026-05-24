<p align="center">
  <h1 align="center">觀瀾 · Financial Analyst</h1>
</p>

<p align="center">
  <strong>一行命令. 25 个 AI Agent. A 股深度研究.</strong>
</p>

<p align="center">
  <em>给一个 6 位股票代码, 让 14 个 sub-agent 协作出一份研报 — 基本面 · 技术面 · 主力情绪 · 量化模型 · 多空风控辩论 — 约 10 分钟.</em>
</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <strong>中文</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/financial-analyst/"><img src="https://img.shields.io/pypi/v/financial-analyst.svg?style=flat&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
  <img src="https://img.shields.io/pypi/pyversions/financial-analyst.svg?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/release-v1.0.0-success?style=flat" alt="Release">
  <img src="https://img.shields.io/badge/tests-712_passed-brightgreen?style=flat" alt="Tests">
  <img src="https://img.shields.io/badge/license-Apache_2.0-yellow?style=flat" alt="License">
  <br>
  <img src="https://img.shields.io/badge/agents-25-7C3AED?style=flat" alt="Agents">
  <img src="https://img.shields.io/badge/swarm_预设-5-2563EB?style=flat" alt="Swarm">
  <img src="https://img.shields.io/badge/buddy_工具-31-0F766E?style=flat" alt="Tools">
  <img src="https://img.shields.io/badge/alpha_因子-440-FF6B6B?style=flat" alt="Alphas">
  <a href="https://huggingface.co/yifishbossman"><img src="https://img.shields.io/badge/数据-HF_Hub-FFD21E?style=flat&logo=huggingface&logoColor=black" alt="HF Datasets"></a>
</p>

<p align="center">
  <a href="#-是什么">是什么</a> &nbsp;·&nbsp;
  <a href="#-能干什么">能干什么</a> &nbsp;·&nbsp;
  <a href="#-快速开始">快速开始</a> &nbsp;·&nbsp;
  <a href="#-25-个-agent">Agent 阵容</a> &nbsp;·&nbsp;
  <a href="#-可插拔记忆">记忆系统</a> &nbsp;·&nbsp;
  <a href="#-数据集">数据集</a> &nbsp;·&nbsp;
  <a href="#-llm-provider">LLM</a> &nbsp;·&nbsp;
  <a href="CONTRIBUTING.md">贡献</a>
</p>

```bash
pip install financial-analyst==1.0.0    # 1 分钟
financial-analyst                        # 零配置一键: 引导 + 后端 + Web UI + 浏览器自动开
```

第一次跑会: 检测配置 → 跑交互向导 (LLM key + 选 HF dataset 档) → 启 buddy backend (`:9999`) → 启 Web UI (`:5173`) → 自动开浏览器. Ctrl+C 停所有.

高级用户可以分别用各子命令:

```bash
fa init                # 只跑引导 (LLM key + 数据包)
fa report SH600519     # 一次性研报 (~10 分钟, 无 UI)
fa launch              # 显式一键启动 (跟无 subcommand 行为相同)
fa --tui               # 终端 TUI 而非 Web UI
```

---

## 💡 是什么

**A 股研究工作站, 思维像买方分析师.**

给一个股票代码, 14 个 sub-agent 分 4 个信任层并发跑:

```
Tier 1 (数据并行)         Tier 2 (分析师并行)        Tier 3 (决策串行)          Tier 4 (复盘)
─────────────────       ──────────────────────     ────────────────────       ────────────
quote · factors        基本面分析师                  多头 ─┐
model · news           技术面分析师                  空头 ─┤───→ writer        introspector
F10 · 海外宏观         主力情绪分析师                 风控 ─┘
板块轮动                量化模型分析师                (单一写入者)
```

输出: 一份 markdown 研报 — **打分 · 归因 · 可证伪**. 只有 `report-writer` 能落盘. 不可信新闻 / F10 在 Tier-1 用 JSON-schema 锁死 (杜绝 prompt 注入). 记忆是 markdown — 改 `.md`, 下次研报立即生效. FTS5 检索省 prompt 60%.

---

## ✨ 能干什么

<table>
<tr>
<td width="50%" valign="top">

### 🎯 14-agent 个股深度研报
给 `SH600519` 一个代码, 10 分钟出完整研报 — 基本面 / 技术面 / 主力 / 量化 / 多空风控辩论 / 复盘自审. **只有 `report-writer` 能写文件**.

```bash
fa report SH600519
```

</td>
<td width="50%" valign="top">

### 🌅 晨会简报 (5-agent v2)
盘前扫: 隔夜美股 + 港股 + VIX + A 股异动 + 催化提取 + 板块轮动 + LLM 综合一段中文 brief.

```bash
fa brief
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🌍 海外雷达 (v1.9.7 新)
国际传导分析: SPX/NDX/HSI/VIX/USDCNY → A 股 follow-through 判读 + 明日可执行信号.

```bash
fa overseas-radar
```

</td>
<td width="50%" valign="top">

### 📈 月级主线雷达
5 状态产业链分类 (mainline / initiation / revival / decay / cold). 抓 `init → mainline` 金信号 (+5.54pp fwd_60d, 胜率 87%).

```bash
fa mainline
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧠 可插拔记忆
24 个 per-agent 记忆目录, 全是 markdown. 改 `risk-officer/hard_rules.md`, 下次研报立即遵守. 不改代码. `_shared/playbook_V1_V10.md` 跨 agent 共享.

</td>
<td width="50%" valign="top">

### 💤 Dream 闭环 (自迭代)
每份研报后 `introspector` 自审, aggregator 聚类提案到 `_proposed/` 等人工 review. **不自动合并** (量化系统错误经验会复利亏损).

```bash
fa dream --since 30
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔌 4 provider LLM 路由
`qwen` (国内直连) · `deepseek-chat/reasoner` (Clash + MITM 兼容) · `openai` · `anthropic`. 按 provider 配网络出口, 不被 fake-ip 接管.

```bash
financial-analyst  # /model deepseek-reasoner
```

</td>
<td width="50%" valign="top">

### 🧬 BYOM 扩展
把私有模型 `.py` 写到 `config/plugins.yaml`, 自动进量化共识. **私有 checkpoint 永远不进开源仓库**.

参考 [examples/](examples/) — FM cluster / CSV loader / TDX F10 等.

</td>
</tr>
</table>

---

## ⚡ 快速开始

### A. PyPI 安装 (推荐, 1 分钟)

```bash
pip install financial-analyst==1.0.0
cp .env.example .env       # 加 DASHSCOPE_API_KEY (默认 qwen)
fa init                    # 交互向导 — 拉 HF 数据
fa report SH600519         # 首份深度研报
```

### B. Docker (零本地配置, 2 分钟)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
cp .env.example .env
docker compose up          # → 交互 TUI
```

### C. 源码 (开发)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
pip install -e ".[dev]"
pytest tests/              # 712 测试, ~8 分钟
```

---

## 🤖 25 个 Agent

| Tier | Agents | 角色 |
|---|---|---|
| **Tier 1** (数据) | quote-fetcher · factor-computer · model-predictor · **news-reader** · **f10-reader** · overseas-market-scanner · sector-rotation-analyzer | 并行拉数据 + 算因子 + 读不可信源 (JSON-schema 锁) |
| **Tier 2** (分析师) | fundamental · technical · whale · quant | 各视角结构化分析 |
| **Tier 3** (决策) | bull-advocate · bear-advocate · risk-officer · **report-writer** | 辩论后综合 (单一写入者) |
| **Tier 4** (复盘) | introspector | 自审 + 经验提案 |
| **Market** | market-scanner · morning-brief-writer · catalyst-extractor (v1.9.7) · global-news-aggregator (v1.9.7) · macro-impact-analyzer (v1.9.7) · mainline-classifier · mainline-writer · intraday-reviewer | 跨股 + 宏观 pipeline |
| **Meta** | ask | 自由问答, 走 31 个 buddy tool 链 |

完整 DAG: [docs/architecture/14_agents.md](docs/architecture/14_agents.md)

---

## 🧠 可插拔记忆

```
memories/
├── README.md                        # ← 目录索引, 必读
├── risk-officer/
│   ├── hard_rules.md                # ← 改这个, 下次研报遵守
│   └── pitfalls.md                  # FTS5 检索 (大文件)
├── technical-analyst/
│   └── factor_insights.md
└── _shared/
    └── playbook_V1_V10.md           # 跨 agent 共享
```

**改 markdown → 下次 agent run 立即生效. 不重启, 不重 build.**

```bash
# TUI 内沉淀经验:
> /lesson 大盘股 PE>50 + 60d涨幅>30% 通常是博弈票, 模型信号失效

# 或者直接改文件:
vim memories/risk-officer/hard_rules.md
```

详见 [memories/README.md](memories/README.md) — 24 个目录用途 + 设计原则.

---

## 📊 数据集

HuggingFace 三档预设, `fa init` 自动拉:

| 档 | 大小 | 股票池 | 5min | 财务报表 | F10 文本 | TDX zip | Repo |
|---|---|---|---|---|---|---|---|
| **demo** | ~155 MB | 300 (CSI300) | ❌ | ❌ | ❌ | ❌ | [data-demo](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-demo) |
| **lite** | ~3 GB | 800 (CSI800) | ✅ ~7天 | ✅ 735 MB | ✅ 1323 codes | ❌ | [data-lite](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-lite) |
| **full** | ~14 GB | 5500+ (含退市) | ✅ | ✅ | ✅ | ✅ 257 MB | [data-full](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-full) |

```python
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="yifishbossman/financial-analyst-data-lite",
    repo_type="dataset",
    local_dir="~/.financial-analyst/data",
)
```

**双二进制格式**: Qlib `.bin` (时序: `[4-byte float32 start_idx] + [float32 array]`, 给 OHLCV+因子); Parquet (列存, 给财报/事件/F10/行业). 兼容 [Microsoft Qlib](https://github.com/microsoft/qlib) 的 `D.features()` API 直接读.

---

## 🔌 LLM Provider

| Provider | 模型 | 网络出口 | 适用场景 |
|---|---|---|---|
| **qwen** *(默认)* | `qwen3.5-plus` · `qwen3-coder-plus` | `domestic` (直连, 无 proxy) | 国内最快最便宜 |
| **deepseek** | `deepseek-chat` · `deepseek-reasoner` | `intl_clash` (Clash + verify=False MITM) | 推理强, 成本低 |
| **openai** | `gpt-4o` · `gpt-4-turbo` | `intl_clash` | 通用 fallback |
| **anthropic** | `claude-opus-4-7` · `claude-sonnet-4-6` · `claude-haiku-4-5` | litellm fallback | 顶级质量 (美元定价) |

TUI 内秒切:
```bash
> /model deepseek-reasoner    # 热切换, 不重启
```

或改 `config/llm.yaml` 默认. 设计详见 [docs/llm_routing.md](docs/llm_routing.md).

---

## 🤝 贡献

欢迎 PR. 详见 [CONTRIBUTING.md](CONTRIBUTING.md):
- 开发循环 (分支 / 测试 / lint / changelog / PR)
- 新增 sub-agent (registry + memory + yaml + tests)
- 新增数据源 (`net.py.domestic_session` + `@rate_limited`)
- Conventional Commits ([angular 风格](https://www.conventionalcommits.org/))

其它文档:
- [VERSIONING.md](VERSIONING.md) — N-2 LTS, semver 政策
- [SECURITY.md](SECURITY.md) — 漏洞上报 (private)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1
- [docs/journey.md](docs/journey.md) — 中英双语构建历程 (空仓库 → 440 因子 + 25 agent, 2 周)

---

## 📄 许可 + 免责声明

Apache 2.0. **仅供研究 / 教学**. 草拟分析师级工作产物供合格专业人士 review. 不构成投资建议, 不执行交易, 不写任何 ledger. 用户须自行遵守适用法律法规.

<sub>v1.0.0 · 2026-05-25 · made by [@jesson-hh](https://github.com/jesson-hh) · 中英双语</sub>
