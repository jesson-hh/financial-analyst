<p align="center">
  <h1 align="center">觀瀾 · Financial Analyst</h1>
</p>

<p align="center">
  <strong>一行命令. 24 个 AI Agent. A 股深度研究.</strong>
</p>

<p align="center">
  <em>给一个 6 位股票代码, 让 16 个 sub-agent 协作出一份研报 — 基本面 · 技术面 · 主力情绪 · 量化模型 · 多空风控辩论 — 约 10 分钟.</em>
</p>

<p align="center">
  <a href="README.md">English</a> &nbsp;·&nbsp; <strong>中文</strong>
</p>

<p align="center">
  <a href="https://pypi.org/project/financial-analyst/"><img src="https://img.shields.io/pypi/v/financial-analyst.svg?style=flat&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
  <img src="https://img.shields.io/pypi/pyversions/financial-analyst.svg?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/release-v1.0.3-success?style=flat" alt="Release">
  <img src="https://img.shields.io/badge/tests-712_passed-brightgreen?style=flat" alt="Tests">
  <img src="https://img.shields.io/badge/license-Apache_2.0-yellow?style=flat" alt="License">
  <br>
  <img src="https://img.shields.io/badge/agents-24-7C3AED?style=flat" alt="Agents">
  <img src="https://img.shields.io/badge/swarm_预设-5-2563EB?style=flat" alt="Swarm">
  <img src="https://img.shields.io/badge/buddy_工具-31-0F766E?style=flat" alt="Tools">
  <img src="https://img.shields.io/badge/alpha_因子-440-FF6B6B?style=flat" alt="Alphas">
  <a href="https://huggingface.co/yifishbossman"><img src="https://img.shields.io/badge/数据-HF_Hub-FFD21E?style=flat&logo=huggingface&logoColor=black" alt="HF Datasets"></a>
</p>

<p align="center">
  <a href="#-是什么">是什么</a> &nbsp;·&nbsp;
  <a href="#-能干什么">能干什么</a> &nbsp;·&nbsp;
  <a href="#-快速开始">快速开始</a> &nbsp;·&nbsp;
  <a href="#-24-个-agent">Agent 阵容</a> &nbsp;·&nbsp;
  <a href="#-可插拔记忆">记忆系统</a> &nbsp;·&nbsp;
  <a href="#-数据集">数据集</a> &nbsp;·&nbsp;
  <a href="#-llm-provider">LLM</a>
</p>

> 🐣 **完全没用过 Python / 命令行?** 没事. 看 **[小白上手指南 →](docs/setup/beginner_zh.md)** (30 分钟从装 Python 开始, 一步一步把命令给你)

```bash
pip install financial-analyst==1.0.3    # 1 分钟, 不用再加 [serve] 后缀
fa start                                 # 零配置一键: 引导 + 后端 + Web UI + 浏览器自动开
```

第一次跑会: 选语言 → 选 workspace (可挂 `D:\fa-workspace` 等任意盘, 不绑死系统盘) → 填 LLM key → 选 HF dataset → 启 buddy backend (`:9999`) → 启 Web UI (`:5173`) → 自动开浏览器. Ctrl+C 停所有. 第二次 `fa start` 自动 fast-path 跳到浏览器, 不重启子进程.

高级用户可以分别用各子命令:

```bash
fa init                # 只跑引导 (workspace + LLM key + 数据包)
fa report SH600519     # 一次性研报 (~10 分钟, 无 UI)
fa update              # 检查 PyPI + 自升级 (editable 安装会拒绝)
fa data refresh        # 智能增量刷新 — 24h 内已更新自动跳过
fa --tui               # 终端 TUI 而非 Web UI
```

> **🆕 v1.0.3 亮点** *(2026-05-25)*
>
> - **零 extras 安装** — `pip install financial-analyst` 即装即用, fastapi + uvicorn 并入 core, 不再需要 `[serve]` 后缀
> - **`fa start`** — 新的零配置启动入口 (`fa launch` 保留为别名). 二次启动自动 fast-path 跳到浏览器, 不重启子进程
> - **Workspace pinning** — 数据可以挂到 `D:\fa-workspace` 等任意盘, 不再绑死系统盘. 通过 `fa init` 一次性配
> - **数据刷新按钮** — 状态栏新增 `✓ / ⏳ 盘中 / ⚠ / ↻` 四态徽章, 一键拉日线 + 5min + 估值
> - **`fa update`** + **`fa data refresh`** — PyPI 自升级 (editable 安装会拒绝) + 智能增量刷新 (24h 内已更新自动跳过)
>
> 完整变更见 [CHANGELOG](CHANGELOG.md).

---

## 💡 是什么

**A 股研究工作站, 思维像买方分析师.**

给一个股票代码, 16 个 sub-agent 分 4 个信任层并发跑:

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

