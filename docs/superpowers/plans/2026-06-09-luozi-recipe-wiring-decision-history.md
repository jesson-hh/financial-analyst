# 落子「接通配方 + agent 研判历史」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每个策略实例自己配的经验卡/研报/因子真正喂进 agent 研判,并把每次研判/条件单的真思维链落盘,提供「研判历史」时间线回看。

**Architecture:** 前端 `recipeForStrategy(stratId)` 解析 GL 里该策略的 `refs` → 带进 `/seats/decide`/`/seats/order` 请求;后端把配方因子写进 LLM prompt(标注「未做确定性回测」)并在成功时把结果追加进 `var/seats_decisions.jsonl`;新增 `GET /seats/decisions` 逆序读;前端「研判历史」抽屉复用思维链渲染回看。

**Tech Stack:** no-build React 18 UMD + @babel/standalone(`ui/seats/*.jsx`,全局挂 `window`)、FastAPI(`guanlan_v2/seats/api.py`,服务在 9999)、GL 档案总线(`ui/_shared/guanlan-bus.js`,localStorage)、DeepSeek(经引擎 `watch-agent` + Clash 代理 7890)。

**Spec:** `docs/superpowers/specs/2026-06-09-luozi-recipe-wiring-decision-history-design.md`

---

## 环境约定(每个任务都按此)

- **非 git 仓库** → **跳过所有 commit 步骤**(本计划不含 `git` 命令)。
- **改前端 `.jsx`:** 必须 bump `ui/seats/观澜 · 落子.html` 里所有 `?v=` 版本号(**用 Edit `replace_all`,绝不用 sed** —— 中文 HTML 会被 sed 损坏 UTF-8),再在 Chrome MCP 里 reload(浏览器按 `?v` 缓存 jsx)。当前版本 `20260608d33` → 本期统一 bump 到 `20260609a`。
- **改后端 `api.py`:** 改完必须重启 9999 才生效。重启法:
  1. `netstat -ano | findstr :9999` 取监听 PID;
  2. `taskkill /F /PID <pid>`(等端口释放,防 `WinError 10048 端口占用`);
  3. 带 Clash 代理重启:`$env:HTTP_PROXY='http://127.0.0.1:7890'; $env:HTTPS_PROXY='http://127.0.0.1:7890'`,再用启动该服务的原命令拉起(`guanlan_v2/server.py` 提供 9999;执行时按当前进程的实际启动命令重启)。
- **后端 smoke:** standalone Python 脚本(只用 stdlib `urllib`,不假设 `requests` 存在)直连 `http://127.0.0.1:9999`。
- **红线:** 配方因子只进 prompt 文本 + 落盘,**绝不进任何数值计算 / 不冒充确定性回测**;LLM 失败(`ok:False`)**不落盘**;系统只出信号不下单。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `ui/seats/luozi-data.jsx` | 数据内核(纯 JS) | **U1** 新增 `recipeForStrategy` + 导出;**U3** `seatOrder` 增第 5 参 `extra` |
| `guanlan_v2/seats/api.py` | 席位后端路由 | **U4** 新增 `_persist_decision` + `GET /seats/decisions`;**U2** `seats_decide` 接配方+落盘;**U3** `seats_order` 接信条+落盘 |
| `ui/seats/luozi-panels.jsx` | 面板组件 | **U2** `DecisionCard.runDecide` 改用配方;**U3** `OrderWatchPanel.runJudge` 传 `extra`;**U5** 抽 `ReasoningChain` + 新增 `DecisionHistory` + 入口按钮 |
| `ui/seats/观澜 · 落子.html` | 页面壳 | 每次前端改动 bump `?v` |
| `scripts/smoke_decisions_store.py` | U4 落盘读取 smoke(无 LLM) | **新建** |
| `scripts/smoke_decide_recipe.py` | U2/U3 接配方+落盘 smoke(走 LLM 快模式) | **新建** |

执行顺序:**U1 → U4 → U2 → U3 → U5**。后端三处(U4/U2/U3)在 U4 落盘骨架就位后逐个加,**各自改完都要重启 9999**(或 U2+U3 合并一次重启,见各任务)。

---

### Task 1 (U1): 前端 `recipeForStrategy` —— 解析策略自己的配方

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(在 `seedDefaultStrategy` 之后、约 154 行后插入函数;并在末尾 `Object.assign(window,{…})` 增导出)
- Modify: `ui/seats/观澜 · 落子.html`(bump `?v`)

- [ ] **Step 1: 新增 `recipeForStrategy` 函数**

在 `ui/seats/luozi-data.jsx` 第 154 行 `function seedDefaultStrategy(){…}` 之后插入:

