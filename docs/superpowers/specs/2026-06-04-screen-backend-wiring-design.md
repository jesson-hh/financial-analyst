# 选股(screen)接入观澜系统 · 设计 spec

- 日期:2026-06-04
- 来源:用户自带单页导出 `D:\download\观澜 · 选股 · 单页.html`(bundler 自包含,模板内 3 个源:数据内核 + 主壳 + 共享 bus/nav)
- 目标模块:新增 `ui/screen/`(选股),入口组件 `XuanguApp`
- 状态:**待用户过目 spec → 再进实现**(用户已选「先出书面方案」,代码一行未动)

---

## 0. 背景与现状

用户做了一个**量化选股工作台**「觀瀾 · 选股 V1.0」,与仓内 `ui/chat`(对话/研报)**不同**,是闭环里**缺失的一格**:

```
研究图谱 → 对话/研报 → 经验卡 → 因子·工作流 → 【选股 ← 本 spec 补这格】 → 席位·落子
```

它**天生为本套件设计**:bundle 里的 `guanlan-bus.js`/`guanlan-nav.js` 与仓内 `ui/_shared/` 同款;已调用 `GL.take('screen')` / `GL.put({type:'decision'})` / `GL.handoff('cockpit')`,并链接 `观澜 · 落子.html` / `观澜 · 研究图谱.html`(均为现有文件名)。

**当前 100% mock**:
- 股票池 = 56 只**合成**标的(`mulberry32` 确定性随机生成行情/因子原值/新闻),非真数据。
- 选股引擎(`window.xgBuild`)**逻辑是真的**:截面 z 打分 → 复合分 `Σ w·dir·z` → 排除规则 → 行业中性限额 → TopN → 分布统计 + 预期 RankIC;但喂的是合成值。
- `llmPick`(NL→因子)= **正则关键词匹配**,非真 LLM。
- 消息面(`genNews`/`llmRead`)= **合成新闻 + 模板解读**,非真新闻/LLM。

后端侧基建**基本就位**(均已建好/验真):`/feature/build`、`/factor/report`、`/factor/compose`、`/factorlib/*`、`/factor/list`(zoo DSL 求值,经 `get_data_paths` 碰真 qlib 数据)。

## 1. 决策记录(待确认项以 ⓥ 标出)

