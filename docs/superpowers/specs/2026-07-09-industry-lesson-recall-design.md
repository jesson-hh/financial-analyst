# 经验反哺按行业相关性召回(方案 a)设计

**日期**:2026-07-09
**范围**:改造 `guanlan_v2/screen/rerank.py` 的教训召回,从"全局取最后 k"变为"按今天盘面行业相关性过滤后取近邻"。纯确定性、零新依赖、零向量/embedding、贴现有「行业·」keyed 设计。

## 背景

P6′ 行业重排层已交付。教训以 keyed 行写在帷幄记忆文件(`_MEMORY_PATH`),格式 `- [YYYY-MM-DD] (行业·XXX) 正文`,由 `ww_rerank_distill`([tools.py:1093](../../guanlan_v2/console/tools.py))写入(key 自由文本,强制加 `行业·` 前缀,除此不归一)。

现召回 [`read_industry_lessons(k=5)`](../../guanlan_v2/screen/rerank.py) 读整个记忆文件、正则挑出所有 `行业·` 行、**取尾部 k 条**——按时间近邻,**不管今天重排的是不是这些行业**。教训一多就串味/失焦:今天排半导体,却可能注入上周攒的消费/医药 5 条。

本次把召回改为**相关性优先**:只召回与今天盘面行业相关的教训。

## 核心事实(设计依据)

- **教训 key** = `行业·<自由文本>`(如 `行业·光芯片`、`行业·上游材料顺风`)。key 里的行业名是 agent 蒸馏时自选的自由文本,**不保证与 seg_name 逐字相同**。
- **今天盘面的行业** = 待重排 rows 里 `chain.seg_name` 的去重集合(seg_name = 段的 `display_name or name`,如 `光芯片`/`液冷`/`半导体材料`,见 [rescore.py:112](../../guanlan_v2/screen/rescore.py))。`chain=None` 的票(不在链上)不贡献行业。
- 因 key 是自由文本,匹配**不能用逐字相等**(会漏 `行业·光芯片顺风` vs 盘面 `光芯片`),须用子串包含。

## 设计

### 改造点 1:`read_industry_lessons` 签名与逻辑

```python
def read_industry_lessons(board_segs: Set[str], k: int = 5) -> Tuple[List[str], List[str]]:
    """按今天盘面行业相关性召回帷幄「行业·」教训。
    命中 = 某盘面 seg 与教训 key(去『行业·』前缀、非空)双向子串相含。
    返回 (lessons, matched_segs);board_segs 空 / 无记忆 / 不可读 / 无命中 → ([], []) 诚实降级。
    lessons 保持既有格式 "(行业·XXX) 正文";matched_segs = 被最终保留的 k 条命中到的盘面 seg 去重升序。"""
```

逻辑:
1. 清洗 `board_segs`(去空白、剔空);空集 → 直接 `([], [])`。
2. 读记忆文件(异常 → `([], [])`)。
3. 逐行正则 `_LESSON_PAT` 挑 `行业·` 行;取 key 去 `行业·` 前缀得 `key`;**若 `key` 为空则跳过**(防 `行业·` 空 key 的空串子串匹配全命中)。
4. 命中判定:`any((s in key) or (key in s) for s in segs)` ——**双向子串**(既容 `光芯片` ⊂ `光芯片顺风`,也容 `半导体` ⊂ 盘面 `半导体材料`),最小化"漏召回相关教训"这类假阴性。
5. 命中行保序累积;取尾部 k 条(近邻)。
6. `matched_segs` 从**最终保留的 k 条**反推(反映真正注入了哪些行业的教训,诚实)。
7. 只匹配 key,**不匹配正文**——key 是刻意的检索把手,正文是整句会引入噪声(不在范围)。

### 改造点 2:调用点 `run_rerank`([rerank.py:115](../../guanlan_v2/screen/rerank.py))

- 把 `ranked` 的构建提到 `read_industry_lessons` 之前(现顺序相反)。
- 从 `ranked` 拼 `board_segs`:`{r["chain"]["seg_name"] for r in ranked if isinstance(r.get("chain"), dict) and r["chain"].get("seg_name")}`。
- `lessons, matched_segs = read_industry_lessons(board_segs, k=5)`。
- 成功返回 dict 增字段 `"matched_segs": sorted(matched_segs)`(与既有 `"lessons_injected": len(lessons)` 并列)。此 dict 是 rescore 归档的 rerank 块,随现有链路落 `var/rescore_runs.jsonl`,无需改归档管线。

### 透明度 / 诚实显形

档案里 `lessons_injected`(注了几条)+ `matched_segs`(命中了哪些行业)让"为什么只注 2 条 / 为什么注 0 条"可查、可复盘。无命中 → 注 0 条,重排照跑(`read_industry_lessons` 返 `[]` 本就是已支持合法路径)。

**透明度缺口(交底,不在本次范围)**:`matched_segs` 只落 `var/rescore_runs.jsonl` 归档;`ww_rescore_view`/`_rescore_lines`([tools.py:985](../../guanlan_v2/console/tools.py))与选股页 UI **当前不显示它**——要人肉复盘"为何注 0/2 条"须直接看 jsonl。要让它在成绩单/UI 显形需另加渲染,属可选后续。

## 已知取舍(交底,不在本次范围)

