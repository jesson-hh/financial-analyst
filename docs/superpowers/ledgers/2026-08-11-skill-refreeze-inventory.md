# SKILL 白名单小重冻 · 闸 + 三类清单 + D-A/B/C 裁决包 (Task 1)

> 章程:`docs/superpowers/specs/2026-08-11-skill-allowlist-refreeze-charter.md`。
> 前置台账:`docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md`(下称 **L3 台账**)。
> 本任务**只测量,不裁决**。三个裁决包(D-A / D-B / D-C)全部标 **待裁**,由控制器答。
> 可执行半边:`tests/orchestration/test_skill_refreeze_gate.py`。
> **本文件里的每一张 `[gate-parsed]` 表都被那份 gate 逐行解析并与真树对账;
> 表与树不一致 = 红 = 清单已陈旧 = STOP。**

---

## 0. 度量身份 (measured at)

| 项 | 值 |
|---|---|
| HEAD | `98f139886c9b0b9895ab6cc90e9d5fe1cc5fcc28`(`98f13988`,skill-allowlist charter 提交) |
| 分支 | `report-evidence-pack` |
| 解释器 | `G:/financial-analyst/.venv/Scripts/python.exe`(CPython 3.13.11) |
| 度量日 | 2026-08-11 |
| 度量方式 | 全部经**真构造器**现推(`phase3_data_surface` / 九个 catalog snapshot 构造器 / 真预设注册表 / 真事件存储);零 fixture 代替事实 |
| 爆炸半径度量法 | **构造法**:对一份 SKILL.md 的读字节追加一个探针字节(内存内 `Path.read_bytes` 包装,磁盘零写入),整链重推,逐身份比对旧/新——「哪些摘要会动」由重建结果回答,不由 grep 推断。grep 三类清单**另做**,二者互为交叉核对(L3 §12.5 的教训:grep 结构上抓不到「钉文件字节 sha」那一类) |

**EOL 纪律**:凡断言「字节没动」一律用 git 对象身份(`git hash-object --path`),不用工作树原始字节
(L3 §12.4.1,本仓 `core.autocrlf=true`)。`config/orchestration/materials` 永不 `git checkout`。

---

## 1. F-E 实测 —— 27 份密封 SKILL 的数据源白名单 vs L3 渲染块 **[gate-parsed]**

**被测身份**:`RenderedDataBlock`(`guanlan_v2/orchestration/data/render.py:59`),
字段 `method`(取值 `news` / `verified_snapshot`)、`trust="untrusted_data"`(闭合字面量)、
`status`、`pit_audit_digest`、`rendered_text`、`block_digest`。它经 `ProviderUntrustedBlock`
进入 `PromptAssemblyRecord.untrusted_blocks`,以 `application/json` 落在模型的 `data_inputs` 通道。

**「收到块」的判据不是猜的**:worker 是否在**密封预取绑定**
(`phase3_data_surface().prefetch_binding`)里持有行。实测四行,**就是全部**:

| worker | method | invocation_mode | success_requires_finalized_call | capability |
|---|---|---|---|---|
| `dec.pm` | `verified_snapshot` | `cache_or_invoke` | False | `cap.data.verified_snapshot` |
| `pv.technical` | `verified_snapshot` | `always_invoke` | True | `cap.data.verified_snapshot` |
| `text.news` | `news` | `always_invoke` | True | `cap.data.news` |
| `text.policy` | `news` | `always_invoke` | True | `cap.data.news` |

**分类词表(封闭三值,gate 断言不存在第四种拼法):**

* `GAP-CONFIRMED` —— 该 worker 持有 ≥1 条密封预取行(**必然收到块**),且其 SKILL 的
  `## ⚠️ CRITICAL: Data Source Priority` 段**不命名任何**块身份记号
  (`RenderedDataBlock` / `untrusted_data` / `verified_snapshot` / `renderer_ref` / `block_digest`),
  并带一条「不在名单内即不存在 / 只从这些来源推理」的排除条款。
* `NO-BLOCK` —— 该 worker 持有**零**预取行,结构上收不到 L3 渲染块;其不引用是**上游断供**,
  不是白名单丢弃。
* `NAMES-BLOCK` —— DSP 段已命名块身份(**今日全树零份**;这正是 T2 要有意识翻转的状态)。

| # | skill | 预取行 | DSP 命名块身份 | 分类 |
|---|---|---|---|---|
| 1 | `dec.bear` | — | no | NO-BLOCK |
| 2 | `dec.bull` | — | no | NO-BLOCK |
| 3 | `dec.pm` | verified_snapshot | no | GAP-CONFIRMED |
| 4 | `dec.research_mgr` | — | no | NO-BLOCK |
| 5 | `dec.risk_debate` | — | no | NO-BLOCK |
| 6 | `dec.trader` | — | no | NO-BLOCK |
| 7 | `market.factor_miner` | — | no | NO-BLOCK |
| 8 | `market.regime` | — | no | NO-BLOCK |
| 9 | `market.rotation` | — | no | NO-BLOCK |
| 10 | `orchestrator.planner` | — | no | NO-BLOCK |
| 11 | `pv.curator` | — | no | NO-BLOCK |
| 12 | `pv.microstructure` | — | no | NO-BLOCK |
| 13 | `pv.price_action` | — | no | NO-BLOCK |
| 14 | `pv.technical` | verified_snapshot | no | GAP-CONFIRMED |
| 15 | `quant.backtest` | — | no | NO-BLOCK |
| 16 | `quant.curator` | — | no | NO-BLOCK |
| 17 | `quant.factor` | — | no | NO-BLOCK |
| 18 | `quant.factor_miner` | — | no | NO-BLOCK |
| 19 | `quant.fundamentals` | — | no | NO-BLOCK |
| 20 | `quant.model` | — | no | NO-BLOCK |
| 21 | `text.macro` | — | no | NO-BLOCK |
| 22 | `text.news` | news | no | GAP-CONFIRMED |
| 23 | `text.policy` | news | no | GAP-CONFIRMED |
| 24 | `text.research_report` | — | no | NO-BLOCK |
| 25 | `text.sentiment` | — | no | NO-BLOCK |
| 26 | `x.number_critic` | — | no | NO-BLOCK |
| 27 | `x.quality_gate` | — | no | NO-BLOCK |

