# 帷幄盯盘执行器 ww_seats_bind 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给帷幄装一个真正能在校场创建/绑定盯盘 agent 的执行器(`ww_seats_bind`),并修 `ww_seats_history` 裸码查询 bug,根治「帷幄宣称已盯盘但校场看不到 + 甩锅前端刷新」。

**Architecture:** 后端工具 `ww_seats_bind` 不直接改状态(后端碰不到浏览器 `window.GL`),只产出一个 `seat_bind` artifact 信封;控制台前端 `dispatchLive` 捕获它、调 `applySeatBind` 直接 `window.GL.put` 一个 `type:'strategy'` 实体(`bind:[code]` = 盯盘),localStorage `storage` 事件实时同步到校场 iframe 重渲染。纯前端落地,不新增后端策略数据模型。

**Tech Stack:** Python / FastAPI(后端工具 + seats 路由)、pytest + fastapi.testclient(后端测试)、React via Babel-standalone JSX(前端)、guanlan-bus(localStorage 跨窗口总线)。

**设计依据:** [docs/superpowers/specs/2026-06-15-weiwo-seats-bind-actuator-design.md](../specs/2026-06-15-weiwo-seats-bind-actuator-design.md)

---

## 重要约定(全计划通用)

- **本仓非 git**(环境 `Is a git repository: false`)。所以本计划用「检查点 = 跑相关测试 + 全量回归」代替 `git commit`。每个 Task 末尾的检查点务必执行。
- **跑测试**:仓根执行 `python -m pytest <路径> -v`。`tests/conftest.py` 已把在仓 `engine/` 前置进 `sys.path`(防 venv 旧分支串扰),无需手动设。
- **改后端生效**:改 `guanlan_v2/**.py` 后须重启 9999(杀监听 PID,等 ~10s 端口释放,看门狗自动拉新代码)。仅最后部署 Task 6 需要;纯 pytest 任务在子进程/导入级运行,不需重启。
- **改前端生效**:浏览器按 `?v` 缓存编译 jsx,验证前 bump `?v` 用 Edit(非 sed),再 reload。

---

## Task 1: 后端工具 `seats_bind_impl` + 注册 + 白名单

**Files:**
- Modify: `guanlan_v2/console/tools.py`(新增 `seats_bind_impl`;`register_console_tools` 的 `specs` 加一项;`CONSOLE_ALLOWED` 加名)
- Test: `tests/test_console_tools.py`(追加 4 个测试)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_tools.py` 末尾)

```python
def test_seats_bind_impl_prefixed_code():
    res = ct.seats_bind_impl(code="SZ000630", name="铜陵有色",
                             creed="盯铜价异动", template="momentum")
    assert res["ok"] is True
    art = res["artifact"]
    assert art["kind"] == "seat_bind" and art["page"] == "seats" and art["channel"] == "cockpit"
    p = art["payload"]
    assert p["code"] == "SZ000630" and p["bareCode"] == "000630"
    assert p["name"] == "铜陵有色" and p["template"] == "momentum" and p["creed"] == "盯铜价异动"
    assert "7×24" in res["content"]            # 诚实口径必须在文案里


def test_seats_bind_impl_bare_code_normalizes(monkeypatch):
    import types
    monkeypatch.setattr(ct, "_buddy_tools_mod",
                        lambda: types.SimpleNamespace(normalize_code=lambda c: "SZ000630"))
    res = ct.seats_bind_impl(code="000630", name="铜陵有色")
    assert res["ok"] is True
    assert res["artifact"]["payload"]["code"] == "SZ000630"
    assert res["artifact"]["payload"]["bareCode"] == "000630"


def test_seats_bind_impl_rejects_bad_code():
    assert ct.seats_bind_impl(code="茅台")["ok"] is False
    assert ct.seats_bind_impl(code="")["ok"] is False


