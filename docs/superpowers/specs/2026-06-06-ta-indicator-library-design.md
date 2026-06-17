# 已验证 TA 指标库(炼·因子表达式 grounding 扩展)— 设计规格

- 日期:2026-06-06
- 模块:`guanlan_v2/factorlib`(新增 TA 指标族)+ `guanlan_v2/cards/refine`(grounding 消费)
- 状态:设计待审阅(brainstorming → 待 writing-plans)

## 1. 背景与问题

经验卡「炼」步骤靠引擎 `LLMClient.for_agent("cards")`(deepseek)把交易概念译成**引擎因子 DSL**。
目前给模型的 grounding = 手写的 `guanlan_v2/cards/factor_dsl_kb.md`(15 条通用范例 + 白名单 + 不支持清单 + 组合规则)。

实测发现一个被低估的事实:**MACD / RSI / KDJ / BOLL / WR 等 TA 指标并不是引擎不支持,而是"表达式没写进知识库"。**

- 引擎算子里有 `sma(x, n, m)` —— GTJA 式递归平滑,本质是 EMA(平滑系数 α = m/n)。
  于是 **P 日 EMA = `sma(x, P+1, 2)`**(EMA 的 α = 2/(P+1))。
- 配合 `ts_min` / `ts_max` / `ts_mean` / `stddev` / `delta` / `max_pair` / `cross`,可重建大部分 TA 指标。
- 已用 `/factor/report`(universe=csi_fast)实测 **status=ok**:
  - MACD 金叉 `cross(sma(close,13,2)-sma(close,27,2), sma(sma(close,13,2)-sma(close,27,2),10,2))`
  - RSI-14 `100*sma(max_pair(delta(close,1),0),14,1)/(...)`(ic_mean 0.082)
  - KDJ-K `sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9))*100,3,1)`(ic_mean 0.065)

**原 `factor_dsl_kb.md` §二 把这些列为"必定编译报错 / 无法量化 / expr 留空" —— 这是错误且过度保守的**,会让模型对本可量化的概念错误拒答。

## 2. 目标

1. 系统性补齐"概念→可编译因子表达式"缺口,**不靠人逐条手写散文**。
2. "已验证"名副其实:每条表达式经 `/factor/report` 实测 **status=ok** 才入库。
3. 「炼」的模型照已验证范例写出**完整、透明**的 expr(用户已选定的消费方式:卡片 expr 是公式本身,不是不透明的注册名)。
4. 修正 `factor_dsl_kb.md` 的错误"不支持清单",只保留**真缺口**。

## 3. 非目标(YAGNI / 本期不做)

- 不做生成时"编译校验→失败回喂模型重试"闭环(那是另一条解法方向;本期只做库)。
- 不给引擎加新原语;OBV / CCI / SAR 等**真缺口**留作未来 engine-side 工作。
- 不改 `engine/` 任何文件;不 push、不合 main。
- 不重建 UI;炼/验前端链路不动(只是 grounding 变准、变全)。

## 4. 架构

挂在**现有** `guanlan_v2/factorlib/` 上,沿用 `cards` / `seats` 先例(薄壳 `create_app` 已挂载,`register_library_factors()` 启动自动拾取 `base/*.json`)。三层:

### A. 作者层 —— `guanlan_v2/factorlib/base/ta_indicators.json`(family = `ta`)

唯一的"手写",但只写一次、是**结构化数据**(`{name, family, expr, description, source}`),不是散文。
分母一律 `+1e-8`(沿用现有 `lib_*` 规矩,避免停牌/一字板除零)。

v1 候选集(每条实现期都过 `/factor/report` 闸门;不过的剔除或修正,记台账):

