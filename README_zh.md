# Financial Analyst (观瀾)

[English](README.md) | **中文**

[![PyPI](https://img.shields.io/pypi/v/financial-analyst.svg)](https://pypi.org/project/financial-analyst/)
[![Python](https://img.shields.io/pypi/pyversions/financial-analyst.svg)](https://pypi.org/project/financial-analyst/)
[![tests](https://img.shields.io/badge/tests-705_passed-brightgreen)](https://github.com/jesson-hh/financial-analyst/actions)
[![license](https://img.shields.io/badge/license-Apache_2.0-green)](LICENSE)
[![status](https://img.shields.io/badge/status-stable_1.9.6-success)](https://github.com/jesson-hh/financial-analyst/releases)
[![alphas](https://img.shields.io/badge/alphas-440-blue)](docs/journey.md)
[![dataset](https://img.shields.io/badge/data-HF_Hub-yellow)](https://huggingface.co/yifishbossman)

**A 股个股深度研究 · 多 Agent 工作站.**

📖 第一次来? 推荐先读 [构建历程与架构总览](docs/journey.md) (中英双语) — 从空仓库到 440 因子 + 21 个 sub-agent 的两周复盘.

**14 个 sub-agent 分 4 层信任级** — 5 个 Tier-1 数据拉取 agent (其中 2 个读不可信新闻 / F10 用 JSON-schema 锁死输出), 4 个 Tier-2 分析师 (基本面 / 技术面 / 主力情绪 / 量化), 4 个 Tier-3 决策 agent (多头 / 空头 / 风控 / 报告撰写), 加 1 个 Tier-4 复盘 introspector — writer 落盘后自动自审, 提出经验更新建议人工 review. **只有 report-writer 能写报告文件**. 每个 agent 的记忆可插拔 (`memories/<agent>/*.md`) — 改 markdown, 下次研报立即生效. FTS5 检索让 prompt 成本比裸全量注入低约 60%.

完整 DAG + I/O schema: [docs/architecture/14_agents.md](docs/architecture/14_agents.md).

灵感来源: [Anthropic financial-services](https://github.com/anthropics/financial-services) (三层信任隔离 + 单写入者模式) 与 [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (YAML swarm 编排 + 多 LLM provider).

## v1.9.6 新增 (2026-05-24)

- **LLM 路由架构重构** — `AsyncOpenAI` 多 provider + per-provider `httpx.AsyncClient` + 3 档 `network_profile` (domestic / intl_clash / intl_system). 国内 qwen 直连阿里云不被 Clash fake-ip 接管走海外节点; deepseek/openai 走 Clash 代理 + verify=False 处理 MITM. 替代旧 litellm 单 client 路径.
- **DeepSeek 接入** — `deepseek-chat` + `deepseek-reasoner` 全可用, UI 端到端验证 通过 (`/model deepseek-chat` 切换, agent 自报 "底层由 DeepSeek 驱动").
- **Quote 多源 fallback** — 借鉴 vibe-trading 的多源 dispatch 思路. 新 `data/quote_fallback.py`, 实时行情 tencent → 雪球 自动 fallback, 不再让单源失败拖死整个 agent.
- **数据出口治理** — tushare/industry 接入 `net.py.domestic_session` + `@rate_limited`; 6 个 opencli xueqiu collector 加限速 (防 Aliyun WAF); tencent_quote 删全局 `NO_PROXY=*` 污染.
- **HF dataset 3 档发布** — demo (155MB) / lite (~3GB) / full (~14GB), 双语 README. 详见 [数据集](#-数据集-huggingface) 段.

## v1.0 核心能力

- **20 个 agent**: 14 个个股研究 (data → analyst → decision → introspector, 见 `config/swarm/stock-deep-dive.yaml`) + 5 个市场级 + 1 个 meta (ask)
- **7 个 swarm 预设**: stock-deep-dive / mainline-radar / morning-brief / intraday-review / dream 等
- **QlibBinaryLoader** (day + 5min) + **TushareLoader** (HTTP + ParquetCache) + **CSV ingester**
- **R7-R20 情绪信号**: board_scorer v5 / volume_regime (super_distr / tail_surge) / whale 信号
- **可插拔记忆系统** — FTS5 检索 + `always_include` 白名单 + `_shared/` 跨 agent playbook
- **Dream loop** — agent 自迭代记忆 (OutcomeTracker + Introspector + `memories/_proposed/` 暂存 + 人工 accept/reject)
- **BYOM** — registry-based 插件点 (models / loaders / collectors / sub-agents / KBs); `config/plugins.yaml` 启动时自动加载用户 `.py`
- **12 个 MCP tool** — 接 Claude Desktop / Claude Code
- **705 测试** (单元 + 集成, 全 mock LLM) + 1 个可选真实 E2E 测试

## 快速开始

三条路径出首份研报:

### A. PyPI 安装 (推荐, 1 分钟)

```bash
pip install financial-analyst
cp .env.example .env   # 编辑: TUSHARE_TOKEN + DASHSCOPE_API_KEY (+ 可选 DEEPSEEK_API_KEY)
financial-analyst                  # 启 TUI
```

### B. Docker (零本地配置, 2 分钟)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
cp .env.example .env   # 编辑 key
docker compose up      # → 交互式 TUI
```

容器内 one-shot:
```bash
docker compose run --rm fa report SH600519
docker compose run --rm fa ask "SH600519 现在 PE 多少"
```

### C. 源码 (开发)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
python -m venv .venv && .venv\Scripts\activate    # Windows; Linux/Mac: source .venv/bin/activate
pip install -e .[dev]
cp .env.example .env
financial-analyst
```

## 首批命令

TUI 内:
```
> 看看 600519                         # 完整深度研报 (~10 分钟)
> /ask SH600519 现在 PE 多少           # 秒级问答
> /mainline                          # 月级主线雷达
> /brief                             # 早盘异动扫描
> /intraday                          # 午休复盘
> /memory search 游资                 # 搜经验库
> /dream --since 30                  # 经验自迭代
> /sessions new my-project           # 多会话切换
> /model deepseek-chat               # 切 LLM 模型
> /quit
```

One-shot:
```bash
financial-analyst report SH600519 --asof 2026-05-15
financial-analyst ask -f question.txt
financial-analyst report -f codes.txt --trace
echo "SH600519 PE 多少" | financial-analyst ask
```

## 架构总览

```
Orchestrator → Tier 1 (数据, 并行) → Tier 2 (分析师, 并行) → Tier 3 (决策, 串行) → Tier 4 (复盘)
                  ↓                          ↓                        ↓                  ↓
              拉数据 + 算因子              4 个分析师            多空 / 风控 / writer    introspector
              + 读不可信源                 消费 Tier 1 JSON      消费 Tier 2 JSON       自审 + 提议
              JSON schema 锁死
```

完整 DAG 与信任模型: [docs/architecture.md](docs/architecture.md).

## LLM Provider 路由 (v1.9.6)

支持 4 个 provider, 不同 provider 走不同网络出口策略:

| Provider | 模型 | 网络出口 (network_profile) | 备注 |
|---|---|---|---|
| **qwen** (默认) | qwen3.5-plus / qwen3-max / qwen3.5-flash / qwen3-coder-plus | `domestic` 直连 aliyuncs.com | 国内最快最便宜 |
| **deepseek** | deepseek-chat / deepseek-reasoner | `intl_clash` 走 Clash + verify=False | Clash fake-ip + MITM 环境 |
| **openai** | gpt-4o / gpt-4-turbo | `intl_clash` | 同上 |
| **anthropic** | claude-opus-4-7 / sonnet-4-6 / haiku-4-5 | litellm fallback | API 格式不兼容 OpenAI, 走 litellm |

**切换方式**:
- TUI 内 `/model deepseek-chat` (秒切, 不重启)
- 启动时改 `config/llm.yaml::default_model`
- 程序: `LLMClient.for_agent('ask').with_overrides(provider='deepseek', model='deepseek-reasoner').chat(...)`

代码细节见 `src/financial_analyst/llm/client.py` 注释.

## BYOM — 接入私有模型

`financial-analyst` 是**框架**, 不是固定产品. 插入你自己的私有模型 / loader / collector:

```python
# G:/my_private_code/my_fm.py
from financial_analyst.models import BaseModel, ModelRegistry

class MyFMCluster(BaseModel):
    def predict(self, code, asof):
        return {"score": ..., "rank_pct": ..., "cluster": ...}
    def metadata(self):
        return {"name": "my_fm", "version": "W10"}

ModelRegistry.register("my_fm", MyFMCluster)
```

```yaml
# config/plugins.yaml
load_at_startup:
  - G:/my_private_code/my_fm.py
```

然后 `financial-analyst report SH600519` 会把你的模型纳入量化共识. **不需要把私有 checkpoint 推入开源仓库**.

完整指南: [docs/byom.md](docs/byom.md). 示例 (FM cluster / CSV loader / Tushare news collector / pytdx F10 collector) 在 [`examples/`](examples/).

## 数据接入

如果没现成 Qlib 数据目录, 用 CSV ingester:

```yaml
# config/data_sources.yaml
sources:
  - name: my_csv
    type: csv
    path: G:/my_data/*.csv
    code_col: ts_code
    date_col: trade_date
    target: ~/.financial-analyst/data/my_csv
```

```bash
financial-analyst ingest --source my_csv
# 然后改 config/loaders.yaml 的 qlib_binary.provider_uri.day 指向 target
```

详见 [docs/data_ingest.md](docs/data_ingest.md).

## 📦 数据集 (HuggingFace)

懒人方案: 我们已经把 A 股历史数据打包发到 HF Hub, `fa init` 自动下载. 三档:

| 档 | 大小 | 股票池 | 5min | 财务报表 | F10 文本 | TDX 历年财报 | HF Repo |
|---|---|---|---|---|---|---|---|
| **demo** | ~155 MB | 当前 mv top-300 (CSI300) | ❌ | ❌ | ❌ | ❌ | [data-demo](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-demo) |
| **lite** | ~3 GB | top-800 (CSI800 ≈) | ✅ ~7 天 | ✅ 735MB | ✅ 1323 codes | ❌ | [data-lite](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-lite) |
| **full** | ~14 GB | 全 5500+ (含退市) | ✅ | ✅ | ✅ | ✅ 257MB | [data-full](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-full) |

下载:
```bash
fa init    # 交互向导, 选档 → 自动 snapshot_download
```

或手动 Python:
```python
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="yifishbossman/financial-analyst-data-lite",
    repo_type="dataset",
    local_dir="~/.financial-analyst/data",
)
```

发布脚本: `scripts/publish_hf_dataset.py --preset {demo|lite|full} --repo your-name/data-xxx`.

## 记忆系统

每个 sub-agent 有 `memories/<agent-name>/` 目录, 内含 markdown 文件. 文件在 runtime 被追加到 agent 的 system prompt. v0.2+: FTS5 检索让标记 `memory_mode: retrieval` 的 agent prompt 成本降下来. `memories/<agent>/always_include.txt` 列出的关键文件无条件加载.

详见 [docs/memories.md](docs/memories.md) — 原则、文件组织建议、CLI 命令.

## Dream Loop — Agent 经验自迭代

跑 `/dream` (或 `financial-analyst dream`):
1. 把历史研报跟 T+5d / T+20d 实际行情对比打分
2. introspector sub-agent 找错预测的规律
3. 把提议 stage 到 `memories/_proposed/<agent>/` 等人工 review
4. `/memory accept` 合并, `/memory reject` 丢弃

**故意不实现 auto-accept** — 错误的经验更新在量化系统里会复利亏损. 只走人工 review.

详见 [docs/dream_loop.md](docs/dream_loop.md).

## MCP Server

从 Claude Desktop / Claude Code 经 MCP 调用 financial-analyst:

```json
{
  "mcpServers": {
    "financial-analyst": {
      "command": "financial-analyst-mcp",
      "env": {"TUSHARE_TOKEN": "...", "DASHSCOPE_API_KEY": "..."}
    }
  }
}
```

暴露 12 个 tool: ask / quick_quote / quick_factors / memory_search / list_past_reports / read_past_report / list_dream_proposals / mainline / brief / intraday / report / dream. 详见 [docs/mcp.md](docs/mcp.md).

## 测试

```bash
pytest tests/                                       # 705 个单元 + 集成测试 (mocked)
FA_E2E=1 pytest tests/integration/test_end_to_end.py  # 真 Tushare + LLM round-trip
```

## 许可

Apache 2.0.

## 免责声明

本工具为合格专业人士提供分析师层级的工作产物草案以供 review. 不构成投资建议, 不执行交易, 不写任何 ledger. 用户须自行遵守适用法律法规.