def test_seats_bind_impl_unknown_template_falls_back():
    res = ct.seats_bind_impl(code="SH600519", template="weird")
    assert res["ok"] is True and res["artifact"]["payload"]["template"] == "momentum"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_console_tools.py -k seats_bind -v`
Expected: FAIL —— `AttributeError: module 'guanlan_v2.console.tools' has no attribute 'seats_bind_impl'`

- [ ] **Step 3: 实现 `seats_bind_impl`**

在 `guanlan_v2/console/tools.py` 中,紧跟 `report_run_impl`(它有同款 code 规范化先例)之后插入:

```python
def seats_bind_impl(code: str, name: str = "", creed: str = "",
                    template: str = "momentum") -> Dict[str, Any]:
    """为某只票在校场创建专属盯盘 agent(纯前端落地:后端只产 seat_bind 信封,
    控制台前端 applySeatBind 写 window.GL 策略 bind=[code] = 盯盘)。
    诚实口径:盯盘 = 校场绑定的 agent、页面开着时前端循环研判,非服务器 7×24。"""
    code = (code or "").strip().upper()
    if _re.match(r"^\d{6}$", code):          # 裸码 → 引擎规范化(同 report_run_impl)
        try:
            code = _buddy_tools_mod().normalize_code(code)
        except Exception:
            return {"ok": False, "content": f"无法规范化代码 {code}(需 SH/SZ/BJ 前缀)", "artifact": None}
    if not _CODE_RE.match(code):
        return {"ok": False, "content": f"代码格式非法: {code}(应为 SH600519 形)", "artifact": None}
    if template not in {"momentum", "reversal", "event"}:
        template = "momentum"
    bare = _re.sub(r"\D", "", code)
    nm = (name or "").strip() or bare
    return {"ok": True,
            "content": (f"已为 {nm}({bare}) 在校场创建盯盘 agent「{nm} · 盯盘」({template} 模板)。"
                        f"它会显现在校场,页面开着时由前端盯盘循环持续研判提醒;"
                        f"这不是服务器 7×24 常驻盯盘。需要立刻看一次研判,我再跑 ww_seats_decide。"),
            "artifact": artifact("seat_bind", page="seats", channel="cockpit",
                                 payload={"code": code, "bareCode": bare, "name": nm,
                                          "creed": (creed or "").strip(), "template": template})}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_console_tools.py -k seats_bind -v`
Expected: PASS(4 passed)

- [ ] **Step 5: 注册工具进 specs**

在 `register_console_tools()` 的 `specs = [...]` 列表里,**紧跟 `ww_seats_decide` 那一项之后**插入(spec 元组格式 = `(name, desc, schema, run, cost, confirm)`):

```python
        ("ww_seats_bind",
         "为某只票在校场创建专属盯盘 agent(绑定策略 bind=该票=盯盘,显现在校场,页面开着时前端循环持续研判)。"
         "用户说『加入盯盘/配个 agent 盯住 X/专门盯这只票』时用。需用户确认。"
         "诚实:盯盘=校场绑定 agent+页面开着时前端研判,非服务器 7×24。",
         {"type": "object", "properties": {
             "code": {"type": "string", "description": "股票代码,如 SZ000630 或 000630"},
             "name": {"type": "string"},
             "creed": {"type": "string", "description": "盯盘信条/重点关注条件(喂给 agent 的依据)"},
             "template": {"type": "string", "enum": ["momentum", "reversal", "event"], "default": "momentum"}},
          "required": ["code"]},
         _wrap(seats_bind_impl), "instant", True),
```

- [ ] **Step 6: 加进 CONSOLE_ALLOWED 白名单**

在 `CONSOLE_ALLOWED = {...}` 集合里,`"ww_seats_decide",` 同行/相邻处加 `"ww_seats_bind",`:

```python
    "ww_seats_decide", "ww_seats_bind", "ww_cards_query", "ww_reports_query",
```

- [ ] **Step 7: 检查点 —— 跑整组 console 工具测试**

Run: `python -m pytest tests/test_console_tools.py -v`
Expected: 除 `test_engine_profile_excludes_ww_but_console_whitelist_resolves` 外全 PASS。该测试会因计数变化而 FAIL(注册数 14→15、白名单 21→22)—— 这是预期的,Task 2 修它。

---

## Task 2: 修计数守卫测试(注册数 +1)

新增了一个 `ww_` 工具,`test_engine_profile_excludes_ww_but_console_whitelist_resolves` 里硬编的计数必须同步。

**Files:**
- Modify: `tests/test_console_tools.py:361,367,368`

- [ ] **Step 1: 跑测试看当前失败**

Run: `python -m pytest tests/test_console_tools.py -k engine_profile -v`
Expected: FAIL —— assert `len(out["registered_ww"]) == 14`(实际 15)。

- [ ] **Step 2: 改三处断言**

把第 361 行:
```python
    assert len(out["registered_ww"]) == 14                    # ww_ 真注册进 TOOL_REGISTRY(有效性守卫)
