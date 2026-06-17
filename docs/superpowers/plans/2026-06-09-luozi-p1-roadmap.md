# 落子 P1 实施计划(研判历史删/导出 · decisionFreq 真节拍 · 自定义 creed)

**日期:** 2026-06-09
**性质:** 路线图 P1 三项,逐项独立可发。前端 no-build React,改前端 bump `?v`(g→h)+ Chrome MCP @9999 实测;改后端重启 9999(杀 PID 防 10048、带 Clash 7890)。**红线**:只出信号不下单、不写 G:/stocks、诚实标注、LLM 失败不落盘。
**关键现状(已核对,简化实现):**
- 研判历史抽屉 `DecisionHistory`(`luozi-panels.jsx:599`)只读,逐行可展开;落盘 `var/seats_decisions.jsonl`,读端 `GET /seats/decisions`(`api.py:139`),写助手 `_persist_decision(kind, rec)`(`api.py:126`,generic `rec.update`)。
- 定时研判节拍**写死每小时**:`OrderWatchPanel` effect(`luozi-panels.jsx:149-155`)`Date.now()-lastJudgeRef>=3600000`。
- **creed 管线已全通**:`runDecide` 已传 `creed:s.creed`(`panels.jsx:869`)、`runJudge` 已传 `extra.creed`(`panels.jsx:79`),后端 decide(`api.py:392/496`)/order(`api.py:746`)已收并用 creed。唯一缺口:`lzSeatMeta`(`panels.jsx:31`)的 creed 取自**模板** `td.creed`,GL strategy 对象无 `creed` 字段 → 自定义 creed 只需「加字段 + 一处回退」。

---

## 任务 1 — 研判历史 删除 / 导出〔S,前端+后端,需重启 9999〕

**Files:** `guanlan_v2/seats/api.py`(后端两端点)、`ui/seats/luozi-panels.jsx`(`DecisionHistory`)

### 1.1 后端 `DELETE /seats/decisions/{id}`(api.py,`_persist_decision` 之后)
- 读 `_DEC_LOG` 全部行 → 跳过 `id` 匹配的那条 → **原子重写**(写 `tmp` → `os.replace`,复用日历护栏同款防半写;`import os` 模块顶已存在或补);返回 `{ok, deleted:bool, remaining:int}`。
- 文件不存在/坏行容错:坏行原样保留(只删 id 命中行)、无文件 → `{ok:true, deleted:false}`。

### 1.2 后端 `GET /seats/decisions/export?format=json|csv`(api.py)
- 复用读取逻辑(全量,不限 limit),`format=json` → `JSONResponse(list)` 带 `Content-Disposition: attachment; filename=seats_decisions.json`;`format=csv` → 扁平化(id,ts,kind,code,name,strategy_name,direction/side,confidence,creed,rationale,model_name,asof;reasoning 截断或独列),CSV 用 `csv.writer`+`io.StringIO` 正确转义(逗号/换行/引号),`PlainTextResponse(media_type="text/csv")` 带 filename。

### 1.3 前端 `DecisionHistory`(panels.jsx:599-669)
- **逐行删**:每行右侧加「✕」(在 `isOrder ? '条件单':'研判'` 标签后),`onClick=stopPropagation`→二段确认(首点变「确认删?」红字,再点真删):`fetch(API+'/seats/decisions/'+r.id,{method:'DELETE'})` 成功后 `setRows(rs=>rs.filter(x=>x.id!==r.id))`。
- **导出**:抽屉头(`研判历史` 标题行,scope toggle 旁)加「导出 ▾」→ 两项 JSON/CSV,点击 `window.open(API+'/seats/decisions/export?format='+fmt)`(浏览器直接下载,GET 安全)。
- 空态/加载态不变。

**验证:** smoke `scripts/smoke_decisions_delete.py`(无 LLM·写 2 条合成→DELETE 一条→GET 少一条→export json/csv 解析→清理,**自清理不污染真历史**);浏览器:研判历史抽屉删一条即时消失、reload 后确实少、导出 JSON/CSV 下载成功。

---

## 任务 2 — decisionFreq 真驱动定时节拍〔S,纯前端〕

**Files:** `ui/seats/luozi-panels.jsx`(`OrderWatchPanel` 定时 effect 149-155)

- 读当前策略 clock:`const fq = (window.lzStrategyGet && window.lzStrategyGet(seat)?.clock?.decisionFreq) || 'hourly'`(seat=strategy.id;模板兜底 hourly)。
- 映射节流(clock 仅 `hourly`/`daily`,见 foundry selCell):
  - `hourly` → `Date.now()-lastJudgeRef.current >= 3600000`(沿用)。
  - `daily` → **当日仅一次**:记 `lastJudgeRef` 对应日期,`new Date(lastJudgeRef.current).toDateString() !== new Date().toDateString()` 才发起。
  - **地板防刷爆**:任何情况下两次研判间隔 `>= 600000`(10min)硬下限(即便将来加更高频)。
