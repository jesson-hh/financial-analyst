# 落子「接通配方 + agent 研判历史」设计

**日期:** 2026-06-09
**状态:** 设计已批准,待写实施计划
**前置审计:** 见同会话 —— 复盘合议/落子/演武全确定性(0 LLM);唯 `/seats/decide`、`/seats/order` 真调 DeepSeek;策略在校场配的经验卡/因子(`strat.refs`)**未喂给 agent**(decide 当前用 `seat_<id>` 老席位查询,对 `strat_xxx` 返回 null,且后端只另算 5 个标准因子);研判结果**不落盘**。

---

## 目标

一句话:**把每个策略实例自己配的经验卡/研报/因子真正喂进 agent 研判,并把每次研判/条件单的真思维链落盘,提供「研判历史」时间线回看。**

## 红线(不可破)

1. **配方因子只喂 LLM 当参考,绝不冒充确定性回测。** prompt 里因子段必须标注「配方因子·供研判参考,未做确定性回测」。落子/演武/合议仍是 template+clock 确定性算法,本设计**不动**它们。
2. **诚实落盘:** LLM 失败 `ok:False` 时**不写**记录(不存假数据)。落盘 reasoning 是模型原文,不编造。
3. **只出信号不下单。** 研判/条件单只产出方向与触发规格,系统不代下单。
4. **不动 G:/stocks。** 落盘只写 `G:/guanlan-v2/var/`。
5. **改后端必重启 9999**(按 PID 杀监听者等端口释放防 10048),改前端 bump `?v` + Chrome MCP @9999 实测。

---

## 架构(5 个单元)

```
前端 ui/seats/                                  后端 guanlan_v2/seats/api.py
──────────────────────────────────────────    ─────────────────────────────────────
[U1] luozi-data.jsx                             [U2] /seats/decide
  recipeForStrategy(stratId)                       payload 增 cards[]/recipe_factors[]
    读 strategyGet().refs → 解析 GL                  prompt 增「本席配方因子」段
    → {cards[], research[], factors[]}              成功 → _persist_decision('decide', rec)
        │ 带进 payload                            [U3] /seats/order
        ▼                                            query 增 creed/note(可选)
[U2/U3 调用方] luozi-panels.jsx                      成功 → _persist_decision('order', rec)
  DecisionCard.runDecide 用 U1 组 payload         [U4] _persist_decision(kind, rec)
  OrderWatchPanel.runJudge 传 creed/note              追加 var/seats_decisions.jsonl
        │ 研判成功后端已落盘                          GET /seats/decisions?code=&kind=&limit=
        ▼                                              读 JSONL 逆序返回
[U5] luozi-panels.jsx
  ReasoningChain(reasoning)  ← 抽出复用
  DecisionHistory 抽屉:GET /seats/decisions
    逆时序时间线 + 点开思维链
  入口:DecisionCard「研判历史 ⏱」按钮
```

**数据流:** 点研判 → 前端 `recipeForStrategy(strat.id)` 解析该策略真配方 → 带进 `/seats/decide` → 后端把配方因子写进 prompt、真调 reasoner → 返回 `reasoning` 真链 + 后端同步 `_persist_decision` 追加一行 → 「研判历史」抽屉随时 `GET /seats/decisions` 拉回时间线,点行用 `ReasoningChain` 回看完整推理。

---

## U1 — recipeForStrategy(前端解析配方)

**文件:** `ui/seats/luozi-data.jsx`(新增函数 + 导出)

策略实例 `refs` 是 GL id 数组(卡/因子/研报混装,如默认 `['card_north','fa_north']`)。新增纯函数把它解析成三类:

```js
// 把策略实例自己配的 refs 解析成 {cards,research,factors};区别于 seatCard/seatResearch(查老 seat_<id> 实体)。
function recipeForStrategy(stratId) {
  const s = strategyGet(stratId);
  const empty = { cards: [], research: [], factors: [] };
  if (!s || !window.GL) return empty;
  const cards = [], research = [], factors = [], seen = new Set();
  (s.refs || []).forEach(rid => {
    const a = window.GL.get(rid);
    if (!a || seen.has(rid)) return; seen.add(rid);
    if (a.type === 'card') {
      cards.push({ name: a.title || a.id, insight: a.insight || a.verdict || '',
        verdict: a.verdict || null, conf: (a.conf != null ? a.conf : null), ic: (a.ic || null) });
      (a.refs || []).forEach(r2 => { const b = window.GL.get(r2);   // card 内层 research 同口径带上
        if (b && b.type === 'research' && !seen.has(r2)) { seen.add(r2); research.push({ title: b.title, from: b.from || '' }); } });
    } else if (a.type === 'research') research.push({ title: a.title, from: a.from || '' });
    else if (a.type === 'factor') factors.push({ name: a.title || a.id, ic: (a.ic || ''), expr: (a.expr || '') });
  });
  return { cards, research, factors };
}
```