```
改为:
```python
    assert len(out["registered_ww"]) == 15                    # ww_ 真注册进 TOOL_REGISTRY(有效性守卫)
```

把第 367-368 行:
```python
    assert out["console_n"] == 21 and out["console_missing"] == []
    assert out["explicit_n"] == 21 and out["explicit_ww_n"] == 14
```
改为:
```python
    assert out["console_n"] == 22 and out["console_missing"] == []
    assert out["explicit_n"] == 22 and out["explicit_ww_n"] == 15
```

- [ ] **Step 3: 检查点 —— 整文件全绿**

Run: `python -m pytest tests/test_console_tools.py -v`
Expected: 全部 PASS。

---

## Task 3: 修 `/seats/decisions` 裸码过滤 bug(数字核同口径)

落盘 code 是 `SZ000630`,查 `000630` 永远 miss。改为「数字核」匹配(strip 非数字),与 `/seats/runs` 已有口径([seats/api.py:659](../../../guanlan_v2/seats/api.py#L659))一致。

**Files:**
- Modify: `guanlan_v2/seats/api.py:598`
- Test: `tests/test_seats_runs.py`(追加 1 个测试)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_seats_runs.py` 末尾)

```python
def test_decisions_filter_code_numeric_core(tmp_path, monkeypatch):
    """裸码 ↔ 带前缀同口径(数字核):落盘 SZ000630,000630 / SZ000630 都命中;
    无 code = 全部(向后兼容);非等价数字核不误命中。"""
    log = tmp_path / "seats_decisions.jsonl"
    monkeypatch.setattr(seats_api, "_DEC_LOG", log)
    seats_api._persist_decision("decide", {"code": "SZ000630", "direction": "观望"})
    seats_api._persist_decision("decide", {"code": "SH600519", "direction": "买入"})
    client = _client()

    assert client.get("/seats/decisions", params={"code": "000630"}).json()["total"] == 1
    assert client.get("/seats/decisions", params={"code": "SZ000630"}).json()["total"] == 1
    assert client.get("/seats/decisions", params={"code": "630"}).json()["total"] == 0
    assert client.get("/seats/decisions").json()["total"] == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_seats_runs.py -k numeric_core -v`
Expected: FAIL —— `code=000630` 返回 `total == 0`(旧逻辑 `"000630".upper() != "SZ000630".upper()`),断言 `== 1` 失败。

- [ ] **Step 3: 改过滤逻辑**

`guanlan_v2/seats/api.py` 第 598 行:
```python
                    if code and str(r.get("code", "")).upper() != code.upper():
                        continue
```
改为:
```python
                    if code and re.sub(r"\D", "", str(r.get("code", ""))) != re.sub(r"\D", "", code):
                        continue
```
(`re` 已在文件顶部 `import re`,第 22 行。)

- [ ] **Step 4: 跑测试确认通过 + 不回归 run 过滤**

Run: `python -m pytest tests/test_seats_runs.py -v`
Expected: 全 PASS(新测试 + 原 `test_decisions_filter_run_id` 等都绿;run_id/exclude_runs 过滤逻辑未动)。

---

## Task 4: 帷幄系统提示词加 `ww_seats_bind` + 纪律

让帷幄知道这个工具存在、何时用、以及诚实口径。

