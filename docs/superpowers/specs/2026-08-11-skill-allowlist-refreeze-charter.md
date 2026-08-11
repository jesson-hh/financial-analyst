# Skill 白名单小重冻 Charter —— 让 L3 授权从「可审计读取」变成「可用证据」

> 立项:2026-08-11,L3 Task 7 真机战役终审缺陷 7(战役最大发现)。
> 前置:L3 全程收官(main `caa67baf`,台账 `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` §12.12-12.13)。
> 性质:**第二次被授权移动密封链的窗口**(第一次=L3 Task 5)。范围极小、纪律全继承。

## 1. 实测事实(全部来自耐久记录,终审复审逐条核过)

- **F-A** run `deep-6718268ea77b6c1a`(2026-08-11,完整十节点,COMPLETED):`news` 节点的
  `PromptAssemblyRecord` 携带 19 行真新闻的 `RenderedDataBlock`(7765 字符,`trust=untrusted_data`,
  `method=news`),节点产出 `NewsDigestReport items: []` + 「No allowlisted news blocks
  (`ww_news_live`, `ww_newsradar`, `ww_live_text`) were provided」。
- **F-B** 同 run `pv.technical`:块内真结算报价(864.58,`SETTLED_PRIOR_SESSION`,PIT passed)→
  产出 `anchor_price=0.0` + 「the prefetched quote block was not made available to this node」+
  `bias=unknown`、全指标 0.0。
- **F-C** 下游级联全空:`ResearchPlan`「唯一上游产物是 SentimentReport」、`BullCase`「技术块缺席」、
  `PortfolioDecision`「唯一证据是 SentimentReport」。
- **F-D** 机理:`guanlan_v2/orchestration/skills/text.news/SKILL.md` 与 `…/pv.technical/SKILL.md`
  的 CRITICAL 数据源白名单只命名 `ww_*` 工具产物,并规定「不在其中的断言对本次读取不存在」。
  L3 授权投喂的是 `RenderedDataBlock(method="news"/"verified_snapshot")`——**不在名单里。
  模型是照密封材料办事**,这不是模型缺陷,是材料与裁决后现实的脱节(Task-4 同类,当时无人知道
  这两份 SKILL 也在脱节集里——它们的脱节只有真 LLM 真数据才显形)。
- **F-E** 强关联待测:`dec.pm` 三次真跑(08-02/08-06/08-11)读数全部 ok+PIT passed 而行文零引用
  ——若 `skills/dec.pm/SKILL.md`(及 `dec.research_mgr`)的数据源清单同样只认 `ww_*`/上游产物,
  则「pm 不引读数」三案与缺陷 7 **同根**,一并在本窗口修,顺带了结 L2-b Task-9 待办#1。
- **F-F** 生产姿态现状:全预设已是 9999 默认(`var/secrets.env` PRESET 行已注释)。缺陷 7 未修前,
  watcher 对 opt-in 票每次深跑 ~7 结算 LLM,其中两个授权证据席贡献为零。

## 2. 裁决预置(立项即裁,执行者不得另择)

**选型 = Option A:白名单加 L3 渲染块身份,如实命名。**

- 每份涉事 SKILL 的数据源白名单增补一个显式条目:运行时预取的 `RenderedDataBlock`
  (按 `method` 命名:`news` / `verified_snapshot`),连同其信任框架——`untrusted_data` 定界、
  `DO_NOT_FABRICATE` 哨兵语义、PIT 徽章(如 `SETTLED_PRIOR_SESSION`)的解读义务、
  引用时必须带 provenance(读数值+结算会话日期),数据内容永不当指令。
- **否决 Option B(装配层把渲染块重标为 `ww_*` 名)**:撒谎标签——块不是 ww 工具产物,
  provenance 诚实是本程序底线。
- **否决 Option C(改走 ww_ 预取旁路)**:L3 授权机器才是经审的数据路(PIT/审计/租约全在);
  绕开等于把十期工程放回抽屉。
- **引用义务随材料一起写进**:当白名单块存在且非空时,报告必须引用其中至少一个读数
  (数值+日期+来源名),或显式陈述为何不可用——「静默无视白名单内的块」从此是材料违规,
  不再是模型自由裁量。这一条同时是 pm 归因实验的固化(留存请求体义务见 §5 验证跑)。

## 3. 范围与爆炸半径(T1 闸实测,禁止照抄此处估计)

