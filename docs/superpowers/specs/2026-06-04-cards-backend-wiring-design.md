# 经验卡(cards)接真数据 · 设计 spec

- 日期:2026-06-04
- 模块:`ui/cards/`(经验卡 / 经验验证区),入口 `ValidationApp`([validation.jsx](../../../ui/cards/validation.jsx))
- 状态:已对齐,待用户过目 spec → 进实现

---

## 0. 背景与现状

cards 模块当前 **100% mock**:`SOURCES`(4 条硬编码原文)、`INITIAL_KB`(6 张硬编码卡)、提炼/对话修改/回测/置信度全为本地假数据;后端 = 无;唯一真实触点是可选 `window.claude` 与 `window.GL`(localStorage **seed**,按本任务定义亦属 mock)。

引擎(`G:/fa-watch-wt/src/financial_analyst`)**有** wisdom 经验卡子系统(`wisdom/card.py`、`store.py`、`extractor.py`、agent 工具 `wisdom_search`/`wisdom_review`),但:
- **无现成 REST 端点**(buddy/server.py 50 端点里无 `/wisdom` `/card` `/knowledge`);只在 agent 工具 / CLI 暴露。
- 三个 wisdom 根目录磁盘上**全空**(无真卡)。
- 形状与 UI 卡不一致(引擎:`status/quality_score/confidence高中低/body 4 段式/corroborates/conflicts`;UI:`cat/verdict/conf 0-100/ic/expr/insight/refs`)。

## 1. 决策记录(均经用户在知情下确认)

1. **架构**:把 wisdom 后端**复制进 guanlan-v2 自己改**。
   - ⚠️ 此决定**有意覆盖**本任务开头的三条硬规则:「禁止把引擎/stocks 代码复制进 guanlan-v2」「新后端能力加到引擎 buddy/server.py」「import 不 fork」。用户在矛盾被完整摆出后明确选择覆盖。因此本 spec 不再视其为违规,而是新基线;相关文档须同步改写。
2. **初始数据**:接受 store 初期为空,管线(沉淀)写入真卡后自然填充。
3. **范围(MVP)**:仅「右栏经验知识库(list)」+「用·沉淀(upsert/status)」接真;「原/炼/验」三阶段**保持现状**(仍为演示),留待后续 Phase 2。
4. **卡形状**:复制进来的 card 模型**直接改成 UI 卡的超集**,无映射层。

## 2. 范围

### In scope(本刈)
- guanlan 自有 `/cards/*` 后端(list / get / upsert / set-status),代码复制自引擎 wisdom 并改造。
- 右栏「经验知识库」从 `/cards/list` 读真卡(替换 `INITIAL_KB`);空库显示真实空态。
- 「用 · 沉淀入知识库」`promote()` → `POST /cards` 真持久化,写后刷新右栏。
- 文档同步改写 + 缓存串 `?v=` + 控制端独立验证。

### Out of scope(Phase 2,本刈不做)
- 「炼」走真 extractor(`POST /cards/extract`,依赖引擎 `LLMClient`)。
- 「验」走真回测(引擎已存在的 `/backtest/run`、`/factor/bench`)。
- 「原」左栏素材来自引擎(`/comments` 雪球、研报等)。
- 与引擎 wisdom store 的双向同步 / 去 drift。

## 3. 卡数据形状(guanlan 自有,UI 超集)

markdown + YAML frontmatter 落盘(沿用引擎 store 的"状态即目录"机制):

```yaml
---
id: EV-001              # store 分配,EV-NNN
title: 缩量企稳反转
status: approved        # draft | approved | rejected(决定落哪个目录)
cat: 价量                # 价量/资金/基本面/风格/情绪/另类/其他
tags: [反转, 缩量, 周频]
verdict: 通过            # 通过 | 存疑 | 驳回(UI 结论)
conf: 76                # 0-100 整数(UI 置信度)
ic: "0.043"            # 字符串保形
expr: "-rank(ts_sum(ret,5)) · (vol_ratio < 0.7)"
src: 研报               # 来源类型:研报/热帖/复盘/快讯/自定义
refs: []               # 关联 research/factor id(可空)
created: "2026-06-04"
reviewed_by: null
---

<insight 正文(可选,UI insight 字段)>
```

> 取舍:不保留引擎的 `quality_score / body 4 段式 / corroborates / conflicts`(MVP 用不到)。这正是"改成 UI 形状"的代价——与引擎 wisdom 卡不再同构。

## 4. 组件与文件