**Files:**
- Modify: `guanlan_v2/console/api.py:31,40`(`_SYSTEM_PROMPT`)
- Test: `tests/test_console_api.py`(追加 1 个测试)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_api.py` 末尾)

```python
def test_system_prompt_mentions_seats_bind():
    from guanlan_v2.console.api import _SYSTEM_PROMPT
    assert "ww_seats_bind" in _SYSTEM_PROMPT
    assert "7×24" in _SYSTEM_PROMPT          # 诚实口径钉死
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_console_api.py -k seats_bind -v`
Expected: FAIL —— `assert "ww_seats_bind" in _SYSTEM_PROMPT`。

- [ ] **Step 3: 改提示词**

`guanlan_v2/console/api.py` 第 31 行(`另有:...` 那一长行),在 `哨兵研判历史 ww_seats_history(...)` 之后、`消息面 ww_news_search` 之前插入一句 `创建盯盘 agent ww_seats_bind(为某票在校场建专属盯盘 agent,需确认)、`。即该行形如:
```python
31	另有:深度研报 ww_report_run(后台5-8分钟,需确认)、调界面 ww_show_page(用户说『调出/打开/看看XX界面』就用它)、沉淀经验卡 ww_cards_save(需确认)、长期记忆 ww_memory_write/ww_memory_read、哨兵研判历史 ww_seats_history(查落子哨兵的研判/条件单记录,全局跨会话)、创建盯盘 agent ww_seats_bind(为某票在校场建专属盯盘 agent,需确认)、消息面 ww_news_search(个股/大盘实时新闻+情绪)。
```

再把第 40 行(纪律第 7 条 + 结尾 `"""`)扩为加一条纪律第 8 条。原:
```python
40	7. 用户问个股/大盘"最近消息面/新闻情绪/有什么新闻"→ 调 ww_news_search(实时东财快讯+情绪,带引用,无则诚实标注)。"""
```
改为:
```python
7. 用户问个股/大盘"最近消息面/新闻情绪/有什么新闻"→ 调 ww_news_search(实时东财快讯+情绪,带引用,无则诚实标注)。
8. 用户说"加入盯盘/配个 agent 盯住 X/专门盯这只票"→ 调 ww_seats_bind 真建校场盯盘 agent(不是只 ww_seats_decide;后者只产一条一次性研判记录、不创建盯盘 agent)。诚实口径:盯盘=校场绑定的 agent、页面开着时前端循环研判,非服务器 7×24,绝不宣称"已 24/7 持续跟踪";需要首次读数再补调 ww_seats_decide。"""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_console_api.py -k seats_bind -v`
Expected: PASS。

- [ ] **Step 5: 检查点 —— console 后端两文件全绿**

Run: `python -m pytest tests/test_console_api.py tests/test_console_tools.py -v`
Expected: 全 PASS。

---

## Task 5: 前端 `applySeatBind` + `dispatchLive` 接线 + ?v bump

控制台收到 `seat_bind` artifact → 在共享 `window.GL` 落地策略 → 校场实时显现盯盘 agent。

**Files:**
- Modify: `ui/console/console-app.jsx`(模块级加 `SEATBIND_TPL` + `applySeatBind`;`dispatchLive` 加分支)
- Modify: `ui/console/观澜 · 帷幄.html:50`(bump `console-app.jsx` 的 `?v`)

- [ ] **Step 1: 加模块级常量 + 落地函数**

在 `ui/console/console-app.jsx` 顶部注释块(第 1-4 行)之后、`function WeiwoApp() {` 之前,插入:

```javascript
// 盯盘 agent 模板默认(clock/glyph/color 与 ui/seats/luozi-data.jsx LZ_TEMPLATES 对齐,钉源防漂移)。
const SEATBIND_TPL = {
  momentum: { glyph: '动', color: 'var(--jin)', clock: { execTF: 'day', decisionFreq: 'hourly', maxHold: 30, stopLoss: 0.08, takeProfit: 0.18 } },
  reversal: { glyph: '反', color: 'var(--zhu)', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 13, stopLoss: 0.05, takeProfit: 0.11 } },
  event: { glyph: '事', color: '#3f6f8a', clock: { execTF: 'day', decisionFreq: 'daily', maxHold: 22, stopLoss: 0.09, takeProfit: 0.26 } },
};

// 帷幄 seat_bind 信封落地:在共享 window.GL 写一个 type:'strategy' 实体(bind=[该票]=盯盘);
// localStorage storage 事件实时同步到校场 iframe(luozi-app GL.on(refresh))重渲染出 owning agent。
// 去重守卫:已有策略 bind 含该票则不重复建。末了把校场调出右栏给用户看。
function applySeatBind(payload, openPage) {
  try {
    const p = payload || {};
    const code = String(p.bareCode || p.code || '').replace(/^(SH|SZ|BJ)/i, '');
    const GL = window.GL;
    if (code && GL && GL.all && GL.put) {
      const already = GL.all('strategy').some(function (s) {
        return Array.isArray(s.bind) && s.bind.some(function (c) {
          return String(c).replace(/^(SH|SZ|BJ)/i, '') === code;
        });
      });
      if (!already) {
        const tpl = ['momentum', 'reversal', 'event'].indexOf(p.template) >= 0 ? p.template : 'momentum';
        const t = SEATBIND_TPL[tpl];
        GL.put({
          id: 'strat_' + Date.now().toString(36) + Math.floor(Math.random() * 1e4).toString(36),
          type: 'strategy', name: (p.name || code) + ' · 盯盘',
          template: tpl, bind: [code], creed: p.creed || '', refs: [],
          clock: t.clock, w: 0, pa: false, glyph: t.glyph, color: t.color,
        });
      }
    }
  } catch (e) { /* 落地失败不崩,仍调出校场让用户手动核 */ }
  if (openPage) openPage('seats');
}
```

- [ ] **Step 2: 在 `dispatchLive` 里捕获 seat_bind**

`dispatchLive`(原第 33-39 行)在 `report_md` 分支之后、函数结束 `};` 之前加一段。改后形如:

```javascript
  const dispatchLive = (a) => {
    dispatch(a);
    if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'report_md') {
      const p = a.ev.artifact.payload;
      setTimeout(() => openReport({ path: p.path, code: p.code, name: p.name || p.code }), 0);
    }
    if (a.type === 'ev' && a.ev.type === 'tool_result' && a.ev.artifact && a.ev.artifact.kind === 'seat_bind') {
      applySeatBind(a.ev.artifact.payload || {}, openPage);
    }
  };
```
(`openPage` 是同组件下面第 41 行的 const;`dispatchLive` 只在事件到达时调用它,运行时已初始化,无 TDZ 问题。)

- [ ] **Step 3: bump `?v` 让浏览器重编译**

`ui/console/观澜 · 帷幄.html` 第 50 行:
```html
<script type="text/babel" data-presets="env,react" src="console-app.jsx?v=20260613n"></script>
```
用 Edit 改为:
```html
<script type="text/babel" data-presets="env,react" src="console-app.jsx?v=20260615a"></script>
```

- [ ] **Step 4: 前端真机验证见 Task 6**(前端无 pytest;在 Task 6 部署后随真机一起验,避免重复重启)

---

## Task 6: 部署 + 全量回归 + 真机端到端验证

**Files:** 无新增改动;部署并验证 Task 1-5。

- [ ] **Step 1: 全量后端回归**

Run: `python -m pytest tests/ -q`
Expected: 全绿(基线 ~373 + 本次新增 6 个测试;0 失败)。若有失败,定位是否本次改动引入,修到全绿再继续。

- [ ] **Step 2: 重启 9999 拉新后端**

杀掉 9999 监听进程(按 PID),等 ~10s 端口释放,看门狗自动拉起新代码。确认服务起来:
Run: `python -c "import urllib.request,json;print(json.load(urllib.request.urlopen('http://127.0.0.1:9999/seats/decisions?limit=1')).get('ok'))"`
Expected: 打印 `True`。

- [ ] **Step 2.5(诚实校验): 确认 `ww_seats_history` 裸码 bug 已修**(对照原会话症状)

Run: `python -c "import urllib.request,json;u='http://127.0.0.1:9999/seats/decisions?code=000630&limit=5';print(json.load(urllib.request.urlopen(u))['total'])"`
Expected: ≥1(原 bug 下恒 0;铜陵有色已有 `SZ000630` 落盘记录,裸码现在能命中)。

- [ ] **Step 3: 加载控制台、确认前端编译无错**

用浏览器/preview MCP 打开 `http://127.0.0.1:9999/ui/console/观澜 · 帷幄.html`(已 bump `?v`,强制重编译)。检查控制台 0 报错(`console-app.jsx` 解析通过)。

- [ ] **Step 4: 总线同步预检(确定性)**

在控制台页 eval(证明 applySeatBind 依赖的跨窗口落地+同步链路通)。用一个一次性票 `600519`:
```javascript
window.GL.put({ id:'strat_test_tly', type:'strategy', name:'贵州茅台 · 盯盘',
  template:'momentum', bind:['600519'], creed:'测试', refs:[],
  clock:{execTF:'day',decisionFreq:'hourly',maxHold:30,stopLoss:0.08,takeProfit:0.18},
  w:0, pa:false, glyph:'动', color:'var(--jin)' });
```
然后导航到落子/校场(或 `ww_show_page seats`),确认校场出现「贵州茅台 · 盯盘」owning agent,600519 显示为盯盘(非自选)。
**清理**:`window.GL.remove('strat_test_tly')`,确认校场该 agent 消失。

- [ ] **Step 5: 真机端到端(真路径)**

在帷幄新会话发:`帮我配个 agent 专门盯住 600519 贵州茅台`。
预期:帷幄调 `ww_seats_bind` → 弹确认框 → 批准(确认按钮,或 eval `window.WW.confirm(<turn_id>, 'y')`)→ 工具返回含「非服务器 7×24」的诚实文案 → 校场右栏自动调出,显现「贵州茅台 · 盯盘」owning agent。
eval 核验绑定真落地:
```javascript
window.GL.all('strategy').filter(s => (s.bind||[]).some(c => String(c).replace(/^(SH|SZ|BJ)/i,'')==='600519')).map(s => ({name:s.name, bind:s.bind, template:s.template}))
```
Expected: 返回 1 条,`name` 以「· 盯盘」结尾、`bind` 含 600519。确认控制台 0 报错;确认帷幄文案**没有**「24/7 持续跟踪」之类越界宣称。

- [ ] **Step 6: 清理测试产物**

- eval 删测试策略:找到上面那条策略的 `id`,`window.GL.remove(id)`;确认校场不再显示「贵州茅台 · 盯盘」。
- `ww_seats_bind` 不落 decision,故 `var/seats_decisions.jsonl` 无需清理。若 Step 5 你额外触发了 `ww_seats_decide`,从该 jsonl 删除对应测试行。

- [ ] **Step 7: 最终回归确认**

Run: `python -m pytest tests/ -q`
Expected: 全绿。

---

## 自检(spec 覆盖核对)

- spec §4.1 新工具 `ww_seats_bind` → Task 1 ✅(impl + 注册 + 白名单 + confirm=True)
- spec §4.2 前端 `applySeatBind`(A1-direct,GL.put + 去重 + openPage)→ Task 5 ✅
- spec §4.3 `ww_seats_history` 裸码 bug(/decisions 数字核)→ Task 3 ✅
- spec §4.4 系统提示词(工具 + 纪律 + 诚实口径)→ Task 4 ✅
- spec §6 测试(后端单测 + 真机)→ Task 1/3/4(pytest)+ Task 6(真机)✅
- spec §7 红线(非 7×24 文案 + bind 不冒充首读 + clock 钉源)→ Task 1 文案 + Task 4 纪律 + Task 5 SEATBIND_TPL 注释 ✅
- spec §8 触达文件(tools.py/console api.py/seats api.py/console-app.jsx/tests)→ 全覆盖 ✅
- 计数守卫连带影响(注册数 +1)→ Task 2 ✅(spec 未列,实现必需,已补)

**类型/命名一致性**:`seats_bind_impl`、artifact `kind='seat_bind'`、payload 键 `{code,bareCode,name,creed,template}`、前端 `applySeatBind(payload, openPage)` + `SEATBIND_TPL` —— 跨 Task 1/5 一致。模板枚举 `momentum/reversal/event`(= `LZ_TEMPLATE_IDS`)跨后端 schema、impl 校验、前端落地一致。

**无占位符**:所有步骤含完整代码与可执行命令。