**计数**:`GAP-CONFIRMED` **4**、`NO-BLOCK` **23**、`NAMES-BLOCK` **0**。

### 1.1 排除条款逐字(gate 按空白归一后逐份钉住今日措辞) **[gate-parsed]**

| # | skill | 白名单命名的来源(逐字) | 排除条款(逐字,空白归一) |
|---|---|---|---|
| 1 | `text.news` | `ww_news_live` / `ww_newsradar` / `ww_live_text` | Only these allowlisted, runtime-prefetched tool products are trusted. If a claim is not in one of them, it does not exist for this read. |
| 2 | `pv.technical` | `ww_live_text` / `ww_market_tape` | Only these allowlisted, runtime-prefetched products are trusted. If a figure is not in one of them, it does not exist for this read. |
| 3 | `text.policy` | `ww_news_live`(两条:rich-layer policy path + flash filter) | Only these allowlisted, runtime-prefetched products are trusted. If a wording is not in one of them, it does not exist for this read. |
| 4 | `dec.pm` | 上游 ResearchPlan / risk-debate seat outputs / validated allowed-actions block / veto+announcement flags / 上游 SentimentReport / past-lessons block / portfolio+position context blocks | Arbitrate only from these artifacts and blocks. Do not invent holdings, cash, or fills; if position context is absent, reason without assuming it. |
| 5 | `dec.research_mgr` | bull-bear debate history block / 上游 SentimentReport / fundamental+factor context blocks / upstream-ratings extract block / regime+market-tape context / experience-library cases | Synthesize only from provided upstream artifacts and blocks. Do not fetch new data; if a needed input is absent, reason around the gap and flag it. |

**读法**:前三份的排除条款是**显式否定式**(「不在名单里的断言/数字/措辞对本次读取不存在」),
`dec.pm` / `dec.research_mgr` 是**限定式**(「只从这些…推理」)。两种措辞对 L3 渲染块的
结果**相同**:块不在枚举里,于是它不是可引证据。差别只在语气,不在效果。

### 1.2 F-E 的耐久证据 —— `dec.pm` 三次真跑,逐条现推(不转述章程)

证据源:`var/orchestration/events/main.jsonl` + `var/orchestration/payloads/main/`(耐久事件存储)。

| run | 日期 | TCR | DataResult | status | `pit_audit.guard_result` | `future_rows` | 块内读数 | 块进 prompt | 决策文本含该读数 |
|---|---|---|---|---|---|---|---|---|---|
| `deep-9b8a1386afcb1dbf` | 2026-08-02 | 1 finalized | `VerifiedSnapshotDataResult` | `ok` | `passed` | 0 | `last_price=864.0`,`available_at=2026-07-30T07:00:00Z`(300308.SZ) | 1 块,`rendered_length=1297` | **否**(`"864"` 全文不出现) |
| `deep-afd5c0859c88718f` | 2026-08-06 | 1 finalized | `VerifiedSnapshotDataResult` | `ok` | `passed` | 0 | `last_price=947.74`,`available_at=2026-08-05T07:00:00Z` | 1 块,`rendered_length=1299` | **否** |
| `deep-6718268ea77b6c1a` | 2026-08-11 | 1 finalized | `VerifiedSnapshotDataResult` | `ok` | `passed` | 0 | `last_price=864.58`,`available_at=2026-08-10T07:00:00Z` | 1 块,`rendered_length=1299` | **否** |

三次 `PortfolioDecision` 的 `rating` 全是 `Hold`、`price_target` 全 `None`,
`executive_summary` 逐次写「唯一上游证据是 SentimentReport / ResearchPlan 归因于证据不足」。
**块真到了 prompt,读数真通过了 PIT,行文里一个数字都没有。**

对照组(同三次 run,同一事件存储现推):

| worker | data_result_refs | tool_call_records | prompt untrusted_blocks |
|---|---|---|---|
| `dec.research_mgr`(三次全部) | 0 | 0 | **0** |
| `text.news`(`deep-6718268e`) | 1 | 1 | 1 |
| `pv.technical`(`deep-6718268e`) | 1 | 1 | 1 |

### 1.3 F-E 结论(测量陈述,不含裁决)

* **`dec.pm` 与缺陷 7 同根 —— CONFIRMED。** 它每次都收到一个 `ok`+PIT-passed 的
  `verified_snapshot` 渲染块;它的 SKILL 白名单枚举里没有这个身份,排除条款是
  「Arbitrate only from these artifacts and blocks.」。模型照密封材料办事,块不在名单里,
  于是不是可引证据——与 `text.news` / `pv.technical` 的机理**逐字同型**。
* **`dec.research_mgr` 同根假说 —— REFUTED(结构性)。** 它在密封绑定里**没有行**,
  三次真跑 `data_result_refs=0`、`untrusted_blocks=0`。它不引用读数是因为**根本没有读数**,
  不是因为白名单把读数丢掉了。它的空白是缺陷 7 的**下游表现**,修上游即自愈。
  (它的 SKILL 若仍要动,理由只能是「预防性对称」,不能是「同根」——那是控制器的裁量,不是测量结论。)
* **章程未点名的第四份:`text.policy` —— GAP-CONFIRMED。** 它持有一条
  `news` / `always_invoke` / `success_requires_finalized_call=True` 的密封预取行,
  白名单只认 `ww_news_live`,排除条款是「If a wording is not in one of them, it does not exist for this read.」
  ——与 `text.news` 逐字同型。**当前三份已提交预设(十节点 / 减证据 / screening lane)都不含
  `text.policy` 节点**,所以这条脱节今天是**休眠**的,不是活的生产缺陷;但只要 planner 选中它、
  或将来任何预设纳入它,它会以与缺陷 7 完全相同的方式失效。