### 🎯 16-agent 个股深度研报
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
pip install financial-analyst==1.0.3
fa start                   # 交互向导 (LLM key + workspace + HF 数据) + 自动开后端 / UI / 浏览器
# 或非交互模式 (CI / 脚本):
fa init --yes --preset demo --workspace D:/fa-workspace   # 一行配好
fa report SH600519                                         # 首份深度研报 (~10 分钟)
```

### B. 源码 (开发)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
pip install -e ".[dev]"
pytest tests/              # 712 测试, ~8 分钟
```

---

## 🤖 24 个 Agent

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

financial-analyst 是个 **重工具调用的 24-agent 系统** — Tier-1 调 buddy tools, Tier-2 跨股 join 数据, Tier-3 写带 `[V#]/[F#]` 锚点的结构化研报, 全系统只有 `report-writer` 一个能落盘. **你选什么 LLM 直接决定 swarm 到底用工具还是凭训练记忆瞎编**.

### 环境变量

`fa init` 向导会让你填一个 provider 的 `*_API_KEY` 到 `.env`. 默认值在 `config/llm.yaml`.

| 变量 | 必填 | 说明 |
|---|:---:|---|
| `DASHSCOPE_API_KEY` | `qwen` 用 *(默认)* | 阿里云百炼 — qwen3.5-plus / qwen3-coder-plus |
| `DEEPSEEK_API_KEY` | `deepseek` 用 | deepseek-chat / deepseek-reasoner |
| `OPENAI_API_KEY` | `openai` 用 | gpt-4o / gpt-4-turbo |
| `ANTHROPIC_API_KEY` | `anthropic` 用 | claude-opus / claude-sonnet / claude-haiku |
| `TUSHARE_TOKEN` | 否 | A 股数据; 不填走 pytdx 主站 + 腾讯实时 (免 token, 免费) |

### 🎯 模型推荐档位

| 档 | 例 | 用场景 |
|---|---|---|
| **顶级** | `deepseek-reasoner` · `claude-opus-4-7` · `gpt-4o` · `qwen3-max` (需通用端点) | Tier-3 决策 agent (bull / bear / risk-officer / report-writer / introspector), 市场级 swarm (overseas-radar / mainline / morning-brief writer) |
| **甜区** *(默认)* | `qwen3.5-plus` · `qwen3-coder-plus` · `deepseek-chat` | 日常驱动 — 工具调用稳, 成本低; Tier-1 数据 agent + Tier-2 分析师跑在这档 |
| **不要用** | `claude-haiku-4-5` · `qwen-flash` · `qwen-turbo` · `*-mini` · 小/蒸馏型 | 工具调用不稳 — agent 会跳过 `D.features()` / TDX-F10 查询直接凭训练记忆瞎编因子分 |

默认是 `qwen3.5-plus`. 阿里云百炼注册送 100 万 token credit — 大约 **150 份个股深度研报** 你才开始付钱.

### 网络出口

`network_profile` 决定每个 provider 怎么穿过国内网络环境 (Clash fake-ip / MITM 等):

| Provider | profile | 细节 |
|---|---|---|
| **qwen** | `domestic` | `trust_env=False`, 直连 `aliyuncs.com` — 绕开 Clash fake-ip (否则被接管走海外节点 10s 超时) |
| **deepseek** · **openai** · **openrouter** | `intl_clash` | 走 `HTTPS_PROXY` (默认 `127.0.0.1:7890`) + `verify=False` — Clash 用 root cert MITM HTTPS |
| **anthropic** | litellm fallback | Anthropic SDK 不兼容 OpenAI 格式, 路由层 fallback 给 litellm |

### 热切换

```bash
> /model deepseek-reasoner    # TUI 内, 不重启, 进行中会话保留
> /model qwen3-coder-plus     # 裸名自动找 provider; 也可用 provider/model 显式
> /model                      # 列已配置的模型
```

或改 `config/llm.yaml` 的 `default_provider` / `default_model`. 多 provider AsyncOpenAI client 设计详见 [docs/llm_routing.md](docs/llm_routing.md).

---

## 🤝 反馈 / Issue

个人项目, 单维护者. Issue 提交到
[github.com/jesson-hh/financial-analyst/issues](https://github.com/jesson-hh/financial-analyst/issues).

- [VERSIONING.md](VERSIONING.md) — N-2 LTS, semver 政策
- [docs/journey.md](docs/journey.md) — 中英双语构建历程 (空仓库 → 440 因子 + 24 agent, 2 周)

---

## 📄 许可 + 免责声明

Apache 2.0. **仅供研究 / 教学**. 草拟分析师级工作产物供合格专业人士 review. 不构成投资建议, 不执行交易, 不写任何 ledger. 用户须自行遵守适用法律法规.

<sub>v1.0.3 · 2026-05-25 · made by [@jesson-hh](https://github.com/jesson-hh) · 中英双语</sub>