```js
// 把策略实例自己配的 refs 解析成 {cards,research,factors};区别于 seatCard/seatResearch(查老 seat_<id> 实体)。
// 配方因子(factors)仅供喂 LLM 研判参考,不参与任何确定性计算。
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
      (a.refs || []).forEach(r2 => {                       // card 内层 research 同口径带上
        const b = window.GL.get(r2);
        if (b && b.type === 'research' && !seen.has(r2)) { seen.add(r2); research.push({ title: b.title, from: b.from || '' }); }
      });
    } else if (a.type === 'research') research.push({ title: a.title, from: a.from || '' });
    else if (a.type === 'factor') factors.push({ name: a.title || a.id, ic: (a.ic || ''), expr: (a.expr || '') });
  });
  return { cards, research, factors };
}
```

- [ ] **Step 2: 导出 `lzRecipeForStrategy`**

在 `ui/seats/luozi-data.jsx` 末尾的 `Object.assign(window, {…})` 里,`lzStrategyForCode: strategyForCode,` 同行附近增:

```js
  lzRecipeForStrategy: recipeForStrategy,
```

- [ ] **Step 3: bump `?v`(前端生效前提)**

在 `ui/seats/观澜 · 落子.html` 用 Edit `replace_all` 把 `?v=20260608d33` 全部替换为 `?v=20260609a`(6 处 script src)。**不要用 sed。**

- [ ] **Step 4: 浏览器验证(替代单测)**

Chrome MCP 打开 `http://127.0.0.1:9999/ui/seats/%E8%A7%82%E6%BE%9C%20%C2%B7%20%E8%90%BD%E5%AD%90.html`,reload,在控制台 eval:

```js
(() => { const id = window.lzStrategyList()[0].id;
  const r = window.lzRecipeForStrategy(id);
  return JSON.stringify({ id, factors: r.factors.map(f=>f.name), cards: r.cards.map(c=>c.name), research: r.research.length }); })()
```

Expected: 返回类似 `{"id":"strat_…","factors":["fa_north"],"cards":[...],"research":N}` —— `factors` 非空且含默认策略配的因子;另测 `window.lzRecipeForStrategy('不存在')` 返回 `{"cards":[],"research":[],"factors":[]}` 不抛。0 console error。

---

### Task 2 (U4): 后端落盘 helper + `GET /seats/decisions`

**Files:**
- Modify: `guanlan_v2/seats/api.py`(模块顶部补 import;router 路由区,如紧挨 `@router.get("/quote")` 之前新增 helper + GET 路由)
- Create: `scripts/smoke_decisions_store.py`

- [ ] **Step 1: 写 smoke 测试(先失败)**

新建 `scripts/smoke_decisions_store.py`:

```python
"""U4 smoke:落盘读取(无 LLM)。直连运行中的 9999。
先写一条合成记录进 var/seats_decisions.jsonl,再 GET /seats/decisions 断言读回 + 过滤。"""
import json, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # G:/guanlan-v2
LOG = ROOT / "var" / "seats_decisions.jsonl"
BASE = "http://127.0.0.1:9999"

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

# 1) 直接追加一条合成 test 记录(模拟 _persist_decision 的产物)
LOG.parent.mkdir(parents=True, exist_ok=True)
marker = "SMOKE_MARK_U4"
rec = {"id": "test_smoke_1", "ts": "2026-06-09T00:00:00", "kind": "test",
       "code": "999999.SZ", "name": marker, "direction": "观望"}
with LOG.open("a", encoding="utf-8") as f:
    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

# 2) GET 读回(kind=test 过滤)
j = get("/seats/decisions?kind=test&limit=10")
assert j.get("ok") is True, j
assert any(d.get("name") == marker for d in j.get("decisions", [])), "没读回合成记录"
# 3) 逆序:最新在前
assert j["decisions"][0]["id"] == "test_smoke_1", "非逆序/未取到最新"
# 4) code 不存在 → 空
j2 = get("/seats/decisions?code=ZZZZ")
assert j2.get("ok") is True and j2.get("decisions") == [], j2
print("U4 smoke PASS")
```

- [ ] **Step 2: 跑 smoke 确认失败**

Run: `python scripts/smoke_decisions_store.py`
Expected: FAIL —— `urllib.error.HTTPError: 404`(`/seats/decisions` 还不存在)。

- [ ] **Step 3: 补模块级 import**

在 `guanlan_v2/seats/api.py` 顶部 import 区,确认存在 `import json`、`import time`、`import random`、`from datetime import datetime`、`from pathlib import Path`。缺哪个补哪个(`json` 现为函数内局部 import,模块级再加一个不冲突)。

- [ ] **Step 4: 新增 `_persist_decision` + `GET /seats/decisions`**

