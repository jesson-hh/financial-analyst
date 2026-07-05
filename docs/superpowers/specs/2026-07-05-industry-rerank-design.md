# P6′ · 行业判断上下文重排层(选股榜 LLM 重排 + 经验闭环)— 设计文档

日期:2026-07-05 · 状态:已获用户批准(五决策 + A案 + 七节设计)
上游:P5 rescore 展示层在产(产业链分/情绪分/综合分 + 档案 `var/rescore_runs.jsonl`);P0 picks 落盘 + P1 basket_perf 前向对照在产;研究回路 keyed 教训机制在产。

## 0. 目标与决策记录

**目标**(用户愿景):行业判断是"判断"不是"数据"——**不做成因子、不数值混入 blend/模型特征**;以完整上下文形态交给 LLM 决策层,对数据榜的最终排名做**有理由的调整**;每次"给了什么上下文、调了什么、为什么、之后真实收益如何"落档,反思(经验回路)看得见——P6 经验自迭代的第一块落地。

**用户拍板的五决策**(2026-07-05):
1. **调整边界:top-N 内自由重排**。LLM 输出 top-N 完整新排序;绝不引入池外票、绝不删票;护栏走**显形**(Δ名次徽章,|Δ|≥10 醒目标)而非限制。
2. **生效路径:双轨攒证据,人审后切换**。正式 picks 仍=数据榜(合并零行为变化);调整榜并行落档,两篮子 basket_perf 前向 A/B;证据成熟后用户人审决定是否切换正式口径(本期不实现切换,仅攒证据)。
3. **反思闭环:全闭环**。档案 → A/B 对照 → 蒸馏 keyed 教训 → 最近 K 条教训反哺下次重排上下文。
4. **触发:与再打分合体一键**。选股页按钮升级「再打分+重排」;ww_rescore 同步升级。
5. **自动日跑:opt-in 开关**。`GUANLAN_RERANK_DAILY=1` 复用 P1 现成 regen 调度器(regen 完顺跑一次打分+重排),**不加新定时器**,默认关=合并零行为变化。

**架构裁定(用户确认)**:
- **不进研究回路**。研究回路研究"因子/模型图×历史窗口"(可回测);重排层研究"今日榜×今日行业判断"——行业上下文无历史 PIT 存档,**本质不可回测,只能前向攒证据**。两回路分域,唯教训层汇合(同一 keyed 记忆,帷幄均可召回)。
- **不覆盖现有两处新闻情绪**:L5 决策面板 ≤5 持仓「新闻研判」保留(持仓深看);P5 三列保留并升格为重排上下文原料。同一共享核心在三个粒度分层使用:榜级打分 → 榜序重排 → 持仓深看。
- 链环圈池做因子研究(universe 加链环池)= 独立小项,本期不做,记展望。

**红线(贯穿)**:数据榜/正式 picks 零行为变化;逐级诚实失败显形(LLM 失败 → 数据榜照旧+失败徽章,绝不半吞);采纳(口径切换/教训入库)全人审;无新定时器;UI 只填充;LLM 成本显形。

## 1. 重排引擎 `guanlan_v2/screen/rerank.py`(新,纯函数)

### 1.1 上下文包构建

```python
def build_context_pack(rows, board, market, lessons) -> dict
# rows: rescore run 的逐票行 [{code,name?,rank,v4pct,chain|None,news|None}](rank=数据榜名次 1..N)
# board: industry build_board 现值(链环景气全景:各环 research/therm/quadrant 排序摘要)
# market: {market_read, market_tilt}(rescore stats 现成)
# lessons: 最近 K=5 条「行业·」前缀 keyed 教训原文(读回机制复用研究回路同款;无教训 → 空列表诚实)
```

逐票材料:数据榜名次+v4 分位、链环隶属(环名/链分/象限/research/therm)或「不在链上」、情绪(tag+read)或「无新闻」。**不给任何因子明细**——行业判断只用行业材料,边界干净。

### 1.2 LLM 重排调用

- 一次整批调用(top-N ≤ 50,单 prompt);走 `screen` 座席(`LLMClient.for_agent("screen", config_path=仓内 LLM_CONFIG_PATH)`,生产默认 deepseek-chat;深思考时经 agent_overrides 覆写,与现行惯例一致)。
- 输出 schema:`{"order": [{"code": str, "stance": "顺风|逆风|中性", "reason": str}], "overall": str}`——order 即 top-N 完整新排序(第 1 项=新榜第 1 名)。
- 线程模式照 P5 news_scores:daemon 线程内 `asyncio.run`(仓内已验模式)。

### 1.3 硬校验(诚实护栏)

```python
def validate_order(codes_in: list[str], order: list[dict]) -> tuple[bool, str]
```

输出票集合必须与输入**逐一相等**(无新增/无缺失/无重复),reason 必非空;违者整体 `rerank_failed`,原因显形——数据榜照常展示,**绝不部分采用**。

### 1.4 档案行(反思原料)

rescore run 行内新增 `rerank` 块:

```json
{"ok": true, "model": "deepseek-chat", "elapsed_sec": 12,
 "overall": "……", "lessons_injected": 3,
 "rows": [{"code": "SH600000", "rank_before": 7, "rank_after": 2,
            "stance": "顺风", "reason": "光芯片环景气+…"}]}
```

