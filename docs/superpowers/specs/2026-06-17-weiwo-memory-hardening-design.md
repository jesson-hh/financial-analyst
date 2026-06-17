# 帷幄记忆子系统加固 — 设计

**日期**:2026-06-17
**承接**:2026-06-17 帷幄中枢审查(记忆长上下文 + 工具发现)。本设计只修审计确认的 **#1 全局记忆召回天花板**(尾-2000 注入 + curator 死代码)与 **#2 key 消毒碰撞误删**。工具发现侧的 #3/#4(提示词漂移、news_query 悬空引用)不在本期范围。

## 目标

让帷幄全局记忆「能记忆长上下文」名副其实:无论 `memory.md` 多大,**稳定偏好永远被每轮注入召回得到**;临时笔记/能力缺口走近期窗 + 自动归档,归档仍可经工具召回;并消灭写入侧不同 key 被消毒折叠成同一个导致的跨主题静默误删。

## 背景(审计确认的两个缺陷)

- **#1**:`_memory_block`(api.py:186)每轮只注入 `memory.md` 尾 **2000 字符**(且从行中间盲切);`memory_write_impl` 每条 cap 280;本该把文件收敛到 120 行的 `curator.consolidate_memory`(curator.py:10)**全仓零生产调用、无调度、无端点 = 死代码**。后果:文件一旦 > 2000 字符(约 6 条满额 / 13 条典型),靠前的稳定偏好每轮完全不可见,agent 不主动调 `ww_memory_read`(也只读尾 4000)就永远看不到。当前线上 `memory.md` 仅 464 字节/2 行,恰好尚未暴露,但自学回路会持续写同一文件,迟早越界。
- **#2**:key 消毒 `_re.sub(r"[^\w一-鿿\-]", "", key)`(tools.py:481)是多对一映射:`a.b` 与 `a/b` 都 → `ab`、`key one` 与 `key.one` 都 → `keyone`。replace 收敛(tools.py:496-500)用消毒后 key 拼锚定正则先删后写 → 两个语义不同的主题被当成同一 key 互相覆盖,旧记忆静默丢失,且返回固定「已记入帷幄记忆。」无任何提示。实测复现。

## 核心架构原则

全局记忆分两类,**注入优先级 == curator 收敛优先级**,从此一致:

| 类别 | 判定 | 注入 | 归档 |
|---|---|---|---|
| **常驻**(keyed) | 行匹配 `- [date] (key) text` | **永远全量注入** | **永不归档** |
| **易逝**(unkeyed) | 行匹配 `- [date] text`(无 `(key)`) | 最近 N 条近期窗 | 超阈值按龄归档(可经工具读回) |

「想让某条永久」= 给它一个 key。能力缺口当前由 agent 以无 key 方式写入 → 属易逝(近期窗仍能浮出最近的);本期不改提示词强制给缺口加 key(YAGNI),仅在设计上保留「加 key 即常驻」的能力。

不匹配任一格式的畸形行 → 归入易逝(近期窗 + 可归档),不丢。

### 模块边界(防循环导入)

共享的「行分类」逻辑(keyed/unkeyed 解析正则 + `classify_lines(text) -> (keyed, unkeyed)`)落在 **`curator.py`**(中性模块,不依赖 tools/api),由组件 1(api)与组件 2(curator 自身)共用,杜绝两处正则漂移。依赖方向:`api.py → curator`、`tools.py → curator`,`curator` 不反向 import tools/api。`tools.py` 对 `consolidate_memory` 采用**惰性 import**(在 `memory_write_impl` 内 `from guanlan_v2.console.curator import consolidate_memory`,对齐仓内 engine 惰性 import 惯例),避免模块加载期循环。注入预算/选择函数 `_select_memory_lines`(只被 `_memory_block` 用)放 `api.py`,调 `curator.classify_lines`。

## 组件 1:结构化注入(重写 `_memory_block`,api.py:186)

**现状**:`_tail(memory.md)[-2000:]` + `_tail(notes.md)[-2000:]`,盲尾、会从行中间切。

**新逻辑**(提取为可单测的纯函数,如 `_select_memory_lines(text, …) -> str`,`_memory_block` 调它):

