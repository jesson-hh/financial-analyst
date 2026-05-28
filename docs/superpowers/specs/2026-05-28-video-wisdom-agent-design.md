# 视频经验 Agent (video-wisdom) 设计文档

| 字段 | 值 |
|---|---|
| 日期 | 2026-05-28 |
| 状态 | 设计已确认, 待 review → 转实施计划 |
| 目标仓库 | `G:/financial-analyst` (实现端) |
| 关联仓库 | `G:/stocks` (转写产出端, 起源 PoC) |
| 主题 | 从视频博主 (B站为主) 内容沉淀结构化投资经验, 供 agent / 研报检索引用 |

---

## 1. 背景与动机

B 站等平台有大量 A 股博主每日做盘面复盘 / 策略分享。这些内容里藏着可操作的经验 (压力位判断、板块虹吸警示、出货信号、仓位纪律等), 但以视频形式存在, 无法被检索和复用。

PoC 已在 `G:/stocks` 跑通: `yt-dlp` 拉音频 + `faster-whisper large-v3` 转写 (RTF 0.088x), 人工浓缩出 12 条高质量经验条 (`G:/stocks/strategy/wisdom/bilibili_notes.md`), 每条含 经验/适用条件/操作建议/反例边界/置信/标签/来源。跨 UP 主对比验证了"互证 vs 冲突"机制的价值。

现在要把这条"拉取 → 总结 → 沉淀"链做成 financial-analyst 生态的一个可复用能力单元 (工具包 agent), 让经验沉淀从一次性手工变为可持续的半自动流程。

## 2. 目标 / 非目标

### 终态目标 (愿景)
一个 **video-wisdom 工具包 agent**: 由 4 个 `wisdom_*` buddy 工具组成, 由现有 buddy agent 编排, 覆盖完整链路:

| 阶段 | 工具 | 职责 | Phase |
|---|---|---|---|
| 拉取 | `wisdom_collect` | UP主清单 → yt-dlp 抓最新视频 → 转写 (降级三档) → 文本 | 2 |
| 总结 | `wisdom_extract` | 转写文本 → LLM 抽草稿经验卡 (含质量自评) | **1 (MVP)** |
| 沉淀 | `wisdom_review` | 列 draft → 一键 approve/reject → 进正式 KB | **1 (MVP)** |
| 检索 | `wisdom_search` | 检索 approved 卡片供研判 / 研报引用 | **1 (MVP)** |

### MVP 边界 (Phase 1)
打通 **总结 → 沉淀 → 检索** (extract / review / search) 闭环。转写文本由 `G:/stocks/scripts/video_transcript/transcribe.py` 现成产出, 手动 / 脚本喂入。先证明"经验沉淀 + agent 消费"有价值, 再补拉取与转写降级。

### 非目标
- **不在 MVP 做自动采集** (UP主清单订阅、定时抓取) — Phase 2
- **不在 MVP 做转写降级** (官方字幕 / 云 ASR / 本地 whisper 三档) — Phase 2
- **不绑 SubAgent 框架** — 用离散 buddy 工具 + 现有 buddy 编排
- **不做实时按需** (UI 给 URL 当场转写返回) — 选定批量沉淀模式
- **不重造检索** — 复用 `knowledge/LocalMarkdownKB`

## 3. 受众与关键约束

| 约束 | 来源 | 设计影响 |
|---|---|---|
| **公开包用户也要能用** | financial-analyst 发布到 PyPI | 转写必须无 GPU 降级 (Phase 2); 抽取走 fa 的 LLM 路由, 不依赖 G:/stocks |
| **批量沉淀模式** | 延迟不敏感 | 后台批处理, 不追求实时; 仿 news_reader 的数据沉淀思路 |
| **质量零容忍水文** | 用户反复强调 | 半自动 + 人工过闸: LLM 抽草稿进待审, 人工 approve 才进 KB |
| **跨仓库零耦合** | stocks 是研究端, fa 是公开端 | fa 只认"转写文本"输入, 不知道 stocks 存在 |

## 4. 架构