失败态:`{"ok": false, "reason": "..."}`。上下文包摘要(board 快照标识=`corpus.latest_publish_ts`+`n_docs`,加 lessons key 列表)一并入块,反思可复原"当时它看到了什么"。

## 2. 编排(rescore 状态机加 phase + opt-in 日跑)

- `screen/rescore.py` 状态机在打分完成后新增 `rerank` phase(单飞锁/进度 lines/当日缓存全复用);top_n 同 rescore(钳 [5,100])。
- **opt-in 日跑**:`GUANLAN_RERANK_DAILY=1` 时,P1 regen 调度器(`GUANLAN_REGEN_DAILY` 现成 18 点后钩子)完成 regen 后顺跑一次「打分+重排」;health 显形 `rerank_scheduler` 字段;默认关。两开关独立(日跑重排要求 regen 开关也开,否则 health 显形提示)。
- 端点面不变(POST /screen/rescore 即触发全流水线);response/status/latest 透传 rerank 块。

## 3. 双轨落档与前向 A/B

- 每次重排把**两个篮子**按 picks 档案格式并行落 `var/screen_picks.jsonl`:`{kind: "rerank_ab", arm: "data"|"rerank", codes: 各榜 top-min(10, top_n), run_id, ts}`;`snapshot` 语义不占用(正式选股标记照旧),**现有 picks 消费方默认过滤掉 rerank_ab 行=零行为变化**。
- `GET /seats/basket_perf` 小扩展:`kind=rerank_ab` 查询参数 → 返回两臂篮子的前向收益对照(同口径收盘进/出、eqw 基准、未成熟 matured:false 显形);默认(无参)行为不变。
- **行业判断加值 = rerank 臂 − data 臂**,从此有真数字。

## 4. 反思闭环(P6 首块)

- `ww_rerank_perf`(instant 只读):逐 run A/B 成绩单 + 分桶命中率(按链环/情绪 tag/market_tilt:该桶内被"顺风提升"的票 vs 实际超额)。
- `ww_rerank_distill`(confirm 门):把对照结论蒸馏成 keyed 教训(key=「行业·<主题>」,如「行业·光芯片环顺风判断」),写入帷幄 keyed 记忆(复用研究回路 memory_written 同款机制);**永远人发起,无定时器**。
- 反哺:§1.1 lessons 注入即读这些「行业·」教训——判断→后果→教训→更好的判断,闭环。

## 5. UI(选股页只填充)

- 结果表在 P5 三列旁新增**名次对照列**:`原名次→新名次`(Δ 徽章,|Δ|≥10 醒目色)+ stance 色点(顺风绿/逆风红/中性灰)+ reason tooltip +「LLM 重排」徽章。
- 按钮文案升级「再打分+重排 ✦」;元数据行加重排成本/模型/教训注入数。
- 无 rerank 块的旧档案零占位;`rerank.ok:false` → 失败徽章+数据榜照旧。改 jsx 必 Edit bump html `?v=`。

## 6. 帷幄工具与四处同步

- `ww_rescore` 升级:跑完回「三列分摘要+重排 top 变动(前 5 大 Δ+理由)」;文案改「再打分+重排」。
- 新增 `ww_rerank_perf` + `ww_rerank_distill`(§4)。
- 计数 **46→48 ww / 71→73 console / 50→52 MCP**;WW_TOOL_TABLE / `_SYSTEM_PROMPT`(能力行+纪律:重排是展示参考双轨,正式 picks 未切换前绝不改)/ test_console_tools / test_guanlan_mcp ×3 / glmcp README ×2 全同步。

## 7. 诚实合约汇总

- LLM 失败/校验失败 → `rerank.ok:false` 显形,数据榜照旧,绝不部分采用、绝不编序。
- 调整榜绝不冒充数据榜:两榜名次并排、LLM 徽章、stance/reason 逐票可查。
- 正式 picks/信号/blend/seats 通路零变化;rerank_ab 档案行对现有消费方不可见(默认过滤)。
- 成本显形:llm_calls/model/elapsed 进档案与 UI 元数据行。
- board 不可用/rescore 失败 → 重排不跑(上游诚实失败传导)。

## 8. 测试计划

1. 引擎单测(新 `tests/test_screen_rerank.py`):上下文包(链外/无新闻/无教训各态)/validate_order(增票/删票/重复/空 reason 全拒)/失败降级/档案行 schema——打桩 LLM 零网络。
2. 编排:rescore run 带 rerank phase 三态;`GUANLAN_RERANK_DAILY` 开关(默认关不跑/开跑/regen 关时显形提示)。
3. picks rerank_ab 行写入+现有消费方过滤回归;basket_perf kind 参数(默认行为不变守护)。
4. 工具:计数 48/73/52 守护 + 三 impl 单测。
5. 真机 e2e@9998(亲手,不转包):`top_n=5` 控成本 → 真 LLM 重排 → 档案 rerank 块+rerank_ab 双篮 → 浏览器名次对照列 → 失败注入验降级 → 9999 收尾重启。

## 9. 展望(本期不做,记档)

- 正式口径切换(picks=调整榜):等 A/B 前向证据 + 用户人审,另立小项。
- 链环圈池因子研究(universe 加链环池,PIT 严格):独立小项。
- 情绪/风格独立重排维度扩展、多链框架(robot_chain 已有数据面)接入上下文包:随经验回路成熟迭代。