**跨行业/通用教训**(key 无具体行业名,如 `行业·大盘逆风统一降档`)在严格相关下**永不被召回**。当前通用教训极少,先 YAGNI。未来若需要:加 `行业·通用·` 约定让其常驻即可(独立小改,不在本次)。

**双向子串的轻度过召回**:极短/极通用的 key(如 `行业·材料`)可能经 `key in seg` 命中多个材料类 seg。属可接受(key 系 agent 自选、语料小);真过召回易后紧。

## 红线(全保持)

- **展示型**:重排结论只进数据榜/A-B 篮,**绝不进任何选股信号/picks/blend/seats**。本次只改召回过滤,不碰这条边界。
- **诚实降级**:无匹配/不可读一律返空、不编造、不回填不相关教训(方案 A:严格相关、不回填)。
- **UI 只填充**:选股页名次对照列不动。
- **零新依赖**:纯 Python 字符串子串,无 embedding/向量库/新包。

## 测试

**`read_industry_lessons`(纯函数,重点)**
- `board_segs` 空集 → `([], [])`。
- 记忆文件不存在 / 读抛错 → `([], [])`。
- seg 与 key 逐字相等 → 命中。
- seg ⊂ key(`光芯片` in `光芯片顺风`)→ 命中。
- key ⊂ seg(`半导体` in 盘面 `半导体材料`)→ 命中。
- 无重叠 → 不命中(严格,不回填)。
- 命中数 > k → 保留尾部 k;`matched_segs` 只反映保留的 k 条。
- `行业·`(空 key)行 → 被跳过,不产生空串全命中。
- 保留行格式仍为 `(行业·XXX) 正文`。
- `matched_segs` 去重升序。

**`run_rerank`(调用点)**
- `board_segs` 从 rows 正确拼出;`chain=None` 的票被跳过。
- 成功返回含 `matched_segs`;`lessons_injected` 与之一致。
- 现有 `test_screen_rerank.py` 5 处引用(第 40/47 行直接调、83/92/111 行 monkeypatch)按新签名 `(board_segs, k=5) -> (lessons, matched_segs)` 更新。
  - **第 40 行是语义重写、非机械补参**:召回算法本身变了(近邻 k → 双向子串过滤后取近邻),期望值须按新过滤逻辑、依传入的 `board_segs` 重新推导,不是补个参数就行。
  - 83/92/111 桩须同步改签名与返回:`lambda k=5: [...]` → `lambda board_segs, k=5: ([...], [...])`(否则 run_rerank 以 `(board_segs, k=5)` 位置调用会撞 `'k' 多值 TypeError` 且裸 list 无法解包)。
  - 第 111 行测试仍断言 `lessons_injected==1`(成立),可顺带加 `matched_segs` 断言。

**回归 + 真机 e2e**
- 全量 pytest 绿。
- 9998 隔离真机跑一次 rescore(带 rerank phase),验档案里 `matched_segs`/`lessons_injected` 如实、重排结论集合校验照旧、名次对照列不变。

## 血缘审计结论(2026-07-09,5-agent 只读工作流 + 对抗复核)

两条最吃重的断言已独立复核 **CONFIRMED**:

- **唯一生产调用者**:全仓 `read_industry_lessons` 仅 [rerank.py:122](../../guanlan_v2/screen/rerank.py) 一处生产调用(无 getattr/动态派发);其余引用皆在 `tests/` 与 `docs/`。
- **红线牢固 + 默认零变化**:重排结论虽写入 `var/screen_picks.jsonl`,但盖 `snapshot=False`+`kind='rerank_ab'`,被 [picks.py:41](../../guanlan_v2/screen/picks.py)(snapshot_only)与 [screen/api.py:1495](../../guanlan_v2/screen/api.py)(默认过滤 rerank_ab)双重隔离于正式 picks;唯一读者是展示型 A/B 对照 [seats/api.py:2032](../../guanlan_v2/seats/api.py)(零信号回写)。无 blend/seats/v4 路径 import rescore/rerank。日跑双重 opt-in 默认关(`GUANLAN_RERANK_DAILY` + `GUANLAN_REGEN_DAILY`),rescore 无自带定时器。

**当下 no-op(已实测坐实)**:`var/console/memory.md` 现有 **0 条「行业·」教训**,`var/rescore_runs.jsonl` 带 rerank 块的 3 个 run `lessons_injected` **全 = 0**。故旧口径(近邻 k)与新口径(相关性过滤)在空池上都返回 `[]`,合并本改动在运行期逐字节相同,直到第一条「行业·」教训被蒸馏。

**A/B 可比性(潜伏,须记账)**:已累积 96 条 `rerank_ab`(48 对)在近邻口径下生成。换召回口径 = 换 rerank 臂(处理组)定义 → A/B 趋势合并点理论上有 regime 断点;今天因 0 教训未兑现,待「行业·」教训开始蒸馏且两口径选集分歧后兑现。度量本身(basket_perf)不读 lessons、口径不漂。

**落地顺序(严格更优)**:趁当前 no-op **先合此改动、再开始蒸馏教训**——则第一条教训诞生前召回配方已是终版,**永不产生 regime 断点**;反之"先近邻攒教训、后切相关性"才会制造断点。

**合并前必配套(同一 diff)**:改造点 2([rerank.py:122](../../guanlan_v2/screen/rerank.py))的生产改写 + `test_screen_rerank.py` 5 处测试同步(见上 §测试),缺一即运行期 TypeError / 测试红。
