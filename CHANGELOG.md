# Changelog

All notable changes to this project follow [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning 2.0.0](https://semver.org/).

## [1.0.1] — 2026-05-25  · One-command launcher + i18n

### Added — Zero-config web launcher (`fa launch`)

Single command boots the entire stack: detects config → runs the first-launch wizard if needed → starts the buddy SSE backend on `:9999` → starts the web UI http.server on `:5173` → polls both for readiness → opens the browser. Ctrl+C gracefully terminates both subprocesses.

```bash
pip install financial-analyst==1.0.1
financial-analyst         # zero-config — wizard if needed, then web UI auto-opens
```

- New module: `src/financial_analyst/launch_cli.py` (~250 lines) — port checks, `httpx` health-polling, OS-aware subprocess signaling (Windows `CTRL_BREAK_EVENT` vs POSIX `SIGTERM`).
- New CLI: `fa launch [--skip-init] [--no-browser] [--backend-port N] [--ui-port N]`.
- Default behaviour change: bare `financial-analyst` (or `fa`) now invokes `launch`. Drop into the terminal TUI with `fa --tui`.
- Web UI bundled into pip wheel: `src/financial_analyst/ui/` (5 files, ~216 KB). `pyproject.toml` `[tool.hatch.build.targets.wheel].include` ships `ui/**/*`. UI source remains at `packaging/src-tauri/ui/` for Tauri desktop bundle; `launch` searches both locations.
- `fa init` HuggingFace dataset repo IDs updated to public `yifishbossman/financial-analyst-data-{demo,lite,full}`.

### Changed — `.py` comments + docstrings translated to English

Translated Chinese comments + docstrings to English across 30 high-value source files. **Strictly preserved** so the LLM stack keeps talking to Chinese users:

- `SYSTEM_PROMPT` constants (4 agents)
- `Tool(description="中文")` LLM-visible tool schemas
- `Field(description="中文")` Pydantic schemas exposed to function-calling
- `typer.echo("中文")` / `console.print("中文")` user-facing CLI output
- `ToolResult("中文")` LLM response bodies
- LLM `messages` bodies
- `memories/<agent>/*.md` knowledge files (unchanged)

712 tests passing.

### Removed — third-party attribution references

Removed external inspiration references across CHANGELOG, README, README_zh, docs, and source comments. The architecture is the team's own design.

### Added — Cross-repo data contract + unified path resolver

Single source of truth for data layout, finally documented and codified:

- New `docs/data_contract.md` — directory tree, 6 field tables (OHLCV / valuation / factors / whale signals / sentiment / TDX F10), units conventions, writer/reader matrix, common pitfalls. Cross-references `G:/stocks/CLAUDE.md` (research lab side).
- New `src/financial_analyst/data/paths.py` — `DataPaths` dataclass + `get_data_paths()` resolver with 4-tier priority: env vars (`FA_QLIB_URI` / `FA_PARQUET_ROOT` / `FA_NEWS_DATA_ROOT`) → `config/loaders.yaml` → `~/.financial-analyst/data/` → dev fallback. All paths resolved independently; mixing sources is OK.
- `config/loaders.yaml` + bundled `_resources/config/loaders.yaml` schema extended: `qlib_binary` block now carries `parquet_root` + `news_data_root` siblings to `provider_uri`.
- `init_cli.py:_write_loaders_config()` writes the two new keys when `fa init` finishes a HuggingFace download — first-user no longer falls back to dev paths.
- `agent/market/sector_rotation_analyzer.py` drops hardcoded `G:/stocks/stock_data/parquet`, now resolves via `get_data_paths().parquet_root`.
- `buddy/tools.py` iwencai plugin path also goes via `_project_root()` helper instead of literal `G:/financial-analyst/...`.

### Fixed — Agent count + Tauri shell version

- `README.md` / `README_zh.md` agents badge `25 → 24` and tier-1 deep-dive description `14-agent → 16-agent` (post-v1.9.7 swarm now has 16 in the stock-deep-dive YAML).
- `packaging/src-tauri/{tauri.conf.json,Cargo.toml}` version `0.1.0 → 1.0.1` for shell ↔ Python package parity. Shell shortDescription/longDescription updated to `16-agent`.
- `scripts/publish_hf_dataset.py` dataset-card footer template `v1.9.6 → v1.0.1`, `--repo` example `jesson-hh → yifishbossman`, dataset description `14-agent → 16-agent`.

### Tests

718 passed / 1 skipped (was 712 — 6 new in `tests/test_data_paths.py` covering the 4-tier priority).

## [1.0.0] — 2026-05-25  · **First public release**

> **Lineage**: This is the **first publicly released version** of the project formerly known as `financial-analyst` (internal `v1.9.x` preview series). All features from internal `v1.9.7` are at GA quality and shipped as `1.0.0`. The internal versioning is preserved in the [Pre-1.0 history (internal preview)](#pre-10-history-internal-preview) appendix below.
>
> **Why 1.0.0 < 1.9.7?** This is a public stable baseline, not a regression. PyPI project was wiped and re-registered to establish a clean public versioning baseline. See [VERSIONING.md](VERSIONING.md) for the LTS policy going forward.

### Highlights of 1.0.0 (vs internal v1.9.7)

- **25 sub-agents** across 4 trust tiers + market-level swarms (stock-deep-dive / morning-brief / overseas-radar / mainline-radar / intraday-review / dream).
- **Multi-provider LLM routing** with `network_profile` (domestic / intl_clash / intl_system) — qwen direct, deepseek + openai via Clash + verify=False MITM.
- **Quote fallback chain** (tencent → xueqiu) for production resilience.
- **HF dataset bundles** (demo 155MB / lite ~3GB / full ~14GB) — `fa init` auto-pulls.
- **712 tests passing**, full Apache-2.0, bilingual README (zh/en).

### Initial release stability commitments

- **Public API** in `financial_analyst.agent.*`, `financial_analyst.llm.*`, `financial_analyst.data.*` is now under SemVer.
- **Breaking changes** require a major version bump (2.0). 1.x guaranteed backward-compatible.
- See [VERSIONING.md](VERSIONING.md) for N-2 LTS support (current + 2 prior minors).

### Migration from internal `financial-analyst` v1.9.x

```bash
# Old (internal preview, will fail after PyPI delete on 2026-05-25):
pip install financial-analyst==1.9.7

# New (public v1.0.0):
pip install financial-analyst==1.0.0
```

The Python import path is unchanged (`import financial_analyst`). All 25 sub-agents, CLI commands, MCP server, and swarm YAMLs are 100% compatible.

---

## Pre-1.0 history (internal preview)

> The following entries are from the internal `v1.9.x` development series, preserved for transparency. **They predate the public release**. Internal versioning followed an inverted scheme (`1.9.x` ascending) before being collapsed to public `1.0.0`.

## v1.9.7 — 2026-05-24 (晚)

### Added — overseas-radar swarm + morning-brief v2 (国际市场传导)

**5 new agents** (总 agent 数 20 → 25), 填补"国际市场 / 海外新闻传导"的 gap.
audit 发现之前 14-agent 全部聚焦个股, 没有任何 agent 处理隔夜美股 / 港股 /
VIX / Fed 政策 / 大宗商品对 A 股的传导.

- **`overseas-market-scanner`** (无 LLM, ~10s) — 拉 tencent qt.gtimg.cn 6
  个核心国际指数 (DJI/IXIC/INX/VIX/HSI/HSTECH), 算 risk_tone
  (risk_on/off/mixed) + detail. 国内 endpoint 不撞 Clash MITM (yfinance
  用 curl_cffi 1.5 跟 Clash 冲突, 弃用).
- **`global-news-aggregator`** (LLM, ~30s) — 基于 overseas-market-scanner
  价格 + memory 中的传导规则, LLM 写"全球格局 narrative" + 6 大 channel
  分类 (us_equity / fed_policy / geopolitical / commodity / china_specific /
  fx_rates) + 受影响 A 股板块.
- **`macro-impact-analyzer`** (LLM, ~15s) — 融合 overseas + global-news +
  当日 A 股 scanner, 判读 A 股 vs 海外 follow-through + 给 3-5 个明日
  actionable signals. 落盘 `out/overseas_radar_<date>.md`.
- **`catalyst-extractor`** (LLM, ~30s) — 对 market-scanner 输出的异动股
  (top_gainers/losers/volume_anomalies top-5), 拉 NewsDB 48h 新闻,
  LLM 一次性提取每股催化类型 + bullish/bearish 判读 + cited news.
  Catalyst 类型: policy / earnings / product / M&A / macro / rumor /
  technical / none.
- **`sector-rotation-analyzer`** (无 LLM, ~5s) — 用 `parquet/tushare_stock_basic.parquet`
  把异动股聚合到行业, 算今日板块 leaders / laggards + 一句话 rotation_signal.

### Changed — 3 swarm yaml + writer + 3 个 Tier-2 prompt 升级

- **`config/swarm/morning-brief.yaml`** 2 → **5 agents**: scanner +
  overseas-market-scanner (并行) + catalyst-extractor + sector-rotation-analyzer
  (并行依赖 scanner) → morning-brief-writer (依赖前 4 个).
- **`config/swarm/overseas-radar.yaml`** 新 swarm 3 agents: overseas-market-scanner +
  global-news-aggregator + market-scanner (并行) → macro-impact-analyzer (融合 3 个).
- **`morning_brief_writer.py`** prompt + `_execute` 接 4 个 upstream input
  (scanner + overseas + catalyst + rotation), 写 8 section markdown brief
  (头部 + 隔夜海外 + 大盘 + 板块轮动 + 领涨/跌/量能/watchlist).
- **`config/swarm/stock-deep-dive.yaml`** 14 → **16 agents**: Tier-1 加
  overseas-market-scanner + sector-rotation-analyzer (并行跟 quote-fetcher
  等). Tier-2: fundamental-analyst 接 overseas (估值锚 + 高 VIX 调整);
  technical-analyst 接 overseas + rotation (momentum 判读 + 行业顺势);
  quant-analyst 接 rotation (cross-sectional 信号 ±0.3 因子加权).
- **3 个 Tier-2 agent prompt 升级** — 显式声明新 input + memory 规则
  (海外 risk_tone 用法 / 板块轮动加减分逻辑).

### Data layer

- **`data/collectors/tencent_global.py`** 新 collector — 跟 `tencent_quote.py`
  同模式 (HTTP GBK 国内 endpoint, `net.py.domestic_session`), 但字段 layout
  不同: 国际指数 `[3]=price, [4]=prev, [5]=open, [31]=change, [32]=changePct,
  [33]=high, [34]=low` (A 股 changePct 在 [32], 但 [4]=prev, [31] 是其他东西).
  注册 `tencent_global` source @rate_limited (qps=2, cache_ttl=30s).
- **`llm.yaml`** 两份 (bundled + cwd) 加 5 个新 agent overrides (默认 qwen3.5-plus).

### Test

- `tests/test_overseas_radar.py` 7 个 smoke test: parser / risk_tone 双向
  (risk_on / risk_off) / sector aggregation / catalyst LLM mock /
  global-news LLM mock / macro-impact markdown 落盘.

### Memory

- `memories/overseas-market-scanner/thresholds.md` — 风险偏好阈值 + 传导经验
- `memories/global-news-aggregator/channels.md` — 6 个 channel 速查 + 经验规则
- `memories/macro-impact-analyzer/playbook.md` — 4 种典型场景 + 操作手册
- `memories/catalyst-extractor/rules.md` — 催化类型判定优先级 + 输出约束
- `memories/sector-rotation-analyzer/rules.md` — 行业分类源 + 5 种轮动模式

### TUI

- `tui.py::_ensure_registered` 现在注册 24 个 sub-agent (was 19).

### Fixed — v1.9.6 收尾 (用户测试反馈)

- **`buddy/server.py::/models`** 过滤掉没 `*_API_KEY` 的 provider, 返回
  `disabled_providers` 列 reason. UI picker 只显示能用的, 避免用户切到坏模型.
- **`config/llm.yaml`** qwen models 列表减为 `[qwen3.5-plus, qwen3-coder-plus]` —
  dashscope coding 端点不认 `qwen3-max` / `qwen3.5-flash` (返 400 invalid_parameter_error,
  用户实测). 通用 qwen 模型需要换通用 dashscope key + base_url, yaml 注释说明.
- **UI runtime LLM error 指示**: `app.jsx` 加 `lastLLMError` state + reducer,
  SSE error event 时 status bar `● 真 LLM` 变 **`⚠ LLM 失败`** 红色 + 错误消息条
  (点击 / 切模型 / 切真 LLM toggle 自动清除). cache-buster `?v=20260524-3`.
- `packaging/src-tauri/ui/` 同步.

## v1.9.6 — 2026-05-24

### Changed — LLM 路由架构重构 (multi-provider direct, 替代 litellm)

- **`llm/client.py` 重写**: 砍掉 litellm 单 client, 改为按 `network_profile`
  分桶的多 provider AsyncOpenAI client. 3 档: `domestic` (trust_env=False,
  国内站直连), `intl_clash` (proxy=HTTPS_PROXY or 127.0.0.1:7890, verify=False
  Clash MITM), `intl_system` (trust_env=True 系统代理).
- **`_resources/config/llm.yaml` + `config/llm.yaml`** 每个 provider 加
  `network_profile`. `qwen` → `domestic` (避免 Clash fake-ip 把 aliyuncs.com
  接管走海外节点 10s timeout, **修了 14 agent 默认 LLM 路径**);
  `deepseek`/`openai` → `intl_clash`. `deepseek` 加 `deepseek-reasoner`.
- **OpenAI-compat provider** (qwen/deepseek/openai/openrouter) 走
  `_chat_openai_compat` → `AsyncOpenAI(http_client=...)`. `anthropic`
  保留 litellm fallback (API 格式不兼容 OpenAI).
- **返回 dict**: `_chat_openai_compat` 末尾 `response.model_dump()`. 21+
  caller 用 `response['choices'][0]['message']['content']` dict-style 访问,
  AsyncOpenAI ChatCompletion 是 pydantic 不 subscriptable, dump 兼容.
- **probe 实测**: qwen domestic direct 13s (qwen 自己啰嗦 675 token);
  deepseek-chat intl_clash 612ms; deepseek-reasoner intl_clash 1017ms.
- **buddy UI 端到端验证通过**: `/model deepseek-chat` 切换, "你好" 一句
  问 → DeepSeek 自报身份 "底层由 DeepSeek 驱动", token 0→7.1k.

### Changed — 数据出口收尾 (`data/net.py` 治理)

- **`data/loaders/tushare.py::_query`** 改 `domestic_session()` + 加
  `@rate_limited("tushare", cache_key=...)`. Tushare token 自有 200/min
  限速, 客户端再限一遍防 burst. `__init__` 删全局
  `os.environ['NO_PROXY']='*'` 污染.
- **`data/loaders/industry.py::refresh_from_tushare`** 同上, 改
  `domestic_session()`.
- **`data/collectors/tencent_quote.py`** 删 `os.environ.setdefault('NO_PROXY','*')`
  全局污染. httpx `trust_env=False` 已局部隔离.
- **6 个 xueqiu opencli collector 加 `@rate_limited("xueqiu", cache_key=...)`**:
  `xueqiu_earnings`, `xueqiu_feed`, `xueqiu_hot_posts`, `xueqiu_watchlist`
  (含 `XueqiuGroupsCollector`), `xueqiu_fund` (含 2 子类), `xueqiu_stock`.
  之前 UI 侧边栏连点 / agent 突发轮询会触 Aliyun WAF, 累及所有 xueqiu
  collector (含已限速的 comments/hot_stock); 现在全部 1qps + 30s cache.

### Added — Multi-source Realtime Quote Fallback

- **`src/financial_analyst/data/quote_fallback.py`** 新增 (~110 行): 通用
  fallback chain helper. `fetch_realtime_quote(code)` 顺序 tencent → xueqiu,
  第一个 valid (有 price 或 current) 就返 `(source_name, quote_dict)`,
  全失败抛 `RuntimeError` + 每源错误明细. `fetch_realtime_quotes(codes)`
  批量版同理.
- **`buddy/tools.py::_tool_realtime_quote` 重构**: 之前手写 tencent → xueqiu
  fallback (2 处 try/except), 现统一走 helper. side_effect 加 `source` 字段
  让 caller 知道实际拿哪源数据.
- **`buddy/tools.py::_tool_quote_batch` 加 fallback**: 之前只 tencent 单源,
  挂了直接 fail. 现走 helper, tencent → xueqiu (xueqiu 退化为循环单股).
- **设计**: fallback 层只做 routing 不做 cache, 各 source 内部 `@rate_limited`
  已有 cache (tencent 2s / xueqiu 30s). 不重复.
- 测试: `tests/test_quote_fallback.py` 15 个 (单/批 × short-circuit / 链式
  fallback / 异常 fallback / 全失败 + details / default chain 顺序).

### Test 兼容

- `tests/test_buddy_improvements.py::_client()` 改用 `anthropic` provider,
  litellm fallback 路径 mock `acompletion` 仍生效. qwen/deepseek/openai/
  openrouter 改走 AsyncOpenAI 后, mock `acompletion` 对它们已无效.
- `tests/conftest.py` 加 `_clear_net_caches` autouse fixture, 每个 test
  前清 `net.py @rate_limited` source cache. 避免同 args 调同 collector
  从前一 test cache 拿 stale value 导致 mock 不生效.
- `tests/test_loaders.py` 4 个 tushare test 改 mock target `requests.post`
  → `domestic_session()` (v1.9.6 tushare._query 改用 net.py session).

## v1.9.5 — 2026-05-23

### Added — Tier-4 Introspector + 14-agent 升级

- **新增 Tier-4 `introspector` agent** (单 agent 单 tier, `agent/tier3/introspector.py`):
  在 report-writer 落盘后启动, 跨上游做 post-mortem 自检. 输出 `quality_flags`
  (本份报告立即可见的问题) + `proposals` (跨案例归纳的规则提议, 写到
  `memories/_pending_introspections/<date>_<code>.json`, 人工 review 后落盘).
  **不自动 patch memory** (LLM 静默改 agent 规则书风险太大).
- `config/swarm/stock-deep-dive.yaml` 升级 13→14 agents, 4 tiers; introspector deps
  覆盖 report-writer + 全 Tier-2 + bull + bear + risk-officer.
- 配套 `tui.py` 注册 introspector; `_ensure_registered()` 现在返回 14 个.
- 单测同步: `test_preset_loading.py::test_load_stock_deep_dive` 改 assert 14;
  `test_cli_list_commands.py::test_agents_list` 加 introspector 名字检查.

### Added — API 稳定性深度探活 / 实时进度 / 经验沉淀 闭环

- `POST /lesson {text}` — 用户通过 buddy `/lesson <text>` 沉淀经验, 追加到
  `memories/_shared/conversation_lessons.md`. **下次 buddy build prompt 时自动
  prepend** (`buddy/agent.py::_load_conversation_lessons` hot reload, 不需重启).
- `GET /diag?quick=0/1` — 并行探 5 源 + LLM (~20s 全 / ~2s quick), 返回每源
  `ok/latency/detail` + `rate_limit_stats` (累计 calls/retries/cache_hits/throttled).
  前端 🩺 探活按钮一目了然哪源红.
- `GET /report-progress?code=X` — 前端轮询 deep-report 14-agent 实时状态
  (state ∈ pending/running/done/fail + elapsed). `tui.run_report_oneshot` 在
  orchestrator `on_event` 里写 `out/<CODE>_progress.json`.

### Added — 限速/重试/缓存基础设施 (`data/net.py`)

新模块统一处理国内/海外接口稳定性:

- `domestic_session()` — `trust_env=False`, 直连. 翻墙环境也不走 Clash
  (Clash 拦 A 股域名常导致 ERR_TIMED_OUT / WAF 拒签).
- `intl_session()` — `trust_env=True`, 走系统代理.
- `@rate_limited(source, cache_key=...)` 装饰器 — per-source QPS 限制 +
  指数退避重试 + TTL 缓存. 线程安全 (`threading.Lock`).
- 预注册 8 个 source: xueqiu / xueqiu_hot / tencent_quote / tushare /
  eastmoney_kuaixun / eastmoney_longhu / sinafinance / ths_hot.
- `source_stats()` 返回累计统计 (在 `/diag` 响应里).

### Changed — 雪球 collectors 切直连

`xueqiu_comments.py` + `xueqiu_hot_stock.py` 从 opencli browser-bridge 改为
Python 直连 HTTP (`domestic_session` + `@rate_limited`). Chrome 内 fetch 走系统
代理被 Aliyun WAF anti-bot 拦截的问题彻底解决.

### Fixed — SSE NaN/Inf 静默吞包

`_safe_json_dumps()` 递归把 `float('nan') / inf / -inf` 替换成 `None`. 浏览器
`JSON.parse('NaN')` 抛 SyntaxError → 整个 SSE event 被吞 → 立昂微 SH605358 速览
卡永不渲染 (pe=NaN). 现在 NaN-safe, brief 卡稳定出来.

### Fixed — bull/bear 空 thesis_bullets

`BullOutput.thesis_bullets: List[str] = []` + 无 prompt 最低约束 → LLM 弱多/弱空
案例诚实地返回空数组 → Pydantic 接受 → 报告里两段全空. 修法:

- prompt 加 `# REQUIRED OUTPUT CONSTRAINTS` 段, 强制 ≥2 条且 `[V#]/[F#]` 锚点开头
- `_execute()` 加 retry loop: 检测空 → 一次激进重发 (温度 +0.2 + explicit feedback) →
  仍空才 `[V0]/[F0]` 占位兜底
- 验证: 茅台 (综合 -1 看空) bull 也给出 3 条 `[V4/V1/V9]` 逆向 bullet, 不再空

### Docs — 大规模整理

新增:
- `docs/architecture/14_agents.md` — 4 tier × 14 agent 完整 DAG + I/O schemas
- `docs/api/sse_endpoints.md` — buddy SSE bridge 全部 21 endpoint 详解
- `docs/setup/zero_to_report.md` — 从零到第一份研报 60 min walkthrough
- `docs/setup/data_pipeline.md` — G:/stocks 数据流 + 单一入口原则
- `docs/ui/guanlan_user_guide.md` — GuanLan UI 用户手册
- `start.bat` / `start.sh` / `stop.bat` / `stop.sh` — 一键启动后端 + 前端

更新:
- `README.md` — 13 agent 三 tier → 14 agent 四 tier
- `docs/architecture.md` — 头部加 redirect 指向 14_agents.md
- `docs/byom.md` — 13 built-in → 14 built-in

### Audit
- 全仓库 grep `"13 sub-agents" / "13 built-in" / "13 single-stock" / "13 agents"`
  统一改 14 (生产代码 / 文档 / 测试). 历史 CHANGELOG / journey 描述特定版本的
  保留 13 不动.

---

## v1.9.4 — 2026-05-22

### Fixed — /models 返回扁平数组 (校对桌面 UI 接线发现)

校对 design 接好的前端时发现: `/models` 返回 `{provider: [models]}` 对象,
但前端模型 picker 期望扁平数组 → picker 列表空. 改 `/models` 返回
`models: [{id, name, provider}]` (扁平) + `by_provider` (保留分组).

(配套前端修复: adapter `_runBackend` 之前漏传 `model` 字段, 已在前端补上.)

### Tests
test_models_endpoint 改验证扁平数组 + by_provider.

## v1.9.3 — 2026-05-21

### Added — UI 对齐后端端点 (多轮 / 模型 / 盯盘)

桌面 UI 接入后审计发现几处 UI 连的还是 mock. 后端先把缺的能力/端点补齐
(前端接线见 INTEGRATION.md):

- **多轮 session 历史**: `/run` 加 `session_id` —— server 按 session 复用
  BuddyAgent (LRU 24), `messages` 累积. 之前每次 /run 新建无记忆, "它同行呢"
  这种追问断. 现在带 session_id 就有上下文.
- **模型切换**: `/run` 加 `model` 参数 (live 切 agent LLM) + `GET /models`
  列可用模型 (前端 picker).
- **盯盘端到端**: `GET /alerts` (读 alerts.yaml 规则列表) + `GET /alerts/check`
  (Tencent batch 评估一次, 返回触发的, honour 交易时段). UI 轮询 /alerts/check
  → 真 toast (替换前端 45s 假触发).

`run_report` 真结果 (tool_done) + confirm (confirm_request/confirm) 后端早已就绪.

### Tests (test_server.py +5, 70 total)
/models · /alerts 列表 · /alerts/check 交易时段空 + 盘中触发 · 多轮 session 复用.

## v1.9.2 — 2026-05-21

### Added — 腾讯批量行情 (支撑 UI 实时监控墙)

opencli (浏览器) 抓单只 2-5 秒, 撑不住大量实时监控. 加轻量行情源:
`TencentQuoteCollector` (qt.gtimg.cn) —— 一次 HTTP 拉几十只, **实测 ~120ms**,
GBK 解码, 无需 cookie. 解析 18 个字段 (price/change/pe/pb/量比/换手/振幅/
流通+总市值…).

#### 新增/改造
- `data/collectors/tencent_quote.py` — 批量行情, code 三格式兼容 (600519/
  SH600519/sz000858) + 输入 code 别名 (调用方任意格式都查得到)
- `quote_batch(codes)` buddy tool — 逗号分隔批量, 给"看这几只"/对比/监控墙
- `realtime_quote` 改: **优先腾讯** (无 cookie, 快 30×), 失败 fallback 雪球
- `brief_data` 改: 速览卡主源换腾讯 —— 没 cookie 也能填满
  price/pe/pb/量比/换手/振幅/市值 (之前要雪球 cookie)
- **盯盘 watch loop 批量化**: `evaluate_batch` 一次拉所有 alert 的 code
  (~120ms), 不再受 v1.9.1 的 8 只上限 / opencli 单只瓶颈
- SSE 服务加 `GET /quotes?codes=...` — UI 监控墙轮询端点

#### 实时监控能力 (现在)
- UI 监控墙 / 盯盘 / 多股对比 → 腾讯批量, 几十只 120ms, 高频可刷
- 深度数据 (选股/评论/资金流榜/研报/F10) → opencli (低频, 几秒可接受)

### Tests (test_tencent_quote.py 13 + 更新 server/alerts/brief, 117 total)
字段解析 / code 转换 / 输入别名 / evaluate_batch 单次拉全 / /quotes 端点.

### Smoke (真跑)
```
GET /quotes?codes=SH600519,300750,sz000858 → ok, 120ms
  茅台 1311 -0.3% PE19.85 量比0.77 · 宁德 411.63 PE24.11 · 五粮液 85.35
```

## v1.9.1 — 2026-05-21

### Fixed — 盯盘成本防御 (防 opencli/Chrome 被拖垮)

opencli 抓单只实时价 2-5 秒 (Chrome 导航). 用户 alert 设多了, watch loop
一轮串行抓 N 只会很慢甚至打爆 Chrome session. 加两道防护:

- **同 code 去重**: `evaluate` 一轮内每个 distinct code 只调一次
  quote_provider — 同股多条规则 (price_below + price_above) 复用一次抓取.
- **distinct code 上限** (`max_codes=8`): 一轮最多评估 8 只不同股, 超出本轮跳过.
  watch loop 超限时 transcript 一次性提示 (不每轮刷屏).

`distinct_codes(store)` 辅助函数. 这是纯防御, 不引入新数据源 —— 大量实时
监控的正解 (批量行情 API) 见 backlog.

### Tests (3 new in test_alerts.py, 46 total)
同 code 去重只抓一次 / 12 alert 截到 8 / distinct_codes 去重.

## v1.9.0 — 2026-05-21

### Added — 桌面 UI 接入 (觀瀾 Tauri app) via HTTP/SSE 桥

把 buddy agent 包成 SSE 服务, 让觀瀾桌面前端 (用户用 claude design 做的
Tauri+JSX 原型) 能驱动真 agent 而非 mock. 接入 gap 分析见
`docs/UI_INTEGRATION_GAP.md` (用户审核后选定方案: 加 B2/B3/B4, B1/B5/B6 降级).

#### A1: `financial-analyst serve` SSE 服务 (`buddy/server.py`)
FastAPI `/run` 把 `BuddyAgent.run_turn` 事件流映射成 SSE:
`plan` / `tool_start` / `tool_done` / `brief` / `answer_progress` /
`confirm_request` / `done` / `error`. `/confirm` 双向解决 permission
future (mode=default/safe 时). `/health` `/tools` 辅助端点. CORS allow-all.
依赖 fastapi+uvicorn 放 optional `[serve]` (lazy import, 不污染核心).

CLI: `financial-analyst serve --port 9999`.

#### B2: 规则意图分类 (`buddy/intent.py`)
8 类 (brief/fundflow/why_move/compare/technical/alert/news/screen) 纯正则,
给 UI 每轮工具链一个中文标题 (资金流扫描/驱动归因…). 纯展示, 不影响 LLM 选工具.

#### B3: stock_brief 结构化输出 (`brief_data()`)
速览卡需要 JSON 字段 (price/change/deltaPct/pe/pb/turn/amp/mc/industry/
main_in/prev_main_in…). `stock_brief` 现在把结构化 dict 放进
`ToolResult.side_effect["brief"]`, server 转成 `brief` SSE event.
`normalize_code()` 处理桌面端的裸 6 位代码 (300750→SZ300750).
主力分解/xq_bull 留空 (B6 跳过).

#### B4: §N 引用标注
system prompt 要求 LLM 总结时关键数据点后标 `[§N]` 对应本轮第 N 个工具,
前端渲染成印章脚注. `run_turn` 的 tool_result event 现在带 side_effect.

### 接入降级 (用户审核 = 不做的部分)
- B1 预规划整链 → 边跑边 append (后端流式忠实, 前端 reducer 动态增长 chain)
- B5 run_report 真进度 → 前端假进度条
- B6 雪球多头% → 卡片字段留空

### 前端改动 (在 finance_analyst_integrated.zip)
`agent-adapter.jsx` 加 `_runBackend` (fetch SSE + 回退 mock);
`app.jsx` reducer 动态 append chain; `index.html` 加 `window.GUANLAN_BACKEND`
开关. 详见包内 `INTEGRATION.md`.

### Tests (test_server.py 7 + test_brief_data.py 7, 156 buddy total)
SSE 全流程 / 意图分类 / confirm gating / 健康检查 · code 规范化 /
结构化字段 schema / side_effect 挂载.

## v1.8.3 — 2026-05-21

### Fixed — 中文表格列错位 (CJK 宽字符对齐)

buddy tool 的表格输出 (ths_fund_flow 4 target / watchlist / fund_holdings /
concept_board rank) 用 f-string `:<N` 按**字符数**补齐, 但中文每字占 2
显示列、算 1 字符 — 导致中文名列和数字列混排时下一列起始位错位 (名字
长度不同的行尤其乱).

**修复**: 加 `_disp_w()` (CJK/全角算 2 列) + `_pad()` (按显示宽度左对齐),
替换全部 6 处表格的 `:<N`. 验证: 三行不同长度中文名的列边界显示位置
完全一致 `[8, 20, 28, 37]`.

### Tests (4 new in test_buddy_improvements.py)
_disp_w CJK 计数 / _pad 显示宽度对齐 / 不截断超长 / 中英混排等宽.

114 buddy/collector/alert tests pass.

## v1.8.2 — 2026-05-21

### Fixed — confirm modal 没有常驻指示器 (headless self-test 发现)

跑 `selftest_tui.py` (headless 构建真 Application 走 22 条交互路径) 发现:
permission modal 等 y/n 时只在 transcript 里写一行, 一旦后台 watch 提醒
弹进来或 transcript 滚动, 用户就看不到"在等确认", 会以为 agent 卡死.
跟 v1.6.3 修过的"看不到队列"同源 — 当时给 queue 加了常驻指示器, 但
v1.7.4 加 confirm modal 时漏了.

**修复**: 加 `confirm_window` (ConditionalContainer, filter = confirm
pending). 等 y/n 时输入框上方常驻红色:
`⚠ 等待工具确认 run_report — y 同意 · n 拒绝 · a 总是 · ESC 取消`

顺手优化 confirm 等待时输入非 y/n 的 reprompt 文案 — 明确提示"正在等
确认, 请 y/n 或 ESC 取消", 避免用户以为在排队新任务.

### Self-test 结果
22/23 交互路径通过. 唯一"未过"的是 "confirm 等待时输入非 y/n 被
reprompt 而非排队" — 这是 by-design (confirm 优先), 不是 bug.

验证覆盖: 全部 render callback 不崩 / default·safe·auto 三模式 modal /
watch 提醒与 confirm 并存不互相破坏 / ESC during confirm 干净取消 /
'a' 缓存 / status line.

### Tests (3 new in test_buddy_modes.py)
confirm indicator 渲染 tool 名+ESC / markup 转义 / 可见性跟随 pending.

## v1.8.1 — 2026-05-21

### Fixed/Improved — 把现有功能打磨到位 (质量/正确性/卫生)

**1. system prompt 工具引导更新 (最高 ROI)**
`agent.py` 的行为引导停在 v1.5.4 (13 tools), 现在 26 tools 但 LLM 缺
"何时用哪个" 的引导. 重写:
- 加 **tool routing cheat-sheet** (表格): 宽泛问题 → `stock_brief` (不再手动串 5 个);
  盘中价 → `realtime_quote`; 日线估值 → `quote_lookup`; 盯盘 → `alert_add`;
  资金流/概念/行业/大单 → `ths_fund_flow` 各 target; 选股 → `iwencai_search`; ...
- 加 **数据时效响应**: 看到 "⚠ 数据偏旧" 提示用户先刷新
- news workflow 加 ths-hot / xueqiu-hot-posts / xueqiu-feed
- tool_list 仍动态枚举全 registry
直接修工具选择质量 — 之前 LLM 不知道 stock_brief 存在, "看下茅台怎么样"
还在串 quote+chain+news 打 max_tool_iters 上限.

**2. 盯盘交易时段感知** (`market_session()`)
A 股时段分类 (open/lunch/closed/weekend). watch loop 非交易时段跳过评估
(收盘后/周末不空跑, 不拿收盘价误报, 省 opencli 调用). status line 显示
`👁 盯盘 5m (交易中/午休/已收盘/休市)`. `/watch on` 在非交易时段提示会
等到开盘. (节假日不建模, 靠实时 market_status 兜底.)

**3. 修预存失败 test_loader_factory**
`test_factory_missing_config_falls_back_to_tushare` 从 v0.2.1 起红 — 根因是
v1.5.2 的 find_config 5 级 fallback 让"explicit path 不存在"仍能找到 bundled
config (default: qlib_binary), 推翻了 test 的前提. 改 test 用 monkeypatch 让
find_config 抛 FileNotFoundError, 验证真正的 last-resort tushare fallback.
**整个 repo 测试现在全绿.**

### Tests
- system prompt × 4 (routing/cheatsheet/staleness/full-list)
- market_session × 8 (weekend/open/lunch/closed/before-open/is_trading/status-line)
- loader_factory 修复后 6/6 过

全 repo 测试通过 (之前唯一的预存 fail 已修).

## v1.8.0 — 2026-05-21

### Added — 盯盘提醒 (price alerts) + 实时行情 + 后台 watch loop

chat 里现在能盯盘了. 加 alert → /watch on → 后台每 N 分钟评估 → 触发的
弹进对话流.

#### 实时行情 (`realtime_quote` tool + XueqiuStockCollector)

`opencli xueqiu stock <code>` 拿盘中实时价/涨跌幅/OHLC/量额/换手/市值/
盘口状态 (交易中/已收盘). 区别于 `quote_lookup` (Tushare/Qlib 日线 EOD).
也是 alert 引擎的 price provider.

#### Alert 引擎 (`buddy/alerts.py`)

纯 Python, 规则存 `~/.financial-analyst/alerts.yaml`:

| kind | 触发条件 |
|---|---|
| `price_below` | 现价 ≤ 阈值 (止损位/抄底位) |
| `price_above` | 现价 ≥ 阈值 (止盈位/突破位) |
| `pct_above` | 当日涨幅 ≥ 阈值 (如 +5%) |
| `pct_below` | 当日跌幅 ≤ 阈值 (如 -5%) |

- 自然键 `{code}:{kind}` — 同 code 同 kind 再加就更新阈值, 不重复
- cooldown 30 分钟防抖 — 卡在阈值边的票不会每 tick 刷屏
- `evaluate(store, quote_provider, cooldown_min)` 注入式 price provider, 可测

#### Alert buddy tools (3 个)

- `alert_add(code, kind, threshold, note)` — LLM 听懂 "茅台跌破1200提醒我" → `alert_add(SH600519, price_below, 1200)`
- `alert_list()` — 列所有提醒
- `alert_remove(rule_id)` — 删 (支持 `SH600519:price_below` 或只给 `SH600519` 删该股全部)

#### 后台 watch loop + `/watch`

```
/watch                          # 状态
/watch on                       # 盯盘, 默认 5 分钟间隔
/watch on 3                     # 3 分钟间隔
/watch on 5 ths-fund-flow,xueqiu-hot
                                # 5 分钟 + 每轮先采集这些源 (数据保鲜)
/watch off
```

- 后台 asyncio task, 每 `watch_interval` 秒: (可选采集 sources) → 评估
  alert → 触发的 `🔔 盯盘提醒` 弹进 transcript
- 实时价抓取 + 评估都走 `asyncio.to_thread`, 不阻塞 UI / 输入
- status line 显示 `👁 盯盘 5m`
- chat 退出时自动 teardown watch task

**这同时覆盖了 scheduled-collect**: `/watch on 5 <sources>` 让数据每 5
分钟自动刷新, 配合 v1.7.5 的时效警告, 数据 always fresh.

### Tests (35 in test_alerts.py, 147 buddy total)

parse_pct · 4 种 rule check · store CRUD + 自然键 upsert + 持久化 +
跳过非法条目 · evaluate 触发/不触发/cooldown/provider 失败/none quote ·
3 alert tools + realtime_quote 注册 · watch off-by-default / status /
无 loop 报错 / on 解析 interval+sources / status line / _eval_alerts 接线

### Smoke (真跑)
```
realtime_quote SH600519 → 贵州茅台 1311 -0.30% [已收盘]
alert: SH600519 跌破 99999 → 触发, quote price=1311
```

## v1.7.5 — 2026-05-21

### Improved — 6 项接口体验补强 (按 ROI 全做)

针对 v1.7.4 review 列出的缺口, 一次补齐:

**1. 数据时效警告** (`_news_staleness_note`)
`news_query` 返回前检查最新一条 ts, 超 18h 在结果顶部插
`⚠ 数据偏旧: 最新一条是 ... (N 小时前)`. LLM 不会再拿隔夜情绪当今日动态.

**2. `/mode` + `/model` 持久化**
permission_mode + provider/model 存 `~/.financial-analyst/buddy.yaml`,
启动自动恢复. 切换时即时落盘 (提示 "已保存"). 失效 model 自动忽略.
测试用 conftest autouse fixture 把 prefs path 重定向到 tmp, 不污染真实环境.

**3. ths-extra plugin 友好安装提示** (`ThsExtraNotInstalled`)
iwencai / fund-flow / concept-board 在 plugin 未装时, 抛带可复制命令的
异常: `opencli plugin install file://<bundled path>`. 区分 "opencli 没装"
(给 npm 命令) vs "plugin 没装" (给 install 命令) vs 其他运行时错误 (原样透出).

**4. LLM token 累计 + status 显示**
`LLMClient.chat` 累加 `usage.prompt_tokens/completion_tokens`,
`with_overrides()` 切模型时计数延续. status line 显示
`🪙 2.3k tok (↑1800 ↓500, 4 calls)`. 切到 claude-opus 后知道烧了多少.

**5. fund-flow 跨日 diff** (`fund_flow_change` tool)
比较一只股/板块在多个 snapshot 的主力净流入变化. `_parse_cn_amount`
解析 "1.69亿"/"−3254万" → float, 算最近两点 delta + 趋势箭头 (↑增/↓减).
需先多次采集积累快照.

**6. `stock_brief` 统一视角 tool** (高频入口)
一次返回 行情+行业+产业链+近期新闻+雪球情绪+资金流+上次研报. 用户问
"看下 X 怎么样" 这种宽泛问题时优先用它, 省掉 5 个 tool round-trip,
避免打 max_tool_iters 上限. 全读本地 cache + 快 loader, 每段独立 try
(一个源挂不影响整体). 注册在 quote_lookup 之后, prompt 引导 LLM 优先选.

### Tests (23 new in `test_buddy_improvements.py`, 550 passing total)

staleness × 5 · 持久化 × 4 · token × 4 · fund-flow diff × 7 · stock_brief × 3
+ ths-extra install hints × 4 (in test_ths_extra.py)

(唯一失败的 `test_loader_factory` 是 v0.2.1 起的预存失败, 与本次无关.)

## v1.7.4 — 2026-05-21

### Added — permission modes + model picker (Claude-Code-style)

参考 Claude Code 的 `/model` + permission modes, 给 buddy 加上三档安全
模式和 LLM 切换. 现在 chat 启动后输入框上方有一行 status:

```
  🛡 default · qwen3.5-plus (qwen)
```

或处于 safe / auto 时:

```
  🚦 safe · qwen3-max (qwen)
  ⚡ auto · claude-opus-4-7 (anthropic)
```

#### Permission modes (`/mode`)

| Mode | 行为 |
|---|---|
| `default` (默认) | instant/seconds 级工具自动跑, **minutes 级** (`run_report` / `alpha_bench`) 弹 y/n |
| `safe` | **每个工具调用前都问 y/n** — 适合审计 agent 行为 / 评估新 prompt |
| `auto` | 全部自动通过, 不弹任何提示 — 用于完全信任 prompt+model 时 |

切换: `/mode safe` / `/mode auto` / `/mode default`. 无参 `/mode` 列
当前 + 说明.

#### Y/N modal

工具确认时输入框临时切换为 y/n 输入. 接受:
- `y` / `yes` / `是` / `同意` → 同意
- `n` / `no` / `否` / `取消` → 拒绝
- `a` / `always` / `总是` → 同意 + **本会话内该工具自动通过** (累积进 `_auto_approved` set)

未识别响应 (e.g. `maybe`) → 不消费 future, 重新提示. 期间按 ESC → cancel
future, agent turn 整体回退.

#### Model picker (`/model`)

切 LLM provider / model 无需重启:

```
/model                           # list available
/model qwen3-max                 # bare name (lookup across providers)
/model anthropic/claude-opus-4-7 # explicit provider/model
```

底层调 `LLMClient.with_overrides()` 重建 client (config 不重新加载, 只换
provider+model 字段). 当前活跃 agent (`BuddyApp.agent`) 立即用新 client.

#### LLMClient API

- `LLMClient.with_overrides(provider=None, model=None) → LLMClient` —
  return a new client sharing the same config but with different
  provider/model.
- `LLMClient.list_models() → Dict[str, List[str]]` — `{provider: [models]}`
  taken from `llm.yaml`'s `providers.*.models`.

### Tests (20 new in `test_buddy_modes.py`, 132 total)

- mode default / show / switch / reject-unknown / auto-clears-approvals
- model show / switch (bare name + provider/model) / reject-unknown
- `_confirm` × 6: auto bypass / default-instant passes / default-minutes prompts /
  safe-instant prompts / `a` caches in `_auto_approved` /
  unrecognised response keeps future pending / ESC cancels future
- `_on_submit` routes to confirm handler when pending
- status line includes mode + model + auto-approved tools

### UX details

- Banner advertises `/mode` and `/model` next to slash commands
- `/help` shows full mode descriptions
- Status line uses different icon+color per mode (🛡 cyan / 🚦 yellow /
  ⚡ magenta) so it's eyeball-distinguishable

## v1.7.3 — 2026-05-21

### Added — 同花顺资金流补全 (3 个 target + reusable adapter)

`ths-extra fund-flow` 重写为 header-driven, 用一个 adapter 覆盖 4 个
leaderboard:

| `--target` | URL | 数据 |
|---|---|---|
| `gegu` (默认) | `/funds/ggzjl/` | 个股资金流 (主力净流入排行) |
| `gainian` | `/funds/gnzjl/` | **概念板块涨幅+资金流** (替代之前的 concept-board rank) |
| `hangye` | `/funds/hyzjl/` | **行业板块涨幅+资金流** |
| `ddzz` | `/funds/ddzz/` | **大单追踪** (实时大额成交流水, 时间/方向/价/量) |

Adapter 改用 thead 表头 + alias 表识别列, 而不是 hardcoded 索引 — 不同
target 列数 / 顺序差异自动适配:

```js
const codeIdx = idxOf('代码', '板块代码');
const nameIdx = idxOf('简称', '名称', '行业', '概念', '板块');
const priceIdx = idxOf('最新价', '现价', '指数', '当前价', '成交价格', '价格');
// ...
```

`gainian`/`hangye` 列里没显式 board code, 从 row anchor href
(`.../code/309152/`) 提取. ``--`` placeholder 行自动 drop.

### Python 侧

- `THSFundFlowCollector.fetch(target=...)` 接受 4 个 target. ValueError on invalid.
- DB schema migration (`_migrate()` in `NewsDB.__init__`): legacy
  `ths_fund_flow` 表自动 `ALTER TABLE ADD COLUMN` 加 target / num_stocks /
  leader / trade_time / direction / volume / url / raw_cells. 新 PK 是
  `(snapshot_ts, target, code, name)` 让 4 个 target 数据不互相覆盖.
- `query_ths_fund_flow(target=...)` filters by leaderboard kind.

### CLI sources (4)

```powershell
financial-analyst news-collect --sources ths-fund-flow      # gegu
financial-analyst news-collect --sources ths-concept-fund   # gainian
financial-analyst news-collect --sources ths-industry-fund  # hangye
financial-analyst news-collect --sources ths-large-orders   # ddzz
```

### Buddy tool

`ths_fund_flow(target, limit, refresh)` — LLM 可按问题语义自动选 target:
- "今天主力买什么" → `gegu`
- "概念主线在哪" → `gainian`
- "行业涨幅排行" → `hangye`
- "盘中大单方向" → `ddzz`

Per-target output format (列宽 / 显示字段) 区分.

### Tests (7 new in `test_ths_extra.py`, 112 total)
- `test_fund_flow_target_passes_through` (parametrised 4 targets)
- `test_fund_flow_target_rejects_invalid`
- `test_fund_flow_gainian_drops_placeholder` (`--` sentinel handling)
- `test_upsert_ths_fund_flow_target_partition` (4 target rows 不互相覆盖)

### Smoke

```
news-collect --sources ths-fund-flow,ths-concept-fund,ths-industry-fund,ths-large-orders
→ 各 5 行入库
LIVE:
  gegu: 龙腾光电 +20% 主力净流出 -3254万
  gainian: AI眼镜 +0.21% 主力净流出 -64.07亿 公司家数 186 领涨股 纬达光电
  hangye: 化学制药 +2.48% 主力净流出 -61.16亿
  ddzz: [11:29:59] 中芯国际 卖盘 3800股 50.55万
```

### 仍 backlog
- `concept-board mode=rank` — 之前的 URL 路由 placeholder, 现在 gainian
  替代它, 这个 backlog 可以正式 drop
- 股吧评论 — 同花顺已无, drop
- ggzjl 默认页超大单/大单/中单/小单细分 — 同花顺没在排行页提供, 在个股详情页有, 后续如需可加

## v1.7.2 — 2026-05-21

### Added — 同花顺扩展接口 (ths-extra opencli plugin)

opencli upstream 的 `ths` site adapter 只有 `hot-rank` 一个命令. 本仓库
新增 `opencli-plugin-ths-extra/` 自带 plugin, 用户一次 install 后获得
三个额外命令:

- `ths-extra iwencai <question>` — 问财自然语言选股
- `ths-extra fund-flow` — 个股资金流主力净流入排行
- `ths-extra concept-board --mode new` — 新概念发布表 (日期+股票数)
- (`--mode rank` 涨幅榜 URL 待验证, 留 backlog)

**安装**:
```powershell
opencli plugin install file://G:\financial-analyst\opencli-plugin-ths-extra
opencli plugin list   # confirms ths-extra registered
```

### Python 接入

新 collectors (`data/collectors/opencli/ths_extra.py`):
- `IWencaiCollector(question, limit)`
- `THSFundFlowCollector(page_no, limit)`
- `THSConceptBoardCollector(mode, limit, page_no)`

新 NewsDB 表 + upsert / query:
- `iwencai_results(snapshot_ts, question, row_index, columns, cells)` — 问财结果, schema 动态所以 cells 用 pipe-join 存
- `ths_fund_flow(snapshot_ts, code, name, price, change_pct, turnover_pct, inflow, outflow, main_net, total_amount)` — 主力资金流快照
- `ths_concept_boards(snapshot_ts, mode, board_code, board_name, release_date, num_stocks, change_pct, board_url)` — 概念板块

新 CLI sources: `ths-fund-flow`, `ths-concept-board` (后者 `--code rank` 切涨幅榜模式).

新 buddy tools (3 个, 已注册到 TOOL_REGISTRY):
- `iwencai_search(question, limit, use_cache)`
- `ths_fund_flow(limit, refresh)`
- `ths_concept_board(mode, limit, refresh)`

### 同花顺股吧不接 (路径死胡同确认)

`t.10jqka.com.cn/list/{code}/` 返回 404 — **同花顺现已无股吧功能**.
A 股股吧只剩东方财富 (`guba.eastmoney.com`). 暂不接入, 散户情绪走
xueqiu-comments 覆盖.

### Tests (13 in test_ths_extra.py, 105 total)
- collectors 正常解析 / 缺 code 过滤 / 参数传递
- 入库 + 查询 + latest-snapshot 语义
- buddy tools 缺 plugin 时 graceful error + use_cache 跳过 opencli

### Smoke
```
news-collect --sources ths-fund-flow,ths-concept-board → 各 5 行入库
LIVE: 龙腾光电 +20.04% 主力净流出 -3210万 (游资行情)
LIVE: 2026一季报预增 / AI应用 / 雅下水电概念 等新概念
```

### Known limitations
- iwencai 结果表用 div 不用 thead, 我们的 columns 字段经常为空, 但 cells 完整
- concept-board mode=rank URL 不正确 (返回 placeholder), 留待 q.10jqka.com.cn 改版后 re-probe
- fund-flow 默认页只有 10 列简版, 没有超大单/大单/中单/小单细分 (在 /funds/zlls/ 上, 后续可加)

## v1.7.1 — 2026-05-21

### Added — 全面接入雪球 (opencli 12 个命令里的剩余 9 个之 6)

**批 1 — 资讯/情绪 (2 个)**:
- `xueqiu-feed` — 关注用户的时间线动态 → `news` 表, source=`xueqiu_feed`
- `xueqiu-hot-posts` — 雪球平台热门帖子 (不同于 hot-stock 是热门标的) → `news` 表, source=`xueqiu_hot_posts`

帖子里的 `$名字(SH600519)$` 现金标签会被 regex 解析到 `related_codes`,
所以 `news_query --code SH600519` 能搜出 mention 茅台的帖子.

**批 2 — 个人账户 (4 个 collector, 3 个 buddy tool)**:

新 4 张表 (schema 在 news_db.py 顶部):
- `user_watchlists(snapshot_date, group_pid, symbol, name, price, change_percent, url)`
- `user_groups(snapshot_date, pid, name, count)`
- `fund_snapshots(snapshot_date, account_id, account_name, total_assets, available_cash, daily_gain, hold_gain)`
- `fund_holdings(snapshot_date, account_name, fd_code, fd_name, market_value, volume, daily_gain, hold_gain, hold_gain_rate, market_percent)`

新 CLI sources:
- `xueqiu-watchlist` (默认 pid=-1 全部, `--code -5` 切沪深 / `--code -7` 港股 / `--code -4` 模拟)
- `xueqiu-groups` — 分组结构
- `xueqiu-fund` — 一次拉 snapshot + holdings (需要蛋卷 cookie, 失败时给清晰提示)

新 buddy tools:
- `watchlist_show(pid, refresh)` — 看自选股
- `fund_snapshot(refresh)` — 看蛋卷总资产
- `fund_holdings(account, refresh)` — 看蛋卷持仓明细

### Tests
- 12 in `test_xueqiu_feed_hot.py` — feed/hot collectors + cashtag regex
- 16 in `test_xueqiu_watchlist_fund.py` — watchlist / groups / fund-snapshot / fund-holdings + 入库 + 查询 + buddy-tool empty-cache + graceful-error paths

92 buddy/collector tests pass total.

### Smoke-tested 真跑
```
news-collect --sources xueqiu-feed,xueqiu-hot-posts → 各 5 行入 news 表
news-collect --sources xueqiu-watchlist,xueqiu-groups → 6 + 6 行入新表
news-collect --sources xueqiu-fund → 给出 "log in to danjuanfunds.com" 提示 (用户未登)
```

### 同花顺现状

opencli `ths` adapter 当前只有 `hot-rank` 一个命令 — 已在 v1.7.0 接入.
深度接入 (问财 / F10 / 概念板块 / 资金流) 需要走自写 collector 路线,
等用户拍板再做.

## v1.7.0 — 2026-05-21

### Added — 同花顺 hot-rank data source

New collector `THSHotRankCollector` wraps opencli's `ths hot-rank`
adapter (browser bridge against `eq.10jqka.com.cn`). Public — no
cookie, no Chrome extension required. Pulls the 同花顺 retail-heat
leaderboard with rank, name, changePercent, heat, and tag string
("3天2板,光伏概念,固态电池" — useful sentiment signal that xueqiu-hot
doesn't carry).

**Caveat**: THS frontend renders by name only; the JSON has no
explicit `code` field. We best-effort extract the 6-digit code from
the leading position of `tags` (occurs on ~60% of rows in observed
samples). Rows without a code-prefixed tag still land in the DB
(name + heat suffice for downstream lookup).

### Wired into

- CLI: `financial-analyst news-collect --sources ths-hot --limit N`
  → writes to `hot_stocks` table with `source='ths_hot_rank'`
- Buddy tool: `news_collect` description now mentions `ths-hot` as a
  public no-cookie option; LLM can pick it autonomously when the user
  asks 同花顺 / 热股榜 / retail heat
- `data/collectors/opencli/__init__.py` exports `THSHotRankCollector`

### Tests (9 in tests/test_ths_hot_rank.py, 64 total)
- Code extraction edge cases (leading whitespace, missing, partial digits)
- JSON normalisation (opencli raw → {rank, code, name, ...})
- Empty / None opencli response handling
- Schema contract with `NewsDB.upsert_hot_stocks`

### Smoke-tested

```
> financial-analyst news-collect --sources ths-hot --limit 10
Collected:
  ths_hot_rank: 10 rows
```
Verified 10 rows written to `hot_stocks` with `source='ths_hot_rank'`,
6/10 had codes extracted from tags.

## v1.6.9 — 2026-05-21

### Fixed — `run_report` and other CLI tools crashed with WinError 3

User report: ran `run_report SH600666` from `chat`, got
`FileNotFoundError: [WinError 3] 系统找不到指定的路径: 'memories'`.

**Root cause**: every buddy tool that wraps a CLI command via
`subprocess.run([...])` was inheriting the chat session's cwd. The
CLI was originally designed to run from inside the project checkout
where `memories/`, `out/`, `config/` live as siblings of `src/` — so
its internal `Path("memories")` lookups crashed when launched from
any other directory.

**Fix**: added `_project_root()` helper in `buddy/tools.py` that
locates the repo root via:
1. `$FINANCIAL_ANALYST_HOME` env var if set
2. Parent of `src/` (works for editable installs — the common case)
3. Cwd as last-resort fallback

All six subprocess-wrapping tools (`run_report`, `news_collect`,
`alpha_bench`, `alpha_snapshot`, `mainline_radar`, `morning_brief`)
now pass `cwd=str(_project_root())` to `subprocess.run`. The output
path glob `Path("out").glob(...)` in `_tool_report` is now also
rooted at `_project_root()`.

In-process `_tool_dream_review` had the same bug at its own level
(`Path("memories") / "_proposed"`) — now `_project_root() / "memories" / "_proposed"`.

Other in-process loaders (`ChainKBLoader`, `StockTimelineLoader`,
`IndustryLoader`, `NewsDB`) already used `Path.home() / ".financial-analyst" / ...`
so they were unaffected.

### Tests (10 new in test_buddy_cwd.py, 65 buddy tests total)
- `test_project_root_resolves_to_repo_root_in_editable_install`
- `test_project_root_uses_env_override`
- `test_project_root_falls_back_to_cwd_when_no_markers`
- `test_report_subprocess_uses_project_root_as_cwd`
- `test_other_subprocess_tools_also_set_cwd` (parametrised over 5 tools)
- `test_dream_review_uses_absolute_memories_path`

## v1.6.8 — 2026-05-21

### Fixed — mouse wheel showed input history, not transcript

User report: "我看不了之前回复的内容 转鼠标滚轮只有我发消息的历史记录"
— turning the mouse wheel cycled through previously-submitted prompts
instead of scrolling the LLM's earlier responses.

**Root cause**: Windows Terminal / cmd.exe (in prompt_toolkit's
alt-screen / full_screen mode) remaps wheel events to Up/Down keys
when no application-level mouse support is enabled. The keyboard focus
sits on the single-line TextArea, whose default behaviour for Up/Down
is `FileHistory.previous` / `.next` — so wheel scroll = browse input
history. PageUp/PageDown (added in v1.6.7) worked correctly but most
users instinctively reach for the wheel.

**Fix**: enable `mouse_support=True` on the Application and attach a
custom `_mouse_handler` to the transcript window that routes
SCROLL_UP/SCROLL_DOWN into `_scroll_history`. Default Window scroll
behaviour (`Window._mouse_handler`) calls `content.move_cursor_up()`
which `FormattedTextControl` doesn't implement, so it's a no-op
without this override.

Wheel step is 3 lines/notch (smaller than PageUp's full-viewport
step) so single notches feel responsive on long transcripts.

**Trade-off**: native click-drag selection is now intercepted by
prompt_toolkit. Windows Terminal users can still hold Shift while
dragging to copy text (Shift bypasses application mouse capture and
goes straight to the OS), and `/save <path>` dumps the transcript to
a plain markdown file.

### Self-tested (the user explicitly demanded this)

`selftest_v168.py` builds the real prompt_toolkit Application using
`create_pipe_input()` + `DummyOutput()` (so it runs headless), then
synthesises `MouseEvent(SCROLL_UP / SCROLL_DOWN)` and walks the full
state machine:
- mouse_support filter resolves to `Always` ✓
- transcript_window._mouse_handler is bound ✓
- SCROLL_UP returns `None` (consumed), enters history-browse ✓
- agent appends during browse don't shift top_line ✓
- SCROLL_DOWN past tail resumes follow_tail ✓

### Tests (4 new in test_buddy_app.py, 55 total buddy tests)
- `test_on_mouse_event_scroll_up_enters_history_browse`
- `test_on_mouse_event_scroll_down_eventually_returns_to_tail`
- `test_on_mouse_event_ignores_non_scroll_events`
- `test_wheel_step_is_smaller_than_pageup_step`

## v1.6.7 — 2026-05-20

### Fixed — auto-scroll worked, but user couldn't browse history

User report: "现在只能翻看历史对话 但是看不了 LLM 输出的内容了 这是一个大 bug"
— v1.6.6's fix made the transcript follow new appends correctly, but
the cursor was hard-pinned to the last line on every render. Pressing
PageUp / arrow keys snapped the viewport right back to the bottom,
making it impossible to read earlier output.

**Root cause**: pinning the cursor to `n_lines - 1` unconditionally
gave `do_scroll` no chance to honour user-initiated scroll moves. Any
keystroke that tried to change `vertical_scroll` was overruled by the
next render's cursor-driven snap-to-tail.

**Fix — follow-tail state machine.**

- `BuddyApp.follow_tail: bool = True` (default): cursor still pins to
  the last line so new content auto-scrolls in.
- `BuddyApp.follow_tail = False`: cursor pins to `top_line` instead;
  do_scroll aligns the viewport top with the cursor, so new appends
  below the viewport don't yank the user's scroll position.

**Keys (transcript window, single-line input box is unaffected)**:

| Key            | Action                                          |
|----------------|-------------------------------------------------|
| PageUp         | Scroll up one viewport; enter history-browse    |
| PageDown       | Scroll down; resume follow-tail past the end    |
| End / Ctrl-↓   | Jump to latest, resume follow-tail              |

When `follow_tail=False` a yellow `📜 浏览历史 (按 End / Ctrl-↓ 回到最新输出)`
hint shows above the input box so the user knows they're paused.

### Tests (7 new in test_buddy_app.py)
- `test_follow_tail_is_default`
- `test_pageup_drops_out_of_follow_tail_and_pins_top_line`
- `test_cursor_uses_top_line_when_not_following_tail`
- `test_appends_during_history_browse_do_not_change_top_line` (the
  exact bug — append must NOT yank top_line while user is browsing)
- `test_end_jumps_back_to_tail_and_resumes_follow`
- `test_pagedown_past_tail_resumes_follow`
- `test_history_browse_hint_visibility_state`

51 buddy tests pass total (28 app + 13 agent + 7 animation + 3 repro).

## v1.6.6 — 2026-05-20

### Fixed — v1.6.5 auto-scroll didn't actually work

User report: "还是没有了 也没滑动" — even after v1.6.5 shipped its
`get_vertical_scroll` callback, the transcript still stayed pinned at
the top. The `news_collect → news_query → (no LLM summary)` flow
ended with the `⚠ 完成 (调了 N tool 但没文字总结)` marker writing
into the transcript correctly, but the user never saw it.

**Root cause (the v1.6.5 fix was wrong)**: `prompt_toolkit.Window`
calls `get_vertical_scroll` *and then* runs `do_scroll()`, which
normalises `vertical_scroll` against `cursor_pos`. Since our
`FormattedTextControl` had `show_cursor=False` and no `get_cursor_position`,
the cursor sat at y=0, so `do_scroll`'s "scroll up if cursor is before
visible part" branch reset `vertical_scroll` from our returned offset
(say 80) back down to 0 every render. The callback fired; its return
value was silently overwritten.

(Verified by reading prompt_toolkit 3.0.52 `Window._scroll_when_linewrapping`
at lines 1107-1109: `if current_scroll > cursor_pos - scroll_offset_start: current_scroll = max(0, cursor_pos - scroll_offset_start)`.)

**Fix**: drop `get_vertical_scroll` entirely; instead pin an invisible
cursor to the last rendered line via
`FormattedTextControl(get_cursor_position=self._get_cursor_at_bottom)`.
`_get_cursor_at_bottom` returns `Point(0, transcript.count('\n') - 1)`.
`do_scroll`'s natural "scroll down to keep cursor visible" branch then
drives the viewport to follow new content automatically — no fighting
with the framework.

### Verified via integration self-test

`tests/test_buddy_v165_reproduce.py` reproduces the exact user scenario:
- LLM emits `news_collect` → `news_query` → empty content with no tool_calls
- Confirms the "调了 2 个 tool 但没文字总结" marker IS written to transcript
  (so v1.6.3's marker logic was always correct)
- Confirms `_get_cursor_at_bottom().y` advances with each append (so
  do_scroll will keep new content in view)

### Tests
- `test_cursor_pinned_to_last_line_empty_transcript`
- `test_cursor_pinned_advances_as_transcript_grows`
- `test_cursor_pinned_handles_multiline_chunks`
- `test_user_scenario_news_collect_then_query_then_no_summary`
- `test_cursor_position_tracks_transcript_bottom`
- `test_full_scenario_with_summary_works_too` (baseline sanity)

24 BuddyApp + repro tests pass (425/426 total tests; unrelated
loader-factory test still fails).

### Process note
Earlier iterations (1.6.0 → 1.6.5) shipped fixes without integration
tests that exercised the real prompt_toolkit rendering path. v1.6.5
"passed unit tests" because the unit test only verified arithmetic,
not whether the callback's return value survived `do_scroll`. v1.6.6
adds a test that watches the actual Point coordinate so the next bug
of the same shape would surface immediately.

## v1.6.5 — 2026-05-20

### Fixed — transcript pinned at top, never followed new appends

User report: "思考过程并没有继续滑动 就停了留在顶行了" — the
transcript window stayed showing the opening banner forever while the
spinner kept ticking and (presumably) new events appended invisibly
below the viewport.

Root cause: the prompt_toolkit `Window` containing the transcript had
no `get_vertical_scroll` callback, so the default behaviour kept the
scroll position at 0 (top). Every `_append_chunk` extended the
content but the viewport never moved.

Fix: added `_scroll_to_bottom(window)` callback computing
`max(0, content_height - window_height)` from the window's
`render_info`. Wired via `Window(..., get_vertical_scroll=_scroll_to_bottom)`.
prompt_toolkit calls it on every render; the result places the last
line of content on the bottom row of the viewport.

Edge cases handled:
- First render (`render_info is None`) → return 0, no crash
- Content fits window → return 0, no needless scroll
- Content exceeds window → return overflow amount

### Tests
- `test_scroll_to_bottom_returns_zero_when_no_render_info`
- `test_scroll_to_bottom_returns_zero_when_content_fits_window`
- `test_scroll_to_bottom_returns_offset_when_content_overflows`

21 BuddyApp tests pass; 39 buddy tests total (13 agent + 21 app +
7 animation).

## v1.6.4 — 2026-05-20

### Fixed — "思考着思考着就断了" (silent mid-turn drop-outs)

User reported turns ending unexpectedly with no clear reason and no
output text. v1.6.4 diagnoses + fixes the three actual root causes:

**1. Transient LLM-call failures killed the turn instantly.**
A single SSL hiccup / DashScope rate-limit blip / network burp would
raise an exception, the agent's `except Exception` block would yield
one `error` event and immediately `return` — the turn vanished and
the user only saw the error briefly before it scrolled away.

Fix: `BuddyAgent.run_turn` now retries the LLM call up to
`max_llm_retries=2` times with exponential backoff (0.8s, 2.4s). The
inner `for attempt in range(retries + 1)` loop catches transient
exceptions and tries again. CancelledError is re-raised so ESC still
works mid-retry.

If all retries are exhausted, the agent yields BOTH an `error` event
AND a `done` event so the BuddyApp finalizer can pick the right
end-of-turn marker:

```
✗ LLM 网络/API 错误 — 上面有详情. 检查: DASHSCOPE_API_KEY 是否
有效 / 网络是否通畅 / DashScope 服务是否正常.
```

Before v1.6.4 the agent would `return` without `done`, leaving the
BuddyApp's done-marker logic confused — usually emitting the
misleading "LLM 返回空响应" warning even when the real cause was a
network failure.

**2. `max_tool_iters=8` was too tight for complex queries.**
Cross-stock comparisons, chained `news_collect → news_query → summary`
flows, or "list peers and report each one's PE" routinely hit the
ceiling without producing closing text. The transcript filled with
tool calls then the turn ended silently.

Fix: default bumped from 8 → 15. The exhaustion message is also
clearer now (Chinese, names the actual remediation):

```
达到 tool 调用上限 (15 次). LLM 在工具循环里转圈, 没收敛到答案.
建议: 再问一句 '前面的结果总结一下' 让它写正文, 或换更具体的 prompt.
```

**3. End-of-turn markers now distinguish error categories.**
v1.6.3 lumped LLM-call-failed and LLM-said-nothing under "空响应".
v1.6.4 separates them:

- LLM call exhausted retries → ✗ LLM 网络/API 错误 (different fix)
- Hit max_tool_iters → ✗ 达上限退出 (different fix)
- LLM ran tools but no text → ⚠ 调了 N 个 tool 但没文字总结
- LLM truly empty response → ⚠ 完成 (LLM 返回空响应)
- Normal → ✓ 完成

BuddyApp's `_run_turn` finally block now tracks `last_error_msg` and
keys off substring matches to pick the marker.

### Tests
- `test_llm_failure_retries_then_yields_error_and_done`: 2 retries
  → 3 total LLM calls → both error and done events emitted.
- `test_llm_recovers_on_second_attempt`: 1st call fails, 2nd
  succeeds → no error event, normal completion.
- Existing `test_max_tool_iters_guard_breaks_infinite_loop` updated
  to match the new Chinese error message.

38 buddy tests pass total (13 agent + 18 app + 7 animation).

### API changes (backward compatible)

`BuddyAgent.__init__` adds:
- `max_tool_iters: int = 15` (was 8)
- `max_llm_retries: int = 2` (new)

Both are constructor args; existing callers pass 0 args or just
`max_tool_iters=N` and get the new defaults / can opt out of retries.

## v1.6.3 — 2026-05-20

### Fixed — three real UX issues found during user testing

**1. Persistent queue indicator above input.**
Previously the "已排队" notice only fired once into the scrolling
transcript. Once scrolled off-screen or after multiple submissions,
the user had no idea what was queued. v1.6.3 adds a permanent 1-row
indicator just above the input field:

```
  ⏳ 排队中 → 顺便看下五粮液行业   (再按 ESC 也可取消)
❯ █
```

Visible only when `queued_input` is set — disappears as soon as the
queued turn picks up or the user ESCs it away.

**2. Queue replacement notice.**
Submitting a second time while another prompt is already queued used
to silently overwrite the first one. v1.6.3 now prints a warning
into the transcript: `⚠ 之前排队的 <first> 被替换为新输入`.

**3. Turn completion signal + no-text-output warning.**
The biggest one. Previously when a turn ended, the spinner just
disappeared — no clear "done" marker. Worse, if the LLM kept calling
tools without writing closing narrative (which happens when it hits
`max_tool_iters=8`), the user saw lots of `▶/✓` tool lines but no
summary, and no indication that the turn ended at all.

v1.6.3 adds three explicit end-of-turn markers:

- `[green]✓ 完成[/]` — normal completion with text response.
- `[yellow]⚠ 完成 (调了 N 个 tool 但没文字总结) — 试试再问一句
  '前面的结果总结一下'[/]` — when text_count=0 but tools ran.
- `[yellow]⚠ 完成 (LLM 返回空响应) — 可能 prompt 太抽象, 换个具体
  问法?[/]` — empty response.

The agent's `_run_turn` finally block tracks text/tool/error counts
and picks the right marker. Cancelled turns still show
`[yellow]✗ 已取消[/]` as before (no spurious "完成" added).

### Architecture

- New layout row: `HSplit([transcript, spinner, queue, input])` where
  the queue row is also a `ConditionalContainer` (visible only when
  `queued_input is not None`).
- `_get_queue_ansi()` renders the indicator text with truncation at
  80 chars so it never wraps.

### Tests
- `test_queue_indicator_renders_queued_text`: queue row shows truncated text
- `test_queue_replacement_notice`: 2nd queue submission notes the swap
- `test_done_marker_on_successful_turn`: normal text turn → `完成`
- `test_warning_when_tools_but_no_text`: tools-only turn → tool-count warning

18 BuddyApp tests pass total. 36 buddy tests across the three suites.

## v1.6.2 — 2026-05-20

### Changed — ESC now peels off one layer per press (Claude Code-style)

User feedback on v1.6.1's "ESC clears everything" behaviour: too
aggressive. The preferred UX is "press ESC to back out one step at a
time", matching Claude Code's step-back semantics:

  - 1st ESC: cancel the currently running turn. The finally block
    drains the queue, so a queued prompt **does** start next.
  - 2nd ESC: cancel that newly-started turn too.
  - ESC repeatedly = peel off layers until you're at a clean prompt.

Why this is better than the v1.6.1 "stop everything" model:

- When typing during a long-running tool, the user is usually queuing
  a *follow-up* they want — cancelling the current to make space for
  the queued one is the natural intent.
- If they truly want to nuke the queue too, two ESC presses takes
  ~200 ms and is muscle-memory consistent with Claude Code.

Code change: `_cancel_current_turn` no longer touches `queued_input`
when a turn is active. Idle-state ESC still drops the queue
defensively (covers the case where queue was set but no task got
started for some reason).

### Tests
- `test_esc_peels_off_one_layer_at_a_time`: start + queue + ESC →
  queued auto-runs; second ESC stops that too.
- `test_esc_at_idle_clears_lingering_queue`: idle + stale queue + ESC
  → queue dropped, no task started.
- The v1.6.1 `test_cancel_clears_queued_input_too` is replaced by
  these two; net 32 buddy tests pass.

## v1.6.1 — 2026-05-20

### Fixed — ESC now clears queued input too

Found during self-test of v1.6.0: if the user queued a second prompt
during a running turn, then pressed ESC, the queue would silently
fire after the cancel. Surprising — ESC should mean "stop
everything", not "skip current and run the queued thing".

Fix: `_cancel_current_turn` now clears `queued_input` BEFORE cancelling
the task, and prints `排队的输入已清空` to the transcript when there
was something to drop.

New test: `test_cancel_clears_queued_input_too` exercises the full
sequence (start turn → queue second → ESC → expect both gone).

13 BuddyApp tests pass (12 + 1 new). 31 buddy tests total (11 agent +
7 animation + 13 app).

## v1.6.0 — 2026-05-20

### Added — Full-TUI BuddyApp (Claude Code-style layout)

`financial-analyst chat` now launches a full-screen prompt_toolkit
Application with a persistent input field. You can type the next
prompt while the agent is still thinking — it queues and runs after
the current turn finishes.

```
┌────────────────────────────────────────────────┐
│ Transcript (scrollable)                         │
│                                                  │
│ ❯ 茅台多少钱                                    │
│ ▶ quote_lookup({'code': 'SH600519'})            │
│ ✓ quote_lookup                                  │
│   SH600519: close=1280, PE=20.14...             │
│ 贵州茅台 现价 1280 元...                         │
├────────────────────────────────────────────────┤
│ ⠋ 调用 chain_for…  [ESC 取消] ▇▃▄▇▂ +0.8% #023  │ ← spinner row
├────────────────────────────────────────────────┤   (only when running)
│ ❯ 顺便看看比亚迪█                                │ ← persistent input
└────────────────────────────────────────────────┘
```

Behaviour:
- **Type any time**: the input field is always focused, even while the
  spinner animates. Press Enter to submit.
- **Submission queue**: if a turn is already running, your new submit
  is queued (single slot — newest replaces older). The agent picks it
  up automatically after the current turn finishes.
- **ESC anywhere**: cancels the running turn cleanly. The transcript
  shows a `✗ 已取消` marker; you stay at the input ready for the next
  prompt. (Ctrl+C does the same.)
- **Slash commands**: `/help /reset /tools /save <path> /quit` —
  identical to v1.5 simple mode.
- **Auto-confirmed costly tools**: `run_report` / `alpha_bench` no
  longer block on a `(y/N)` prompt; the transcript shows a
  `⚠ 启动耗时工具 — 按 ESC 随时取消` notice and proceeds. You can
  ESC out if you change your mind.

### Architecture

New module `financial_analyst.buddy.app`:
- `BuddyApp` class wraps a prompt_toolkit `Application` with
  `HSplit([transcript, conditional_spinner, input])` layout
- Rich → ANSI bridge: every transcript chunk goes through
  `_rich_to_ansi()` so existing `[bold]...[/]` markup still works
- Lazy `_build_application()`: state init is decoupled from terminal
  init, so tests can poke at state (submit/queue/cancel) without
  needing a real console
- Async animator task ticks the K-line spinner every 120 ms and calls
  `application.invalidate()` to schedule redraws
- ESC / Ctrl+C key bindings cancel `current_turn_task`; the task's
  finally block drains the queue and starts the next turn if any

### Migration

| Mode | Command | When |
|---|---|---|
| **v1.6 full TUI** (default) | `financial-analyst chat` | Default new UX |
| v1.5 simple REPL | `financial-analyst chat --simple` | If full TUI misbehaves |
| v0.x legacy slash | `financial-analyst chat --legacy` | Old slash-only TUI |

The `financial-analyst buddy` alias also now uses the v1.6 app.

### Tests

12 new tests in `tests/test_buddy_app.py`:
- Rich→ANSI bridge
- transcript growth on `_append_rich`
- banner present at startup
- submit-while-idle starts a turn
- submit-while-running queues (not starts)
- slash command handling (`/help`, `/reset`, `/tools`, `/save`)
- ESC cancels the current turn task
- queued input runs after current finishes

30 buddy tests pass total (11 agent + 7 animation + 12 app).

### Known limitations

- Tool confirmation modal: auto-accepts with an ESC-hint notice.
  Future: proper modal dialog.
- Mouse not enabled (`mouse_support=False`) since it conflicted with
  Windows terminal scroll. Use keyboard / PageUp / PageDown.
- The input field is single-line. Multi-line input via Shift+Enter is
  on the v1.6.x roadmap.

## v1.5.5 — 2026-05-20

### Added — ESC to cancel a running turn (Claude Code-style)

Pressing ESC during agent thinking / tool execution now cancels the
current turn cleanly and returns to the prompt. Ctrl+C does the same
thing.

```
❯ 跑一份 csi500 全因子 bench
⠋ 调用 alpha_bench…  [ESC 取消] ▇▃▄▇▂   -1.34%   #032
                                                       ← ESC pressed
✗ 已取消 (ESC). 继续输入下一个 prompt 或 /quit.

❯ 算了, 看下茅台行业就好          ← immediately type next prompt
⠋ 调用 industry_show…  ▆▃▅▇▂   +0.8%   #002
```

**Architecture**: `_run_turn_with_spinner` now spawns three concurrent
asyncio tasks per turn — the agent driver, the K-line animator, and
an ESC watcher (Windows uses `msvcrt.kbhit()` for non-blocking
keystroke polling at 60 ms cadence). `asyncio.wait(...,
FIRST_COMPLETED)` returns as soon as either the agent finishes or ESC
fires; on ESC we cancel the agent task, which propagates
`CancelledError` through the LLM call and tool dispatch chain.

Subprocess-based tools that started before ESC cannot be killed
mid-flight (subprocess.run is uninterruptible), but the result will
be discarded and the user is back at the prompt immediately. The
subprocess finishes in the background.

### Changed — spinner shows `[ESC 取消]` hint

The inline ticker line now reads:

```
⠋ 调用 chain_for…  [ESC 取消] ▇▃▄▇▂   -1.34%   #032
```

The hint is in `dim` style so it's permanently visible but doesn't
draw the eye.

Banner also mentions ESC + Ctrl+C as cancellation keys.

### Not done — input box visible during thinking (v1.6 planned)

Claude Code-style "you can keep typing while the agent thinks" still
requires a full prompt_toolkit `Application` rewrite — that's
substantial and slated for v1.6. For now, ESC gets you out instantly
and you can type the next prompt. Other keys pressed during a turn
are silently consumed.

## v1.5.4 — 2026-05-20

### Fixed — spinner freezes during long tools (event-loop block)

The K-line spinner animator ran on the asyncio event loop. When a tool
shelled out via `subprocess.run()` (run_report / alpha_bench /
mainline_radar / morning_brief / news_collect), the synchronous call
**blocked the entire event loop**, freezing both the spinner and the
agent's ability to process new events.

Symptom: bar counter (`#NNN`) stops incrementing in the middle of a
long tool, looks stuck even though the underlying subprocess is
working fine.

Fix: in `BuddyAgent.run_turn`, all tool calls now go through
`asyncio.to_thread(tool.run, **args)` so they execute on a worker
thread. The event loop stays free; the spinner keeps animating.

### Added — `news_collect` tool

The buddy registry was missing a way to refresh the news database, so
the LLM was forced to pick the wrong tool (e.g. `morning_brief`) when
users asked about 今日新闻 / 雪球情绪. New tool:

```python
news_collect(sources="kuaixun,longhu,sinafinance", limit=200, code=None)
```

Wraps `financial-analyst news-collect`. Supports public sources
(`kuaixun`, `longhu`, `sinafinance`, `shareholders`) and cookie-mode
sources (`xueqiu-comments`, `xueqiu-hot`, `xueqiu-earnings`).

### Changed — SYSTEM_PROMPT teaches the right news/sentiment flow

LLM now knows the correct chain when user asks about news/sentiment:

  1. `news_query` first to see what's cached
  2. If empty → `news_collect` to refresh the right sources
  3. `news_query` again to read freshly-collected data
  4. **Don't** use `morning_brief` for ad-hoc news (it's market-wide).

Tool registry: 13 → 14 tools. All 18 buddy tests still pass (no
behaviour change in tool-use loop logic — the to_thread wrap is
transparent).

## v1.5.3 — 2026-05-20

### Changed — K-line spinner compressed to inline sparkline

User feedback: the v1.5.1 6-row K-line block was too big and sat flush
against the previous text. v1.5.3 redesigns it as a single-line
sparkline ticker:

```
  ⠋ 整合中…  ▇▃▄▇▂   -3.12%   #000
  ⠙ 整合中…  ▄▄▇▃▂   -0.67%   #001
  ⠹ 整合中…  ▄▇▃▂▄   +1.47%   #002
  ⠸ 整合中…  ▇▃▂▄▂   -1.63%   #003
```

- **5 candles** (down from 18); each is one sparkline character
  (`▁▂▃▄▅▆▇█`) whose vertical fill encodes the close-price level within
  the visible window
- **1 row** of chart (down from 5), totalling **2 rows** with the
  blank padding above
- **Braille spinner** (`⠋⠙⠹⠸⠼⠴⠦⠧`) cycles every frame so motion is
  visible even when the sparkline hasn't shifted yet
- Up candles bright_green, down candles bright_red (unchanged)
- Live delta % + frame counter on the right
- One blank padding line above so the indicator doesn't slam against
  prior text

The animation is now ~30-40 columns wide, fits in a single visual
breath, and feels more "ticker-like" than the old chart-style block.

### Tests

7 animation tests updated to reflect the new 2-row layout (was 7-row).
All buddy tests (11 agent + 7 animation = 18) pass.

## v1.5.2 — 2026-05-20

### Fixed — config file not found in pip-installed wheels (HOTFIX)

`financial-analyst chat` crashed on first run from a fresh `.venv` with:

```
FileNotFoundError: 'G:\\...\\.venv\\Lib\\config\\llm.yaml'
```

**Root cause**: `LLMClient`, `loader_factory`, and `plugins` resolved
their config paths as `Path(__file__).parents[N] / "config" / *.yaml`,
which works in dev mode (repo root has `config/`) but breaks for
pip-installed wheels — the wheel never included the `config/` directory.

**Fix**: bundled all five config files into the package at
`financial_analyst/_resources/config/` and replaced the three
hard-coded paths with a shared `financial_analyst._config.find_config()`
lookup chain:

  1. Explicit `path=` argument
  2. `$FA_CONFIG_DIR/<name>` env override
  3. `~/.financial-analyst/config/<name>` user override
  4. `<cwd>/config/<name>` dev mode (repo root)
  5. Bundled `_resources/config/<name>` shipped default

Pip-installed users now Just Work without copying configs anywhere.
Dev-mode (`pip install -e .` from repo) still resolves the repo's
`config/` directory first, so live edits to `config/llm.yaml` take
effect immediately.

### Changed
- Wheel build now explicitly includes `src/financial_analyst/_resources/**/*`
  via hatch's `[tool.hatch.build.targets.wheel].include`. Confirmed via
  `zipfile.ZipFile.namelist()`: 5 yaml files present in the wheel.
- `buddy/repl.py` banner now reads `__version__` dynamically instead
  of hardcoding "v1.5.0".

### Migration note

If you previously copied `config/llm.yaml` to some custom location and
relied on the old `Path(__file__).parents[3]` lookup, that still works
via the cwd/config branch as long as you run the CLI from a directory
that has `./config/llm.yaml`. Cleaner: move your overrides to
`~/.financial-analyst/config/llm.yaml`.

## v1.5.1 — 2026-05-20

### Added — K-line thinking animation

The buddy REPL was silent during LLM thinking and tool execution.
v1.5.1 adds a finance-themed animated K-line chart that runs in a
Rich `Live` region at the bottom of the screen while the agent works:

```
█ █                                
█ █ ━ ━ ━ █                        
        │ █ ━ █   █ █              
              █ ━ █ █ ━ ━ █   █ ━ ━
                        │ █ ━ █ │  
  +1.59%   bar #005
  ▸ 调用 chain_for...
```

- 18-candle window, ~8 fps (one new candle every 120 ms)
- Bounded random walk with occasional shocks; up candles bright_green,
  down candles bright_red, doji as `━`
- Percentile-trimmed y-axis so a single big shock doesn't compress all
  other candles into doji-height
- Live status line: `思考中…` / `调用 <tool>…` / `整合中…`
- Live delta indicator: `+/-N.NN%   bar #NNN`
- Transient: clears when the agent finishes (no scrollback clutter)

**Architecture**: `KLineSpinner` (in `buddy/animation.py`) is a pure
state machine — `tick()` advances one bar, `render()` returns a Rich
`Group` of 5 chart rows + 1 delta row + 1 status row. The REPL wraps
the agent's turn in `rich.live.Live`, spawns an asyncio task that ticks
the spinner every 120 ms, and prints each `TurnEvent` ABOVE the live
region so the transcript scrolls normally while the spinner stays
pinned at the bottom.

### Refactored — repl.py

- Removed unused `_render()` (dead since the Live-region rewrite)
- Added `_run_turn_with_spinner()` and `_render_above_live()`
- Status transitions per event kind:
  - `text` → `思考中…`
  - `tool_call` → `调用 {tool}…`
  - `tool_result` → `整合中…`
  - `done` → exit

### Tests
- 7 new in `tests/test_buddy_animation.py`: init window size,
  group-renderable count, no-candles safety, tick continuity (new open
  == prior close), status persistence, status constants non-empty,
  red/green colouring on deterministic up/down candles.
- 18 tests total in `test_buddy*` (11 agent + 7 animation), all pass.

## v1.5.0 — 2026-05-20

### Added — Conversational front-end (Buddy)

A Claude Code-style conversational REPL: natural-language prompts in,
LLM autonomously picks tools, results stream back. Replaces the old
slash-command-only TUI as the default entry point for `chat`.

**Workflow**:
```
❯ financial-analyst chat
❯ 茅台是什么行业
[CALL] industry_show({'code': 'SH600519'})
[RESULT] SH600519: 白酒
贵州茅台（SH600519）属于白酒行业。需要我查看产业链或最新研报吗?

❯ 寒武纪在产业链什么位置 它有哪些同行
[CALL] chain_for({'code': 'SH688256'})
[RESULT] SH688256 → AI_chip_GPU (anchor, compute_chain upstream)
寒武纪（SH688256）核心产品: AI 加速 GPU/DCU
上游: 先进晶圆代工 / HBM 存储 / 先进封装
同行: 海光信息 / 景嘉微 / 国芯科技 / 紫光国微 / 芯原股份 / 龙芯中科
催化: NVDA GPU 发布周期 + BIS 出口管制 + 互联网云厂订单...
```

**13 tools auto-callable**:
- `run_report` (full deep-dive, confirm required)
- `quote_lookup`, `news_query`, `industry_show`
- `alpha_bench` (confirm required), `alpha_snapshot`, `alpha_list`, `alpha_show`
- `chain_for`, `stocks_show`
- `mainline_radar`, `morning_brief`, `dream_review`

Each tool's `description` is bilingual so the LLM matches Chinese
prompts. Costly tools (`run_report`, `alpha_bench`) gate behind a
confirmation callback — the REPL asks the user "(y/N)" before running.

**New module**: `financial_analyst.buddy/`
- `tools.py` — 13-tool registry with `Tool` dataclass, JSON schemas, run callable. Both Anthropic and OpenAI/Qwen function-call shapes via `to_anthropic_schema()` / `to_openai_schema()`.
- `agent.py` — `BuddyAgent` class with tool-use loop driven by LiteLLM. Yields `TurnEvent` (kind={text, tool_call, tool_result, error, done}) so the REPL can render as the agent thinks.
- `repl.py` — prompt_toolkit + Rich REPL. Slash commands: `/help /reset /quit /tools /save <path>`.

**Safety features**:
- Confirmation gate on costly tools (`run_report` ~5min, `alpha_bench` ~3min)
- `max_tool_iters=8` loop guard prevents infinite tool-call loops
- Tool errors surface verbatim to user + LLM (so it can recover)
- Conversation history persists across turns; `/reset` clears

**CLI**:
- `financial-analyst chat` — default → buddy
- `financial-analyst chat --legacy` — old slash-command TUI
- `financial-analyst buddy` — explicit alias

### Tests
- 11 new in `test_buddy.py`: registry sanity / schema well-formedness /
  confirm-required gating / single-turn text-only / single tool call /
  tool error recovery / declined confirmation / unknown tool name /
  max-iter loop guard / conversation state persistence.
- All 11 pass with mock LLM.
- Real LLM smoke tests (Qwen via DashScope) verify:
  - "茅台是什么行业" → industry_show → "白酒"
  - "寒武纪在产业链什么位置 它有哪些同行" → chain_for → upstream/peers/catalyst

### Migration note

The legacy slash-command TUI still ships (`chat --legacy`). New users
land on buddy automatically. No data migration needed — buddy stores
nothing persistent beyond `~/.financial-analyst/buddy_history.txt`
(prompt-toolkit input history).

## v1.4.6 — 2026-05-20

### Added — gtja143 + gtja149 (the last two "unportable" alphas)

Both alphas previously declared infeasible in v1.4.1 now ship,
bringing gtja191 to **191/191 (100%)** and alpha101 already at
101/101 — both reference catalogues complete.

**gtja143** was declared unportable because of its `SELF` recursion:
> `SELF_t = X_t * SELF_{t-1}` where `X_t = ratio` on up-days, `1.0`
> otherwise.

Closed-form realisation: cumulative product of the per-bar multiplier.
Per-code `cumprod` fits the stateless `compute(panel)` API without any
new "iterative" infrastructure. The handbook's `(CLOSE/DELAY)` form is
adopted (the literal `(CLOSE/DELAY-1)` decays to zero in tens of bars,
clearly a typo in some printings).

**gtja149** was declared unportable because of its benchmark
dependency:
> `REGBETA(FILTER(stock_ret, bench_close < delay(bench_close,1)),
>          FILTER(bench_ret, bench_close < delay(bench_close,1)),
>          252)`

New module `financial_analyst.data.loaders.benchmark.BenchmarkLoader`
fetches the chosen index close (CSI 300 default, configurable via
`FA_BENCHMARK` env var: `csi300 / csi500 / csi800 / csi1000 / zz500 /
sse / szse`), broadcasts to the panel index (same value across codes
per date). `PanelData.from_loader(..., benchmark_loader=...)` injects
the `benchmark_close` column.

New operator `filter_where(x, mask)` returns `x` where `mask` is True,
NaN elsewhere — natural way to express the GTJA `FILTER(...)`
construct. NaNs flow through `regbeta`'s rolling computation naturally.

`regbeta` gained an optional `min_periods` parameter (default `n`).
For filter-based alphas like gtja149 the rolling window is half NaN
by construction; we pass `min_periods=50` so the regression still
emits a beta with ~125 valid obs in the 252-bar window.

### Auto-loading

`alpha bench` and `alpha snapshot` now auto-detect a benchmark loader
just like industry: it's silently attached when the default loader can
fetch the index. If your loader can't serve `SH000300`, gtja149 just
returns NaN — bench result still completes for the other 441 alphas.

### Verified on synthetic 3-stock × 300-day panel

```
gtja143: last per code → 10.04 / 9.37 / 13.88  (cumulative up-day index)
gtja149: 561/900 non-null, betas in [-0.34, +0.34]
```

### Total catalogue status (final)

| Family | Covered | Of paper | Coverage |
|---|---:|---:|---:|
| alpha101 | 101 | 101 | **100%** |
| gtja191 | **191** | 191 | **100%** |
| qlib158 | 150 | 158 | 95% |
| **Total** | **442** | **452** | **98%** |

Compared to the 452-alpha target, the only
gap left is 8 of Qlib158's window-variant features (low signal value;
existing 150 cover all the underlying feature kinds).

### Tests
- +6 in `test_factor_zoo.py`: gtja143 cumprod reduction (hand-verified
  on 5-day sequence), gtja149 with-benchmark / without-benchmark
  branches, `filter_where`, `BenchmarkLoader.broadcast_to_panel_index`,
  env override, regbeta `min_periods` parameter. 28 zoo tests pass.

## v1.4.5 — 2026-05-20

### Added — Industry-chain knowledge base (chain_kb)

The last big knowledge-import gap from `G:\stocks` closes. Every report
on a stock that's in a known industry chain now sees: chain position,
upstream/downstream products, peer codes, role (anchor / data_supported /
llm_inferred) + weight, and the chain catalyst — fed directly into the
`fundamental-analyst` prompt.

**New module: `financial_analyst.data.loaders.chain_kb`**
- `ChainKBLoader` reads `~/.financial-analyst/memories/chain_kb/products/*.md`
  (override via `FA_CHAIN_KB_DIR`).
- Parses YAML frontmatter into `Product` dataclass: `node_id`,
  `display_name`, `category` (chain slug), `layer` (upstream/mid/down),
  `related_codes` (with role + weight), `upstream_products` /
  `downstream_products` graph edges, plus the markdown body for catalyst
  text.
- Builds a reverse code → products index on first load. Cached in memory;
  call `loader.reload()` to pick up changes.
- `chain_context(code)` returns a compact dict suitable for LLM injection:
  primary product (ranked by role-priority then weight), all products
  mentioning the code, upstream/downstream graph, top-N peer codes, and
  the "催化逻辑" tail of the primary product body.

**`factor-computer.chain_context`** (Dict, default empty): lookup
happens at report time. Silent skip if no chain file exists for the
code.

**`fundamental-analyst` prompt mandates**: when chain context is
present, must frame at least one bull/bear point around the chain
role, cite at least one peer code + the chain catalyst, and flag
`llm_inferred` chain links with weight < 0.5 as
`red_flags="chain_link_inferred_only"`.

### Added — `chain` CLI

```bash
financial-analyst chain list                            # 72 products across 6 chains
financial-analyst chain show AI_chip_GPU                # full content + frontmatter
financial-analyst chain for SH688256                    # which products + peers
financial-analyst chain import G:/stocks/strategy/chain_kb/products
financial-analyst chain stats
```

`import` filters: only files with `node_type: product` in the frontmatter
are copied; `_template.md`, `theme.md`, plain README's are skipped.

### Verified end-to-end

Bulk-imported 72 products × 6 chains × 158 unique stock codes from
`G:/stocks/strategy/chain_kb/products`. Example for SH688256 (寒武纪):

```
Stock SH688256 → primary product: AI_chip_GPU (AI 加速 GPU/DCU)
  Chain: compute_chain  layer=upstream
  Role: anchor weight=+1.00
  Upstream:   ['wafer_foundry_advanced', 'HBM_storage', 'advanced_packaging']
  Downstream: ['AI_server']
  Peer codes: 海光信息 / 景嘉微 / 国芯科技 / 紫光国微 / 芯原股份 / 龙芯中科
  Catalyst: NVDA GPU 发布周期 + BIS 出口管制 + 互联网云厂订单 ~10 万卡
```

### Knowledge-import status now (final)

| Source | Local destination | Status |
|---|---|---|
| `rating_system.md / pitfalls.md / factor_insights.md` | per-agent `memories/` | ✅ v0.x |
| `playbook_V1_V10 / R7-R20 / hard_rules` | per-agent `memories/` | ✅ v1.x |
| `stocks/{CODE}.md` (187 stocks) | `memories/stocks/<CODE>.md` | ✅ v1.4.4 |
| **`chain_kb/products/*.md` (72 products)** | `memories/chain_kb/products/` | ✅ **v1.4.5** |

All "经验 → 产出接通点" items from G:\stocks CLAUDE.md are now wired
into the financial-analyst report pipeline.

### Tests
- 13 new chain_kb tests: default path / env override / parsing / multi-product
  membership / anchor-rank priority / peer filtering / catalyst extraction /
  unknown code / list categories / import filters / reload / stats.

## v1.4.4 — 2026-05-19

### Added — Per-stock research timeline injection

The biggest knowledge-import gap from `G:\stocks` is closed. Each
stock now gets its accumulated research timeline injected into every
new report on that code, so the Bull / Bear / Risk-officer / Report-
writer agents see prior judgements, prior ratings, prior mistakes,
and explicit lessons — instead of starting cold every time.

**New module: `financial_analyst.data.loaders.stock_timeline`**
- `StockTimelineLoader` — reads `~/.financial-analyst/memories/stocks/<CODE>.md`.
- Override path via `FA_STOCK_TIMELINE_DIR` env var or ctor arg.
- API: `load(code)`, `load_tail(code, max_chars=4000)`, `list_codes()`,
  `import_from(source_dir, overwrite=False)`, `stats()`.
- Tail-mode loading caps at ~4 KB per stock to keep prompts bounded
  even when timelines reach 50 KB+.

**`factor-computer` now emits `stock_timeline`** (silent skip if no
file). The field carries the tail of the user's research markdown for
this code.

**Tier-3 agents now mandated to use it**:
- `bull-advocate`: SYSTEM_PROMPT requires citing prior rating + date
  and noting what's changed. User-message gets a `# 上次研报时间线
  (必读)` block.
- `bear-advocate`: same discipline; bear case must reconcile with
  prior judgements.
- `risk-officer`: SYSTEM_PROMPT extended to use the timeline
  specifically to catch **repeating prior mistakes** — if a trigger
  matches a previously-wrong call, emit
  `anti_signals="timeline_lesson_ignored:<reason>"`.
- `report-writer`: SYSTEM_PROMPT now requires a "上次回顾" section
  at the top of §一 综合评级 in `markdown_body`. The stock_timeline
  is stripped from the JSON dump and surfaced as its own block so
  the markdown body can cite the prior call directly.

### Added — `stocks` CLI

```bash
financial-analyst stocks list                                    # what's loaded
financial-analyst stocks show SH600100 [--tail 4000]             # show timeline
financial-analyst stocks import G:/stocks/strategy/stocks        # bulk copy
financial-analyst stocks import G:/stocks/strategy/stocks --overwrite
financial-analyst stocks stats                                   # n_codes + sizes
```

`import` filters non-stock files (skips `INDEX.md`, `missed_bulls_*.md`,
etc — only copies files whose stem matches `^(SH|SZ|BJ)\d+$`).

### Verified

```
$ financial-analyst stocks import G:/stocks/strategy/stocks
Imported 187 new stock timelines from G:/stocks/strategy/stocks
Total now: 187 codes
```

187 stocks × ~1.2 KB each = ~236 KB of accumulated research now
reachable by every relevant report. Each report on a code with a
timeline sees `<= 4 KB` of the latest entries in its Bull / Bear /
Risk / Report-writer prompts.

### Tests
- 10 new in `tests/test_stock_timeline.py` (default path /
  env-override / has-load-tail / short-file-no-truncation / unknown
  code / list-sorted / import-from / import-overwrite / missing-src /
  stats).

### Why this matters

CLAUDE.md (G:\stocks project) explicitly says:
> 经验 → 产出接通点: report_v2.py 生成研报时自动把 pitfalls /
> factor_insights / rating_system / stocks/{CODE}.md 的上次时间线
> 塞进 _agent_ctx/{CODE}.json 的 knowledge_pack 字段, agent_prompts.py
> 强制 sub-agent 必读.

The first three (`pitfalls / factor_insights / rating_system`) have
been in agent memories since v0.1. The per-stock timeline was the
missing piece — different per code, can't be embedded in a single
per-agent memory file. v1.4.4 closes it via factor-computer
injection + prompt mandates.

## v1.4.3 — 2026-05-19

### Added — `dream review / accept / reject` subcommands

The dream loop was code-complete since v0.3 but missing the
human-in-the-loop tools for triaging proposals. v1.4.3 closes that:

```bash
financial-analyst dream                                        # = dream run (default)
financial-analyst dream review                                 # list pending proposals
financial-analyst dream accept whale-analyst/no-vr-without-obv # promote to permanent
financial-analyst dream reject whale-analyst/bad-idea          # discard
```

- `dream review` walks `memories/_proposed/` and prints each proposal
  with `[confidence] agent/slug  (N cases)` + the title + the file path.
- `dream accept <agent>/<slug>` moves the proposal from
  `memories/_proposed/<agent>/<date>_<slug>.md` to
  `memories/<agent>/<slug>.md` (preserving the YAML frontmatter +
  body). Refuses to overwrite an existing permanent memory file.
- `dream reject <agent>/<slug>` deletes the proposal.

After accept, the next `financial-analyst report` call automatically
uses the new rule — markdown memory is hot-reloadable.

### Closing the self-update loop

End-to-end workflow now possible without leaving the CLI:

```bash
financial-analyst report SH600519                              # 1. run reports over time
# ...wait T+5d for outcomes...
financial-analyst dream                                        # 2. introspect
financial-analyst dream review                                 # 3. read what was proposed
financial-analyst dream accept whale-analyst/<slug>            # 4. promote good ideas
financial-analyst dream reject <other-agent>/<slug>            # 5. discard noise
financial-analyst report SH600519                              # 6. new rule in effect
```

### Tests
- 7 new dream CLI tests (review empty / review lists / accept promotes /
  accept refuses overwrite / reject deletes / accept unknown / accept bad
  target). 11 dream tests pass total; old 4 unchanged.
- Backward compat: `financial-analyst dream` with no args still defaults to `dream run`.

## v1.4.2 — 2026-05-19

### Added — dynamic zoo signal selection (440-rolling instead of fixed top-10)

The hardcoded `PRODUCTION_TOP10` is no longer the only path. v1.4.2 wires
up a rolling top-N pick from the latest bench result, so when the alpha
catalogue or universe regime shifts, the report pipeline picks up the new
strongest signals automatically.

**New module: `financial_analyst.factors.zoo.selector`**
- `select_top_alphas(bench_df, n=20, min_n_dates=30, min_abs_rank_ir=0.05,
   require_sign_agreement=True, family=None)` — filters out noise alphas
  (short bench, weak signal, sign-disagreement between `rank_IR` and
  `hit_rate`), then returns top-N by `|rank_IR|`.
- `load_latest_bench(universe)` — reads the canonical cached CSV.
- `alpha_metadata_from_bench(bench_df, names)` — returns
  `{name: {bench_rank_ic, bench_hit_rate, bench_n_dates}}` for snapshot
  enrichment.
- `bench_csv_path(universe)` — canonical filename for cached bench output.

**CLI: `alpha bench --save`**
- After benching, persists the full result CSV to
  `~/.financial-analyst/cache/bench_<universe>_latest.csv`. Used as the
  input for `snapshot --auto`.

**CLI: `alpha snapshot auto`**
- New target keyword: pass `auto` instead of `top10` or a comma-list.
- Reads the cached bench for the same `--universe`, picks the top-N
  (via `--top-n`, default 20) using `select_top_alphas`, builds the
  snapshot. Each row now carries `bench_rank_ic`, `bench_hit_rate`,
  and `bench_n_dates` so downstream LLM consumers know each alpha's
  validated direction without hard-coded sign conventions.

**Recommended workflow (weekly cron)**:
```bash
financial-analyst alpha bench --universe csi300_active \
    --since 2024-06-01 --until 2024-12-31 --save
financial-analyst alpha snapshot auto --universe csi300_active \
    --until 2024-12-31 --top-n 20
```

### Changed — `quant-analyst` SYSTEM_PROMPT is now sign-agnostic

Previously the prompt listed the v1.3.4 sign convention for the
hardcoded top-10 alphas (`qlib_VSTD60 POSITIVE`, `gtja095 NEGATIVE`,
etc.). With dynamic top-N this is no longer maintainable.

The new prompt teaches the LLM to derive direction per-alpha from each
row's `bench_rank_ic` sign:
- bullish if `(rank_pct > 0.7 AND bench_rank_ic > 0)` OR
  `(rank_pct < 0.3 AND bench_rank_ic < 0)`
- bearish symmetrically
- low-confidence if `|bench_rank_ic| < 0.05 OR bench_n_dates < 30`
- `bull_points` / `bear_points` must cite both `rank_pct` and
  `bench_rank_ic` so readers can verify the direction.

### Verified end-to-end on CSI300 / 2024-12-31

```
bench --save: 440 alphas across 868 codes × 144 days → CSV cached
snapshot auto --top-n 20: picked top-20 by |rank_IR|, each with
  bench_rank_ic + bench_hit_rate metadata. Examples:
    gtja042       bench_rank_ic=+0.0650  hit=52.5%
    qlib_VSUMP20  bench_rank_ic=-0.0457  hit=49.1%
    qlib_STD5     bench_rank_ic=-0.0701  hit=48.9%
    alpha089      bench_rank_ic=-0.0266  hit=48.9%
17219 rows = 20 alphas × ~860 codes
```

### Backward compatibility

- Old `snapshot top10` keyword still works → uses `PRODUCTION_TOP10`.
- Old snapshot parquet files (without bench metadata) still readable —
  rows just lack the optional `bench_*` columns and quant-analyst
  treats them as low-confidence.

## v1.4.1 — 2026-05-19

### Zoo catalogue completion — 440 alphas total

The closing batch toward complete coverage of the three reference
catalogues.

- **alpha101 +3 → 101/101 (100% COMPLETE)**. The final three:
  - `alpha056` — uses `cap` (market cap) in the original; we substitute
    `amount` (close × volume) since the formula only consumes `cap`
    inside `rank()`, where the ordering of dollar volume vs market cap
    is identical for cross-sectional ranking on A-share large caps.
  - `alpha071` — max of two decayed ts-ranks (close-ADV180 corr vs
    squared low+open-2*vwap rank). Long-window, ported as written.
  - `alpha073` — negative max of VWAP-momentum decay vs blend-delta
    decay ts-rank.
- **gtja191 +31 → 189/191 (99% COMPLETE)**. Added 112 (RSI direction),
  115 (high-close blend × ADV30), 121 (VWAP-floor × ADV60 ts-rank),
  123/148 (boolean corr-vs-floor), 124/125 (close-VWAP / decay
  composites), 131 (VWAP-delta × close-ADV50), 137 (single-day TR-
  normalised momentum), 138/140 (sister of alpha097/088),
  141 (high-ADV15 rank-corr), 146 (Z-score-style return deviation),
  152 (MACD on momentum), 154 (VWAP-floor boolean), 156 (sister to
  alpha073), 157 (deep-nested rank composite), 159 (triple-window
  stochastic %K composite), 162 (stochastic-RSI), 164 (smoothed
  up-day-inverse-return), 165 (cumulative-deviation range / 48d
  stddev), 166 (skewness-style central moment), 169 (MACD chain on
  EWMA momentum), 170 (sister to alpha047), 173 (TEMA + log
  correction), 180 (sister to alpha007), 181 (20d variance), 182
  (bench-aligned up-day proxy), 183 (cumulative-deviation excursion),
  187 (sister to gtja093), 190 (asymmetric vol log-ratio).
  Skipped permanently: 143 (recursive SELF — needs prior-step output;
  fundamentally incompatible with our stateless compute API), 149
  (benchmark-relative beta — requires benchmark return series we don't
  carry in PanelData).
- **qlib158 +23 → 150 (95% of 158 target)**. Wider window coverage for
  IMAX/IMIN/IMXD × {30,60}, SKEW/KURT × 5, CORR × 3, SUMP/SUMN/SUMD ×
  {10,30}, VSUMP/VSUMN/VSUMD × {10,30}, CNTD × {10,30}.

Total: **440 alphas across 3 families** (across alpha101 / gtja191 / WorldQuant family taxonomy). Compared to v1.3.0's 22 alphas, this is
a 20× expansion in two days.

### Fixed
- `gtja157` (nested ranks with `product()`) silently compute_error'd —
  `product` wasn't in gtja191's import list. Same class of bug as
  `alpha029` in v1.3.5. Fixed.

### Tests
- Count baselines bumped (alpha101 ≥ 101, gtja191 ≥ 189, qlib158 ≥ 150).
- All 18 zoo tests pass. Sample30 bench across 440 alphas runs to
  completion with 0 compute errors.

### What's truly unportable
Only 2 of the 452 reference alphas remain unportable, and they're both
architectural rather than complexity-bound:
- `gtja143`: recursive — formula references its own prior output as
  `SELF`. Our stateless `compute(panel) → series` API can't express
  this without major restructuring. Future work: optional iterative
  alphas (compute_iterative).
- `gtja149`: benchmark-index relative beta — needs the daily close of
  CSI 300 (or equivalent) as a parallel series in PanelData. Future
  work: BenchmarkLoader.

## v1.4.0 — 2026-05-19

### Added — Industry classifier loader + IndNeutralize alphas
v1.4.0 is the third pillar of the zoo: industry-neutralisation.
Previously stubbed, now wired end-to-end.

**`financial_analyst.data.loaders.industry.IndustryLoader`**:
- Pulls 申万 (Shenwan) level-1 industry classifications from Tushare
  `stock_basic(fields='ts_code,name,industry')` via the raw POST endpoint
  (bypassing the official `tushare` package's flaky round-robin).
- Caches to `~/.financial-analyst/cache/industry_map.parquet`. One
  refresh covers ~5500 A-share codes across ~110 industries.
- API: `get(code)`, `get_map(codes)`, `refresh_from_tushare()`, `stats()`.
- New CLI: `financial-analyst industry refresh / show / stats`.

**`PanelData.from_loader(..., industry_loader=...)`**:
- Optional kwarg. When provided, the panel carries an `industry` column
  indexed by (date, code).
- `panel.industry` property exposes the Series; falls back to `"未知"`
  when no loader is attached so old alphas don't crash.

**`indneutralize(x, group)` operator now actually used**:
- Already shipped as a stub in v1.3.0. v1.4.0 finally has data to feed
  it: alpha101 IndNeutralize alphas pass `panel.industry`.
- Verified: within any (date, industry) group, demean produces ~0 mean
  to floating-point precision.

**Alpha bench and snapshot CLI auto-load IndustryLoader** when the
cache exists (silent skip when absent).

### Added — alpha101 +19 → 98/101 (97%)
Final batch of IndClass-dependent alphas now operable:
- 048 (250d delta-corr, industry-demean)
- 058, 059 (IndNeutralize VWAP × volume corr)
- 063 (industry-neutral close-momentum vs blend-ADV180)
- 067 (IndNeutralize VWAP-ADV20 corr exponent)
- 069 (IndNeutralize VWAP-delta × blend-ADV20)
- 070 (IndNeutralize close × ADV50 long-corr)
- 076 (IndNeutralize low × ADV81 multi-decay)
- 079 (IndNeutralize blend-delta vs VWAP-ADV150)
- 080 (IndNeutralize open-high blend sign-delta)
- 082 (IndNeutralize volume × open corr)
- 087 (IndNeutralize ADV81 × close corr decay)
- 089 (IndNeutralize VWAP-delta vs low-ADV10 ts-rank)
- 090 (IndNeutralize ADV40-low corr)
- 091 (IndNeutralize close × volume long-decay)
- 093 (IndNeutralize VWAP × ADV81 corr)
- 095 (boolean composite on long-window corr)
- 097 (IndNeutralize blend-delta vs low-ADV60 ts-rank)
- 100 (IndNeutralize MFI-volume composite)

Zoo: **383 alphas** across 3 families.

### Real-world signal on sample30
14 of 19 new IndNeutralize alphas produce real signals (5 need >144
trading days to warm up due to 250d / adv150 / adv180 windows):

```
alpha089  rank_IR=-0.324  (industry-neutral VWAP-delta vs low-ADV10)
alpha091  rank_IR=-0.194  (IndNeutralize close × vol long-decay)
alpha067  rank_IR=+0.171  (IndNeutralize VWAP-ADV20 corr)
alpha069  rank_IR=-0.067  (IndNeutralize VWAP × ADV20 blend)
alpha080  rank_IR=-0.100  (IndNeutralize open-high blend)
```

### Tests
- New: `test_indneutralize_demean_per_industry` — verifies the
  per-(date, industry) demean invariant on hand-built groups.
- New: `test_industry_loader_round_trip` — IndustryLoader cache I/O
  without touching Tushare.
- New: `test_panel_carries_industry_when_loader_supplied` — wiring test
  for the new `industry_loader` kwarg.
- 18 zoo tests pass total.
- Baselines bumped (alpha101 ≥ 98).

### Remaining (v1.4.x+)
- alpha101: 3 left — 033 (?), 056 (uses cap = market cap), 071/073
  (very complex nested ts-rank chains).
- gtja191: 33 left (recursive SELF, benchmark-relative, exotic SUMIF).
- qlib158: 31 left (low-marginal-value window variants).

### Upgrade note
After upgrading, run:
```bash
financial-analyst industry refresh
```
once to populate the industry cache. From then on, every `alpha bench`
and `alpha snapshot` call automatically uses it.

## v1.3.6 — 2026-05-19

### Added — +74 alphas (zoo: 290 → 364)
Final pre-IndustryLoader push toward 440+ alpha coverage.

- **gtja191 +49 → 158/191 (83%)**: added 064, 073, 075, 087, 089, 090,
  091, 092, 094, 101, 103-105, 107, 108, 110, 111, 113, 114, 116, 117,
  119, 120, 122, 127, 130, 132, 134, 136, 142, 144, 145, 147, 151, 153,
  155, 158, 163, 171, 172 (ADX-style), 174, 175 (short ATR), 177, 179,
  185, 186, 188, 189, 191. Hits MACD-style (089/155), Williams %R
  variants, multi-window OBV, recency indicators, CCI/ADX patterns,
  short ATR, rolling kurtosis-style displacement (127).

- **qlib158 +25 → 127/158 (80%)**: rolling SKEW/KURT × {10,20,60} (6),
  MA30/STD30/ROC30 (3), longer CORR/CORD × {10,60} (4),
  WVMA × {10,30} (2), VMA/VSTD × {10,30} (4), CNTP/CNTN × {10,30} (4),
  RSV × {30,60} (2).

**Zoo: 364 alphas across 3 families** —
80%+ of two of the three reference catalogues.

### Remaining work (v1.4.0 +)
- alpha101: 22 left, all use `IndNeutralize` (need IndustryLoader). v1.4.0.
- gtja191: 33 left, mostly very complex/exotic (recursive `SELF`,
  benchmark-relative formulas, multi-stage SUMIF). Incremental.
- qlib158: 31 left, mostly window variants of existing features that
  add no signal capacity. Optional.

### Tests
- 15 zoo tests pass; baselines bumped (alpha101 ≥ 79, gtja191 ≥ 158,
  qlib158 ≥ 127).
- Sample30 bench across 364 alphas completes with 0 compute errors.

## v1.3.5 — 2026-05-19

### Added — +148 alphas (zoo: 142 → 290)
A push toward catalog completeness. Three batches across three families:

- **alpha101 +37 → 79/101** (77% of WorldQuant's catalogue): added
  021, 027, 029, 031, 032, 036-039, 046, 047, 051, 057, 060-062, 064-066,
  068, 072, 074, 075, 077, 078, 081, 083-086, 088, 092, 094, 096, 098,
  099, 101. Skipped: ~22 that need `IndNeutralize` (industry classifier
  loader, planned for v1.4.0) and a few using `cap` (market cap from
  daily_basic, not yet in PanelData).
- **gtja191 +65 → 109/191** (57% of GTJA's catalogue): added 015, 016,
  023, 026, 030, 032, 033, 035, 036, 039, 041, 043-045, 048-051, 055, 056,
  059-063, 066, 067, 069-072, 074, 077-086, 088, 093, 096-100, 102, 106,
  109, 118, 126, 129, 133, 135, 139, 150, 161, 167, 168, 176, 178, 184.
- **qlib158 +46 → 102/158** (65% of Qlib's Alpha158): new
  SUMP/SUMN/SUMD × {5,20,60} on close (9), VSUMN/VSUMD × {5,20,60} (6),
  CORD × {5,20} (2), WVMA × {5,20,60} (3), MAX/MIN × 4 windows (8),
  QTLU/QTLD × 4 windows (8), RANK × 4 (4), CNTD × 3 (3),
  IMXD × 3 (3).

**Zoo now ships 290 alphas total** — close to two-thirds of the original
452-alpha goal. Remaining: ~80 alpha101 (mostly
IndNeutralize-blocked), ~82 gtja191 (mostly complex/exotic), ~56 qlib158.

### Fixed
- `alpha029` / `alpha081` used `product()` but it wasn't imported into
  alpha101/alphas.py — silent `compute_error` until now. Fixed.
- `qlib_CORD5` / `qlib_CORD20` used `log()` for log-volume ratios but
  it wasn't imported into qlib158/alphas.py — same silent error. Fixed.

### Tests
- 15 zoo tests still pass; count baselines bumped (alpha101 ≥ 79,
  gtja191 ≥ 109, qlib158 ≥ 102).

### Performance note
A `alpha bench --universe csi300_active` over 290 alphas now takes
~3-4 minutes (vs ~2m for 142 in v1.3.3). All alphas use the same
panel — adding more alphas grows linearly in benchmark time.

## v1.3.4 — 2026-05-19

### Added — Alpha-Zoo snapshot integration into the research pipeline
The 142-alpha zoo finally reaches end users. New flow:

1. **Periodic snapshot** — user runs
   `financial-analyst alpha snapshot top10 --universe csi300_active --until 2024-12-31`
   weekly (or any cadence). Output:
   `~/.financial-analyst/cache/zoo_snapshot_<universe>_<asof>.parquet`,
   with one row per (code, alpha) carrying the current value plus the
   stock's cross-sectional percentile rank within the snapshot universe.
2. **Factor-computer auto-lookup** — every stock report now looks up
   the most-recent snapshot whose asof ≤ report asof and surfaces a
   `zoo_signals` block with the target stock's values + rank_pct for the
   curated production-top-10 alphas. Silent skip when the cache is
   absent (preserves backward compatibility).
3. **Quant-analyst consumes** — `quant-analyst`'s system prompt now
   includes the v1.3.4 sign conventions (positive vs negative-rank
   alphas) and decision rules:
   - 3+ zoo alphas agreeing with the model bumps conviction
   - Zoo + model disagreement flagged as `zoo_model_disagreement` in
     `anti_signals`
   - `bull_points` must cite specific zoo alphas by name + rank_pct

### Added — `PRODUCTION_TOP10` curated alpha list
Hard-coded in `financial_analyst.factors.zoo.snapshot`. Derived from the
CSI300 2024-H2 bench (docs/csi300_bench_2024h2.md §8) — the 10 alphas
with strongest cross-universe `|rank_IR|` and >50% hit rate:

```
qlib_VSTD60, gtja095, qlib_STD10, gtja052, gtja042,
qlib_VSUMP20, qlib_KLEN, qlib_BETA20, qlib_ROC60, qlib_IMAX20
```

### Tests
- `test_snapshot_round_trip` — builds a 40-stock snapshot with a stub
  loader and verifies `load_snapshot_for_code` round-trips correctly.
- Full test suite: 15 zoo tests pass.

### Verified end-to-end on SH600519 (asof 2024-12-31)
Snapshot lookup shows the LLM:
```
qlib_VSTD60   rank_pct=19.8%   (low — bearish for VSTD60 positive sign)
gtja095       rank_pct=98.4%   (high turnover vol — bearish, negative sign)
qlib_STD10    rank_pct=10.9%   (low close vol — bullish, negative sign)
gtja042       rank_pct=8.9%    (low vol-of-high crowd — bearish, positive sign)
qlib_KLEN     rank_pct=2.1%    (very tight range — bullish, negative sign)
qlib_BETA20   rank_pct=74.4%   (strong 20d slope — bearish, negative sign)
qlib_ROC60    rank_pct=67.8%   (mid-high 60d ratio — slightly bullish)
```

quant-analyst now produces `bull_points` / `bear_points` grounded in
these specific alpha readings instead of just the LGB rank.

### Roadmap
- v1.3.5: industry-neutralise the volatility alphas (need industry
  classifier loader first — Tushare `stock_basic` has `industry` field)
- v1.3.x: backfill remaining alpha101/gtja191/qlib158 alphas (~310 left)
  for completeness, even though the top-10 already captures 80%+ of
  bench signal magnitude.

## v1.3.3 — 2026-05-19

### Added — regression operators (regbeta / regresi / rsqr / sequence / wma)
- `regbeta(y, x, n)` — rolling OLS β over the last n bars per code
- `regresi(y, x, n)` — rolling OLS residual `y - (βx + α)`
- `rsqr(y, x, n)` — rolling OLS R², in [0, 1]
- `sequence(panel_template, n)` — synthetic time-index series (1, 2, 3, ...
  per code), so `regbeta(close, sequence, N)` computes the slope of close
  against time. Matches GTJA-191's `SEQUENCE(N)` notation.
- `wma(x, n)` — linear-weighted MA (alias of `decay_linear` for formula
  fidelity)
- `max_pair / min_pair` — element-wise max/min, named to disambiguate
  from `ts_max / ts_min` (time-series ops)

These unlock the regression-based half of all three families.

### Added — 38 more alphas (zoo: 104 → 142)
- **alpha101 +11 → 42**: `041` (sqrt(high·low) - vwap), `042` (rank-skew
  on VWAP), `043` (vol-rank × neg-momentum rank), `044`, `045`, `049`
  (slope-reversal regime switch), `050`, `052`, `053`, `054`, `055`.
- **gtja191 +6 → 44**: `gtja021` (slope of MA6 via REGBETA), `gtja027`
  (WMA of 3d+6d returns), `gtja076` (CV of return-per-volume), `gtja095`
  (20d std of dollar volume), `gtja128` (MFI-style typical-price volume
  ratio), `gtja160` (down-day-only volatility EWMA).
- **qlib158 +21 → 56**:
  - BETA/RSQR/RESI × {5,10,20,60} = 12 new trend-regression features
  - VMA/VSTD/VSUMP × {5,20,60} = 9 new volume statistics

### Top signals on sample30 (2024-06 to 2024-12, fwd_5d)
The v1.3.3 regression operators paid off — 3 of the top 7 are new:

```
qlib_CNTP60  rank_IR=-0.605  (60d up-day count, reversal)
qlib_ROC60   rank_IR=+0.592
qlib_CNTN60  rank_IR=+0.531
qlib_RSQR60  rank_IR=-0.508  ← new (60d trend linearity)
qlib_BETA60  rank_IR=-0.431  ← new (60d trend slope)
gtja076      rank_IR=-0.330
qlib_RESI60  rank_IR=+0.278  ← new (60d trend-residual)
```

The clean interpretation: in 2024-H2 on A-share large caps, **strong
linear 60-day trends predicted reversal**. RSQR60 and BETA60 both
negative-rank-IR confirms this from two angles (R² magnitude, slope
magnitude), and RESI60 positive-rank-IR is its complement (large
residuals = away-from-trend = mean-revert toward trend).

### Tests
- Count baselines bumped to v1.3.3 (alpha101 ≥ 42, gtja191 ≥ 44,
  qlib158 ≥ 56). 14 zoo tests pass unchanged.

### Roadmap
- alpha101 remaining: 59 alphas. The next batch needs *industry
  neutralisation* (`indneutralize`) — `alpha48`, `56`, `58`, `59`, `63`,
  `67`, `69`, `70`, `76`, `79`, `80`, `82`, `87`, `89`, `90`, `91`, `93`,
  `97`, `100`, `101` all use `IndNeutralize(...)`. Industry classifier
  loader is the v1.3.4 prerequisite.
- gtja191 remaining: 147 alphas.
- qlib158 remaining: 102 alphas (mostly WVMA / SUMD-style which are
  doable with existing ops).

## v1.3.2 — 2026-05-19

### Added — qlib158 family (35 first-batch alphas)
New family `qlib158` ports the simple OHLC-ratio + moving-stat + stochastic
features from Microsoft Qlib's `Alpha158` handler. v1.3.2 ships the first 35
of 158:

- **6 candle shape**: `qlib_KMID`, `qlib_KLEN`, `qlib_KMID2`, `qlib_KUP`,
  `qlib_KLOW`, `qlib_KSFT` — body/wick/range ratios.
- **12 MA/STD/ROC**: `qlib_MA{5,10,20,60}`, `qlib_STD{5,10,20,60}`,
  `qlib_ROC{5,10,20,60}` — relative MA, dispersion, lagged-close ratios.
- **9 stochastic / argmax-argmin**: `qlib_RSV{5,10,20}`,
  `qlib_IMAX{5,10,20}`, `qlib_IMIN{5,10,20}` — %K position, high-recency,
  low-recency.
- **6 up/down counts**: `qlib_CNTP{5,20,60}`, `qlib_CNTN{5,20,60}` —
  positive/negative day fractions.
- **2 price-vol correlations**: `qlib_CORR{5,20}` —
  `correlation(close, log(volume), N)`.

### Added — 20 more alphas across alpha101 + gtja191
- **alpha101 +9 → 31 total**: `alpha017`, `023`, `026`, `028`, `030`,
  `033`, `034`, `035`, `040`.
- **gtja191 +11 → 38 total**: `gtja022`, `024`, `029`, `031`, `034`,
  `038`, `040`, `046`, `054`, `057`, `065`.

**Zoo now ships 104 alphas total** (was 49 in v1.3.1).

### Fixed
- `config/universes/sample30.txt` had `SH000858` (五粮液); correct prefix
  is `SZ000858`. Bench output silently dropped this code as "empty";
  fixing brings the sample universe back to 30 codes.

### Top signals on sample30 (2024-06 to 2024-12, fwd_5d)
The qlib158 family dominates the leaderboard once added:

```
qlib_CNTN60  -0.651   ← new strongest signal in entire zoo
qlib_CNTP60  +0.599
qlib_RSV20   +0.579
alpha005     -0.262
qlib_STD60   -0.237
gtja008      -0.233
gtja001      -0.225
```

The CNTN/CNTP findings re-confirm the well-known "high recent down-day
count ⇒ mean-reversion bounce" effect on A-share large caps. n_dates
is smaller (~80) because of the 60-day window.

### Tests
- Count assertions bumped to v1.3.2 baseline (alpha101 ≥ 31,
  gtja191 ≥ 38, qlib158 ≥ 35).
- Existing 14 tests pass unchanged.

### Roadmap
- alpha101 remaining: 70 alphas (mostly the regression-based ones —
  REGBETA, RESIDUAL — which need a new linear-regression operator first).
- gtja191 remaining: 153 alphas.
- qlib158 remaining: 123 (including the heavier BETA/RSQR/RESI regression
  features which need the same operator as alpha101's regression
  variants).

## v1.3.1 — 2026-05-19

### Added — 27 more alpha ports
- **alpha101**: +12 → 22 total. Added `alpha005`, `008-011`, `016`, `018-020`,
  `022`, `024`, `025`. The new strongest signal on `sample30` (2024-06 to
  2024-12) is `alpha005` with `|rank_IR|=0.262` (open vs 10d-VWAP rank
  weighted by negative `|close-VWAP rank|`).
- **gtja191**: +15 → 27 total. Added `gtja006`, `008`, `010`, `011`, `013`,
  `017`, `019`, `020`, `025`, `028`, `037`, `047`, `052`, `058`, `068`.
  `gtja008` (mid+VWAP blend 4-day rank-change) jumps to `|rank_IR|=0.233`,
  second-strongest in the entire zoo.

Zoo now ships **49 alphas total** (was 22 in v1.3.0).

### Changed — cleaner bench output
- `bench_runner` traps `numpy.RuntimeWarning: invalid value encountered in
  divide` (raised by `numpy.corrcoef` whenever a rolling window has zero
  variance, which is expected behaviour on quiet days) so the CLI table is
  no longer drowned in noise.
- `PanelData.returns` now passes `fill_method=None` to silence pandas
  `pct_change` `FutureWarning` (also a correctness improvement: a
  suspended trading day no longer forward-fills as a zero-return day).

### Tests
- Synthetic bench panel extended from 60 → 300 days to cover the deep-
  history alphas (`alpha019`, `alpha024`, `gtja025` reach back 100-250 days).
- New count assertions lock the v1.3.1 baseline so future patches must
  preserve at least 22 alpha101 / 27 gtja191.

### Notes for v1.3.x roadmap
- Remaining: 79 alpha101 + 164 gtja191 (formula text confirmed; just
  ports). Patch releases will bring 15-20 alphas at a time.
- `qlib158` family stub deferred to v1.3.x — direct Qlib `Alpha158`
  re-export needs `D.features()` semantics that don't fit our `PanelData`
  yet. Working on a thin adapter.

## v1.3.0 — 2026-05-19

### Added — Alpha Zoo
A registry of named alpha formulas with a `alpha bench` CLI that emits
IC / IR / hit-rate per alpha against a chosen universe and period.
Two families ship in this release:

- **`alpha101`** — 10 of the most-cited WorldQuant 101 Formulaic Alphas
  (Kakushadze 2015, arXiv:1601.00991): alpha001-004, 006, 007, 012-015.
- **`gtja191`** — 12 of the most-cited Guotai Junan 191 Alphas (国泰君安
  2017), designed specifically for A-share short-horizon prediction:
  gtja001-005, 007, 009, 012, 014, 018, 042, 053.

Three CLI commands:
- `financial-analyst alpha list [family]` — Rich table of names + descriptions
- `financial-analyst alpha show <name>` — formula text + paper citation
- `financial-analyst alpha bench <family> --universe <path|name> --since <date> --until <date> [--fwd-days N] [--top K]`

Bench output is sorted by `|rank_IR|` descending. Verified end-to-end on
30 A-share large caps × 138 trading days: `gtja001 rank_IR=-0.225`,
`gtja014 rank_IR=+0.201` etc.

### Added — sample30 universe
`config/universes/sample30.txt` — 30 hand-picked A-share large caps
(Maotai/Wuliangye/Ping An/CATL/BYD/Hikvision/etc) so `alpha bench
--universe sample30` works out-of-box with no additional setup.

### Added — `financial_analyst.factors.zoo` package
Public API: `register`, `get`, `list_alphas`, `families`, `PanelData`,
`run_bench`, `bench_one`. Operators (`rank`, `ts_rank`, `delta`,
`correlation`, `decay_linear`, `sma`, etc.) live in
`factors.zoo.operators`. All `ts_*` ops use `min_periods=window` so
alphas never emit partial-window signals — full look-ahead protection
on shipped alphas.

User-supplied alphas register via `register(AlphaSpec(...))` from any
plugin under `~/.financial-analyst/plugins/`.

### Tests
- `tests/test_factor_zoo.py` — 14 new tests covering registry, panel
  alias normalisation, operator semantics, and end-to-end bench. Total
  package test count now 335+.

### Docs
- `docs/alpha_zoo.md` — full reference: CLI usage, operator catalogue,
  how to add your own alpha, how the bench loop works.

### Known limitations (rolling forward in 1.3.x patches)
- Only 22 / 292 alphas ported in v1.3.0; remaining alphas land in 1.3.x
  patches.
- `qlib158` and `academic` families are placeholders.
- Daily-bar panel only; 5min support is a future PanelData extension.

## v1.2.2 — 2026-05-19

### Fixed
- **xueqiu social_posts dedup collapse**. The opencli xueqiu/comments
  adapter returns items shaped `{author, text, likes, replies, retweets,
  created_at, url}` with no explicit `id`. The earlier upsert chain only
  consulted `id` / `post_id` / `ts`, so every row in a 30-comment batch
  hashed to `xueqiu_comments::SH600519::` and `INSERT OR REPLACE` left
  only the last item alive. Net effect on v1.2.0 / v1.2.1: every
  `news-collect --sources xueqiu-comments` call wrote exactly **1 row**
  no matter how many comments came back.
- Fix: extend the post_id fallback chain to consult `url` (xueqiu's
  unique per-post URL) and `created_at`. Also map `replies` →
  `comments_count` to match xueqiu's field name.
- **whale-analyst dropped all retail-sentiment insight**. SYSTEM_PROMPT
  enumerated the policy (14 S/SS signals, score aggregation rules) but
  never listed the WhaleOutput JSON schema. The LLM hallucinated its own
  keys (`ticker`, `whale_judge`, `analyst_note`, `playbook_v_anchors`
  etc); pydantic silently dropped them and used defaults, so the
  `bull_points` / `bear_points` / `alerts` lists arrived at
  `report-writer` empty even when the LLM had read 雪球 posts and
  formed an opinion on them.
- Fix: spell out the exact JSON schema in SYSTEM_PROMPT with hard rules
  ("Use the EXACT keys", "If 雪球 social posts are supplied, you MUST
  surface their signal in bull/bear or alerts"). Verified that the
  WhaleAnalyst paragraph in the SH600519 report now reads e.g.
  「雪球高赞帖文（102赞/86评）集中引用段永平长期持有框架」.

### Changed
- `whale-analyst` social_posts lookback widened from 7 → 30 days.
  xueqiu activity for any single stock is bursty; a 7-day window
  frequently misses the latest discussion wave even for liquid names.

### Verification
- Cleared `social_posts`, re-collected 30 SH600519 xueqiu comments →
  30 distinct rows in DB with intact Chinese, `replies` mapped to
  `comments_count`, url-based unique IDs.
- New regression test `test_social_posts_real_xueqiu_schema` feeds the
  exact upstream payload shape to lock in the fix.

If you were on v1.2.0 / v1.2.1 and collected xueqiu data, **re-run the
collection** — only the last comment per stock survived in your DB:
```bash
python -c "from financial_analyst.data.news_db import NewsDB; \
db=NewsDB(); db.conn.execute('DELETE FROM social_posts'); \
db.conn.commit(); db.close()"
financial-analyst news-collect --sources xueqiu-comments --code SH600519 --limit 30
```

## v1.2.1 — 2026-05-19

### Fixed
- **Windows utf-8 mojibake** in NewsDB. Calling `opencli.CMD` via `subprocess`
  + `shell=True` routed the node child's stdout through `cmd.exe`, which
  transcoded utf-8 → the active console code page (GBK / cp936 on a Chinese
  Windows). Result: all Chinese characters in collected news / 龙虎榜 / 十大股东
  were stored as `���` mojibake.
- Fix: parse the npm-generated `.CMD` shim to recover the underlying
  `main.js` path, then call `node <main.js> ...` directly with `shell=False`.
  cmd.exe is no longer in the loop and utf-8 reaches Python unchanged.
- New regression tests: `test_run_opencli_decodes_utf8_chinese` round-trips
  Chinese through the bytes-mode pipe, and `test_resolve_npm_shim_parses_main_js`
  locks in the .CMD parser against the actual npm-generated wrapper format.

If you were on v1.1.0 / v1.2.0 on Windows, **rebuild your NewsDB**:
```bash
python -c "from financial_analyst.data.news_db import NewsDB; \
db=NewsDB(); c=db.conn; [c.execute(f'DELETE FROM {t}') for t in \
['news','lhb','holders','social_posts','hot_stocks','earnings_dates']]; \
c.commit(); db.close()"
financial-analyst news-collect --sources kuaixun,longhu,sinafinance --limit 500
```
Linux / macOS users were not affected.

## v1.2.0 — 2026-05-18

### Added
- **3 xueqiu cookie-mode collectors**: `xueqiu-comments` (散户讨论), `xueqiu-hot` (热股榜), `xueqiu-earnings` (财报日历)
- NewsDB extended with 3 new tables: `social_posts`, `hot_stocks`, `earnings_dates`
- `news-collect --sources xueqiu-comments --code SH600519` etc
- `financial-analyst doctor` command for env diagnostics (OpenCLI / Chrome / NewsDB / loaders)
- `whale-analyst` sub-agent now pulls retail sentiment from `social_posts` when available

### Requirements
xueqiu commands need OpenCLI Chrome extension + Chrome login on xueqiu.com. See [docs/xueqiu_setup.md](docs/xueqiu_setup.md).

### Why this matters
Tushare and other APIs can't access retail-investor discussion. Xueqiu is the largest Chinese stock community — its `social_posts` give whale-analyst access to crowd sentiment that quantitative signals miss. Initial use case: validate that 主力 OBV trend matches retail engagement (or detect divergence).

## v1.1.0 — 2026-05-18

### Added (OpenCLI integration → local news DB)
- **NewsDB** at `~/.financial-analyst/data/news.sqlite` with `news`, `lhb`, `holders` tables + FTS5 full-text index
- **4 OpenCLI collectors** for eastmoney 7x24 快讯 + 龙虎榜 + 十大流通股东 + sinafinance 7x24
- **3 CLI commands**: `news-collect`, `news-query`, `news-stats`
- `news-reader` + `f10-reader` sub-agents now augment from NewsDB when drop-zone is sparse

### Use case
Daily cron / scheduled task:
```bash
financial-analyst news-collect --sources kuaixun,longhu --limit 500
```
Then every `financial-analyst report SH600519` automatically has the latest news + 龙虎榜 + 股东 context — without consuming LLM tokens to scrape.

### Requirements
- `npm install -g @jackwener/opencli` (Node.js >= 21)
- Collectors are PUBLIC (no login). xueqiu cookies-based ones reserved for v1.2.

## v1.0.0 — 2026-05-18

### Added
- **Docker support**: `Dockerfile` + `docker-compose.yml` for zero-config deployment.
- **README polish**: three install paths (PyPI / Docker / source), all 13 CLI commands documented in quick-start.
- **Badges**: PyPI version, Python compat, tests, license, status.

### Changed
- Bumped version to **1.0.0** — stable API.
- README quick-start rewritten to highlight Docker as 2-minute path.
- `Development Status` classifier updated to `5 - Production/Stable`.

### Stability promise from 1.0
- All public APIs (`BaseLoader`, `BaseModel`, `BaseIngester`, `BaseNewsCollector`, `BaseF10Collector`, `KnowledgeBase`, `SubAgent`, registries, CLI subcommands) follow semver from here.
- Breaking changes require major version bump.
- v1.x will focus on stability + ecosystem (additional collectors / models / docs), not protocol changes.

### Capabilities at 1.0
- 13 single-stock sub-agents in three trust tiers + 5 market-level agents + introspector + ask-agent = 20 agents total
- 7 swarm presets: stock-deep-dive, mainline-radar, morning-brief, intraday-review, dream (implicit)
- 12 MCP tools exposed for Claude Desktop integration
- 11 CLI subcommands (report / ask / ingest / dream / mainline / brief / intraday / models / loaders / agents / collectors / version)
- 290 tests + 1 opt-in real E2E test
- Memory system: per-agent + _shared + always_include + FTS5 retrieval + hot reload + dream-loop proposals
- BYOM via `config/plugins.yaml` — register your private models / loaders / collectors without forking

## v0.10.0 — 2026-05-18

### Added (MCP Server)
- `src/financial_analyst/mcp_server.py` — MCP stdio server exposing 12 tools to Claude Desktop / Claude Code / OpenClaw.
- Tools: `ask`, `quick_quote`, `quick_factors`, `memory_search`, `list_past_reports`, `read_past_report`, `list_dream_proposals`, `report`, `mainline`, `brief`, `intraday`, `dream`.
- `financial-analyst-mcp` console script entry point registered in `pyproject.toml`.
- `docs/mcp.md` — setup guide + tool reference + security model + troubleshooting.
- `tests/test_mcp_server.py` — 10 unit tests covering tool registry, dispatch, and schema validation.
- `mcp>=1.0` added to dependencies.

### Changed
- Version bump 0.6.0 → 0.10.0.
- README: added MCP Server section + updated test count.

## v0.6.0 — 2026-05-18

### Added
- First PyPI release. Install: `pip install financial-analyst`.
- Polished `pyproject.toml` with full metadata (classifiers, urls, keywords, authors).

### Changed
- README quick-start lead now shows `pip install financial-analyst` instead of `git clone`.
- Version bump 0.5.0 → 0.6.0.

### Notes
- No functional code changes vs v0.5.0. This release is packaging-only.

## v0.4.0 — 2026-05-18

### Added (BYOM: Bring Your Own Models)
- `BaseNewsCollector` ABC — plug-in interface for auto-collecting news into `news/<code>/` drop-zone (`data/collectors/news/base.py`).
- `BaseF10Collector` ABC — plug-in interface for F10 data (公司公告/龙虎榜/大宗交易) into `f10/<code>/` (`data/collectors/f10/base.py`).
- 4 example stubs under `examples/`:
  - `custom_model_fm_cluster.py` — FM cluster model pattern
  - `custom_loader_csv_only.py` — minimal CSV-backed `BaseLoader`
  - `custom_news_collector.py` — Tushare news API skeleton
  - `custom_f10_collector.py` — pytdx F10 skeleton
- Plugin discovery: `config/plugins.yaml` lists user `.py` files exec'd at startup (`src/financial_analyst/plugins.py`).
- CLI introspection: `financial-analyst {models,loaders,agents,collectors} list`.
- `docs/byom.md` — full Bring-Your-Own-Models guide.

### Changed
- README "Extending" section now points to BYOM workflow.
- `tests/test_agent_registry.py` no longer pollutes the `SubAgentRegistry`; fixtures clear it.

## v0.3.0 — 2026-05-18

### Added (Ingest + Dream Loop)
- **CSV → Qlib binary ingester** (`data/ingest/csv_ingester.py`) with both long-format and per-code-filename support, schema-configurable, ohlcv field mapping.
- `BaseIngester` ABC + reserved `AkshareIngester` / `YfinanceIngester` stubs for v0.4+.
- CLI: `financial-analyst ingest --source <name> [--dry-run]`.
- **Dream loop** for agent self-improving memory:
  - `OutcomeTracker` — measure T+5d/T+20d outcomes against past predictions in `out/*.json`, scoring verdict ∈ {correct, wrong, partial, pending}.
  - `Introspector` sub-agent — LLM-driven post-mortem analyst (NOT in stock-deep-dive preset).
  - `ProposalWriter` — writes `Introspector` proposals to `memories/_proposed/<agent>/<date>_<slug>.md` with YAML frontmatter.
  - `memories/introspector/introspector_rules.md` meta-rules (focus on wrong>partial>correct, 2/3-5/6+ confidence thresholds, target risk-officer when in doubt).
- CLI: `financial-analyst dream [--since 30] [--dry-run]`.
- TUI: `/dream`, `/memory list-proposals`, `/memory accept _proposed/<file>`, `/memory reject _proposed/<file>`.
- `docs/data_ingest.md`, `docs/dream_loop.md`.

### Changed
- `config/data_sources.yaml` template added for ingester config.
- Memory CLI usage strings updated to list all 11 subcommands.

### Safety
- Dream proposals require human review (no auto-merge); auto-accept is explicitly NOT implemented.
- `/memory accept` only operates on paths starting with `_proposed/`.

## v0.2.3 — 2026-05-18

### Fixed (Hotfix found during real SH600666 testing)
- **`AgentMemory.load_relevant` falls back to `load_all` on 0 FTS5 hits** — prevents agents going "blind" when the JSON-derived query doesn't match memory wording.
- **Per-agent `always_include.txt`** — listed files load unconditionally regardless of retrieval results. Initial entry: `memories/risk-officer/always_include.txt` lists `hard_rules.md` (game-capital veto must never be missed).
- **`report-writer` post-validation** — if `risk-officer.veto_flags` is non-empty OR `rating_overall ≤ 0`, `position_pct` is forced to 0 and `action` re-derived. Sanity-override notes appended to the markdown report.
- **`mv_tier` enum** — `fundamental-analyst.FundamentalOutput.mv_tier` changed from `str` to `Literal["large","mid","small"]`; pre-normalize Chinese variants (`中小盘`→`small`, `大盘`→`large`, etc.) before pydantic validation.

## v0.2.2 — 2026-05-18

### Added
- **5min bar support**. `QlibBinaryLoader` now accepts `dict` provider_uri with `day` + `5min` (+ optional `1min`) keys.
- `BaseLoader.fetch_quote` signature extended with `freq: str = "day"` (backward compatible).
- `factor-computer` auto-fetches 5min bars where available, activating:
  - **board_scorer v5 `seal_micro` dimension** (-3..+3): `seal_bar`, `seal_at_close`, `gap_open`, `open_count`.
  - **volume_regime R11 `tail_surge`** signal: last-30-min volume + return ramp.
  - **R14 super_distr** combined signal (`r9_distr AND r11_tail_surge`).
- TushareLoader gracefully returns empty DataFrame for non-day freq.

## v0.2.1 — 2026-05-18

### Added
- `ParquetCache` wired into `TushareLoader` (cache miss → API; cache hit → no network). Configurable TTL (default 86400s = 1 day) and `enable_cache=False` opt-out.
- `QlibBinaryLoader` reads Qlib binary directories — zero-network microsecond reads. Schema: `<provider_uri>/calendars/day.txt` + `instruments/all.txt` + `features/<code_lower>/<field>.day.bin` (4-byte float32 start_index + float32 array).
- `loader_factory.get_default_loader()` — reads `config/loaders.yaml` to instantiate the configured default. Sub-agents (`quote-fetcher`, `factor-computer`, `LGBMomentumModel`) use this factory.
- `config/loaders.yaml` template with both `tushare` (cache) and `qlib_binary` options.

### Changed
- `TushareLoader` re-implemented using raw `requests.post` (bypasses the `tushare` Python library's round-robin to `api.waditu.com` which times out behind corporate proxies). HTTP only.
- `quote-fetcher` uses `_safe_float` for `daily_basic` fields (handles None/NaN gracefully for stocks without dividends, etc.).
- `cli.py` calls `load_dotenv(override=True)` so `.env` overrides shell env vars (fixes Windows user-level TUSHARE_TOKEN conflicts).
- `llm.client` now explicitly passes `api_key` from per-provider `api_key_env` so LiteLLM doesn't fall back to OPENAI_API_KEY when using qwen/deepseek/etc.
- `config/llm.yaml` default switched to Qwen (`qwen3.5-plus`) since most users have DashScope keys, not Anthropic.
- Report writer renders the markdown report inline in terminal (Rich Markdown) and exports a colored HTML copy next to the .md. Clickable `file:///` URL in TUI output.
- Forced UTF-8 stdout/stderr at module load (cli.py + tui.py) so Windows zh-CN PowerShell doesn't choke on `¥` / emoji / rare CJK.

## v0.2.0 — 2026-05-18

### Added
- `MemoryIndex` — SQLite FTS5 full-text index over `memories/**/*.md` with CJK tokenization, incremental updates, agent-filtered search.
- `AgentMemory.load_relevant(query, top_k)` — hybrid retrieval that pulls top-K snippets via FTS5 while always including `_shared/` core rules. Backward-compatible with `load_all()`.
- Per-agent `memory_mode: full | retrieval` configuration in swarm preset YAML. Defaults to `full` (v0.1 behavior preserved).
- TUI `/memory` subcommands: `search`, `show`, `edit`, `stats`, `diff`, `reindex` (in addition to existing `list`, `reload`).
- `bear-advocate` and `risk-officer` opted into retrieval mode by default (biggest memory libraries).

### Changed
- `SubAgent.__init__` accepts optional `index: Optional[MemoryIndex] = None`.
- `swarm.load_preset()` accepts `memory_index` parameter; passes through to retrieval-mode agents only.
- `MemoryIndex.stats()` now includes `total_bytes` and `per_agent_bytes`.

### Token cost impact
- Single-stock report: ~80K → ~30K tokens (estimated 62% reduction) when both retrieval-mode agents are exercised.
- Per-report Qwen cost: ~¥0.05 → ~¥0.02.

## v0.1.0 — 2026-05-17

### Initial release
- 13 sub-agents in 3 trust tiers (5 Tier-1 data fetchers, 4 Tier-2 analysts, 4 Tier-3 decision agents).
- Pluggable per-agent memory (`memories/<agent>/*.md`) with `_shared/` cross-agent playbook.
- Tushare data loader, LGB momentum model, LiteLLM multi-provider abstraction.
- Rich TUI with prompt-toolkit REPL.
- 100+ pydantic-validated tests, opt-in real E2E test.