- `tick` 每 60s 查一次(沿用);deps 加 `seat`(切策略即按新 freq);仅盘中(`fresh`,已有)触发。
- UI:循环开关 title 文案「每小时封顶」→ 跟随 freq 动态(hourly=每小时 / daily=每日一次)。

**验证:** 浏览器把当前策略 clock 设 `daily` → 定时不再每小时发、当日已判则跳过;设 `hourly` → 恢复;终端看 `/seats/order` 调用频率随之变;0 console error。**注:不真等一小时**,用临时把 `lastJudgeRef` 回拨 + 改 freq 的方式在 console 验证节流分支命中(只读判断,不伪造研判)。

---

## 任务 3 — 自定义 creed + 落盘存 creed〔S–M,前端+后端,需重启 9999〕

**Files:** `ui/seats/luozi-data.jsx`(`strategySave`)、`ui/seats/luozi-panels.jsx`(`lzSeatMeta`)、`ui/seats/luozi-foundry.jsx`(新建/编辑表单)、`guanlan_v2/seats/api.py`(decide/order 落盘)、`luozi-panels.jsx`(`DecisionHistory` 显示)

### 3.1 strategy 加 `creed` 字段(data.jsx:137-144 `strategySave`)
- `obj` 加 `creed: (o.creed != null ? String(o.creed) : '')`(空串=用模板信条,不存死值)。其余不变。

### 3.2 `lzSeatMeta` 优先自定义 creed(panels.jsx:31)
- `creed: td.creed || ''` → `creed: (st.creed && String(st.creed).trim()) || td.creed || ''`(strategy 有非空 creed 用之,否则回退模板)。**runDecide/runJudge 自动跟随**(已传 `s.creed`/`meta.creed`),无需再改。

### 3.3 foundry 新建/编辑表单加 creed 输入(luozi-foundry.jsx,模板 creed 只读行 ~207 之后)
- 模板 creed 那行(显示 `tpl.creed`,只读)下加一个**下划线输入**`信 · 自定义信条(留空=用模板「{tpl.creed}」)`,受控绑 `editing.creed`,`onChange` 改草稿;保存时 `strategySave({..., creed: editing.creed})`(草稿已含)。沿用现有 `lab()`/下划线输入风格,**不改布局骨架**。

### 3.4 后端落盘记录加 `creed`(api.py)
- `seats_decide` 的 `_persist_decision("decide", {...})` rec 加 `"creed": creed`(creed 已在作用域,line 392)。
- `seats_order` 的 `_persist_decision("order", {...})` rec 加 `"creed": creed`(line 746 后)。

### 3.5 DecisionHistory 展开显示 creed(panels.jsx:652-660 展开块)
- 在 model 脚注前加:`{r.creed && <div className="mono" style={{fontSize:8.5,color:'var(--ink-3)',marginTop:4}}>信条:{r.creed}</div>}`(诚实回看 agent 当时用的信条)。

**老策略迁移:** 无 `creed` 字段 → `lzSeatMeta` 回退模板 creed(3.2 已处理),无需一次性迁移脚本。

**验证:** smoke `scripts/smoke_decide_creed.py`(真 deepseek·payload 带自定义 creed→断言 prompt 用之 + 落盘 rec 含 creed,自清理);浏览器:新建带自定义信条的策略→研判→研判历史展开看到「信条:<自定义>」、reload 持久(GL+localStorage);老「动量·默认」策略仍显模板信条;0 console error。

---

## 收尾(全部做完后一次性)
1. **bump `?v` 20260609g→h**(Edit replace_all,非 sed)。
2. **重启 9999**(`Get-NetTCPConnection :9999`→`Stop-Process -Force`→等端口释放→`$env:HTTP/HTTPS_PROXY=http://127.0.0.1:7890`+`PYTHONPATH=G:\guanlan-v2`+`python -m guanlan_v2.server`→轮询端口 200)。
3. **三 smoke 跑通 + Chrome MCP 全验真**(删/导出、节拍、自定义 creed)+ `py_compile` + 0 console error。
4. **memory** `luozi-live-trading-roadmap.md` 记 P1 完成 + `?v=h`。

## 我先定的默认(可推翻)
1. **删除** = 二段确认(点「✕」→「确认删?」→真删),不弹原生 confirm。
2. **导出** = JSON + CSV 两个,CSV 正确转义、reasoning 截断进单元格(完整思维链在 JSON 里)。
3. **daily 节拍** = 当日仅一次(按 `toDateString` 比对),且全局 10min 硬地板防刷爆。
4. **creed 存储** = 只存 GL strategy 对象(localStorage),不加新后端存储;creed 随每次请求落进决策记录。

## 执行顺序
任务 2(纯前端最轻)→ 任务 3(creed 管线已通,改动小)→ 任务 1(新后端端点最重)→ 收尾(一次 bump+重启+验真)。每项独立验证后再下一项。