- 材料:`skills/text.news/SKILL.md`、`skills/pv.technical/SKILL.md` 必动;
  `skills/dec.pm/SKILL.md`、`skills/dec.research_mgr/SKILL.md` 按 F-E 实测结果决定(T1 出裁决包)。
- 摘要链(待测,方向参考 L3 ww_f10 前例):skill 材料 digest → skill manifest → WorkerSpec
  语义 digest → P8 catalog manifest → P8→P9→P10 链 → 两个 deep preset 记录 + screening lane
  记录 → §3 类冻结字面量 + golden 文件级 byte-sha 钉。
- **租约连坐**:链 digest 一动,现存与未来租约的四元组全部失配——重冻后须重铸,
  且旧租约行为=静默退人批(L3 §12.11 I-2 同型)。写进 T4 战役开工义务。
- T1 闸必须跑 L3 学到的**三类清单 grep**:digest 字面量、文件 byte-sha、
  **位置索引访问**(I-1 教训:会随集合增长而静默换对象的 `[0]` 类)。

## 4. 红线(全数继承 L3,重申不减)

TDD RED-first + mutate→red→revert(sha256 逐字节还原);一次性窗口(红集 ⊆ 实测清单,
超出即 STOP);对照表逐 digest 记因,零不可归因;注册模型 docstring = 密封材料;
`config/orchestration/materials` 永不 `git checkout`(EOL 判定唯 `git hash-object --path`);
显式 pathspec,并发脏区绝不 stage;PIT 红线(available_at 只排除永不伪造);LLM 零买卖;
租约信封按预算额铸(cap ≥ 每准入预算 × 准入数);验证跑用**新鲜身份**(同日同身份烧毁不可复用)。

## 5. 出口闸(全部达成才关账)

1. **一次性重冻序列**(再生 → 有意识翻转 → 台账),对照表零不可归因,schema manifest 零移动。
2. 全 `tests/orchestration` 收官树全绿(基线 6141+1x)。
3. **验证跑(真金)**:新鲜身份的完整十节点生产真跑——
   (a) `news`/`technical` 报告携带**非空证据**(源自块内真数据,数值可对账);
   (b) `research-mgr`/`pm` 行文**引用**块内读数(数值+日期)或显式陈述不可用——000858 标准的另一半;
   (c) **请求体留存**(L2-b Task-9 待办#1 义务:线上字节,非组装意图);
   (d) run COMPLETED + orchestrated 台账行。
4. screening lane 顺跑验证(小 top_n、选有近讯的候选)或书面缓期(记原因;600071 八天无新闻
   的数据现实在案)。
5. **生产姿态复决**:验证跑通过 → 全预设默认维持并记录;未过 → 一行回拨 reduced 并重启 9999。
6. 台账写账 + 终审式复核(引用链两半的达成状态逐字记录,证据=耐久记录非进程日志)。

## 6. 任务骨架(SDD,预计 4 任务 + 战役)

- **T1 闸+清单+裁决包**:F-E 实测(pm/research_mgr 的 SKILL 数据源清单现状)→ 涉事材料集终裁;
  爆炸半径三类 grep 全清单;链基线 = L3 §12.2 收官值逐字节核。
- **T2 材料文本(RED-first)**:白名单条目 + 信任框架 + 引用义务写进每份涉事 SKILL;
  `test_l3_material_texts.py` 式文本钉(旧措辞复活=红)。
- **T3 一次性重冻**:再生+翻转+对照表;独立复审(链端点亲手再推)。
- **T4 真机验证战役**(控制器亲跑):重铸租约 → 出口闸 3/4/5 逐项;
  预算:Lane-0 2 + 十节点 ~7-8 + screening ~5-6 ≈ **15 次结算 LLM**,授权上限 20。

## 7. 悬而未裁(T1 一并端上,控制器答)

- D-A:`dec.pm`/`dec.research_mgr` 是否入窗(F-E 实测后裁——倾向:同根则同窗,免二次开窗)。
- D-B:引用义务的强度——「必须引用或显式声明不可用」写为 SKILL 硬性条款(倾向)
  vs 仅提示性(否决理由:三次真跑已证明提示性=零效果)。
- D-C:验证跑失败(模型仍不引用)的处置——材料再迭代一轮 vs 记录为模型侧极限并回拨 reduced。