### 模块布局
```
src/financial_analyst/wisdom/                 ← 新建顶层模块
├── __init__.py
├── card.py        # WisdomCard dataclass + markdown ⇄ 对象 序列化 + schema
├── extractor.py   # extract_cards(transcript, source, existing) → [草稿卡]  调 LLMClient
├── store.py       # WisdomStore: save/load/list_by_status/set_status/next_id  状态=目录
├── prompts.py     # 抽取 prompt (质量标准 + 用 12 条 few-shot + 互证指令)
└── cli.py         # python -m financial_analyst.wisdom.cli extract <txt> --source...

src/financial_analyst/buddy/tools.py          ← 扩 2 个 Tool (MVP), 不新建文件
├── wisdom_review  # 列 draft + approve/reject  ← 人工过闸入口
└── wisdom_search  # 检索 approved, 复用 LocalMarkdownKB

复用 (不改):
├── llm/client.py          LLMClient.for_agent("wisdom")
└── knowledge/local_markdown.py   LocalMarkdownKB 指向 approved/ 目录
```

### 存储 (状态即目录)
```
~/.financial-analyst/wisdom/        ← 路径走 fa 配置入口 (_config.py) 解析, 默认此处
├── draft/      EV-*.md   (status=draft, 待审)
├── approved/   EV-*.md   (status=approved, 正式 KB, LocalMarkdownKB 索引此目录)
└── rejected/   EV-*.md   (status=rejected, 留痕不删, 防重复抽取)
```
**状态机 = 文件位置**: approve 即把文件从 `draft/` 移到 `approved/` 并回填 frontmatter。检索只需把 `LocalMarkdownKB` 指向 `approved/`, 天然只搜通过的卡。

### 数据流 (实线 MVP / 虚线 Phase 2)
```
   ┌─ Phase 2 ───────────────────────┐
   ┊ UP主清单 → yt-dlp → 转写降级三档 ┊  (wisdom_collect)
   └───────────────┬──────────────────┘
                   ┊
        [转写文本 .txt]  ← MVP: stocks/transcribe.py 现成产出喂入
                   │
                   ▼
   extractor.extract_cards() ──→ llm/client.py (for_agent("wisdom"))
                   │   1 次调用, JSON schema 约束, 输出 N 张草稿卡 + quality_score
                   │   同时喂 existing approved 卡摘要 → 判 corroborates/conflicts
                   ▼
   store.save() ──→ ~/.financial-analyst/wisdom/draft/EV-*.md
                   │
                   ▼
   wisdom_review (buddy tool) ──→ 列 draft(按 quality_score 降序) → approve/reject
                   │   approve: 移 draft/→approved/ + status/reviewed_by 回填
                   ▼
   wisdom_search (buddy tool) ──→ LocalMarkdownKB(approved/) 全文检索
                   │
                   ▼
        buddy agent 研判 / 研报引用
```

## 5. 数据契约: 经验卡片 schema

one-card-per-file, YAML frontmatter + 4 段式正文 (沿用 PoC 12 条结构):

```markdown
---
id: EV-008                  # 全局递增, 跨视频/平台共享序号
title: 证券板块作为科技兑现/抄底切换信号
status: draft               # draft | approved | rejected
quality_score: 0.82         # LLM 自评 0-1, 仅用于待审排序
confidence: 高               # 高/中/低, 经验本身置信, 会被检索消费
tags: [板块组合, 择时, 证券]
source:
  platform: bilibili
  up: 来去由心
  bvid: BV1F3Gy6oEMx
  date: 2026-05-27
  segments: "71-167"
corroborates: [EV-002]      # 互证卡片 id (LLM 判定 + 人工复核)
conflicts: []               # 冲突卡片 id → 渲染时标 [分歧]
created: 2026-05-28
reviewed_by: null           # approve 时填 (用户名 / agent)
---

## 经验
...

## 适用条件
...

## 操作建议
...

## 反例 / 边界
...
```

**字段说明**:
- `quality_score` (LLM 自评, 排序待审用) 与 `confidence` (经验置信, 消费用) 是两个不同维度, 都保留
- `corroborates` / `conflicts` 自动由 LLM 在抽取时判定 (喂 existing 摘要), 人工过闸时复核
- `id` 全局递增 `EV-NNN` (现有到 EV-012), 不按 UP/平台分命名空间 — 便于互证引用

## 6. 组件接口