导出:`lzRecipeForStrategy: recipeForStrategy`(加入 `luozi-data.jsx` 末尾 `Object.assign(window,{…})`)。

**单测点:** seed 默认策略 `refs:['card_north','fa_north']` → `recipeForStrategy('<默认id>')` 返回 `factors` 含 `fa_north`(name+expr);`cards` 含 card_north(若在 GL)。空策略 / null id → 三空数组,不抛。

---

## U2 — /seats/decide 接通配方

### 前端(`luozi-panels.jsx` DecisionCard.runDecide,当前 774-786 行)

把 `lzSeatCard/lzSeatResearch(dec.seat)` 换成 `lzRecipeForStrategy(dec.seat)`,payload 增 `cards`(数组)+ `recipe_factors`;保留 `card`(取 cards[0])向后兼容:

```js
const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(dec.seat) : { cards: [], research: [], factors: [] };
const regimeNow = (mode === 'live' && market && market.regime) ? market.regime : (ev && ev.regime);
window.lzSeatDecide({
  code: symbol.meta.code, name: symbol.meta.name, date: dec.date,
  seat_cn: s.cn, creed: s.creed, mode: agentMode,
  strategy_id: dec.seat, strategy_name: s.cn,            // 落盘用
  card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
  cards: rcp.cards,                                      // 多张配方卡
  recipe_factors: rcp.factors,                           // [{name,ic,expr}] 配方因子
  research: rcp.research.map(r => r.title + (r.from ? ' · ' + r.from : '')),
  regime: regimeNow,
}).then(d => { setDecide(d); setDeciding(false); });
```

### 后端(`guanlan_v2/seats/api.py` seats_decide,当前 312-467 行)

1. 读新字段:`cards = payload.get("cards") or ([payload.get("card")] if payload.get("card") else [])`;`recipe_factors = payload.get("recipe_factors") or []`;`strategy_id/strategy_name`。
2. `card_line` 改为汇总 `cards`(最多 3 张,`名:洞见(验证x conf y IC z)`,用 `\n` 分隔)。
3. **新增 prompt 段**`【本席配方因子·供研判参考·未做确定性回测】`:遍历 `recipe_factors` 取 `名(IC=… expr=…)`,无则「无」。`usr_p` 在「相关研报」段后插入这一行。
4. 后端**仍**另算 5 个标准 PIT 因子(`fac_line` 不动,标的「量化因子·PIT」),与配方因子并列两段 —— 标准因子是真算的硬证据,配方因子是用户配的参考视角,prompt 明确区分。
5. 返回体增 `recipe_factors`(回显),供前端核验「配方确实进了 prompt」。
6. 成功路径 `return JSONResponse(...)` 前调 `_persist_decision("decide", {...})`(见 U4)。

**诚实边界:** 配方因子段标题写死「供研判参考·未做确定性回测」;`recipe_factors` 只进 prompt 文本与落盘,不参与任何数值计算。

---

## U3 — /seats/order 接通信条 + 卡洞见

### 前端(`luozi-panels.jsx` runJudge,当前 69-88 行的 77 行)

`lzSeatOrder` 增传 `extra`(策略信条 + 配方首卡一句洞见 + 落盘标识):

```js
const strat = (strategies || []).find(s => s.id === seat) || (strategies || [])[0] || null;
const tmpl = strat ? strat.template : ((window.LZ_TEMPLATE_IDS && window.LZ_TEMPLATE_IDS.indexOf(seat) >= 0) ? seat : 'momentum');
const meta = window.lzSeatMeta ? window.lzSeatMeta(seat) : null;        // creed 来自模板,GL strategy 对象无 creed 字段
const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(seat) : { cards: [] };
const extra = { creed: (meta && meta.creed) || '', note: (rcp.cards[0] && rcp.cards[0].insight) || '',
  strategy_id: seat, strategy_name: strat ? (strat.name || seat) : (meta ? meta.cn : seat) };
window.lzSeatOrder(code, tmpl, otf, hold, extra).then(o => { … });
```