在 `guanlan_v2/seats/api.py` 的 router 路由定义区(紧挨 `@router.get("/quote")` 之前)插入。`_DEC_LOG` 用 `parents[2]`(= `G:/guanlan-v2`)对齐 `var/`,与 `seats_decide` 内已有的 `parents[2]/"var"/"seats_fm_backfill.parquet"` 同根:

```python
    _DEC_LOG = Path(__file__).resolve().parents[2] / "var" / "seats_decisions.jsonl"

    def _persist_decision(kind: str, rec: dict) -> None:
        """追加一条研判/条件单记录到 JSONL(一行一条)。失败静默,绝不阻断主响应。"""
        try:
            _DEC_LOG.parent.mkdir(parents=True, exist_ok=True)
            full = {"id": f"{kind}_{int(time.time()*1000)}_{random.randint(0, 9999)}",
                    "ts": datetime.now().isoformat(timespec="seconds"), "kind": kind}
            full.update(rec)
            with _DEC_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(full, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — 落盘失败不影响研判返回
            pass

    @router.get("/decisions")
    async def seats_decisions(code: str = "", kind: str = "", limit: int = 50):
        """读 var/seats_decisions.jsonl,逆序(最新在前),可按 code/kind 过滤。
        文件不存在 / 坏行 → 空列表不崩(恒 HTTP200)。"""
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

> **作用域注:** `_persist_decision`/`_DEC_LOG` 须与 `@router.post("/decide")`、`@router.get("/order")` 同作用域(看 `api.py` 现状,这些路由定义在 `build_seats_router(...)` 函数体内,故这两者也放该函数体内、`/quote` 之前),U2/U3 才调得到。执行时读 `api.py` 确认作用域一致。

- [ ] **Step 5: 重启 9999**

按「环境约定」重启(netstat 取 PID → taskkill → 带 7890 代理重启)。

- [ ] **Step 6: 跑 smoke 确认通过**

Run: `python scripts/smoke_decisions_store.py`
Expected: `U4 smoke PASS`。

---

### Task 3 (U2): `/seats/decide` 接通配方 + 落盘

**Files:**
- Modify: `guanlan_v2/seats/api.py`(`seats_decide`,当前约 312-467 行)
- Modify: `ui/seats/luozi-panels.jsx`(`DecisionCard.runDecide`,当前 774-786 行)
- Create: `scripts/smoke_decide_recipe.py`

- [ ] **Step 1: 写 smoke 测试(先失败)**

新建 `scripts/smoke_decide_recipe.py`:

```python
"""U2 smoke:decide 接配方 + 落盘(走 LLM 快模式,十几秒内)。直连 9999。"""
import json, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "var" / "seats_decisions.jsonl"
BASE = "http://127.0.0.1:9999"

def post(path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))

n0 = len(LOG.read_text(encoding="utf-8").splitlines()) if LOG.exists() else 0
body = {"code": "600519.SH", "name": "贵州茅台", "date": "2026-06-06",
        "seat_cn": "测试席", "creed": "测试信条", "mode": "fast",
        "strategy_id": "strat_smoke", "strategy_name": "smoke策略",
        "cards": [{"name": "smoke卡", "insight": "测试洞见"}],
        "recipe_factors": [{"name": "测试因子A", "ic": "0.05", "expr": "rank(x)"}],
        "research": ["测试研报 · smoke"]}
j = post("/seats/decide", body)
assert j.get("ok") is True, j
# 1) 返回体回显 recipe_factors(证明配方进了后端)
assert any(f.get("name") == "测试因子A" for f in (j.get("recipe_factors") or [])), ("无 recipe_factors 回显", j)
# 2) 落盘 +1 行,且新行含配方因子 + kind=decide + strategy_name
n1 = len(LOG.read_text(encoding="utf-8").splitlines())
assert n1 == n0 + 1, (n0, n1)
last = json.loads(LOG.read_text(encoding="utf-8").splitlines()[-1])
assert last["kind"] == "decide", last
assert any(f.get("name") == "测试因子A" for f in (last.get("recipe_factors") or [])), last
assert last.get("strategy_name") == "smoke策略", last
print("U2 smoke PASS ·", j.get("direction"), "· model", j.get("model_name"))
```

- [ ] **Step 2: 跑 smoke 确认失败**

Run: `python scripts/smoke_decide_recipe.py`
Expected: FAIL —— `AssertionError: ('无 recipe_factors 回显', …)`(后端还没读/回显 recipe_factors,落盘也没加)。

- [ ] **Step 3: 后端 `seats_decide` 读新字段 + prompt 段 + 回显 + 落盘**

在 `guanlan_v2/seats/api.py` `seats_decide` 内:

(a) 读 payload 处(`card = payload.get("card") or {}` 一带,当前约 327-329 行)改为:

```python
        card = payload.get("card") or {}
        cards = payload.get("cards") or ([card] if card else [])
        recipe_factors = payload.get("recipe_factors") or []
        strategy_id = str(payload.get("strategy_id") or "")
        strategy_name = str(payload.get("strategy_name") or seat_cn)
        research = payload.get("research") or []