| name | 概念 | expr(引擎 DSL,重建写法) |
|---|---|---|
| `ta_macd_dif` | MACD DIF(EMA12−EMA26) | `sma(close,13,2) - sma(close,27,2)` |
| `ta_macd_dea` | MACD DEA(DIF 的 9 日 EMA) | `sma(sma(close,13,2) - sma(close,27,2),10,2)` |
| `ta_macd_hist` | MACD 柱 | `2*((sma(close,13,2)-sma(close,27,2)) - sma(sma(close,13,2)-sma(close,27,2),10,2))` |
| `ta_macd_golden_cross` | MACD 金叉(DIF 上穿 DEA) | `cross(sma(close,13,2)-sma(close,27,2), sma(sma(close,13,2)-sma(close,27,2),10,2))` |
| `ta_macd_dead_cross` | MACD 死叉 | `cross(sma(sma(close,13,2)-sma(close,27,2),10,2), sma(close,13,2)-sma(close,27,2))` |
| `ta_rsi6` / `ta_rsi12` / `ta_rsi14` / `ta_rsi24` | RSI(n=6/12/14/24) | `100*sma(max_pair(delta(close,1),0),N,1)/(sma(max_pair(delta(close,1),0),N,1)+sma(max_pair(-delta(close,1),0),N,1)+1e-8)` |
| `ta_kdj_k` | KDJ-K | `sma((close-ts_min(low,9))/(ts_max(high,9)-ts_min(low,9)+1e-8)*100,3,1)` |
| `ta_kdj_d` | KDJ-D | `sma(ta_kdj_k 展开,3,1)` |
| `ta_kdj_j` | KDJ-J | `3*K - 2*D`(K/D 展开) |
| `ta_kdj_golden_cross` | KDJ 金叉(K 上穿 D) | `cross(K, D)`(展开) |
| `ta_boll_pctb` | 布林 %B(close 在带内位置) | `(close - ts_mean(close,20) + 2*stddev(close,20))/(4*stddev(close,20)+1e-8)` |
| `ta_boll_bandwidth` | 布林带宽 | `(4*stddev(close,20))/(ts_mean(close,20)+1e-8)` |
| `ta_boll_upper_break` | 上穿布林上轨 | `cross(close, ts_mean(close,20)+2*stddev(close,20))` |
| `ta_wr14` | 威廉 %R(0~−100) | `-100*(ts_max(high,14)-close)/(ts_max(high,14)-ts_min(low,14)+1e-8)` |
| `ta_bias20` | 乖离率(20 日) | `(close-ts_mean(close,20))/(ts_mean(close,20)+1e-8)` |
| `ta_roc20` | 变动率(20 日) | `close/(delay(close,20)+1e-8) - 1` |
| `ta_atr14` | 平均真实波幅 | `ts_mean(max_pair(max_pair(high-low, abs(high-delay(close,1))), abs(low-delay(close,1))),14)` |

说明:KDJ-D/J、金叉条目里的 `K`/`D` 在 JSON 里写成完整展开式(DSL 无变量绑定)。

诚实注脚:这些是**指标的忠实计算值**,不预设交易方向;好不好用由「验」的真 IC 揭示,库不替它下结论。

### B. 验证层 —— `scripts/verify_ta_indicators.py` + 快速单测

- **验证脚本(live,需 9999 + 数据)**:逐条 POST `/factor/report`(universe=csi_fast)→ 断言 `status=ok` → 打印台账(`name / status / ic_mean / coverage / error`)。
  **只有 status=ok 的留在 JSON**;不过的剔除/修正。台账范式沿用 factorlib README。可重复跑(引擎变更后回归)。
- **快速单测(进常规 pytest,不碰数据)**:每条过引擎 `validate_expr` + `compile_factor`;断言无 forbidden token、能编译;`LibraryFactorStore` 注册计数 = JSON 条目数。

### C. 消费层 —— 注册 + 炼 grounding

1. **注册(白拿)**:`register_all()` 启动时自动把 `ta_*` 校验+编译+注册进引擎运行期 zoo registry,
   出现在 `/factor/list` 的 `registered`;`/factorlib/list` 标 `valid=true`。screen/workflow 可复用。
   **无需改 `server.py`**(它已 `register_library_factors()`,自动拾取新 JSON)。