> 注:GL `strategy` 实体(`strategySave`)字段为 `{id,name,template,refs,clock,bind,color,glyph}`,**无 `creed`** —— creed 由 `lzSeatMeta(id)` 从 `LZ_TEMPLATES[template].creed` 解析(与 DecisionCard 取 `s.creed` 同源)。

`seatOrder(code, seat, tf, hold, extra)`(`luozi-data.jsx` 781-797)增第 5 参,把 `extra.creed/note/strategy_id/strategy_name` 拼进 query(`encodeURIComponent`,空值不附)。

### 后端(`seats_order` 当前 622-… 行)

签名增 `creed: Optional[str]=Query(None)`、`note: Optional[str]=Query(None)`、`strategy_id: Optional[str]=Query(None)`、`strategy_name: Optional[str]=Query(None)`。`seat_cn, creed_default = _CREEDS[seat]`;**`creed = (传入 creed) or creed_default`**。`sys_p` 末尾若 `note` 非空加一句「本席经验:{note}」。成功路径(triggers 校验后构造 `order` dict 处)调 `_persist_decision("order", {...})`。

---

## U4 — 落盘 + 读取(后端)

**文件:** `guanlan_v2/seats/api.py`(模块级 helper + 一个 GET 路由)

```python
_DEC_LOG = Path(__file__).resolve().parents[2] / "var" / "seats_decisions.jsonl"

def _persist_decision(kind: str, rec: dict) -> None:
    """追加一条研判/条件单记录到 JSONL(一行一条)。失败静默(不阻断主响应)。"""
    try:
        _DEC_LOG.parent.mkdir(parents=True, exist_ok=True)
        full = {"id": f"{kind}_{int(time.time()*1000)}_{random.randint(0,9999)}",
                "ts": datetime.now().isoformat(timespec="seconds"), "kind": kind}
        full.update(rec)
        with _DEC_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(full, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 落盘失败绝不阻断研判响应
        pass
```

**落盘记录字段(decide):** `id, ts, kind:'decide', code, name, strategy_id, strategy_name, mode, direction, confidence, rationale, key_evidence[], reasoning, model_name, asof, factors_std{}, recipe_factors[], card_names[]`。
**落盘记录字段(order):** `id, ts, kind:'order', code, name, strategy_id, strategy_name, tf, side, triggers[], stop, take, note, validity, model_name, asof`。

样例 decide 行(合成值):

```json
{"id":"decide_1749453972000_3271","ts":"2026-06-09T09:46:12","kind":"decide","code":"300750.SZ","name":"宁德时代","strategy_id":"strat_lz3a","strategy_name":"动量·默认","mode":"deep","direction":"买入","confidence":62,"rationale":"…","key_evidence":["动量60=0.18","量比1.87"],"reasoning":"…真链…","model_name":"deepseek/deepseek-reasoner","asof":"2026-06-09","factors_std":{"rev_20":-0.03,"mom_60":0.18},"recipe_factors":[{"name":"测试因子","ic":"0.05","expr":"rank(x)"}],"card_names":["北向资金"]}
```

**读取路由:**

```python
@router.get("/decisions")
async def seats_decisions(code: str = "", kind: str = "", limit: int = 50):
    """读 var/seats_decisions.jsonl,逆序(最新在前),可按 code/kind 过滤。文件不存在/坏行 → 空列表不崩。"""
    out = []
    try:
        if _DEC_LOG.exists():
            lines = _DEC_LOG.read_text(encoding="utf-8").splitlines()
            cap = max(1, min(int(limit or 50), 300))
            for ln in reversed(lines):
                try:
                    r = json.loads(ln)
                except Exception:  # noqa: BLE001 — 坏行跳过
                    continue
                if code and str(r.get("code", "")).upper() != code.upper():
                    continue
                if kind and r.get("kind") != kind:
                    continue
                out.append(r)
                if len(out) >= cap:
                    break
    except Exception:  # noqa: BLE001
        pass
    return JSONResponse({"ok": True, "decisions": out, "total": len(out)})
```