```

(b) `card_line` 构造处(当前约 397-403 行)改为汇总多卡(最多 3 张):

```python
            card_line = "无"
            if cards:
                _cl = []
                for cd in cards[:3]:
                    if not cd:
                        continue
                    extra = " ".join(filter(None, [
                        cd.get("verdict") and ("验证" + str(cd.get("verdict"))),
                        (cd.get("conf") is not None) and ("conf" + str(cd.get("conf"))),
                        cd.get("ic") and ("IC" + str(cd.get("ic")))]))
                    _cl.append(f"{cd.get('name', '')}:{cd.get('insight', '')}" + (f"({extra})" if extra else ""))
                card_line = "\n".join(_cl) or "无"
```

(c) 新增配方因子段并插进 `usr_p`(当前 `usr_p` 约 417-421 行,在「相关研报」行后插一行):

```python
            rf_line = "无"
            if recipe_factors:
                _rf = []
                for f in recipe_factors[:8]:
                    if not f or not f.get("name"):
                        continue
                    tail = []
                    if f.get("ic") not in (None, ""):
                        tail.append("IC=" + str(f.get("ic")))
                    if f.get("expr"):
                        tail.append("expr=" + str(f.get("expr")))
                    _rf.append(str(f.get("name")) + (f"({' '.join(tail)})" if tail else ""))
                rf_line = "; ".join(_rf) or "无"
            usr_p = (f"【标的】{name} {c} 截至 {asof}\n"
                     f"【量化因子·PIT≤当日收盘】{fac_line}\n"
                     f"【本席经验卡】{card_line}\n"
                     f"【相关研报/情绪】{res_line}\n"
                     f"【本席配方因子·供研判参考·未做确定性回测】{rf_line}\n"
                     f"【市况】{regime or '—'}\n" + _ask)
```

(d) 成功 `return JSONResponse({...})`(当前约 457-465 行)增 `recipe_factors` 回显,并在 return **之前**落盘:

```python
            _persist_decision("decide", {
                "code": c, "name": name, "strategy_id": strategy_id, "strategy_name": strategy_name,
                "mode": mode, "direction": j.get("direction"), "confidence": j.get("confidence"),
                "rationale": j.get("rationale"), "key_evidence": (j.get("key_evidence") or []),
                "reasoning": reasoning, "model_name": f"{client.provider}/{client.model}", "asof": asof,
                "factors_std": fac, "recipe_factors": recipe_factors,
                "card_names": [cd.get("name") for cd in cards if cd and cd.get("name")],
            })
            return JSONResponse({
                "ok": True, "code": c, "name": name, "asof": asof, "seat": seat_cn,
                "mode": mode, "model_name": f"{client.provider}/{client.model}",
                "direction": j.get("direction"), "confidence": j.get("confidence"),
                "rationale": j.get("rationale"), "key_evidence": (j.get("key_evidence") or []),
                "reasoning": reasoning,
                "factors": fac, "model": mdl, "recipe_factors": recipe_factors,
            })
```

> 诚实边界:`recipe_factors` 只进 `rf_line`(prompt 文本)与落盘,**不参与任何数值计算**;段标题写死「供研判参考·未做确定性回测」。LLM 失败走下方 `except` 分支,不会执行到落盘行 → **失败不落盘**。

- [ ] **Step 4: 前端 `DecisionCard.runDecide` 改用配方**

在 `ui/seats/luozi-panels.jsx` 把 `runDecide`(当前 774-787 行)整体替换为:

```js
            const runDecide = () => {
              if (!window.lzSeatDecide || deciding) return;
              setDeciding(true); setDecide(null);
              const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(dec.seat) : { cards: [], research: [], factors: [] };
              const regimeNow = (mode === 'live' && market && market.regime) ? market.regime : (ev && ev.regime);
              window.lzSeatDecide({
                code: symbol.meta.code, name: symbol.meta.name, date: dec.date,
                seat_cn: s.cn, creed: s.creed, mode: agentMode,
                strategy_id: dec.seat, strategy_name: s.cn,
                card: rcp.cards[0] ? { name: rcp.cards[0].name, insight: rcp.cards[0].insight, verdict: rcp.cards[0].verdict, conf: rcp.cards[0].conf, ic: rcp.cards[0].ic } : null,
                cards: rcp.cards,
                recipe_factors: rcp.factors,
                research: rcp.research.map(r => r.title + (r.from ? ' · ' + r.from : '')),
                regime: regimeNow,
              }).then(d => { setDecide(d); setDeciding(false); });
            };
