# Financial Analyst (中文)

A 股个股深度研报 multi-agent 工作站。

**13 个 sub-agent, 三层信任域**: Tier 1 (5 个数据 fetcher, 其中 news-reader / f10-reader 读不可信源, JSON schema 强约束) → Tier 2 (4 个分析师: 基本面/技术/主力情绪/量化) → Tier 3 (4 个决策角色: bull/bear/CRO/writer). 只有 report-writer 能写文件。

经验沉淀 (rating_system / pitfalls / R7-R20 情绪信号 / V1-V10 视角等) 以 markdown 形式放进每个 sub-agent 自己的 `memories/<name>/` 目录, 改 markdown 立即生效。

借鉴 [Anthropic financial-services](https://github.com/anthropics/financial-services) 的三层隔离 + [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) 的多角色 DAG + 多 provider LLM。

## 快速开始

```bash
git clone <repo-url> && cd financial-analyst
python -m venv .venv && .venv\Scripts\activate
pip install -e .[dev]
cp .env.example .env       # 填 TUSHARE_TOKEN + ANTHROPIC_API_KEY
financial-analyst          # 进 TUI
```

```
> 看看 600519
> /agents
> /quit
```

## License

Apache 2.0