需在 api.py 顶部确保模块级 `import time, json, random`、`from datetime import datetime`、`from pathlib import Path` 可用(按现状最小补缺失的 import;`json` 现为函数内局部 import,模块级补一个不冲突)。

---

## U5 — 研判历史抽屉(前端)

**文件:** `ui/seats/luozi-panels.jsx`(抽出 `ReasoningChain` + 新增 `DecisionHistory` + DecisionCard 加入口)

1. **抽出 `ReasoningChain({ reasoning })`:** 把 DecisionCard 现有 `<details>思维链` 块(当前 831-836 行)抽成复用组件;DecisionCard 与 DecisionHistory 都用它。无 reasoning → 渲染 `null`。
2. **`DecisionHistory({ code, open, onClose })`:** 抽屉(右侧滑出浮层,沿用 tokens 宣纸/月夜)。`useEffect([open,code])` 时 `fetch(API+'/seats/decisions?code='+code+'&limit=50')`;状态 `rows/loading`。顶栏切「本票 / 全部」(全部时不传 code)。每行:`ts` · 印章色方向 · 标的 · 策略名 · 置信 · `kind` 标(研判/条件单);点开展开 `rationale` + `key_evidence` + `ReasoningChain`(decide)或 triggers 概要(order)。空态「暂无研判记录」。
3. **入口:** DecisionCard 的「席位·agent 研判」Field 标题右侧加「研判历史 ⏱」小按钮 → `setHistOpen(true)`;`DecisionHistory` 渲染在 DecisionCard 根;新增 `const [histOpen,setHistOpen]=useState(false)`。

**注:** `DecisionHistory` 只读后端,不依赖前端 GL;与 `realSyms`/`strategies` 无耦合。导出在 `luozi-panels.jsx` 末尾 `Object.assign(window,{…})` 增 `DecisionHistory`(若需被 app 引用)或仅内部使用。

---

## 错误处理 / 诚实

| 场景 | 行为 |
|---|---|
| LLM 失败 / `ok:False` | 不落盘(`_persist_decision` 只在成功路径调) |
| `recipe_factors` 空 | prompt 段写「无」;不报错 |
| 策略无 refs / 查不到卡 | `recipeForStrategy` 返回空三数组;decide 退化为「只有标准因子」诚实研判 |
| JSONL 文件不存在 | `/seats/decisions` 返回空列表 |
| JSONL 坏行 | 逐行 try/except 跳过坏行 |
| 配方因子 | 永远只进 prompt 文本 + 落盘,标注未回测,**不进任何数值** |

## 测试 / 验证

**后端 smoke(standalone,直连 9999):**
1. `POST /seats/decide`(带 `recipe_factors:[{name:'测试因子',ic:'0.05',expr:'rank(x)'}]`,`mode:'fast'` 省时)→ 断言 `ok:True`、返回体 `recipe_factors` 与传入一致、`var/seats_decisions.jsonl` 新增一行且该行 `recipe_factors` 含「测试因子」、`kind=='decide'`。
2. `GET /seats/decisions?kind=decide&limit=5` → 断言 `ok:True`、`decisions[0]` 是刚写那条。
3. `GET /seats/decisions?code=ZZZZ` → `decisions:[]` 不崩。

**Chrome MCP @9999 实测:**
4. 给某策略配上一个因子(校场配方)→ 实盘/复盘点「真·agent 研判」→ 等真链返回 → 控制台核验请求体含 `recipe_factors`。
5. 点「研判历史 ⏱」→ 抽屉出现刚那条 → 点开看到真思维链。
6. 0 console error。

## 不在本期范围

- 配方因子驱动确定性回测/落子信号(违红线,需后端真算因子时序,另起)。
- decisionFreq 真驱动定时节拍(仍每小时封顶)。
- 后端自定义 creed 持久化(creed 仍前端传)。
- 历史记录的删除/编辑/导出。
- LLM 调用计费/限流。

## 实现顺序(供写计划)

U1(纯函数,可单测)→ U4(落盘+读取,后端,可 smoke)→ U2(decide 接配方+落盘)→ U3(order 接信条+落盘)→ U5(历史抽屉前端)。每单元后 bump `?v` + 实测。后端动 3 处(U2/U3/U4)合并一次重启 9999。
