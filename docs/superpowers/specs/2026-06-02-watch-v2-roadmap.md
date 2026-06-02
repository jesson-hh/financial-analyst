# 盯盘 v2 完善 Roadmap — A 知识注入 / B 触发器扩容 / C 复盘闭环

> 状态: roadmap 草案 (待逐包 brainstorm→spec→plan→实现). 用户 2026-06-02 选定 **A+B+C**, 执行顺序 **A→B→C**.
> 前置: 盯盘 v1 已完工+三层验证 (172 单测 / 真实数据冒烟 / 浏览器 240 蜡烛+SSE / 真 deepseek 决策). merge-stocks commits `42aad3f`(功能) `f5f949f`(deepseek seed) + main 工作树未提交 `config/llm.yaml`(watch-agent→deepseek).

## 总原则
- **只接已验证的 alpha**, 不凭空加功能 (项目护城河 = 沉淀知识 + 验证信号).
- watch 是 **fa 侧** (py3.13, 无 qlib); 验证逻辑很多在 **stocks/research 侧** (py3.10+qlib). 跨仓库**零 import** —— 要么读 parquet, 要么 port 纯函数.
- 每包 TDD, subagent-driven; 改 research/ 经验 markdown 立即生效不用改代码.

---

## A. advisor 注入项目知识 ⭐ 先做 (性价比最高)
**目标**: watch-agent 的 `build_messages` system/user prompt 注入项目沉淀知识, 让盘中决策对齐验证过的策略 (现在 prompt 只有通用"反转/T+1/涨跌停").

**复用资产**:
- fa 已有机制: 其它 agent 用 `_resources/memories_seed/{agent}/*.md` 注入 (如 `bull-advocate/factor_insights_long_side.md`, `_shared/playbook_V1_V10.md`). **唯独 watch-agent 没 memory**.
- 知识源: R7-R20 情绪信号 (super_distr fwd_5d -4.2pp / seal_bar / tail_surge) · pitfalls (游资博弈票模型失效 / 系统性行情失效) · V1-V10 分析师视角 · rating_system 市值分层 · 反转为核心.

**方式 (两选一, brainstorm 定)**:
- (a) 给 watch-agent 建 `_resources/memories_seed/watch-agent/*.md` (仿其它 agent), build_messages 读 memory 注入.
- (b) 在 `watch/agent.py` 内联一个精炼"盘中决策守则"知识块 (固定字符串, 不走 memory 机制).

**开放问题**: 注入哪些 + 多长 (prompt 预算 vs 盘中延迟); memory 机制 vs 内联; 是否按 trigger_kind 选择性注入 (如 stop_break 注入风控守则).

**文件**: `_resources/memories_seed/watch-agent/*.md` (若选 a) · `watch/agent.py` (build_messages) · `tests/test_watch_agent.py`.

---

## B. 触发器扩容 (把验证信号接成触发)
**目标**: 现在只 breakout_high/volume_surge/stop_break/news 四种, 接已验证 alpha.

**⚠ 跨仓库可行性 (定做法)**:
- **B1 tdx_f10 负向事件硬卖** —— `tdx_f10_warnings_latest.parquet` 是 **parquet**, fa 经 `get_data_paths()` 直读 → **零 import, 最易, 先做**. severity≥2 持仓硬卖 / 未持仓禁建仓 (复刻 rule_signal_generator 的 `load_warnings_dict` 语义, 但 fa 直读 parquet).
- **B2 volume_regime super_distr 减仓预警** —— `strategy/sentiment/volume_regime.py` 是**纯 pandas** (super_distr=r9_distr AND r11_tail_surge, fwd_5d -4.2pp 月度 11/12 SS). fa **不能 import** → **port 纯函数进 `watch/`** (输入 close_day/turnover/bars_5m, 输出 regime label). 复刻需带单测对拍 stocks 版数值.
- **B3 board_scorer v5 首板/封板** —— 同样 port 纯函数 (可选, 仅当盯到涨停票).
- **B4 止盈到目标价 / 均线破位 / RSI** —— 纯 fa 侧, 在 `backtest.intraday` 风格的触发器里加.

**优先**: B1 (读 parquet) → B2 (port super_distr) → B3/B4.

**开放问题**: 触发是**硬规则**直接出 rec (如 severity≥2 硬卖, 不耗 LLM) 还是仍走 advisor; port 的纯函数放 `watch/signals.py`; parquet 路径/刷新频率.

**文件**: `watch/triggers.py` (扩 check_item) · 新 `watch/signals.py` (port 的纯函数) · `watch/feed.py` (读 warnings parquet / EOD 序列) · tests.

---

## C. 推荐复盘闭环 (self-improving)
**目标**: 推荐不止记 parquet + 人工 ack, 加 **T+1/T+5 outcome 打分** + **advisor 命中率看板** + 推荐历史页.

**复用资产**: fa `backtest` 的 `Outcome`/`_action_verdict` (records.py, 已切断 dream 依赖) · fa `data/loaders/qlib_binary.py` 读日线 bin 算 fwd 收益 · rec parquet 已有 `ts/code/action/target_price/stop_loss/user_action`.

**可行性**: fa 能读日线 bin (qlib_binary loader) 算 T+1/T+5 ret; outcome 口径 = action→预期方向 (buy/add 期望涨, reduce/sell 期望跌/避损). 离线回填 (盘后批量), 非实时.

**开放问题**: 打分口径细则; 命中率看板放 quant.jsx (WatchMode 加 tab/页) 还是 CLI; 是否回灌进 advisor (真 self-improve = 把命中率统计注入 A 的知识块).

**文件**: 新 `watch/outcome.py` (scorer) · `buddy/server.py` (回填 endpoint / 命中率 GET) · `ui/quant.jsx` (推荐历史 + 命中率) · tests.

---

## 执行建议
- 顺序 A→B→C; 每包独立 brainstorm (几个定向问题) → spec → plan → subagent-driven TDD.
- A 最小可独立交付 (纯 prompt 增强, 零跨仓库风险), 先做能立刻提质.
- 会话过长时先 /compact, 本文件即接续锚点.