```python
# card.py
@dataclass
class WisdomCard:
    id: str
    title: str
    status: str               # draft/approved/rejected
    quality_score: float
    confidence: str           # 高/中/低
    tags: list[str]
    source: dict              # {platform, up, bvid, date, segments}
    body: str                 # 4 段式正文 markdown
    corroborates: list[str]
    conflicts: list[str]
    created: str
    reviewed_by: str | None

    def to_markdown(self) -> str: ...
    @classmethod
    def from_markdown(cls, text: str) -> "WisdomCard": ...   # 与 to_markdown 可逆

# extractor.py  (无状态, async)
async def extract_cards(
    transcript: str,
    source: dict,
    existing: list[WisdomCard] | None = None,   # 现有 approved 卡, 供互证/去重
) -> list[WisdomCard]:
    """转写文本 → 草稿卡 (status=draft). 失败抛异常, 不返回半成品."""

# store.py
class WisdomStore:
    def __init__(self, root: Path | None = None):   # 默认走 _config 解析 ~/.financial-analyst/wisdom
    def save(self, card: WisdomCard) -> Path:        # 按 status 写到对应子目录
    def load(self, card_id: str) -> WisdomCard:
    def list_by_status(self, status: str) -> list[WisdomCard]:
    def set_status(self, card_id: str, status: str, reviewed_by: str | None = None) -> None:  # 移文件+回填
    def next_id(self) -> str:                        # 扫所有子目录 max(EV-NNN)+1

# cli.py
#   python -m financial_analyst.wisdom.cli extract <transcript.txt> \
#       --platform bilibili --up "舵主老徐" --bvid BV1f2VA6yEXS --date 2026-05-26
#   → 调 extractor + store.save(draft), 打印新增 draft 数 + 待审总数

# buddy/tools.py  扩 2 个 Tool (沿用现有 Tool dataclass 注册模式)
#   wisdom_review:
#     input_schema: {action: "list"|"approve"|"reject", card_id?: str, reviewed_by?: str}
#     - list: 返回 draft 卡 (按 quality_score 降序) 的 id/title/score/confidence/互证关系
#     - approve/reject: 调 store.set_status
#     cost_hint: "seconds", confirm_required: True (approve/reject 时)
#   wisdom_search:
#     input_schema: {query: str, tags?: list[str], top_k?: int}
#     - 复用 LocalMarkdownKB(approved/) 全文检索, 返回相关经验卡正文
#     cost_hint: "seconds", confirm_required: False
```

## 7. 质量保证

1. **质量门** (prompts.py 强约束): 每张卡必须含 (具体数字/阈值) **或** (可操作动作), **且** 必须有反例/边界, 否则 LLM 指令要求不产出。`quality_score` 给待审排序, 但**人工过闸是最终关**。
2. **互证/冲突**: 抽取时把现有 approved 卡的 `id+title+tags` 摘要喂 LLM, 判定新卡与哪些 corroborates / conflicts, 写进 frontmatter。复现 PoC 手动做的 EV-002/003/007 互证 (自动化版)。
3. **去重**: prompt 给 existing 让 LLM 不要重复产同一条; 同 `bvid` + 高相似 `title` 跳过 (rejected/ 留痕也参与去重比对)。
4. **人工过闸**: 半自动核心。draft 不进检索, 只有 `wisdom_review` approve 后才进 `approved/` 被 `wisdom_search` 命中。

## 8. 错误处理

| 场景 | 处理 |
|---|---|
| LLM 返回非法 JSON | 重试 1 次 → 再失败**跳过该视频 + 记日志**, 绝不写半成品脏卡 |
| LLM provider 不可用 | cli 明确报错退出 (非零 exit code) |
| 卡片目录不存在 | 首次运行自动建 draft/approved/rejected |
| id 分配 | `next_id()` 扫所有子目录 max+1, MVP 单进程无并发 |
| 转写文本超长 | MVP 单视频一般 <5000 字够用; 超长截断 + 警告 (分块留 Phase 2) |
| approve 不存在的 card_id | store 抛 KeyError, tool 返回 is_error |

## 9. 测试策略

fa 是正式包 (有 pytest)。所有测试 **mock LLMClient**, 不依赖真实 LLM / 真实视频:

