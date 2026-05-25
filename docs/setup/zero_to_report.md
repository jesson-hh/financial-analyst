# 从零到第一份研报 (Zero → Report)

> 目标读者: 第一次跑 financial-analyst 的开发者 / 量化研究员.
> 全部跟完: 60 分钟 (含 Tushare 注册 + LLM 充值 ~10 min, 数据准备 ~30 min, 跑第一份 ~10 min).
>
> 已经熟悉 Python venv + 装过 LLM key 的, 直接跳到 [§5 第一份研报](#5-跑第一份研报).

## 0. 前置检查

| 项 | 要求 | 检查命令 |
|----|------|---------|
| OS | Windows 10/11 64-bit (推荐) / macOS / Linux | — |
| Python | 3.11 或 3.12 | `python --version` |
| 磁盘 | 10GB+ 可用 (轻量模式) / 50GB+ (完整 A 股) | — |
| 内存 | 8GB 起步, 16GB+ 推荐 (跑 14-agent 时 ~3GB 峰值) | — |
| 网络 | 直连国内 (雪球 / 腾讯行情) + 走代理 (LLM 海外 API 可选) | — |

> Windows 用 conda 也可以, 但本文档默认 `python -m venv`. 区别只是激活脚本路径.

---

## 1. 注册数据 API (10 分钟)

### 1a. Tushare (必须)

研报需要 daily_basic (PE/PB/PS/股息/市值/换手率). Tushare Pro 免费等级 (积分 100+) 已够用.

1. 浏览器开 https://tushare.pro/register?reg=458678
2. 注册 + 邮箱验证
3. 个人中心 → 接口 TOKEN, 复制
4. 留着, 第 4 步要填到 `.env`

> 完整 daily_basic 接口需要积分 ≥ 100. 注册即送 100 分. 高频跑 (每日 5000+ 调用)
> 需要充值升级到积分 5000+. 本项目日更脚本日均消耗 ~1500 次, 通常免费够用.

### 1b. 阿里云百炼 / DashScope (必须 — LLM)

14-agent 都用 qwen3.5-plus 跑. 阿里云首次注册送 100 万 token 体验额度, 跑 ~150 份研报.

1. 浏览器开 https://bailian.console.aliyun.com/
2. 阿里云账号登录 → 实名认证 → 开通百炼
3. 右上角"API-KEY" → 创建新 Key, 复制
4. 留着

> 一份研报消耗 ~3-8 万 token (含 14 agent + memory injection). 公开价 qwen3.5-plus
> 输入 4¥/百万 token, 输出 12¥. 单份 ~0.2-0.5 元.

### 1c. 可选: Anthropic / OpenAI / DeepSeek
任一替代或补充. `.env` 里只填你有的, LLMClient 自动按 provider 优先级降级:
DashScope > OpenAI > Anthropic > DeepSeek.

---

## 2. 装包 (5 分钟)

### 路径 A — PyPI (零开发, 推荐)

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install financial-analyst
# 一行拉所有运行时依赖 (含 fastapi + uvicorn, 给 GuanLan UI 后端用)
```

### 路径 B — 源码 (开发 / 改 agent 源码)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
python -m venv .venv
.venv\Scripts\activate
pip install -e .[dev,serve]   # editable, 改 src/ 立即生效
```

### 验证装好了

```bash
financial-analyst version
# → financial-analyst 1.9.4

financial-analyst agents
# → 列出 15 个 built-in agent (含 introspector + market scanner)
```

---

## 3. 配 .env (1 分钟)

仓库根目录 `.env.example` 复制成 `.env`:

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

编辑 `.env` 填进去:

```dotenv
# 必填 — LLM
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxx

# 必填 — Tushare
TUSHARE_TOKEN=xxxxxxxxxxxxxxxxxxxx

# 可选 — 备用 LLM provider
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
DEEPSEEK_API_KEY=

# 可选 — 日志 / 缓存
FA_LOG_LEVEL=INFO
FA_CACHE_DIR=~/.financial-analyst/cache

# 可选 — 主线雷达 panel (G:/stocks/strategy 才有)
FA_MAINLINE_PANEL=
```

> ⚠ `.env` 不要 commit. 已在 `.gitignore`.

---

## 4. 数据准备 (30 分钟)

研报需要 **Qlib bin 格式日线 OHLCV + daily_basic**. 三种获取方式:

### 路径 A — 你已有 G:/stocks 项目 (推荐)

如果你也在跑 G:/stocks 量化研究环境, 已经有完整 Qlib 数据, **直接用**:

```bash
# financial-analyst 默认从环境变量 / config/loaders.yaml 读 Qlib URI
# 编辑 config/loaders.yaml 把 qlib_binary.provider_uri.day 指向 G:/stocks/stock_data/cn_data
```

跳到 [§5](#5-跑第一份研报).

### 路径 B — 从零建库 (~30 min, 走 Tushare)

```bash
# 装 G:/stocks 项目的数据脚本依赖
pip install qlib pytdx

# 拉 5 年日线 + daily_basic (~10000 个股票 × 5y = ~30 min)
python -c "
from financial_analyst.data.ingesters.tushare_ingester import bootstrap
bootstrap(since='20200101', codes=None)  # None = 全 A 股
"
```

或者用 G:/stocks 单一入口:

```bash
python G:/stocks/scripts/incremental_update_tushare.py --since 20200101
```

详见 [`data_pipeline.md`](data_pipeline.md).

### 路径 C — 你只想 demo 几只股票

```bash
# 临时拉 SH600519 单只
python -c "
from financial_analyst.data.ingesters.tushare_ingester import bootstrap
bootstrap(since='20240101', codes=['600519.SH', '300750.SZ', '002594.SZ'])
"
```

---

## 5. 跑第一份研报

```bash
financial-analyst report SH600519
```

正常情况下会看到:

```
Running stock-deep-dive for SH600519 (asof=2026-05-23)…
[Tier 1] quote-fetcher    ✓ 0.02s
[Tier 1] factor-computer  ✓ 0.36s
[Tier 1] model-predictor  ✓ 0.07s
[Tier 1] news-reader      ✓ 0.00s
[Tier 1] f10-reader       ✓ 0.00s
[Tier 2] fundamental      ✓ 89.97s   ← Tier 2 全并行, 看最慢一个
[Tier 2] technical        ✓ 51.06s
[Tier 2] whale            ✓ 87.82s
[Tier 2] quant            ✓ 70.53s
[Tier 3] bull-advocate    ✓ 67.47s   ← bull/bear 并行
[Tier 3] bear-advocate    ✓ 95.73s
[Tier 3] risk-officer     ✓ 57.34s
[Tier 3] report-writer    ✓ 137.19s  ← 单线程, 最长
[Tier 4] introspector     ✓ 86.90s   ← 异步 post-mortem

Report saved: out/SH600519_2026-05-23.md
```

**~7 分钟**. 看报告:

```bash
# Windows
notepad out\SH600519_2026-05-23.md

# 或浏览器开 HTML 版
start out\SH600519_2026-05-23.html
```

报告结构 (固定 8 段):

1. 综合评级 (五维总分 -10..+10)
2. Variance Table (实际 vs 共识)
3. 基本面
4. 技术与情绪 (走势 / 主力行为 / 量能 regime)
5. 量化共识
6. 多空辩论 (BullAdvocate + BearAdvocate, 含 V#/F# 锚点)
7. 风控审查 (RiskOfficer, blind_spots + veto + 仓位)
8. 操作建议 (目标价 + 仓位 + 止损 + 监控事件)

JSON 版 (`SH600519_2026-05-23.json`) 是结构化字段, 给 UI / 后续 dream loop 用.

---

## 6. 跑 GuanLan UI 桌面工作站 (可选)

CLI 看 markdown 不爽? 开 GuanLan UI:

```bash
# 终端 A: 起后端
financial-analyst serve --port 9999
# 验证: 浏览器开 http://127.0.0.1:9999/health 应回 {"ok":true,"version":"1.9.4",...}

# 终端 B: 起前端
cd G:/stocks/fa_ui_ready
python -m http.server 5173

# 浏览器开 http://localhost:5173
# 在 index.html 第 64 行确保 window.GUANLAN_BACKEND = 'http://127.0.0.1:9999';
```

或一键启动:
```bash
fa launch
```

详见 [`docs/ui/guanlan_user_guide.md`](../ui/guanlan_user_guide.md).

---

## 7. 多会话 / 后台批量

### 多会话
```bash
financial-analyst chat
> /sessions new my-茅台研究
> 看下茅台怎么样
> /sessions new my-比亚迪
> 比亚迪呢
> /sessions list
```

### 批量研报
```bash
# 把代码写进文件 (一行一个)
echo SH600519 > codes.txt
echo SZ300750 >> codes.txt
echo SZ002594 >> codes.txt

# 顺序跑
financial-analyst report -f codes.txt
```

跑 N 只需要约 N × 7 分钟. 大批量建议挂后台:

```bash
nohup financial-analyst report -f codes.txt > batch.log 2>&1 &
# 完后用 dream 聚合 introspector 提议
financial-analyst dream review
```

---

## 8. 常见错误

### Q: `pip install` 后 `financial-analyst: command not found`
A: venv 没激活, 或 `pip install --user` 装到了用户 site-packages 但 PATH 没指. 重新激活 venv.

### Q: `dashscope.api_entities.dashscope_response.HTTPResponse 401 Unauthorized`
A: DASHSCOPE_API_KEY 错或没付 (体验额度耗尽). 控制台看一下用量.

### Q: `tushare TushareError: 抱歉，您没有访问该接口的权限`
A: Tushare 积分不够 100. 个人中心补提交资料 (姓名 + 单位) 或充值.

### Q: `qlib.utils.exceptions.LoadObjectError: ... 600519.day`
A: Qlib bin 没建 / 路径不对. 见 [§4](#4-数据准备-30-分钟) + `data_pipeline.md`.

### Q: 报告里 `pe: null`, `mv_yi: null`
A: daily_basic 没拉到. 检查 `Stock_data/cn_data/features/sh600519/pe_ttm.day.bin` 是否存在.

### Q: 跑了 30 分钟还没出报告
A: LLM 慢或网络抖动. 看终端是不是卡在某个 Tier. 14-agent 正常 ~7 min, ≥ 15 min
   就不正常.

### Q: 报告里 `[V0] (LLM 未能给出明确看多论点...)`
A: bull-advocate 占位兜底触发了. 重跑一次. 持续触发 = 说明这只股票 bull 论点确实
   很难找 (例如基本面 + 技术面 + 主力全都-2), 这是正确行为.

---

## 9. 下一步

- 看完整 architecture: [`docs/architecture/14_agents.md`](../architecture/14_agents.md)
- 接 SSE API 自己写客户端: [`docs/api/sse_endpoints.md`](../api/sse_endpoints.md)
- 加你自己的私有模型: [`docs/byom.md`](../byom.md)
- 用 dream loop 自迭代 agent 经验: [`docs/dream_loop.md`](../dream_loop.md)
- G:/stocks 数据 pipeline 完整说明: [`docs/setup/data_pipeline.md`](data_pipeline.md)
- GuanLan UI 使用: [`docs/ui/guanlan_user_guide.md`](../ui/guanlan_user_guide.md)
