# 帷幄盯盘执行器 `ww_seats_bind` 设计

日期:2026-06-15
作者:观澜团队
状态:已批准设计,待写实现计划

## 1. 背景与问题

用户在「铜陵有色」会话(`var/console/sessions/cs_df6ee158d694/events.jsonl`)里让帷幄
「把这只票加入盯盘 / 配个 agent 专门盯住它」。帷幄反复调 `ww_seats_decide`、宣称
「✅ 已加入盯盘 / 哨兵已持续跟踪」,但校场里始终看不到任何盯铜陵有色的 agent。用户连续
四次反驳后,帷幄无工具可补救,只能甩锅「前端展示层的问题,请刷新」——这是它**猜错的根因**。

### 真实根因(已核实)

- **盯盘的定义**([luozi-data.jsx:600](../../../ui/seats/luozi-data.jsx#L600)
  `monitoredCodes`/`poolIsMonitored`):盯盘 = 存在一个校场策略,其 `bind` 数组非空且含该
  code。策略实体由 [`strategySave()`](../../../ui/seats/luozi-data.jsx#L237) 写进
  `window.GL`(= 浏览器 localStorage,纯前端)。
- **帷幄的工具面**([console/tools.py `CONSOLE_ALLOWED`](../../../guanlan_v2/console/tools.py#L639)):
  跟落子相关的只有 `ww_seats_decide`(一次性 LLM 研判,落一条决策记录)、`ww_seats_history`
  (只读)、`ww_show_page`(只显示)。**没有任何工具能创建/绑定校场策略。**
- **后端无策略 CRUD 端点**(`guanlan_v2/seats/api.py` 只有 `/decide`、`/runs`、`/ledger`、
  `/decisions`、`/quote`…)。帷幄是后端 agent,物理上也碰不到浏览器的 `window.GL`。

→ 这是**执行器缺口**(capability gap):帷幄能研究、能展示,但不能改写校场/盯盘状态。

### 次要 bug

`ww_seats_history(code="000630")` 永远返回「暂无记录」(会话事件 47/87),但不带 code 时
铜陵有色记录明明在(事件 67/89)。根因 [seats/api.py:598](../../../guanlan_v2/seats/api.py#L598):
过滤 `str(r["code"]).upper() != code.upper()`,而落盘 code 是规范化的 `SZ000630`,裸码
`000630` 永远匹配不上。

## 2. 目标与非目标

**目标**:让帷幄能真正创建一个盯盘 agent,该 agent 立刻显现在校场(owning agent),并被现有
前端盯盘循环(`fleetWatch`,页面开着时)持续研判。顺带修 `ww_seats_history` 裸码 bug。

**非目标(本期不做)**:服务器端 7×24 盯盘守护(后端 scheduler/定时任务/推送)。这是另一个
独立项目(见 `luozi-run-rework.md` 挂账「二期 backend scheduler」)。本期诚实口径:盯盘 =
**校场绑定的 agent,页面开着时前端循环研判**,不是服务器常驻进程。

## 3. 关键架构发现:guanlan-bus 是现成执行器通道

[guanlan-bus.js](../../../ui/_shared/guanlan-bus.js):

- `window.GL` 由 localStorage 支撑(`LS_KEY = 'guanlan:store:v1'`)。所有策略
  (`type:'strategy'`)都存这里。
- **跨窗口实时同步**:控制台页与校场是同源的两个文档(校场是控制台右栏的 iframe)。一个文档
  写 localStorage,另一个文档收到 `storage` 事件(bus.js:20)→ 重载 state → `emit()` →
  订阅者刷新。校场 app 通过 [`GL.on(refresh)`](../../../ui/seats/luozi-app.jsx#L78) 订阅。
- 因此:**控制台侧 `window.GL.put({type:'strategy', bind:[code], ...})` → storage 事件 →
  校场 `GL.on` → 实时重渲染出盯盘**(校场开没开都行,无需 reload)。
- 控制台已有用 `window.GL` 跨界触达校场的先例:[console-app.jsx:52](../../../ui/console/console-app.jsx#L52)
  `window.GL.handoff('cockpit', …)`。
- `strategy` 类型已在 bus 的后端影子同步白名单 `SYNC_TYPES` 内(bus.js:116),控制台写的策略
  会 fire-and-forget 推到 `/archive`,刷新缓存不丢。

## 4. 方案(A1-direct,已选定)

后端工具只产出 artifact 信封;控制台前端消费信封、直接 `window.GL.put` 落地策略。

(备选 A2 = 控制台写 bind-request、由校场调真 `strategySave` 消费 —— 零模板重复但要握手。
已否决:活动件更多、live 同步不如 A1 稳。A1 唯一代价是 clock 默认那几行来源重复,以注释钉源。)

### 4.1 新后端工具 `ww_seats_bind`

文件:[guanlan_v2/console/tools.py](../../../guanlan_v2/console/tools.py)
(`seats_bind_impl` + `register_console_tools` specs + `CONSOLE_ALLOWED`)。

- **入参**:`code`(必填)、`name`(默认空)、`creed`(盯盘信条/条件,默认空)、
  `template`(枚举 `momentum`/`reversal`/`event`,默认 `momentum`)。
- **行为**:不直接改任何状态(后端碰不到 `window.GL`)。
  - 规范化 code:裸 6 位 → 经引擎 `normalize_code` 加前缀(同 `report_run_impl`);校验
    `_CODE_RE`。
  - 返回:
    ```python
    {"ok": True,
     "content": "<诚实文案,见下>",
     "artifact": artifact("seat_bind", page="seats", channel="cockpit",
                          payload={"code": "SZ000630", "bareCode": "000630",
                                   "name": name, "creed": creed, "template": template})}
    ```
- **confirm_required = True**(创建持久 agent = 变更操作,与 `ww_seats_decide`/
  `ww_report_run`/`ww_cards_save` 一致)。
- **诚实 content 文案**(根治「幻觉式成功 + 甩锅前端」):
  > 已为 {name}({bareCode}) 在校场创建盯盘 agent「{name} · 盯盘」({template} 模板)。
  > 它会显现在校场,**页面开着时**由前端盯盘循环持续研判提醒;这不是服务器 7×24 常驻盯盘。
  > 需要立刻看一次研判,我再跑 ww_seats_decide。

### 4.2 前端消费 `applySeatBind`

文件:[ui/console/console-app.jsx](../../../ui/console/console-app.jsx)
(`dispatchLive`,镜像第 35 行的 `report_md` 分支)。

- 在 `dispatchLive` 加分支:`a.ev.type === 'tool_result' && a.ev.artifact &&
  a.ev.artifact.kind === 'seat_bind'` → `applySeatBind(a.ev.artifact.payload)`。
- `applySeatBind(payload)`:
  1. **去重守卫**:若 `window.GL` 不存在 → 直接 `openPage('seats')` 返回(降级,不崩)。
     若现有某策略 `bind` 已含该裸码(复用 `monitoredCodes` 同口径:遍历
     `window.GL.all('strategy')` 判 `bind` 去前缀含 `bareCode`)→ 不重复建,仅
     `openPage('seats')`。
  2. 否则 `window.GL.put({...})`,对象形如(与 `strategySave` 产物逐字段对齐):
     ```js
     {
       id: 'strat_' + Date.now().toString(36) + Math.floor(Math.random()*1e4).toString(36),
       type: 'strategy',
       name: (payload.name || payload.bareCode) + ' · 盯盘',
       template: ['momentum','reversal','event'].includes(payload.template) ? payload.template : 'momentum',
       bind: [payload.bareCode],
       creed: payload.creed || '',
       refs: [],
       // clock 默认对齐 LZ_TEMPLATES[template].clock —— 注释钉源防漂移
       clock: MOMENTUM_CLOCK,   // {execTF:'day',decisionFreq:'hourly',maxHold:30,stopLoss:0.08,takeProfit:0.18}
       w: 0, pa: false,
       glyph: TPL_GLYPH[template], color: TPL_COLOR[template],
     }
     ```
     - clock/glyph/color 按 template 取(momentum=动/var(--jin),reversal=反/var(--zhu),
       event=事/#3f6f8a);三模板的 clock 字面值从 [luozi-data.jsx:202-207](../../../ui/seats/luozi-data.jsx#L201)
       拷入控制台常量,注释指明源。
  3. `openPage('seats')`(把校场调出右栏,用户立刻看到新建的盯盘 agent)。
- **为何不复用 `strategySave`**:它在校场 iframe 的 window(luozi-data.jsx),控制台 window
  没有它。A1 接受拷贝三模板的 clock 默认(静态、稳定),以注释钉源换取活动件最少 + live 同步。

### 4.3 修 `ww_seats_history` 裸码过滤 bug

文件:[guanlan_v2/seats/api.py:598](../../../guanlan_v2/seats/api.py#L598)。

- 改过滤:对 `r["code"]` 与查询 `code` 两边都 strip `^(SH|SZ|BJ)` 前缀后比裸 6 位(与前端
  `_monCode` 同口径)。`code` 为空时不过滤(旧行为不变)。
- 这样 `ww_seats_history(code="000630")` 与 `code="SZ000630"` 都能命中落盘的 `SZ000630`。

### 4.4 帷幄系统提示词

文件:[guanlan_v2/console/api.py:26](../../../guanlan_v2/console/api.py#L26) `_SYSTEM_PROMPT`。

- 工具清单加 `ww_seats_bind`(创建校场盯盘 agent,需确认)。
- 纪律加一条:用户说「加入盯盘 / 配个 agent 盯住 X / 专门盯这只票」→ 调 `ww_seats_bind`
  **真建校场 agent**,不是只 `ww_seats_decide`。诚实口径:盯盘 = 校场绑定的 agent、页面开着
  时前端循环研判,**非服务器 7×24**;不要宣称「已 24/7 持续跟踪」。
- 引导链:先 `ww_seats_bind` 建 agent,需要首读再 `ww_seats_decide`。

## 5. 数据流(端到端)

```
用户:「帮我配个 agent 盯住铜陵有色」
  → 帷幄 LLM 调 ww_seats_bind(code=000630, name=铜陵有色, creed=…, template=momentum)
  → confirm 弹窗(需确认)→ y
  → seats_bind_impl 规范化 → 返回 artifact{kind:'seat_bind', payload:{code:SZ000630, bareCode:000630, …}}
  → SSE tool_result(ev.artifact) 到控制台
  → dispatchLive 命中 'seat_bind' → applySeatBind
  → window.GL.put({type:'strategy', bind:['000630'], name:'铜陵有色 · 盯盘', …})
  → localStorage 改 → storage 事件 → 校场 iframe GL.on(refresh) → 重渲染
  → 校场出现「铜陵有色 · 盯盘」owning agent;monitoredCodes() 含 000630 = 盯盘成立
  → openPage('seats') 把校场调出右栏
```

## 6. 测试

- **后端单测**(`tests/`):
  - `seats_bind_impl`:裸码规范化为 SZ 前缀;artifact 信封 kind/page/channel/payload 形状;
    缺 code → ok:False;非法 code → ok:False。
  - `register_console_tools` 含 `ww_seats_bind` 且 `confirm_required=True`;`CONSOLE_ALLOWED`
    含之。
  - `/decisions` 过滤:落盘 `SZ000630`,`code="000630"` 与 `code="SZ000630"` 都能查到;
    `code=""` 返回全部(旧行为)。
- **前端真机**(preview,bump `?v`):帷幄会话触发 bind → 校场出现「铜陵有色 · 盯盘」
  owning agent、`lzMonitoredCodes()` 含 000630、控制台 0 报错;去重守卫:重复 bind 同票不
  产生第二个策略。

## 7. 红线 / 诚实

- 工具 content + 系统提示词都钉死「非 7×24 服务器盯盘」,根治本次幻觉式成功 + 甩锅前端。
- `ww_seats_bind` 只创建绑定,不冒充已做首次研判(首读走 `ww_seats_decide`)。
- clock 默认拷贝以注释钉源(luozi-data.jsx LZ_TEMPLATES),防与校场漂移。
- 纯前端落地策略,不新增后端策略数据模型;后端影子同步走现有 bus → `/archive` 通道。

## 8. 触达文件清单

| 文件 | 改动 |
|---|---|
| `guanlan_v2/console/tools.py` | 新增 `seats_bind_impl`;`register_console_tools` 加 spec;`CONSOLE_ALLOWED` 加名 |
| `guanlan_v2/console/api.py` | `_SYSTEM_PROMPT` 加 ww_seats_bind + 纪律 + 诚实口径 |
| `guanlan_v2/seats/api.py` | `/decisions` code 过滤改裸码同口径 |
| `ui/console/console-app.jsx` | `dispatchLive` 加 'seat_bind' 分支 + `applySeatBind` + 模板常量;bump `?v` |
| `tests/...` | 后端单测(bind impl + decisions 过滤) |

## 9. 运维提示

- 改后端(tools.py/api.py)须重启 9999(杀监听 PID 等端口释放,看门狗自动拉新代码)。
- 前端验证前 bump `?v` 再 reload(浏览器按 `?v` 缓存编译 jsx)。
- 本仓非 git(环境 `Is a git repository: false`),spec 不入 git,仅落盘。