```

- [ ] **Step 5: 重启 9999(后端改了)+ bump `?v`(前端改了)**

重启 9999(同 Task 2 Step 5);`?v` 本期已是 `20260609a`(Task 1 已 bump),若本任务先于 Task 1 跑则同样把 `观澜 · 落子.html` 的 `?v` bump 到 `20260609a`。

- [ ] **Step 6: 跑 smoke 确认通过**

Run: `python scripts/smoke_decide_recipe.py`
Expected: `U2 smoke PASS · <方向> · model deepseek/deepseek-chat`。

- [ ] **Step 7: 浏览器核验配方真进了请求体**

Chrome MCP:reload 页面 → 进任一标的的研判卡(复盘或实盘)→ 点「▶ 真·agent 研判」→ 用 `read_network_requests` 抓 `/seats/decide` 请求,确认 request body 含 `recipe_factors` 且为该策略所配因子。0 console error。

---

### Task 4 (U3): `/seats/order` 接通信条 + 卡洞见 + 落盘

**Files:**
- Modify: `guanlan_v2/seats/api.py`(`seats_order`,当前约 622 行起)
- Modify: `ui/seats/luozi-data.jsx`(`seatOrder`,781-797 行)
- Modify: `ui/seats/luozi-panels.jsx`(`OrderWatchPanel.runJudge`,当前 69-88 行)

- [ ] **Step 1: 后端 `seats_order` 签名增参 + 用传入 creed + note + 落盘**

在 `guanlan_v2/seats/api.py` `seats_order`:

(a) 签名(当前 622-626 行)增 4 个可选 query:

```python
    @router.get("/order")
    async def seats_order(code: str, seat: str = "momentum", tf: str = "day",
                          hold_entry: Optional[float] = Query(None),
                          hold_since: Optional[str] = Query(None),
                          hold_days: Optional[int] = Query(None),
                          creed: Optional[str] = Query(None),
                          note: Optional[str] = Query(None),
                          strategy_id: Optional[str] = Query(None),
                          strategy_name: Optional[str] = Query(None)):
```

(b) creed 覆盖(当前 `seat = seat if seat in _CREEDS else "momentum"` / `seat_cn, creed = _CREEDS[seat]` 约 638-639 行)改为:

```python
        seat = seat if seat in _CREEDS else "momentum"
        seat_cn, creed_default = _CREEDS[seat]
        creed = (creed or "").strip() or creed_default
```

(c) `sys_p` 拼好之后(当前 `sys_p = (…)` 约 706-709 行那块之后),若有 `note` 追加一句:

```python
            if note and str(note).strip():
                sys_p += f" 本席经验参考:{str(note).strip()[:80]}。"
```

(d) 成功构造 `order` dict 之后、`return JSONResponse(...)` 之前落盘(在 triggers 清洗、`order` 已就绪处):

```python
            _persist_decision("order", {
                "code": c, "name": name, "strategy_id": str(strategy_id or ""),
                "strategy_name": str(strategy_name or seat_cn), "tf": tf,
                "side": order.get("side"), "triggers": order.get("triggers") or [],
                "stop": order.get("stop"), "take": order.get("take"),
                "note": order.get("note"), "validity": order.get("validity"),
                "model_name": f"{client.provider}/{client.model}", "asof": asof,
            })