1. 按行解析 `memory.md`,用 `^- \[\d{4}-\d{2}-\d{2}\] \((?P<key>[^)]+)\) ` 区分常驻/易逝。
2. 全局块 = `全部常驻行(旧→新) + 最近 N_UNKEYED 条易逝行`,**整行拼接,绝不从行中间截断**。
3. 安全钳:
   - 易逝预算 `_INJECT_UNKEYED_MAX_CHARS`(默认 1500):超了从最旧易逝行起整行丢弃。
   - 常驻预算 `_INJECT_KEYED_MAX_CHARS`(默认 4000):**仅当常驻行总量超此预算**才从最旧常驻整行丢弃,并在块尾追加一行 `(更早常驻偏好已超注入预算,可用 ww_memory_read 查看全部)` 诚实标注。常规情况下常驻数远小于此,不触发。
4. 会话 `notes.md`:同样行级、最近 `N_SESSION`(默认 12)条整行;无文件省略整段(行为不变)。
5. 段落标题沿用现有:`[帷幄记忆·全局]` / `[本会话笔记]`。

**参数**(具名常量,集中在 tools.py 或 api.py 顶部,可调):
- `N_UNKEYED = 6`、`_INJECT_UNKEYED_MAX_CHARS = 1500`、`_INJECT_KEYED_MAX_CHARS = 4000`、`N_SESSION = 12`。

**不变量**:常驻行在 `memory.md` 存在 ⇒ 必出现在注入块(除非常驻总量越过 4000 预算,此时有诚实标注且 `ww_memory_read` 仍可读全量)。

## 组件 2:curator 接线 + 归档可召回(curator.py + tools.py)

**2a. `consolidate_memory` 改为常驻感知**(curator.py:10):
- 入参不变(`mem_path, archive_path, max_lines=120`),逻辑改为:
  - 解析 keyed/unkeyed(同组件 1 的正则,抽共享 helper 避免两处漂移)。
  - 保留 = `全部 keyed 行 + 最近 (max_lines − len(keyed)) 条 unkeyed 行`(若 `len(keyed) ≥ max_lines`,保留全部 keyed + 0 条 unkeyed)。
  - 归档 = 溢出的最旧 unkeyed 行,追加进 `archive_path`,带 `## 归档于 <ts>` 头(沿用现有格式)。**keyed 永不进归档**。
  - 返回 `{ok, archived, kept}`(沿用)。
- 保持纯函数、不持锁、不物理删(归档可恢复)。

**2b. 写入后触发**(`memory_write_impl`,tools.py):
- 成功 append global 行后,在**同一 `with _MEMORY_LOCK` 块内**,若 `memory.md` 行数 > `_CURATOR_TRIGGER_LINES`(默认 120),调 `consolidate_memory(_MEMORY_PATH, _ARCHIVE_PATH, max_lines=120)`。
- 在锁内调用是硬要求(审计指出 curator 不自持锁;`_MEMORY_LOCK` 是 `threading.Lock` 非可重入,curator 自身不再 acquire,故单次持锁安全,杜绝与复盘 fork 竞争)。
- curator 失败不影响写入成败(写已落盘);异常吞掉记 best-effort(收敛是增强项)。
- session scope 不触发 curator(notes 短命,delete_session 整体清)。

**2c. 归档可召回**(`memory_read_impl`,tools.py:848):
- scope=global / all 时,正文后追加 `_ARCHIVE_PATH` 尾部(`[-4000:]`),标注 `\n\n归档(更早易逝笔记,可恢复):\n…`;无归档文件则省略。
- scope=session 不变。

**常量**:`_ARCHIVE_PATH = _MEMORY_PATH.parent / "memory.archive.md"`、`_CURATOR_TRIGGER_LINES = 120`。

## 组件 3:收窄 key 消毒(`memory_write_impl`,tools.py:481)

- 改 `key = _re.sub(r"[\[\]()\r\n]", "", (key or "").strip())` —— **只剔除会破坏 `(key)` 标签格式或行/匹配的字符**(圆括号、方括号、CR/LF),保留 `.` `/` `:` 空格 `;` `-` `_` CJK 词字符等。
- 后果:`a.b` 仍 `a.b`、`a/b` 仍 `a/b`、`风险:x` 仍 `风险:x` → **不同 key 不再折叠碰撞**。匹配侧 `re.escape(key)`(tools.py:497)已足够中和残余正则元字符。
- 边界:消毒后为空(key 全是被剔字符)→ 当作无 key(走纯追加,不收敛),与现有无 key 路径一致。
- 锚定收敛正则(tools.py:496-500)逻辑不变,仅 key 取值变干净。