2. **炼 grounding**:`refine.py` 读 `ta_indicators.json` → 格式化成"概念 → 完整 expr"范例块 → 追加进 `SYSTEM_PROMPT`。
   范例永远 = 已入库(已验证)集合,**不会再像上次那样写错或漂移**。
3. **修正 `factor_dsl_kb.md`**:
   - **删除** §二里"MACD / KDJ / RSI / BOLL 无法量化、留空"的错误表述;改为"这些可由 `sma`/`ts_min`/`ts_max` 重建,见 TA 指标库范例"。
   - **只保留真缺口**为"不支持、expr 留空":`OBV`(缺"上市起累加"的 expanding cumsum)、`CCI`(缺平均绝对偏差)、`SAR`(缺抛物线递归)—— 并写明缺的是哪种原语。
   - 保留 §一(通用 alpha 范例)、§三(组合用 `*` 不用 `and`/`or` 规则)。

## 5. 数据流

```
炼请求 POST /cards/refine
  → build_refine_messages(SYSTEM_PROMPT = 基础规则 + factor_dsl_kb.md[已修正] + 生成的 TA 范例块)
  → deepseek 返回 JSON(expr = 完整 DSL 公式)
  → parse_refine_output → 卡片 patch
验时 expr → POST /factor/report → 真单因子回测(已有安全网:非法 expr 当场 compute_error)
```

## 6. 错误处理 / 诚实失败

- 验证脚本:`status≠ok` 的条目**不入库**,台账记原因(不写假因子)。
- `register_all()` 已是幂等、单条失败记 `ledger`、`skipped+1`、不崩、不阻断启动。
- 除零:分母 `+1e-8`。
- 引擎不可导入时 `list_factors` 跳过校验仍出清单(现有行为,不回归)。

## 7. 测试

- **单测(常规 suite,不碰数据)**:
  - `ta_indicators.json` schema(每条有 name/expr/family);
  - 每条 `validate_expr` + `compile_factor` 通过;
  - `LibraryFactorStore` 注册计数 = 条目数;
  - `refine.SYSTEM_PROMPT` 注入断言:含 TA 范例(如 `sma(close,13,2)`),且 §二**不再**声称 MACD 无法量化、OBV/CCI/SAR 仍在真缺口清单。
- **验证脚本(live)**:`/factor/report` status=ok 台账;作为入库门槛与回归。

## 8. 影响文件

- **新增**:
  - `guanlan_v2/factorlib/base/ta_indicators.json`
  - `scripts/verify_ta_indicators.py`
  - `tests/test_ta_indicators.py`(或并入 `test_factorlib_*` / `test_cards_refine.py`)
- **改**:
  - `guanlan_v2/cards/refine.py`(注入 TA 范例块)
  - `guanlan_v2/cards/factor_dsl_kb.md`(修正 §二)
  - `guanlan_v2/factorlib/README.md`(加 TA 族台账)
  - `ui/cards/README.md` / `docs/module_map.md`(状态/开放项,按需)
- **不改**:`engine/`、`guanlan_v2/server.py`(`register_all` 自动拾取新 JSON)。

## 9. 验收标准

1. `/factorlib/list` 含 `ta_*` 且 `valid=true`;`/factor/list` 的 `registered` 增加 N 条(N = 验证通过数)。
2. `/cards/refine` 对"给这条经验写个 MACD 金叉因子"输出**可编译**的 `sma` 重建 expr;对 OBV/CCI/SAR 仍诚实留空并说明。
3. `scripts/verify_ta_indicators.py` 台账:入库条目全部 status=ok。
4. `pytest` 全绿。

## 10. 未来开放项(非本期)

- 解法方向一(生成时"编译校验→回喂重试"闭环):覆盖无限长尾概念,作为本库的安全网叠加。
- 给引擎补 `OBV/CCI/SAR` 所需原语(expanding cumsum / 平均绝对偏差 / 抛物线递归)。
- TA 指标的方向化/标准化封装(若发现某指标 IC 稳定,沉淀成带方向的 alpha)。