| 文件 | 动作 | 说明 |
|---|---|---|
| `guanlan_v2/cards/__init__.py` | 新增 | 包 |
| `guanlan_v2/cards/card.py` | 复制+改造 | 自 `wisdom/card.py`;`WisdomCard` → UI 超集字段;只依赖 yaml |
| `guanlan_v2/cards/store.py` | 复制+改造 | 自 `wisdom/store.py`;根目录走 `GUANLAN_WISDOM_ROOT`,默认 `guanlan-v2/.data/wisdom/` |
| `guanlan_v2/cards/api.py` | 新增 | FastAPI `APIRouter`,定义 `/cards/*` |
| `guanlan_v2/server.py` | 改 | `create_app()` 在 `build_app()` 后 `app.include_router(cards_router)` |
| `ui/cards/观澜 · 经验验证区.html` | 改 | 注入 `window.GUANLAN_BACKEND`;`validation.jsx?v=…` |
| `ui/cards/validation.jsx` | 改 | 右栏 KB ← `/cards/list`;`promote()` → `POST /cards`;删除 `INITIAL_KB` 作为数据源 |

## 5. 端点契约(guanlan 自有,挂在薄壳)

- `GET /cards/list?status=approved|draft|rejected|all`(默认 `approved`)
  → `{ "cards": [ {id,title,cat,tags,verdict,conf,ic,expr,insight,src,status,refs,created} ] }`
- `GET /cards/{id}` → 单卡对象(404 若无)
- `POST /cards`(upsert)body = 卡字段(无 `id` → `next_id()` 分配;`status` 默认 `approved`)
  → `{ "id": "...", ...卡 }`
- `POST /cards/{id}/status` body `{status, reviewed_by?}` → 在 draft/approved/rejected 目录间移动 → `{ "id","status" }`

## 6. 前端接线

- HTML:`validation.jsx` 前注入
  `window.GUANLAN_BACKEND = new URLSearchParams(location.search).get('backend') || '';`(同源约定,9999 薄壳即引擎);`src="validation.jsx?v=20260604"`。
- `validation.jsx`:
  - `const API = (window.GUANLAN_BACKEND || '');`
  - 挂载时 `fetch(API + '/cards/list')` → `setKb(...)`;失败 → 空数组 + 轻量提示(因"接受空",空态正常)。
  - `promote()`:除现有 `GL.put`(跨模块闭环,保留)外,新增 `POST API + '/cards'`,成功后重新拉 `/cards/list` 刷新右栏。
  - 删除 `INITIAL_KB` 作为初始数据(`kb` 初始 `[]`)。
  - `SOURCES` / 炼 / 验 维持现状(MVP 范围外)。

## 7. 数据契约合规

- 经验卡是 **guanlan 自有应用数据,非 stock_data**;存 `GUANLAN_WISDOM_ROOT`(默认 `guanlan-v2/.data/wisdom/`)。
- stock_data 仍**只经引擎 `get_data_paths` 引用**,本刈不碰——铁律不破。
- 不改 `G:/stocks`、不改引擎源、不 push、不合 main。

## 8. 文档同步改写(否则文档变假)

- [ui/cards/README.md](../../../ui/cards/README.md):后端行 `无` → `/cards/*`(guanlan 自有);数据来源改写;**状态/开放项**更新(MVP 接真说明 + drift 代价 + 炼/验待接)。
- [docs/module_map.md](../../module_map.md):cards 行后端列 `/cards/list,/cards,/cards/{id}/status`;状态改"KB 接真"。
- [ARCHITECTURE.md](../../../ARCHITECTURE.md):薄壳"不加业务端点"定义加注 cards 例外(有意 fork)。
- [README.md](../../../README.md):模块表 cards 后端;约束节注明 cards 后端为有意覆盖硬规则的 fork。

## 9. 控制端独立验证(不自报通过)

启动 `guanlan_v2.server` → Claude_Preview 开 cards 页 → `preview_eval` / `preview_network`:
1. `rootKids > 0`、`navTabs === 5`(基础渲染)。
2. 观察到 `GET /cards/list` 网络请求且 200、返回 `{cards:[...]}`。
3. 右栏**不含** `INITIAL_KB` 的标志性条目(如"业绩超预期漂移 PEAD"硬编码集);初始为真实空态。
4. 端到端:从页面上下文 `POST /cards` 一张卡 → 重新 `list` → 该卡出现在右栏(证明读写贯通真后端,而非 mock)。

## 10. 开放项 / 已知代价

- **drift**:复制使 guanlan cards 后端与引擎 wisdom 代码分叉,引擎后续改动不自动同步——用户选定的 fork 路线固有成本,记入 cards README 开放项。
- 「炼」「验」「原」仍为演示,Phase 2 再接(extractor / backtest / 雪球研报)。
- 形状已偏离引擎 wisdom 卡,若将来要回灌引擎需另做迁移。

---

> 备注:本仓库非 git 仓库(`Is a git repository: false`),故 spec 仅落盘,未做 commit。