1. **接入方式**:**新模块 + 仓内薄壳 `/screen/run`**(用户在 4 选项里倾向此为推荐;此 spec 据此展开)。
   - 守红线:UI **原样落地、逐字保留布局**;后端落 `guanlan_v2/screen/`(薄壳 + 复用引擎 primitive),**不重造引擎、不改 engine/**。
2. **截面口径**:`/screen/run` 取 universe 面板的**最新可用交易日截面**做打分排名(「今日候选清单」= 最新截面),不是时序回测。
3. ⓥ **MVP 因子范围**:先接**能用现有 DSL 编译的价量类因子**(见 §7);北向/PEAD/消息面因子因**数据/字段缺口**列 Phase 2。**待用户拍板 MVP 是否接受「先上 1–2 个真因子,其余占位」**。
4. ⓥ **mock 兜底策略**:沿用 chat 先例——连后端后**失败显形**(`ok:false`→UI 提示),不偷偷回退合成数据。**待确认**是否保留 `file://` 直开时的纯 mock 预览(建议保留,与其他模块一致)。

## 2. 范围

### In scope(本刈)
- 新模块 `ui/screen/`:页面 + 2 个 jsx(数据内核 / 主壳)原样落地,引用共享 `_shared/`。
- 接入全局导航(`guanlan-nav.js` 加第 6 个 tab「选股」)+ 共享总线(`GL` handoff/decision 已在 UI 内,验证贯通即可)。
- 仓内 `guanlan_v2/screen/` 薄路由:`POST /screen/run`(cfg → 今日截面排名篮子,返回 `xgBuild` 同形)。
- 把 `window.xgBuild` 由**纯客户端引擎**改为**调 `/screen/run`**;组件树/props **不动**。
- 文档同步 + 缓存串 `?v=` + 控制端独立验证。

### Out of scope(Phase 2)
- `llmPick` 接真 LLM 出 DSL 表达式(可复用 `/run` 或专用端点 `/screen/pick_factors`)。
- 消息面因子接真新闻(chat 已有 `/news_query`、`/xueqiu/feed`)+ 真 LLM 情绪。
- 北向资金 / 业绩超预期(PEAD)因子——依赖 DSL 当前**不暴露**的字段(见 §7),需先在引擎/数据侧补字段或迁专用因子。
- 「据此落子」后落子页(seats)消费 `GL.take('cockpit')` 的端到端执行(落子页职责,非本刈)。

## 3. 数据形状

### 入参 cfg(前端现有 state,原样上送)
```jsonc
{
  "factors": [ { "id": "fa_reversal", "w": 1.0 } ],   // id→后端解析为 expr/注册名 + dir
  "topN": 20,
  "industryNeutral": true, "indCap": 0.25,
  "liqMin": 5,                                          // 成交额下限(亿)
  "exclST": true, "exclHalt": true, "exclLimit": true, "exclNew": false,
  "universe": "csi_fast",                               // 见 workflow_api.md §6
  "newsCfg": { "windowDays": 7, "sources": ["公告","研报","快讯","热帖"] }  // Phase 2
}
```

### 返回(= `window.xgBuild` 输出超集,UI 不需重构)
```jsonc
{
  "ok": true,
  "date": "2026-06-03",                                 // 实际截面日
  "chosen":  [ { "s": {"code":"...","name":"...","ind":"...","price":0,"chg":0,"amt":0,"st":false}, "score":0, "pct":0, "excl":[], "news": null } ],
  "benched": [ { "s": {}, "benchReason": "行业满额" } ],
  "pool":    [], "scored": [],                          // UI 工具条用 .length
  "stat": { "n": 0, "indDist": [{"ind":"...","n":0,"frac":0}], "combIC": 0, "avgPct": 0, "newsDist": null }
}
```
失败:`{ "ok": false, "reason": "..." }`(HTTP 200,前端降级,沿用全仓诚实失败约定)。

## 4. 组件与文件

| 文件 | 动作 | 说明 |
|---|---|---|
| `guanlan_v2/screen/__init__.py` | 新增 | 包;导出 `build_screen_router`(工厂式,随 cards/seats/factorlib 先例) |
| `guanlan_v2/screen/api.py` | 新增 | `APIRouter`,`POST /screen/run`;引擎 primitive **函数体内延迟 import**(`get_default_loader`/`load_panel_cached`/`compile_factor`/截面预处理),诚实失败 |
| `guanlan_v2/screen/engine.py` | 新增(可选) | 截面打分→约束→行业中性→TopN→分布统计(把 UI `xgBuild` 逻辑搬到服务端,喂真面板值) |
| `guanlan_v2/server.py` | 改 | `create_app()` 在 `build_app()` 后 `app.include_router(build_screen_router())`(紧随 workflow router) |
| `ui/screen/观澜 · 选股.html` | 新增 | 入口;引 React UMD + Babel + `../_shared/{tokens.css,guanlan-bus.js,guanlan-nav.js}` + `screen-data.jsx` + `screen-app.jsx`;注入 `window.GUANLAN_BACKEND` |
| `ui/screen/screen-data.jsx` | 新增 | = bundle 的「数据内核」(block5);`window.xgBuild` 改为 `async`→`fetch(API+'/screen/run')`;`UNIVERSE`/合成新闻退为 `file://` 预览兜底 |
| `ui/screen/screen-app.jsx` | 新增 | = bundle 的「主壳」(block6);`useMemo(xgBuild)` 改为带 loading 的 effect(因转异步);组件/布局**不动** |
| `ui/screen/README.md` | 新增 | 模块说明(随各模块先例) |
| `ui/_shared/guanlan-nav.js` | 改 | `MODULES` 加 `{ label:'选股', file:'../screen/观澜 · 选股.html' }`(置于「因子·工作流」与「席位·落子」之间) |
| `ui/index.html` / 各页 nav 计数 | 注意 | navTabs 5→6;验证脚本相应更新 |
| `docs/module_map.md`、`ui/README.md`、`README.md`、`ARCHITECTURE.md` | 改 | 加 screen 模块行 + `/screen/run` 端点 |

## 5. 端点契约(guanlan 自有,挂薄壳)

**`POST /screen/run`** — 见 §3 入参/返回。
- 内部:`universe` → `load_panel_cached` 取面板 → 对每个 factor 用 `compile_factor`(注册名优先,否则编译 DSL 表达式)求值 → **取最新截面** → 截面标准化(z) → 复合分 `Σ (w/Σw)·dir·z` → 排除规则(ST/停牌/涨跌停/次新/流动性,字段从面板/行情取)→ 行业中性限额(`ceil(topN·indCap)`/行业)→ TopN → 分布统计。
- `combIC`:各 factor 的 RankIC 按权重加权;factor RankIC 取 factorlib 元数据 `ic` 或 `/factor/report` 实算(MVP 用元数据,标注口径)。
- 复用与 `/feature/build` **同一条** universe→panel→eval 链(不新造数据通道)。

**`POST /screen/pick_factors`**(Phase 2)— `{ prompt }` → `{ factors:[{expr,w,dir,reason}], summary }`,LLM 受 DSL 白名单(workflow_api.md §4)约束出表达式(对齐「受约束生成」)。

## 6. 前端接线

- HTML:`screen-app.jsx` 前注入
  `window.GUANLAN_BACKEND = new URLSearchParams(location.search).get('backend') || (location.protocol.startsWith('http') ? location.origin : '');`
  脚本串 `?v=20260604`。
- `screen-data.jsx`:`window.xgBuild` 改为 `async (cfg) => { const r = await fetch(API+'/screen/run',{POST,json:cfg}); const j = await r.json(); if(!j.ok) throw ...; return j; }`;无 `API`(file://)时退回现有合成引擎(预览)。
- `screen-app.jsx`:`const result = useMemo(()=>xgBuild(cfg),[cfg])` → 改为 `useState(result)` + `useEffect` 拉取(因转异步),期间 loading 态;**渲染树、props、样式逐字不变**。
- 总线:`GL.take('screen')` / `GL.put` / `GL.handoff('cockpit')` 已在 UI 内,保留;只需确认 seats 页消费 `cockpit` handoff(集成验证项)。

## 7. 因子映射 + DSL 缺口(**诚实标注,不糊**)

UI 5 因子的表达式含**引擎 DSL 白名单外**字段。映射现状:

| UI 因子 | UI 伪表达式 | 真 DSL 可编译? | MVP 处置 |
|---|---|---|---|
| `fa_reversal` 缩量反转 | `-rank(ts_sum(ret,5))·(vol_ratio<0.7)` | ⚠️ 部分:`ret/close` 有,`vol_ratio` 无 → 近似 `volume/ts_mean(volume,20)` | **接真**(用近似量比) |
| `fa_distrib` 退潮风控 | `−(near_60d_high & vol_ratio>2 & ret_1d<2%)` | ⚠️ `near_60d_high` 需 `close/ts_max(close,60)`;`vol_ratio` 近似 | 可接真(组合表达式) |
| `fa_north` 北向动量 | `rank(ts_sum(north_hold_chg,3))` | ❌ 无 `north_hold_chg`(北向持股)字段 | **Phase 2**(补数据字段) |
| `fa_pead` PEAD 漂移 | `rank(eps_surprise)·hold(60d)` | ❌ 无 `eps_surprise`(业绩超预期)字段 | **Phase 2**(财报数据) |
| `fa_news` 消息面 LLM | `LLM(公告·研报·快讯·热帖)→情绪分` | ❌ 非 DSL,需真新闻 + LLM | **Phase 2**(`/news_query`+LLM) |

> 含义:**MVP 真正能上的是价量类(reversal、distrib 近似)**;其余因子在 MVP 里要么占位(仍合成)、要么禁用并在 UI 标注「待数据」。**这是接入的真实代价,§1.3 待用户拍板。** 不接受「占位」则需先在引擎/数据侧补北向/财报字段——工作量更大。

## 8. 数据契约合规

- 选股是 **guanlan 自有应用层**;`/screen/run` 求值经引擎 `get_data_paths`/`load_panel_cached` 碰**真 qlib 数据**,不硬编码、不复制 stocks。
- 不改 `G:/stocks`、不改 `engine/` 源、不 push、不合 main(本仓非 git 仓库,spec 仅落盘)。
- 引擎 primitive 全部**函数体内延迟 import**(对齐 factorlib/workflow 先例),引擎缺失时路由仍可构造。

## 9. 文档同步(否则文档变假)

- `ui/README.md`:结构表加 `screen/` 行;模块数 5→6。
- `ui/_shared/README.md`(若列 nav 模块)同步。
- `docs/module_map.md`:加 screen 模块 + `/screen/run`。
- `README.md` / `ARCHITECTURE.md`:模块表 + 闭环图加「选股」格;薄壳端点清单加 `/screen/run`。
- 新增 `ui/screen/README.md`。

## 10. 控制端独立验证(不自报通过)

启动 `guanlan_v2.server` → Claude_Preview 开选股页 → `preview_eval`/`preview_network`:
1. `navTabs === 6`、选股 tab 高亮;`rootKids > 0`(渲染)。
2. 观察 `POST /screen/run` 200、返回 `{ok:true, chosen:[...]}`;**候选清单非合成**(对比:合成池固定 56 只且代码集已知 → 真 universe 应不同/带真行情)。
3. 调 TopN/行业中性 → 重新 `POST /screen/run` → 清单随约束变(证明真后端在算,非本地)。
4. 「据此落子」→ `GL.put` decision 写入 → 落子页 `GL.take('cockpit')` 拿到 basket(闭环贯通)。

## 11. 开放项 / 已知代价

- **因子数据缺口**(§7):北向/PEAD/消息面 MVP 不可全真;闭环「真选股」完整度受限,Phase 2 才补齐。
- **截面口径**:「今日」= 面板最新可用日(可能滞后 1 日,非盘中实时);如需盘中需另接 `/quotes`(Phase 2)。
- `combIC` MVP 用 factorlib 元数据 IC(非每次实算),口径标注。
- `llmPick` MVP 仍正则;真 LLM 出表达式留 Phase 2(与上轮「只生成因子表达式、单独测试」一脉,可优先提级)。

---

> 备注:本仓库非 git 仓库,spec 仅落盘,未 commit。实现前需用户在 §1.3 / §1.4 两个 ⓥ 项确认。