```

> 字段从已构造好的 `order` dict 取(执行时按 `seats_order` 实际变量名对齐 —— 读 `api.py` 该函数确认 `order`/`name`/`asof`/`client` 变量名)。LLM 失败路径不会执行到这里 → **失败不落盘**。

- [ ] **Step 2: 前端 `seatOrder` 增第 5 参 `extra`**

在 `ui/seats/luozi-data.jsx` 把 `seatOrder`(781-797 行)整体替换为:

```js
async function seatOrder(code, seat, tf, hold, extra) {
  const API = (window.GUANLAN_BACKEND || '');
  if (!API || !code) return null;
  try {
    let url = API + '/seats/order?code=' + encodeURIComponent(code) + '&seat=' + encodeURIComponent(seat || 'momentum') + '&tf=' + encodeURIComponent(tf || 'day');
    if (hold && hold.entry != null) {
      url += '&hold_entry=' + encodeURIComponent(hold.entry);
      if (hold.since) url += '&hold_since=' + encodeURIComponent(hold.since);
      if (hold.days != null) url += '&hold_days=' + encodeURIComponent(hold.days);
    }
    if (extra) {
      if (extra.creed) url += '&creed=' + encodeURIComponent(extra.creed);
      if (extra.note) url += '&note=' + encodeURIComponent(extra.note);
      if (extra.strategy_id) url += '&strategy_id=' + encodeURIComponent(extra.strategy_id);
      if (extra.strategy_name) url += '&strategy_name=' + encodeURIComponent(extra.strategy_name);
    }
    const r = await fetch(url);
    if (!r.ok) return null;
    const j = await r.json();
    if (!j || !j.ok) return null;
    return j;
  } catch (e) { return null; }
}
```

- [ ] **Step 3: 前端 `runJudge` 传 `extra`(creed 来自模板,经 `lzSeatMeta`)**

在 `ui/seats/luozi-panels.jsx` `runJudge`(当前 74-77 行)把那 3 行替换为:

```js
    const strat = (strategies || []).find(s => s.id === seat) || (strategies || [])[0] || null;
    const tmpl = strat ? strat.template : ((window.LZ_TEMPLATE_IDS && window.LZ_TEMPLATE_IDS.indexOf(seat) >= 0) ? seat : 'momentum');
    const meta = window.lzSeatMeta ? window.lzSeatMeta(seat) : null;      // GL strategy 无 creed 字段,从模板解析
    const rcp = window.lzRecipeForStrategy ? window.lzRecipeForStrategy(seat) : { cards: [] };
    const extra = { creed: (meta && meta.creed) || '', note: (rcp.cards[0] && rcp.cards[0].insight) || '',
      strategy_id: seat, strategy_name: strat ? (strat.name || seat) : (meta ? meta.cn : seat) };
    window.lzSeatOrder(code, tmpl, otf, hold, extra).then(o => {
```

(保留原 `.then(o => {…})` 函数体不变 —— 只是给 `lzSeatOrder` 加了第 5 参,并在调用前多了 `meta`/`rcp`/`extra` 三个 const。)

- [ ] **Step 4: 重启 9999 + 确认 `?v`**

重启 9999;确认 `观澜 · 落子.html` 为 `?v=20260609a`。

- [ ] **Step 5: 浏览器 + 命令行验证(替代单测)**

Chrome MCP:进**实盘**模式某标的 → 条件单区点「立单」→ 等 agent 出条件单。然后命令行核验落盘:

```python
python -c "import json,urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9999/seats/decisions?kind=order&limit=3',timeout=20).read().decode('utf-8')[:600])"
```

Expected: 含一条 `"kind":"order"` 记录,`strategy_name`/`side`/`triggers` 非空。浏览器 0 console error。

---

### Task 5 (U5): 前端「研判历史」抽屉 + 思维链复用

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(抽 `ReasoningChain`;新增 `DecisionHistory`;`DecisionCard` 加 `histOpen` 状态 + 入口按钮 + 挂载抽屉;末尾导出)
- Modify: `ui/seats/观澜 · 落子.html`(确认 `?v=20260609a`)

- [ ] **Step 1: 抽出 `ReasoningChain` 复用组件**

在 `ui/seats/luozi-panels.jsx` `DecisionCard` 定义**之前**(模块作用域)新增:

```js
// 思维链复用块:DecisionCard 与 DecisionHistory 共用。无 reasoning → null。
function ReasoningChain({ reasoning }) {
  if (!reasoning) return null;
  return (
    <details style={{ marginTop: 6 }}>
      <summary className="mono" style={{ fontSize: 9.5, color: 'var(--yin)', cursor: 'pointer' }}>思维链 ▾(reasoner 真逐步推理 · 点开)</summary>
      <div className="mono" style={{ fontSize: 9.5, color: 'var(--ink-2)', marginTop: 4, lineHeight: 1.65, whiteSpace: 'pre-wrap', maxHeight: 220, overflowY: 'auto', background: 'rgba(28,24,20,0.035)', borderRadius: 6, padding: '7px 9px' }}>{reasoning}</div>
    </details>
  );
}
```

把 `DecisionCard` 里原 `decide.reasoning && (<details>…</details>)`(当前 831-836 行)整段替换为:

```js
                  <ReasoningChain reasoning={decide.reasoning} />
```

- [ ] **Step 2: 新增 `DecisionHistory` 抽屉组件**

在 `ReasoningChain` 之后、`DecisionCard` 之前新增:

```js
// 研判历史抽屉:只读后端 /seats/decisions,逆时序时间线 + 点开思维链。不依赖前端 GL。
function DecisionHistory({ code, open, onClose }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [scope, setScope] = useState('code');   // 'code' 本票 / 'all' 全部
  const [openId, setOpenId] = useState(null);
  useEffect(() => {
    if (!open) return;
    const API = (window.GUANLAN_BACKEND || '');
    if (!API) { setRows([]); return; }
    setLoading(true);
    const q = '/seats/decisions?limit=50' + (scope === 'code' && code ? '&code=' + encodeURIComponent(code) : '');
    fetch(API + q).then(r => r.json()).then(j => { setRows((j && j.decisions) || []); setLoading(false); })
      .catch(() => { setRows([]); setLoading(false); });
  }, [open, code, scope]);
  if (!open) return null;
  const dirColor = (d) => d && /买/.test(d) ? 'var(--zhu)' : (d && /卖/.test(d) ? 'var(--dai)' : 'var(--ink-2)');
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(20,16,12,0.32)', display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={(e) => e.stopPropagation()} className="paper-bg" style={{ width: 460, maxWidth: '92vw', height: '100%', background: 'var(--paper)', borderLeft: '1px solid var(--line)', boxShadow: '-8px 0 30px rgba(20,16,12,0.18)', display: 'flex', flexDirection: 'column', animation: 'fadeIn 0.18s ease' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 16px', borderBottom: '1px solid var(--line)' }}>
          <span className="serif" style={{ fontSize: 15, fontWeight: 600, color: 'var(--ink-1)' }}>研判历史</span>
          <div style={{ display: 'flex', gap: 4, marginLeft: 6 }}>
            {[['code', '本票'], ['all', '全部']].map(([k, label]) => (
              <span key={k} onClick={() => setScope(k)} className="mono" style={{ fontSize: 9.5, padding: '2px 9px', borderRadius: 5, cursor: 'pointer', border: '1px solid ' + (scope === k ? 'var(--zhu-soft)' : 'var(--line)'), background: scope === k ? 'rgba(168,57,45,0.07)' : 'transparent', color: scope === k ? 'var(--yin)' : 'var(--ink-3)' }}>{label}</span>
            ))}
          </div>
          <span onClick={onClose} style={{ marginLeft: 'auto', fontSize: 16, cursor: 'pointer', color: 'var(--ink-3)' }}>✕</span>
        </div>
        <div style={{ flex: 1, overflowY: 'auto', padding: '10px 14px' }}>
          {loading && <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>读取中…</div>}
          {!loading && rows.length === 0 && <div className="mono" style={{ fontSize: 10.5, color: 'var(--ink-3)', padding: '20px 0', textAlign: 'center' }}>暂无研判记录</div>}
          {!loading && rows.map((r) => {
            const isOpen = openId === r.id;
            const isOrder = r.kind === 'order';
            const dir = isOrder ? r.side : r.direction;
            return (
              <div key={r.id} style={{ borderBottom: '1px solid var(--line-soft)', padding: '9px 2px' }}>
                <div onClick={() => setOpenId(isOpen ? null : r.id)} className="hover-row" style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', width: 96, flexShrink: 0 }}>{String(r.ts || '').replace('T', ' ').slice(5, 16)}</span>
                  <span className="serif" style={{ fontSize: 12.5, fontWeight: 600, color: dirColor(dir) }}>{dir || '—'}</span>
                  <span className="mono" style={{ fontSize: 9, color: 'var(--ink-2)' }}>{r.name || r.code}</span>
                  <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{r.strategy_name || ''}</span>
                  {r.confidence != null && <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>置信{r.confidence}</span>}
                  <span className="mono" style={{ marginLeft: 'auto', fontSize: 8, padding: '1px 5px', borderRadius: 4, border: '1px solid var(--line)', color: 'var(--ink-3)' }}>{isOrder ? '条件单' : '研判'}</span>
                </div>
                {isOpen && (
                  <div style={{ marginTop: 6, paddingLeft: 4 }}>
                    {r.rationale && <div className="serif" style={{ fontSize: 11, color: 'var(--ink-1)', lineHeight: 1.6 }}>{r.rationale}</div>}
                    {isOrder && (r.triggers || []).length > 0 && <div className="mono" style={{ fontSize: 9, color: 'var(--ink-2)', marginTop: 4 }}>触发:{(r.triggers || []).map(t => t.kind + t.op + t.value).join(r.logic === 'OR' ? ' 或 ' : ' 且 ')}{r.stop != null ? ' · 止损' + r.stop : ''}{r.take != null ? ' · 止盈' + r.take : ''}</div>}
                    {(r.key_evidence || []).length > 0 && <div style={{ marginTop: 4, display: 'flex', flexWrap: 'wrap', gap: 4 }}>{r.key_evidence.map((k, i) => <span key={i} className="mono" style={{ fontSize: 8.5, color: 'var(--ink-2)', border: '1px solid var(--line)', borderRadius: 4, padding: '1px 5px' }}>{k}</span>)}</div>}
                    {(r.recipe_factors || []).length > 0 && <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 4 }}>配方因子(供参考·未回测):{(r.recipe_factors || []).map(f => f.name).join('、')}</div>}
                    <ReasoningChain reasoning={r.reasoning} />
                    <div className="mono" style={{ fontSize: 8, color: 'var(--ink-3)', marginTop: 4 }}>{r.model_name || ''}{r.asof ? ' · as-of ' + r.asof : ''}</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `DecisionCard` 加状态 + 入口按钮 + 挂载抽屉**

在 `DecisionCard` 组件内,与其它 `useState` 同处新增:

```js
  const [histOpen, setHistOpen] = useState(false);
```

把「席位 · agent 研判(真)」`<Field label="席位 · agent 研判(真)">`(当前 772 行)下一行起插入入口按钮(放在 `{(() => {` 之前):

```js
        <Field label="席位 · agent 研判(真)">
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: -22, marginBottom: 4 }}>
            <span onClick={(e) => { e.stopPropagation(); setHistOpen(true); }} className="mono" title="查看历次 agent 研判 / 条件单的真思维链"
              style={{ fontSize: 9, padding: '2px 8px', borderRadius: 5, cursor: 'pointer', border: '1px solid var(--line)', color: 'var(--ink-3)' }}>研判历史 ⏱</span>
          </div>
```

在 `DecisionCard` 返回 JSX 的最外层结束 `</div>` 之前挂抽屉(当前约 844-845 行,`</div></div>` 结束处之前):

```js
      <DecisionHistory code={symbol && symbol.meta && symbol.meta.code} open={histOpen} onClose={() => setHistOpen(false)} />
```

- [ ] **Step 4: 导出(便于浏览器核验)**

`ui/seats/luozi-panels.jsx` 末尾 `Object.assign(window, {…})`(当前 849 行)增 `DecisionHistory, ReasoningChain`。

- [ ] **Step 5: bump `?v` + reload**

确认 `?v=20260609a`(Task 1 已 bump;若独立跑则用 Edit `replace_all` bump)。Chrome MCP reload。

- [ ] **Step 6: 浏览器验证(替代单测)**

Chrome MCP:
1. 进某标的研判卡 → 点「▶ 真·agent 研判」(深模式)→ 等真链返回;
2. 点「研判历史 ⏱」→ 抽屉滑出 → 顶部「本票」应见刚那条(方向/策略名/置信)→ 点开该行 → 看到 `rationale` + 「思维链 ▾」点开是真逐步推理;
3. 切「全部」→ 列出跨标的记录;点 ✕ 关闭抽屉。
4. `read_console_messages` 确认 0 console error。

---

## Self-Review

**1. Spec coverage:**
- U1 recipeForStrategy → Task 1 ✓
- U4 _persist_decision + GET /seats/decisions → Task 2 ✓
- U2 decide 接配方 + 落盘 → Task 3 ✓
- U3 order 接信条/note + 落盘 → Task 4 ✓
- U5 ReasoningChain 抽出 + DecisionHistory + 入口 → Task 5 ✓
- 红线(因子只喂 LLM 不冒充回测 / 失败不落盘 / 只出信号)→ Task 3 Step 3 诚实边界注 + 各 `_persist_decision` 只在成功路径调 ✓
- 验证(后端 smoke + 浏览器)→ 每任务含 ✓

**2. Placeholder scan:** 无 TBD/TODO;代码块均为完整可粘贴内容。后端 `seats_order` 的 `order` dict 字段名标注「执行时读现有 api.py 对齐」属合理(该函数体未在本计划全量贴出,执行者需读 `api.py` 确认变量名 `order`/`name`/`asof`/`client`)。

**3. Type consistency:**
- 前端导出 `lzRecipeForStrategy`(Task 1)↔ Task 3/4 调用名一致 ✓
- 后端 decide payload 字段 `cards`/`recipe_factors`/`strategy_id`/`strategy_name`(Task 3 前端发)↔ 后端读(Task 3 Step 3a)一致 ✓
- 落盘字段 `kind/code/strategy_name/recipe_factors/reasoning/triggers/side`(Task 2 schema)↔ smoke 断言(Task 2/3)↔ `DecisionHistory` 读取字段(Task 5)一致 ✓
- `seatOrder(code, seat, tf, hold, extra)` 第 5 参(Task 4 Step 2 定义)↔ `runJudge` 调用(Task 4 Step 3)一致 ✓
- creed 来源:GL strategy 无 creed → `lzSeatMeta(seat).creed`(Task 4 Step 3,与 spec 修正一致)✓
- `?v` 统一 `20260609a`,各任务一致 ✓

无 git commit 步骤(非 git 仓库,符合约定)。