## 测试策略

**单元(TDD,先红后绿)**:
- `test_select_memory_lines_*`:① 常驻行在超大文件下必现;② 易逝只取最近 N;③ 整行切不产生半行;④ 易逝预算超限只丢最旧易逝、常驻不动;⑤ 常驻超预算才丢且带诚实标注;⑥ 畸形行归易逝不丢。
- `test_memory_block_*`:全局 + 会话两段、大文件场景。
- curator 常驻感知:`test_consolidate_keeps_all_keyed`、`test_consolidate_archives_oldest_unkeyed`、`test_consolidate_idempotent_under_threshold`、`test_consolidate_keyed_exceeds_maxlines`。
- 触发接线:`test_memory_write_triggers_curator_over_threshold`(写到 >120 行→archive 生成、memory.md 收敛、keyed 全保留)、`test_curator_runs_under_memory_lock`(并发/锁内调用)。
- read 归档:`test_memory_read_global_includes_archive`。
- #2:`test_memory_write_distinct_punctuation_keys_no_collision`(`a.b` 与 `a/b` 各留各的)、`test_memory_write_key_strips_only_format_breakers`、`test_memory_write_empty_sanitized_key_falls_back_to_no_key`;现有 `test_memory_write_replace_key_converges` / `_anchored_no_false_delete` 保持绿。
- 全部既有测试(511+)保持绿。

**真机(独立验证 + 性能证据)**:
- T1 召回天花板:程序性把 `memory.md` 灌到远超旧 2000 窗口(多条 keyed + unkeyed),起全新会话问老的 keyed 偏好(csi300/月频)→ 应 0 工具召回(对比:旧代码会丢)。
- T2 curator:写到 >120 行触发 → `memory.md` 行数有界、`memory.archive.md` 生成、keyed 全保留、`ww_memory_read` 能读回归档。
- T3 常规召回不退化(对比修复前)。
- T4 #2:经真机或直调写两个标点差异 key,验证各留各的。
- T5 性能:注入块体积/主 turn 延迟修复前后对比,确认结构化注入不显著加延迟。
- 验证后清理测试产生的 session / 还原 memory.md(零残留),不污染生产。

## 改动文件

| 文件 | 改动 |
|---|---|
| `guanlan_v2/console/api.py` | `_memory_block` 重写 + `_select_memory_lines`(调 `curator.classify_lines`);注入预算常量(N_UNKEYED/N_SESSION/两个 MAX_CHARS) |
| `guanlan_v2/console/tools.py` | `memory_write_impl`(#2 收窄消毒 + 惰性调 `curator.consolidate_memory` 触发);`memory_read_impl`(读 archive);常量 `_ARCHIVE_PATH`、`_CURATOR_TRIGGER_LINES` |
| `guanlan_v2/console/curator.py` | `classify_lines`(共享行分类,中性)+ `consolidate_memory` 常驻感知 + archive 路径常量 |
| `tests/test_console_api.py` | 结构化注入用例 |
| `tests/test_console_tools.py` | #2、读归档、curator 触发用例 |
| `tests/test_curator.py` | 常驻感知收敛用例 |

## 红线 / 非目标

- 全量 `events.jsonl` / 记忆原文不改写;归档不物理删(可恢复)。
- 所有 `memory.md` read-modify-write(含 curator 触发)在 `_MEMORY_LOCK` 内串行。
- 诚实标注:注入块若因预算钳掉常驻内容,必须标注且 `ww_memory_read` 能读回全量。
- 守护计数 26/44 不变(本期不新增/删 ww_ 工具;curator 仍不入 ww_ 表,只被 `memory_write_impl` 内部触发)。
- **非目标**:工具发现侧 #3(提示词↔工具表漂移)/#4(news_query 悬空引用);把能力缺口强制设为常驻(prompt 改动);运行期热注册等远期项。