* **全树零份 SKILL 命名过块身份。** 没有「已经对了」的先例可抄。最接近的措辞先例是
  `market.regime` / `market.rotation` 的「rendered market_factor_report block(the ONLY numeric source)」
  ——那是**另一套**机制(Lane-0 因子报告),但它证明「白名单里如实命名一个渲染块」在本程序里
  是**已有的、被接受的写法**,不是新发明。

### 1.4 T2 的两条硬约束(实测,写给下一位)

1. **`lint_skill_supply` 只扫 DSP 段里的 `ww_` 记号**
   (`guanlan_v2/orchestration/capability_manifest.py:289` `_promised_tools`)。新增的渲染块条目
   **只要不引入新的 `ww_` 记号**,promise-supply lint 保持干净;引入一个该 worker 的
   allowlist 不供给的 `ww_*` 立刻红(`test_lane_batch_*.py::test_promise_supply_lint_clean`)。
2. **镜像必须同步。** 树文件是唯一人编辑源,`config/orchestration/materials/skills/<id>.md`
   是逐字构建产物,三处独立钉(§4 #4-#7)。改树不改镜像 = 红。
   `dec.pm` / `dec.research_mgr` 还额外被**交付物③文档**逐字钉(§5 #1,fragile)。

---

## 2. 链基线 + 卫星身份 —— 现推值与本窗口裁定 **[gate-parsed]**

`verdict` 是**封闭三值词表**(gate 断言无第四种拼法):

* `FROZEN` —— 本窗口**任何** SKILL 编辑都不动它(gate:五份候选 SKILL 逐一探针,五次全不动)。
* `MOVES` —— 只要窗口非空就动(gate:五次探针**每一次**都动)。
* `MOVES-IF-IN-WINDOW` —— 只有它自己那份 SKILL 进窗才动(gate:自己那次动、其余四次不动)。

| # | identity | digest (measured @ HEAD) | verdict |
|---|---|---|---|
| 1 | `P2-static` | `b41bf223f0dd0b05c5a4f4f7bbd32960eef90d7176a17300dbe576f6b61bf0d3` | FROZEN |
| 2 | `P3-data` | `13210944b6afe738a34c4767453a6514d1147dd5c4a51ae71580425a4c5a72a4` | FROZEN |
| 3 | `P3-full` | `0601eef771be221dbc203c56849f357b277d213a6b00274ee94aeaf70725b231` | FROZEN |
| 4 | `P4` | `8aea7bc0cb543a8bed8ea4505caa2f4a7f2cce9636762ad690b624d14de1e0f9` | FROZEN |
| 5 | `P5` | `af064cbaaa1e9b0cdea7b23ced1cffb103f6605fbcda476598b5762651df0b8d` | FROZEN |
| 6 | `P6` | `af064cbaaa1e9b0cdea7b23ced1cffb103f6605fbcda476598b5762651df0b8d` | FROZEN |
| 7 | `P7` | `b78b63e476bfd5bf0c303524244dbc9a23a585ccc924c8230dcfe906f1b07750` | FROZEN |
| 8 | `P8` | `78f79d330ae99e7efcadb7b367f6f84badb0cca2541473824a6fb7618eb4f951` | MOVES |
| 9 | `P9` | `e3a293615a21db3d88c10969395850369d71be9349c0d3462abc4e4015401e9d` | MOVES |
| 10 | `P10` | `f1b730529dab1d0d1e3243cde86127b5ee960dc9ac42c9fd639023159c3a535a` | MOVES |
| 11 | `prefetch_binding` | `734d5c204c088c0f2a37f3485db3f3f7b844f2a19179c698274aaa6d3de8ac58` | FROZEN |
| 12 | `bridge.data_runtime.prefetch@1` | `cab80122821607f8d6de0f72be4062174615c6789a039f67f5fe338f9250469a` | FROZEN |
| 13 | `bridge.data_runtime.descriptor@1` | `817d2882e20760ebe06cfd41e5a1656b7554961952644fc04ee077a75ab89a7b` | FROZEN |
| 14 | `bridge.data_runtime.provider@1` | `3a1e727b2e3f761bfdecadf919dc678bc8dfd19b993495ca5dd223ecf3d8e065` | FROZEN |
| 15 | `bridge.data_runtime.analyzer@1` | `3f93fdf29d28f26676e7291dbeeb1d6e3b899265862940bf5c16ae477aeb1968` | FROZEN |
| 16 | `worker:text.news` | `079de03217501ee09e306bd4ee265845553cd19729b8eb6247a81a51fcbdf332` | MOVES-IF-IN-WINDOW |
| 17 | `skill.text.news@1` | `b1775ea2a5579c9679204cfb73e8780d8b9c52a2ec51e19251c471486bfd8242` | MOVES-IF-IN-WINDOW |
| 18 | `worker:pv.technical` | `3b2ae16497fbf36327467b6be61b9ca9d77696ce0030a1f90f79697afc2d7172` | MOVES-IF-IN-WINDOW |
| 19 | `skill.pv.technical@1` | `35ecf2c54c5d3f73d99cbd890579a40c1459024b872200f6934c1598370e17d2` | MOVES-IF-IN-WINDOW |
| 20 | `worker:dec.pm` | `9fd517166ec0843f9a3c4d8c72416e577b51ec9db0fe64a71c731a897954f2af` | MOVES-IF-IN-WINDOW |
| 21 | `skill.dec.pm@1` | `08b4c40590fa56852f0ea1cb53dd75107089123925a1866535b5859378232998` | MOVES-IF-IN-WINDOW |
| 22 | `worker:text.policy` | `f3c23a78cbd8af0971395c89586f249efa65964d565c5b807272ae6acbde2750` | MOVES-IF-IN-WINDOW |
| 23 | `skill.text.policy@1` | `a2bf4bf86ea559ec495022178ab3ffc778441cffbfd79125d7adb06632b65107` | MOVES-IF-IN-WINDOW |
| 24 | `worker:dec.research_mgr` | `a512057933ec18a26d80a70ed5a4f4a40a6b494a9ff5185bc82ed2a34d6b7e6d` | MOVES-IF-IN-WINDOW |
| 25 | `skill.dec.research_mgr@1` | `c8a6db63138f9a7103aefe947c277caaef93be7c0c502c892d8e2fd06c705271` | MOVES-IF-IN-WINDOW |
| 26 | `preset:pipeline.luozi_deep_decide` | `696daf8044e55039a6c5d82ccb4e815d678d4952462ba0d1f971dc5f6a5cf6ca` | FROZEN |
| 27 | `preset:pipeline.luozi_deep_decide_reduced` | `f44646dcd0448dee79a49f50748343c255bda30fe95114d981802613ba29d91c` | FROZEN |
| 28 | `preset:pipeline.screening_lane` | `f739f7fcce79939d23e282f6b7819a43cf5d56038825cfaf189df221e4c535ec` | FROZEN |

**与 L3 §12.2 收官值的对账:零漂移。** 上表 1-10 行逐字节等于 L3 台账 §2 的收官表
(gate 直接解析 L3 台账那张表并做**三方相等**:live == L3 台账 == 本表);
11-15 行等于 L3 §2.3 的四个数据桥材料 + §12.2 #10 的绑定摘要;
17 行等于 L3 §12.2 #18(ww_f10 撤回后的 `b1775ea2…`,**本次重新度量,不是抄的**);
16/18 行等于 L3 §12.2 #17/#16;26-28 行等于三份 golden 预设 record digest。
**任一处不等 = 清单陈旧 = STOP(gate 的失败信息逐字写着这句)。**

### 2.1 章程 §3 的四处方向性猜测,实测修正三处

`docs/.../2026-08-11-skill-allowlist-refreeze-charter.md` §3 明写「T1 闸实测,禁止照抄此处估计」。实测:

| 章程 §3 的猜测 | 实测 | 结论 |
|---|---|---|
| skill 材料 digest → skill manifest | ✅ 成立 | `skill.<id>@1` 的 `ContentRef.content_digest` 就是 skill manifest 里那一格 |
| → WorkerSpec 语义 digest | ✅ 成立 | 该 worker 的 `semantic_digest()` 内嵌 `SkillBinding.skill_ref` |
| → P8 catalog manifest → P8→P9→P10 链 | ✅ 成立 | 但**只从 P8 起**:P2-P7 全不动(树文件只被 P8 的 lane 构造器读) |
| → 两个 deep preset 记录 + screening lane 记录 | ❌ **不成立** | 三份预设 record digest 在五次探针里**全部不动**:record 是预设图的语义摘要,不内嵌 catalog/worker 摘要 |
| 租约四元组连坐 | ✅ 成立(推论) | 租约绑 `catalog_digest`;P8/P9/P10 动 ⇒ 现存租约四元组失配 ⇒ T4 开工必须重铸(与 L3 §12.11 I-2 同型) |

**「租约连坐」不是本表能直接测的(它是运行期对象),但它的前提(catalog_digest 移动)已实测成立,
故按成立记录,并写进 T4 开工义务。**

---

## 3. 三类清单 · (a) 摘要字面量 **[gate-parsed]**

扫描范围:`tests/` + `guanlan_v2/` + `config/`,后缀
`.py .json .md .txt .yaml .yml .cfg .ini .toml`,排除 `__pycache__`。
(`docs/` 有意排除:本文件与 L3 台账都在那里,自扫会自爆;跨台账连坐单列 §6。)
`form` 是**封闭二值词表**:`full64`(完整 64 位十六进制)/ `prefix8`(前 8 位缩写)。

**最坏情形(四份 GAP-CONFIRMED + `dec.research_mgr` 全部入窗)的落点全集:**

| # | file | line | identity | form |
|---|---|---|---|---|
| 1 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 4 | P9 | full64 |
| 2 | `tests/orchestration/golden/phase10_catalog_manifest_v1.json` | 6 | P10 | full64 |
| 3 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 6 | P8 | full64 |
| 4 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 142 | worker:dec.pm | full64 |
| 5 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 143 | worker:dec.research_mgr | full64 |
| 6 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 153 | worker:pv.technical | full64 |
| 7 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 161 | worker:text.news | full64 |
| 8 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 162 | worker:text.policy | full64 |
| 9 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 176 | skill.dec.pm@1 | full64 |
| 10 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 177 | skill.dec.research_mgr@1 | full64 |
| 11 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 183 | skill.pv.technical@1 | full64 |
| 12 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 191 | skill.text.news@1 | full64 |
| 13 | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | 192 | skill.text.policy@1 | full64 |
| 14 | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | 4 | P8 | full64 |
| 15 | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | 6 | P9 | full64 |
| 16 | `tests/orchestration/test_phase10_handoff.py` | 123 | P8 | full64 |
| 17 | `tests/orchestration/test_phase10_handoff.py` | 127 | P9 | full64 |
| 18 | `tests/orchestration/test_phase9_handoff.py` | 318 | P8 | full64 |
| 19 | `tests/orchestration/test_l3_refreeze_gate.py` | 159 | P8 | prefix8 |
| 20 | `tests/orchestration/test_l3_refreeze_gate.py` | 159 | P9 | prefix8 |
| 21 | `tests/orchestration/test_l3_refreeze_gate.py` | 159 | P10 | prefix8 |

**计数**:**21 处**,分布在 **6 个文件**(3 golden + 3 测试模块);
其中 `full64` **18 处**、`prefix8` **3 处**(全部在 `_REFREEZE_CHAIN_PREFIXES` 那一行)。
**零处非测试落点** —— `guanlan_v2/` 与 `config/` 里没有任何这些摘要的字面量
(链摘要全部现推,不硬编码)。

**按窗口规模拆分(D-A 定价用):**

| 窗口 | 链摘要落点(#1-3,14-21) | 每份 SKILL 自带的两处(worker+skill,均在 `phase8_catalog_manifest_v1.json`) | 合计 |
|---|---|---|---|
| 2 文件(`text.news` + `pv.technical`) | 11 | 4(#6,7,11,12) | **15** |
| 3 文件(+ `dec.pm`) | 11 | 6(+#4,9) | **17** |
| 4 文件(+ `text.policy`) | 11 | 8(+#8,13) | **19** |
| 5 文件(+ `dec.research_mgr`) | 11 | 10(+#5,10) | **21** |

**链摘要那 11 处对任何非空窗口都相同** —— 这是 D-A 的核心定价事实:
**第 3、4、5 份 SKILL 的边际字面量代价各只有 2 处,且同在一个 golden 文件里。**

### 3.1 不动摘要的携带者(必须原样不动) **[gate-parsed 计数]**

| identity | 落点数 |
|---|---|
| P2-static | 6 |
| P3-data | 4 |
| P3-full | 7 |
| P4 | 7 |
| P5/P6 | 8 |
| P7 | 4 |
| `bridge.data_runtime.prefetch@1` | 1 |
| `bridge.data_runtime.descriptor@1` | 1 |
| `prefetch_binding` | 1 |
| `preset:pipeline.luozi_deep_decide` | 3 |
| `preset:pipeline.luozi_deep_decide_reduced` | 1 |
| `preset:pipeline.screening_lane` | 1 |

合计 **44 处 / 20 个文件**。T3 的替换脚本必须对每个文件断言这些摘要的出现次数**前后相等**
(L3 §12.3 同法)。

---

## 4. 三类清单 · (b) 文件字节 sha 钉 **[gate-parsed]**

L3 §12.5 的教训:grep 摘要值**结构上不可能**发现「钉 golden **文件**字节 sha」的守卫,
因为那些常量不是任何摘要的值。此表独立枚举。
`subject` 列写的是**被钉字节的那个文件**;gate 断言 `file` 存在、`anchor` 在 `file` 内、
且 `subject` 的现算 sha256 等于 `sha` 列。

| # | file | anchor | subject | sha (measured @ HEAD) |
|---|---|---|---|---|
| 1 | `tests/orchestration/test_phase9_registry_chain.py` | `test_upstream_goldens_are_byte_unchanged` | `tests/orchestration/golden/phase8_catalog_manifest_v1.json` | `cd5050598925a14c8dc8f10128ef27f64168c43300e9f2df46531b5c78137ef2` |
| 2 | `tests/orchestration/test_phase10_chain.py` | `test_upstream_goldens_are_byte_unchanged` | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | `e0c57da25671adc44c8c75e8504dba022d3fdaedea833ddbe6137f82f2b0b4cf` |
| 3 | `tests/orchestration/test_phase10_handoff.py` | `phase9_catalog_golden_sha256` | `tests/orchestration/golden/phase9_catalog_manifest_v1.json` | `e0c57da25671adc44c8c75e8504dba022d3fdaedea833ddbe6137f82f2b0b4cf` |
| 4 | `tests/orchestration/test_lane_batch_text.py` | `test_skill_grammar_and_mirror_identity` | `config/orchestration/materials/skills/text.news.md` | — |
| 5 | `tests/orchestration/test_lane_batch_pv.py` | `test_skill_grammar_and_mirror_identity` | `config/orchestration/materials/skills/pv.technical.md` | — |
| 6 | `tests/orchestration/test_lane_batch_decision.py` | `test_skill_grammar_and_mirror_identity` | `config/orchestration/materials/skills/dec.pm.md` | — |
| 7 | `tests/orchestration/test_l3_material_texts.py` | `test_the_committed_mirror_stays_byte_identical` | `config/orchestration/materials/skills/text.news.md` | — |
| 8 | `tests/orchestration/test_skilltree.py` | `test_real_tree_mirror_is_clean` | `config/orchestration/materials/skills/` | — |

**读法:**

* #1-#3 是**摘要位移的二阶落点**:golden 文件被重写 ⇒ 其字节 sha 变 ⇒ 这三处必须有意识翻转。
  `phase10_catalog_manifest_v1.json` 是链尾,**无人钉它的字节 sha**(实测,不是假定)——
  其现值 `b229b9a27d3aa1cdb74b35d61f1afa9d4f5693fcb36e080322af99d93d017730` 记在此仅作交叉核对。
* #4-#8 是**镜像同步义务**:镜像不是「顺手也改一下」,而是四处独立守卫。
  `sha` 列写 `—` 是因为这些钉断言的是 **tree == mirror 的相等关系**,不是一个冻结的常量值——
  T2 同步改两边即绿,不需要 T3 重冻。
* `text.policy` 的镜像由 #4 同一个参数化钉覆盖(`lane_batch_text` 的 `EXPECTED` 含五份文本席位);
  `dec.research_mgr` 的镜像由 #6 同一个参数化钉覆盖(`DECISION_LANE_WORKER_IDS` 六份)。

---

## 5. 三类清单 · (c) 位置索引访问(L3 I-1 教训成法) **[gate-parsed]**

`verdict` 是**封闭三值词表**:

* `fragile` —— 索引进入的集合会随本次改动而增长/重排,或索引与被改物件同一份文档;
* `self-protecting` —— 同一测试(或紧邻断言)先钉住集合长度/形状,索引不可能悄悄换对象;
* `benign` —— 索引进入的集合在本次改动下长度与顺序都不变,或两侧都是现推值。

| # | file | anchor | 索引的集合 | verdict |
|---|---|---|---|---|
| 1 | `tests/orchestration/test_lane_batch_decision.py` | `_extract_deliverable3_blocks()[idx]` | 交付物③ 文档里的 ```markdown 安装块序列(`[0]`=research_mgr、`[1]`=pm) | **fragile** |
| 2 | `tests/orchestration/test_lane_batch_decision.py` | `test_deliverable3_has_exactly_two_install_blocks` | 同上,先钉块数==2 | self-protecting |
| 3 | `tests/orchestration/test_catalog_runtime.py` | `len(rw.skills) == 1 and rw.skills[0].kind` | `WorkerSpec.skills` | self-protecting |
| 4 | `tests/orchestration/test_phase8_registry_catalog.py` | `w.skills[0].skill_ref.content_digest == skilltree._skill_digest` | `WorkerSpec.skills` | benign |
| 5 | `tests/orchestration/test_lane_batch_decision.py` | `test_pilot_install_digest_matches_bound_skill` | `WorkerSpec.skills` | benign |
| 6 | `tests/orchestration/test_lane_batch_text.py` | `specs["text.sentiment"].skills[0].skill_ref.content_digest == d` | `WorkerSpec.skills` | benign |
| 7 | `tests/orchestration/test_lane_batch_decision.py` | `u.skills[0].skill_ref.id == "skill.dec.pm"` | `WorkerSpec.skills` | benign |
| 8 | `tests/orchestration/test_phase8_registry_catalog.py` | `by_id["dec.pm"].skills[0].skill_ref.id` | `WorkerSpec.skills` | benign |
| 9 | `tests/orchestration/test_catalog_runtime.py` | `rt.worker("text.demo").capability_allowlist[0]` | `capability_allowlist`(合成目录) | benign |
| 10 | `tests/orchestration/test_worker.py` | `worker.capability_allowlist[0]` | `capability_allowlist`(合成 worker) | benign |
| 11 | `tests/orchestration/test_catalog.py` | `dup = snap.content_manifest[0]` | `content_manifest`(合成 snapshot) | benign |
| 12 | `tests/orchestration/test_skilltree.py` | `s0 = tree[0]` | `load_skill_tree` 结果(tmp_path 播种树) | benign |
| 13 | `tests/orchestration/test_catalog_runtime.py` | `parse_skill_v1(rw.skills[0].raw_utf8.decode("utf-8"))` | `WorkerSpec.skills`(合成) | benign |
| 14 | `tests/orchestration/test_lane_batch_pv.py` | `text.split("## ⚠️", 1)[1].split("## ", 1)[0]` | **SKILL 文本按标题切片**(DSP 段) | self-protecting |

**为什么绝大多数是 benign:Option A 不改任何 typed 集合的长度。**
`skills` 每个 worker 恒为 1 条 `SkillBinding`;`capability_allowlist` 一格不动(材料窗口不授新权);
`content_manifest` 条目数不变。**真正会增长的只有 SKILL 文本里那条项目符号列表** ——
于是 #14 这类「按标题切片」的读法是唯一结构上相关的一类,且它带 `maxsplit=1`,
新增一条**段内**项目符号不会换段;只有新增一个 `## ` 标题才会。

**#1 是本表唯一的 fragile,也是 D-A 的隐藏成本:**
`dec.pm` / `dec.research_mgr` 的树文件被 `docs/superpowers/specs/2026-07-17-dec-research-mgr-pm-skills.md`
里的两个 ```markdown 块**逐字节钉住**,索引按块在文档中的**出现顺序**取。
它们进窗 ⇒ T2 必须**同时**改那份规格文档的对应块(且不能改变块的顺序/数量),
否则 `test_pilot_tree_is_deliverable3_byte_verbatim` 红。
`text.news` / `pv.technical` / `text.policy` **没有**这层文档钉(实测:只有交付物②/③ 三份有,
分别对应 `text.sentiment` / `dec.research_mgr` / `dec.pm`)。

---

## 6. 跨台账连坐 —— T3 必须与本窗口同提交移动的外部落点 **[gate-parsed]**

`kind` 是**封闭三值词表**:`LEDGER-EQUALITY`(某份台账的表被 gate 断言 == 真树)/
`ANCHOR`(某份台账的行以摘要字符串作锚,锚随重冻消失)/
`FROZEN-EXPECTATION`(测试里的冻结期望常量)。

| # | file | anchor | kind | 后果 |
|---|---|---|---|---|
| 1 | `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` | `## 2. 链路摘要基线 (chain digests)` | LEDGER-EQUALITY | L3 gate `test_the_ledger_records_exactly_the_measured_chain` 断言该表 == 现推链。P8/P9/P10 一动,不同提交更新即红 |
| 2 | `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` | `3b2ae16497fbf36327467b6be61b9ca9d77696ce0030a1f90f79697afc2d7172` | ANCHOR | L3 §4 #59 的锚(`pv.technical` worker 摘要)。重冻后该字符串不再出现在 `phase8_catalog_manifest_v1.json` ⇒ L3 gate `test_every_pin_row_names_a_real_file_and_a_real_anchor_inside_it` 红 |
| 3 | `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` | `079de03217501ee09e306bd4ee265845553cd19729b8eb6247a81a51fcbdf332` | ANCHOR | 同上,L3 §4 #60(`text.news` worker 摘要) |
| 4 | `docs/superpowers/ledgers/2026-07-31-L3-refreeze-inventory.md` | `b1775ea2a5579c9679204cfb73e8780d8b9c52a2ec51e19251c471486bfd8242` | ANCHOR | 同上,L3 §4 #61(`skill.text.news@1`) |
| 5 | `tests/orchestration/test_l3_refreeze_gate.py` | `_REFREEZE_CHAIN_PREFIXES` | FROZEN-EXPECTATION | 其 docstring 明写「窗口已关,此处任何位移现在是回归」。本 charter 是**第二次授权移动密封链**,所以 T3 必须**有意识地**重写 P8/P9/P10 三格并改写那段措辞——不是默默改数字 |

**L3 §4 #62/#63/#64/#65 的锚(`e794e012…` / `696daf80…`)不受影响** ——
它们钉的是 P3-full 材料与预设 record digest,实测本窗口不动(§2 #3、#26)。

**这是本清单最容易被漏的一类:它落在 `docs/` 里,却由测试强制。**
L3 台账被两份 gate 当**可执行工件**读,不是散文。

---

## 7. STOP 条件实测 **[gate-parsed]**

章程 §"STOP conditions":①链与 L3 §12.2 漂移;②skill 材料若与注册模型 docstring 耦合到
能推动 schema manifest(schema manifest 移动 = 在所有裁决之外)。

| 判据 | 实测 | 结论 |
|---|---|---|
| 链现推 vs L3 §12.2 收官值 | 十节点逐字节相等(§2 #1-#10) | **无漂移** |
| 11 份 schema manifest golden 的字节 sha,在 SKILL 探针下 | 全部不变(0/11 移动) | **零触发** |
| 七个 registry digest(P3-data / P3-full / P7 / P7-planner_spec / P8 / P9 / P10),在 SKILL 探针下 | 全部不变(0/7 移动) | **零触发** |
| 三份预设 record digest,在 SKILL 探针下 | 全部不变(0/3 移动) | **零触发** |
| SKILL 材料是否耦合注册模型 docstring | 否:树文件经 `_MaterialSpec.path.read_bytes()` 进 lane 构造器,不进任何 `ContractModel` 的 docstring | **不适用** |

**结论:本任务未触发任何 STOP 条件。**

---

## 8. RULING D-A —— `dec.pm` / `dec.research_mgr` 是否入窗(**待裁**)

### 8.1 实测输入(全部现推,见 §1)

| skill | 收到 L3 渲染块 | 白名单命名块身份 | 与缺陷 7 同根 | 今日在生产预设中 |
|---|---|---|---|---|
| `text.news` | 是(`news`) | 否 | **是**(F-A 直接证据) | 十节点 + screening lane |
| `pv.technical` | 是(`verified_snapshot`) | 否 | **是**(F-B 直接证据) | 十节点 |
| `dec.pm` | 是(`verified_snapshot`) | 否 | **是,CONFIRMED**(三跑逐条现推,§1.2) | 十节点 + 减证据 |
| `text.policy` | 是(`news`) | 否 | **是(机理同型,休眠)** | **无** |
| `dec.research_mgr` | **否(零预取行)** | 否 | **否,REFUTED** | 十节点 + 减证据 + screening lane |

### 8.2 四个选项与逐项代价

| 选项 | 材料 | §3 字面量 | §4 字节 sha | §5 fragile | 额外文档同改 | 未修的残留 |
|---|---|---|---|---|---|---|
| **A2 · 2 文件** `text.news`+`pv.technical` | 2 | **15** | #1-#3 + 镜像 #4/#5/#7/#8 | 无 | 无 | pm 三跑同型不引用**继续**;`text.policy` 休眠脱节继续 |
| **A3 · 3 文件** +`dec.pm` | 3 | **17** | 同上 + 镜像 #6 | **#1(交付物③ `[1]`)** | `docs/superpowers/specs/2026-07-17-dec-research-mgr-pm-skills.md` 第 2 块 | `text.policy` 休眠脱节继续 |
| **A4 · 4 文件** +`text.policy` | 4 | **19** | 同 A3 | 同 A3 | 同 A3 | 无已知同根脱节 |
| **A5 · 5 文件** +`dec.research_mgr` | 5 | **21** | 同上 | **#1 两个块都动** | 同上,第 1 块也动 | 无;但含一份**测量上无缺陷**的材料 |

**边际读法**:链摘要 11 处对任何非空窗口都一样;每多一份 SKILL 只多 **2 处**字面量,
且都在同一个 `phase8_catalog_manifest_v1.json` 里。**字面量不是选型的主要成本。**
主要成本差在两处:(i)A3 起触发交付物③文档的逐字节同改(§5 #1,唯一 fragile);
(ii)A5 把一份**实测无缺陷**的材料放进红窗,与「红集 ⊆ 实测清单」的红线相抵。

### 8.3 三条测量陈述(不构成裁决)

1. `dec.pm` 与缺陷 7 **同根 CONFIRMED**,证据是三份耐久记录逐条现推(§1.2),不是推断。
2. `dec.research_mgr` 的同根假说 **REFUTED**:它零预取行、三跑零块;它的空白是 pm/news/technical
   修好后**自动消失**的下游表现。若仍要动它,理由必须写成「预防性对称/引用义务的措辞统一」,
   **不能写成「同根」**——那会在台账里留下一句实测不成立的话。
2 的推论:L2-b Task-9 待办#1「pm 归因实验」的**材料侧**由 A3 起即可了结;
   `dec.research_mgr` 侧无材料可归因。
3. `text.policy` 是章程未点名的第四份 GAP-CONFIRMED。它**今天不在任何生产预设里**,
   所以「不修」不会在明天的 watcher 里造成损失;但它与 `text.news` 共用同一条 `news` 预取行机理,
   将来任何一次 planner 选中或预设扩张都会以完全相同的方式失效。
   **一次开窗顺手修 vs 留给第三次开窗**,是控制器的取舍。

**状态:待裁。**

---

## 9. RULING D-B —— 引用义务的强度(**待裁**)

### 9.1 五份 SKILL 今天关于「引用证据」的原话(逐字)

| skill | 今日措辞 | 强度 |
|---|---|---|
| `text.news` | `headline` and `summary` must be faithful to the block — no sharpening, no added figures. Every number, price or date in the `summary` must trace to the item it summarizes (source_span discipline). | 硬(但只约束**已经写下的**数字的溯源,不强制**必须写下**一个数字) |
| `pv.technical` | Anti-fabrication is absolute: every indicator value must trace to the verified anchor or a prefetched block. | 同上:约束溯源,不约束「必须引」 |
| `text.policy` | (同族溯源条款) | 同上 |
| `dec.pm` | Every figure traces to an upstream artifact, block or flag; when the chain is too weak to arbitrate, return Hold, state the insufficiency driver, and give the flip triggers that would resolve it. | 同上;并且**明确给了一条「证据不足就 Hold」的合规出口** |
| `dec.research_mgr` | Do not restate upstream reports back into your rationale — cite the decisive points. | 提示性(`cite` 是祈使,但没有「否则违规」的界定) |

**共同结构**:五份都是**反捏造**条款(写下的必须有来源),没有一份是**反沉默**条款
(名单内非空的块必须被引用或被显式声明不可用)。

### 9.2 「提示性 = 零效果」的实测账

| 证据 | 数值 |
|---|---|
| `dec.pm` 收到 ok+PIT-passed 读数而行文零引用 | **3/3 次**(2026-08-02 / 08-06 / 08-11,§1.2) |
| 三次里模型主动**声明**「有一个读数但不可用/不相关」 | **0/3 次** |
| 三次里模型**捏造**数字 | **0/3 次**(反捏造条款有效) |
| `text.news` 收到 19 行真新闻而产出 `items: []` | 1/1 次(F-A) |
| `pv.technical` 收到真结算报价而产出 `anchor_price=0.0` | 1/1 次(F-B) |

**读法(重要,不要读反)**:现有条款**不是失效**,是**只覆盖了一半**——
它成功阻止了捏造(0/3 捏造),它从未要求过引用。所以「提示性=零效果」这句话的准确形式是:
**现有材料对「沉默」这一失败模式零覆盖**,而不是「模型不听材料」。三次真跑恰恰证明模型**极其听话**。

### 9.3 两个选项

* **B-硬**:写成 SKILL 硬性条款 ——「当白名单内的块存在且非空时,报告必须引用其中至少一个读数
  (数值 + 日期 + 来源名),或显式陈述为何不可用;静默无视是材料违规」。
  * 代价:每份涉事 SKILL 多一段;`dec.pm` 需要与它现有的「证据不足就 Hold」出口**并存而不打架**
    (措辞必须是「Hold 仍要说明块里那个读数为何不足以改变裁决」,否则模型会走旧出口)。
  * 收益:这是出口闸 §5(b)「引用链另一半」唯一可验的写法。
* **B-软**:仅提示性(如 `research_mgr` 现有的 `cite the decisive points`)。
  * 代价:与 §9.2 的 3/3 记录同型的措辞已在树上三个月,零次触发引用。
  * 收益:材料改动更小,模型自由度更高。

**章程 §2 已预置倾向 B-硬**(「引用义务随材料一起写进…从此是材料违规」),
本包只补上一条测量修正:**B-软不是「没效果」,而是「从未覆盖过这个失败模式」**——
所以「三次真跑已证明提示性=零效果」这句否决理由,准确表述应是
「三次真跑证明该失败模式当前零覆盖」。

**状态:待裁。**

---

## 10. RULING D-C —— 验证跑失败(模型仍不引用)的处置(**待裁**)

### 10.1 两个选项的实测代价

| 选项 | 是否需要第二次开窗 | 生产姿态后果 | 可复用的先例 |
|---|---|---|---|
| **C-迭代**:材料再迭代一轮 | **否** —— 窗口在 T3 重冻**关账**前一直开着;若 T4 验证跑失败时窗口已关,则第二轮材料修改需要**再一次重冻**(P8/P9/P10 再动一次,§3 的 11+2N 处再走一遍) | 维持全预设默认,直到第二轮验证跑 | L3 Task 3 的「修订轮」在**同一窗口内**改材料、Task 5 一次性重冻——**同窗内迭代零额外重冻** |
| **C-记录**:记为模型侧极限并回拨 reduced | 否 | `var/secrets.env` 取消注释 `GUANLAN_SEATS_DEEP_PRESET=reduced` + 重启 9999(L3 §12.12 已实操过一次,备份 `.bak-l3t7`) | L3 §12.12「生产姿态切换」逐字可复用 |

**关键测量事实(纠正章程 §7 D-C 的括注)**:「材料再迭代 = 第二次开窗?」的答案取决于**时序**:

* 若 T4 验证跑安排在 **T3 重冻提交之前**(窗口未关)——再迭代**零额外成本**,
  只是把 T3 的重冻推后。
* 若 T4 在 **T3 关账之后**(章程 §6 的任务骨架正是这个顺序:T3 重冻 → T4 战役)——
  再迭代 = **第二次重冻** = 第三次开窗授权。

**因此 D-C 实际上不是一个「失败后怎么办」的问题,而是一个「T3 与 T4 的顺序」问题。**
控制器可以通过把 T3 的**关账提交**推迟到 T4 验证跑之后,使 C-迭代变成同窗内的零成本路径
(代价:红窗停留更久,`tests/orchestration` 在窗口期非全绿,违反 L3 §12 那条
「窗口自本节所在提交起 CLOSED」的既有纪律——需要控制器明示豁免)。

### 10.2 回拨 reduced 的实测代价

`reduced` 预设(§1 表:8 节点)**不含** `text.news` 与 `pv.technical`,
但**含** `dec.pm`。所以回拨 reduced:

* 消灭 F-A/F-B 两个空证据席(它们不在图里)——每票省 ~2 次结算 LLM;
* **不消灭** pm 的同型不引用(pm 在减证据图里仍持有那条 `verified_snapshot` 预取行,
  实测 run `deep-bc98936a9131b574` 走的就是这条路);
* 徽章三件套逐字显形(`subject_run_scoped_v1` + `reduced_evidence_preset_v1` +
  `reduced_evidence_missing:pv.technical,text.news`),生产上诚实可见。

**状态:待裁。**

---

## 11. T1 出口清点

| 项 | 结果 |
|---|---|
| F-E 全树实测(27 份) | 完成;4 份 GAP-CONFIRMED、23 份 NO-BLOCK、0 份 NAMES-BLOCK |
| `dec.pm` 同根 | **CONFIRMED**(三跑逐条现推) |
| `dec.research_mgr` 同根 | **REFUTED**(零预取行、三跑零块) |
| 章程未点名的新发现 | `text.policy` 同型 GAP-CONFIRMED(休眠) |
| 三类清单 | (a) 21 处 / 6 文件;(b) 8 行(3 摘要二阶 + 5 镜像);(c) 14 行(1 fragile / 3 self-protecting / 10 benign) |
| 跨台账连坐 | 5 行(L3 台账 §2 相等 + §4 三个锚 + L3 gate 的冻结期望) |
| 链基线 vs L3 §12.2 | **零漂移(MATCH)** |
| STOP 条件 | 零触发(schema manifest 0/11、registry 0/7、preset record 0/3) |
| 裁决包 | D-A / D-B / D-C 三份,全部**待裁** |
| 本任务移动的密封物 | **零**。T1 只新增一个测试文件与本台账 |