| 模块 | 测试点 |
|---|---|
| `card.py` | `to_markdown` ⇄ `from_markdown` round-trip; frontmatter 缺字段降级 |
| `store.py` | save/load; set_status 移动文件 + 回填; list_by_status 过滤; next_id 序列 (EV-012→EV-013); 目录自动创建 |
| `extractor.py` | mock LLM 返回, 验证 prompt 含质量标准+few-shot+existing 摘要; JSON 解析; 非法 JSON 重试; 质量过滤 |
| `buddy tools` | mock store, 验证 review list/approve/reject 行为; search 调 LocalMarkdownKB 且只搜 approved |

## 10. 现有 12 条迁移

一次性脚本: 把 `G:/stocks/strategy/wisdom/bilibili_notes.md` 的 EV-001~012 拆成 one-card-per-file, 写到 `~/.financial-analyst/wisdom/approved/` (它们已人工 review 过, 直接 approved)。frontmatter 从现有索引表 + 各条来源段提取。迁移脚本是 MVP 的一部分 (验证 schema 设计无缝)。

> 注: stocks 侧 `bilibili_notes.md` 作为研究端原始记录保留, fa 侧是消费副本; 长期是否单向同步 (stocks→fa) 留作 Phase 2 决策, MVP 一次性导入即可。

## 11. Roadmap

- **Phase 1 (MVP, 本 spec)**: extract / review / search 三工具 + card/store/extractor/prompts/cli + 12 条迁移 + 测试
- **Phase 2**: `wisdom_collect` — UP主清单管理 + yt-dlp 抓取 + 转写降级三档 (官方字幕 cookies / 云 ASR (阿里云百炼) / 本地 faster-whisper); 在 cli 前接一层, 不动 MVP 模块
- **Phase 3 (可选)**: 经验卡量化验证回环 (EV-002/009 等可量化条目接 factors/, 跟 stocks 的 ic_monitor 呼应); UP主清单订阅 + 定时任务

## 12. 关键决策记录

| # | 决策 | 选择 | 理由 |
|---|---|---|---|
| D1 | 受众 | 公开包用户也要能用 | 转写降级 + 抽取走 fa LLM 路由, 最通用 |
| D2 | 使用模式 | 批量沉淀为主 | 转写有延迟, 实时按需 UI 体验差; 仿 news 沉淀 |
| D3 | 质量保证 | 半自动 + 人工过闸 | 用户对水文零容忍, LLM 自评只排序不拍板 |
| D4 | MVP 边界 | 抽取→待审→过闸→KB→检索 | 先证明核心价值闭环, 拉取/降级 Phase 2 |
| D5 | 代码组织 | 方案 B 独立 wisdom 模块 | 5 步天然是 pipeline+状态机, 边界最清晰, 不绑 SubAgent |
| D6 | agent 形态 | 离散工具族 + buddy 编排 | 复用现有 buddy, 不需独立 SubAgent, 最轻 |
| D7 | 状态机实现 | 文件位置 (draft/approved/rejected 目录) | 无需数据库, 检索指向 approved/ 即天然过滤 |

## 13. 风险与开放问题

- **LLM 抽取质量**: 自动抽取能否接近手工 12 条水平是未知数。缓解: 用 12 条做 few-shot + 强质量门 + 人工过闸兜底。MVP 跑通后用真实视频评估命中率。
- **互证自动判定准确性**: LLM 判 corroborates/conflicts 可能误判。缓解: 人工过闸时复核, frontmatter 可手改。
- **回声室**: 已识别 (PoC 两个 UP 主结论高度一致)。缓解: 多 UP 主 + conflicts 字段显式记录分歧, 不在本 MVP 解决, 是内容策略问题。
- **跨仓库同步**: MVP 一次性导入 12 条; 长期 stocks→fa 单向同步策略 Phase 2 再定。
- **检索召回 (已验证)**: `LocalMarkdownKB(root)` 确认接受自定义目录 (`rglob("*.md")`), 指向 `approved/` 可行。但其 `query()` 是**朴素子串计数** (`text.lower().count(q)`), 非语义检索, 中文长词召回可能弱 (查"半导体虹吸"未必命中"单板块成交占比")。缓解: `wisdom_search` 同时支持 `tags` 过滤; 卡片数量级小 (几十~几百) 时朴素检索够用; 语义检索留 Phase 3。
