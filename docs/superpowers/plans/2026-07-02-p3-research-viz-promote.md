# P3 研究回路可视化+人审转正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 研究回路历史在落子右栏可视化(ResearchLoopCard);draft 因子人审转正面(选股页待审区 + 帷幄 ww_factor_drafts/ww_factor_promote);工作流页 ?load= 深链与两个诚实性小修。

**Architecture:** 后端零新端点(数据源 /research/runs、/research/rounds、/factorlib/list、/factorlib/promote 全部已在);前端三页各自填充(babel-standalone 无模块系统,组件照抄进各页既定文件);console 加两工具走 _self_get/_self_post 老范式。

**Tech Stack:** React18 UMD + @babel/standalone(浏览器内转译 JSX,inline style + tokens.css 变量)、FastAPI console 工具、pytest。

**Spec:** `docs/superpowers/specs/2026-07-02-p3-research-viz-promote-design.md`(已获批)。

## Global Constraints

- **测试命令**:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest <file> -v`(仓根执行)。当前全量基线 **794 passed**(注意:并行会话正在 main 直接提交另一个项目,基线可能漂移——非 P3 所致的失败原样上报不掩盖)。
- **台账用 `.superpowers/sdd/progress-p3.md`**——`.superpowers/sdd/progress.md` 已被并行会话的另一个项目占用,**绝不覆写它**。
- **UI 只填充不重建(用户红线)**:零新页面、零新弹窗、现有布局逐字保留。
- **不做定时器**;后端零新端点;`research/api.py` docstring「零定时器」承诺不动。
- **babel 全局作用域铁律**:落子页各 `<script>` 共享全局词法作用域——**绝不**在新代码里写 `const { useState } = React`(useState/useEffect/useRef 来自 luozi-chart.jsx:2 的既有声明,直接裸用;useMemo/useCB 来自 luozi-app.jsx:2)。screen-app.jsx 顶部已解构 useState/useMemo/useEffect/useRef,组件内裸用。workflow.jsx 顶部已解构 useState/useRef/useCallback/useEffect。
- **改 jsx 必 Edit bump 对应 html 的 `?v=`**:落子页现值 `luozi-data.jsx?v=20260615h / luozi-panels.jsx?v=20260615h / luozi-app.jsx?v=20260615i`(观澜 · 落子.html:47-52);选股页 `screen-app.jsx?v=20260702b`(观澜 · 选股.html:43);工作流页 `workflow.jsx?v=90`(观澜 · AI 工作流.html:37)。统一 bump 至 `20260702p3`(工作流页 `?v=91`)。
- **跳转透传 embed/ws**(先例 validation.jsx:475-476):帷幄 iframe 内导航丢参会跌回无 ws 独立态。
- **诚实徽章两式**:真 LLM/正式=实线边(var(--yin)/var(--jin));非 LLM/未入库=虚线边 var(--line) 淡墨。draft 徽章绝不冒充正式。
- **rounds 数据剔 graph**(每行带完整 DAG 很重)且**新在前→前端转时间正序**;轮次序号用 `r.k+1` 不用数组下标;因子名在 `metrics.factor`(后端轮次行无顶层 factor)。
- **draft 判断用 `f.status==='draft'`**(正式因子无 status 键,别用 `!f.status` 反推);draft 数据必须另拉 `/factorlib/list`(XG_FACTORS←/screen/factors 链路永远拿不到 draft)。
- 前端 fetch 惯例:裸 fetch + try/catch 诚实降级 null/[],判 `j.ok` 不判 HTTP status;POST 手写 method/headers/body 三件套。
- **工具四处同步铁律**(加 2 工具):WW_TOOL_TABLE 42→**44** / console/api.py `_SYSTEM_PROMPT` / tests/test_console_tools.py(42→44 ×3处、67→69 ×3处、expected 集 +2:/factorlib/promote、/factorlib/list)/ tests/test_guanlan_mcp.py(46→**48** 三处,:13 注释 39→41)。glmcp/README.md :4 与 :13 两处 46→48 + :26-29 写锁点名清单补 `ww_factor_promote`。
- 打桩签名铁律:`_self_get` 桩 `lambda path, timeout=30:`、`_self_post` 桩 `lambda path, payload, timeout=120:`。
- 改后端(console/tools.py 等)要重启 server 才生效;真机 e2e 用独立端口(先探 9998 是否被占,占则用 9997),**绝不碰生产 9999**。
- 前端无测试框架:Task 2-4 靠仔细转录+自审+任务评审,真机验证集中在 Task 5(babel 语法错=整文件组件不注册页面部分白,T5 必查浏览器 console)。

## File Structure

- Modify: `guanlan_v2/console/tools.py`(+factor_drafts_impl/factor_promote_impl + 2 表条目 + 2 处文案)
- Modify: `guanlan_v2/console/api.py`(_SYSTEM_PROMPT 能力句+纪律 14 改写)
- Modify: `tests/test_console_tools.py`、`tests/test_guanlan_mcp.py`、`guanlan_v2/glmcp/README.md`
- Modify: `ui/seats/luozi-data.jsx`(+researchRuns/researchRounds+导出)
- Modify: `ui/seats/luozi-panels.jsx`(+ResearchLoopCard 组件+导出)
- Modify: `ui/seats/luozi-app.jsx`(右栏挂载一行)
- Modify: `ui/seats/观澜 · 落子.html`(bump ?v ×3)
- Modify: `ui/screen/screen-app.jsx`(+DraftFactorSection 组件+FactorLibrary 内一行挂载)
- Modify: `ui/screen/观澜 · 选股.html`(bump ?v ×1)
- Modify: `ui/factor/workflow.jsx`(?load= effect + HistoryModal 一行修 + FactorLibModal status 合并+徽章)
- Modify: `ui/factor/观澜 · AI 工作流.html`(bump ?v=91)

---

### Task 1: 帷幄两工具 ww_factor_drafts / ww_factor_promote + 四处同步

**Files:**
- Modify: `guanlan_v2/console/tools.py`
- Modify: `guanlan_v2/console/api.py`
- Modify: `tests/test_console_tools.py`、`tests/test_guanlan_mcp.py`、`guanlan_v2/glmcp/README.md`

**Interfaces:**
- Consumes: 既有 `_self_get(path, timeout=30)`/`_self_post(path, payload, timeout=120)`;后端 `GET /factorlib/list`(行含可选 `status:'draft'`)与 `POST /factorlib/promote {name}`→`{ok,name,file}|{ok:false,reason:'not_found: …'}`(幂等,恒 HTTP 200)。
- Produces: `factor_drafts_impl(limit=20) -> dict`;`factor_promote_impl(name="") -> dict`;两表条目(drafts confirm=False instant;promote confirm=True seconds)。

- [ ] **Step 1: 写失败测试**

1a. `tests/test_console_tools.py` 计数修改(先改数字让守护红灯指路):
- :613 `assert len(out["registered_ww"]) == 42` → `== 44`,行尾注释追加 ` +2 P3 draft转正面`
- :619 `assert out["console_n"] == 67 ...` → `== 69`
- :620 `assert out["explicit_n"] == 67 and out["explicit_ww_n"] == 42` → `== 69` / `== 44`
- :1084 `== 42` → `== 44`;:1086 `== 67` → `== 69`
- expected 集(:1099-1134)在 `"/research/rounds", ` 行后追加:

```python
        "/factorlib/promote",     # ww_factor_promote(P3 人审转正)
        "/factorlib/list",        # ww_factor_drafts(P3 列待审)
```

1b. `tests/test_guanlan_mcp.py`:三处 `== 46` → `== 48`(:13/:71/:100);:13 行尾注释 `# 39 ww_(42−3 excluded) + 7 alpha-zoo` → `# 41 ww_(44−3 excluded) + 7 alpha-zoo`。

1c. `tests/test_console_tools.py` 文件尾(:1664 之后)追加:

```python
# ── P3: ww_factor_drafts / ww_factor_promote ────────────────────────────────

def test_factor_drafts_impl_lists_and_empty(monkeypatch):
    rows = {"ok": True, "factors": [
        {"name": "lib_rl_ab_r0", "expr": "rank(-delta(close,5))", "status": "draft", "ic": 0.031},
        {"name": "lib_ok", "expr": "rank(close)"}]}
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: rows)
    res = ct.factor_drafts_impl()
    assert res["ok"] is True and "lib_rl_ab_r0" in res["content"] and "+0.0310" in res["content"]
    assert "lib_ok" not in res["content"]                              # 正式因子(无 status 键)不混入
    assert "ww_factor_promote" in res["content"]                       # 引流到转正工具
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30:
                        {"ok": True, "factors": [{"name": "lib_ok"}]})
    res2 = ct.factor_drafts_impl()
    assert res2["ok"] is True and "无待审 draft" in res2["content"]


def test_factor_promote_impl(monkeypatch):
    sent = {}

    def fake_post(path, payload, timeout=120):
        sent["path"] = path
        sent.update(payload)
        return {"ok": True, "name": payload["name"], "file": "x.json"}

    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.factor_promote_impl(name="lib_rl_ab_r0")
    assert sent["path"] == "/factorlib/promote" and res["ok"] is True
    assert "已转正" in res["content"] and "下次选股目录刷新" in res["content"]
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120:
                        {"ok": False, "reason": "not_found: lib_x"})
    res2 = ct.factor_promote_impl(name="lib_x")
    assert res2["ok"] is False and "not_found" in res2["content"]
    assert ct.factor_promote_impl(name="")["ok"] is False              # 缺名早退,不打后端
```

- [ ] **Step 2: 跑测确认失败**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q`
Expected: FAIL(计数 44≠42、48≠46、AttributeError factor_drafts_impl)

- [ ] **Step 3: 实现 tools.py**

3a. 在 `factorlib_save_impl`(:1267-1297)之后加两个 impl:

```python
def factor_drafts_impl(limit: int = 20) -> Dict[str, Any]:
    """列待审 draft 因子(研究回路达标产物,未上选股货架)。
    数据=GET /factorlib/list 过滤 status=='draft'(正式因子无 status 键)。"""
    try:
        r = _self_get("/factorlib/list?validate=false")
    except Exception as e:
        return {"ok": False, "content": f"因子库读取失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"因子库读取失败: {r.get('reason', '未知原因')}", "artifact": None}
    drafts = [f for f in (r.get("factors") or []) if f.get("status") == "draft"]
    if not drafts:
        return {"ok": True, "artifact": None,
                "content": "无待审 draft。研究回路(ww_research_loop)达标产物会自动出现在这里。"}
    cap = max(1, min(int(limit or 20), 100))
    lines = []
    for f in drafts[:cap]:
        ic = f.get("ic")
        ic_s = f"{float(ic):+.4f}" if isinstance(ic, (int, float)) else "—"
        lines.append(f"{f.get('name')} | IC {ic_s} | {str(f.get('expr') or '')[:60]}"
                     + (f" | {str(f.get('description') or '')[:40]}" if f.get("description") else ""))
    return {"ok": True, "artifact": None, "raw": {"drafts": drafts[:cap]},
            "content": f"待审 draft {len(drafts)} 条(转正用 ww_factor_promote,需确认):\n" + "\n".join(lines)}


def factor_promote_impl(name: str = "") -> Dict[str, Any]:
    """draft 因子人审转正(摘 status;幂等)。转正后下次选股目录热刷新上货架。"""
    nm = (name or "").strip()
    if not nm:
        return {"ok": False, "content": "缺少因子名 name(ww_factor_drafts 可列待审清单)", "artifact": None}
    try:
        r = _self_post("/factorlib/promote", {"name": nm})
    except Exception as e:
        return {"ok": False, "content": f"转正调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"转正未执行: {r.get('reason', '未知原因')}", "artifact": None, "raw": r}
    return {"ok": True, "artifact": None, "raw": r,
            "content": f"已转正「{nm}」(promote 幂等;下次选股目录刷新后上货架——选股页/ww_screen_factors 可见)。"}
```

3b. WW_TOOL_TABLE:在 `ww_research_runs` 条目(:2028-2036)之后、`ww_capabilities`(:2037)之前插入:

```python
    {"name": "ww_factor_drafts",
     "description":
         "列待审 draft 因子(P3:研究回路达标产物,未上选股货架):名/IC快照/表达式。"
         "用户问『有哪些待审因子/研究回路产出了什么』用它;转正走 ww_factor_promote(需确认)。",
     "input_schema": {"type": "object", "properties": {
         "limit": {"type": "integer", "default": 20}}},
     "impl": factor_drafts_impl, "cost": "instant", "confirm": False,
     "reachable": ["/factorlib/list"]},
    {"name": "ww_factor_promote",
     "description":
         "draft 因子人审转正(P3,上选股货架):摘 status → 下次选股目录热刷新可见。"
         "我只能提请,用户确认后才执行;幂等,not_found 诚实失败。",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "draft 因子名(lib_rl_ 开头,ww_factor_drafts 可查)"}},
      "required": ["name"]},
     "impl": factor_promote_impl, "cost": "seconds", "confirm": True,
     "reachable": ["/factorlib/promote"]},
```

3c. 文案一致性两处:
- `_research_run_line` :851 `verdict = f"达标 ✅ 已入 draft:{pr.get('name')}(待人审 POST /factorlib/promote 转正)"` → `verdict = f"达标 ✅ 已入 draft:{pr.get('name')}(待人审:ww_factor_promote 或选股页待审区转正)"`
- `ww_research_loop` 表条目 description(:2014-2015)`(不上选股货架,人审 POST /factorlib/promote 转正)` → `(不上选股货架,人审 ww_factor_promote/选股页待审区转正)`

- [ ] **Step 4: 实现 console/api.py 提示词**

4a. :39 P2 能力句之后(新起一行)插入:

```
另有(P3):列待审 draft 因子 ww_factor_drafts(只读)、draft 转正上货架 ww_factor_promote(需用户确认)。
```

4b. 纪律 14(:55,行尾带 `"""` 闭合)整行替换为(保持行尾 `"""`):

```
14. 用户说「研究一个因子/让 AI 自己炼因子/自主研究」→ ww_research_loop(需确认;单飞,已在跑会拒);复盘研究历史/成绩 → ww_research_runs。draft 因子转正(上选股货架)须经用户明确同意:先 ww_factor_drafts 列出待审 draft 给用户看,用户点头后用 ww_factor_promote(需确认)转正;绝不擅自转正、未转正前绝不宣称 draft 已可用于选股。"""
```

- [ ] **Step 5: glmcp/README.md**

:4 `(**46 个**)` → `(**48 个**)`;:13 `(46 个 guanlan 工具)` → `(48 个 guanlan 工具)`;:26-29 写锁点名清单在 `ww_factorlib_save`、 之后补 `ww_factor_promote`、。

- [ ] **Step 6: 跑测确认通过**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q`
Expected: 全绿(计数 44/69/48 三方一致)

- [ ] **Step 7: Commit**

```bash
git add guanlan_v2/console/tools.py guanlan_v2/console/api.py guanlan_v2/glmcp/README.md tests/test_console_tools.py tests/test_guanlan_mcp.py
git commit -m "feat(console): P3 T1 帷幄 draft 转正面 ww_factor_drafts/ww_factor_promote(计数 44/69/48 四处同步)"
```

---

### Task 2: 落子右栏 ResearchLoopCard(数据函数+组件+挂载+bump)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(runDecisions :1271 之后加两函数;导出块 :1604 附近加两键)
- Modify: `ui/seats/luozi-panels.jsx`(:1620 文件尾 Object.assign 之前加组件;Object.assign 补 ResearchLoopCard)
- Modify: `ui/seats/luozi-app.jsx`(右栏 :750 OrderWatchPanel 之前加一行)
- Modify: `ui/seats/观澜 · 落子.html`(三个 ?v= bump)

**Interfaces:**
- Consumes: `GET /research/runs?limit=`→`{ok,runs:[{run_id,goal,ts,status:'done'|'error'|'running'|'interrupted',n_rounds,best_k,best_metrics,promoted,workflow_saved,error}...]}`;`GET /research/rounds?run_id=&limit=`→`{ok,rounds:[{k,stage,diag,metrics{rank_ic,oos_verdict},gate{passed},failed,error,graph(重,须剔)}...]}`(新在前)。
- Produces: `window.lzResearchRuns(limit)->Promise<runs[]|null>`;`window.lzResearchRounds(runId)->Promise<rounds[]>`(已剔 graph、已转时间正序);组件 `ResearchLoopCard`(无 props)。

- [ ] **Step 1: luozi-data.jsx 加数据函数**

在 `runDecisions`(:1261-1271)之后插入:

```javascript
// ── P3 研究回路档案(落子右栏「研究回路」卡数据源)──────────────────────
// runs=合并行(status 四态后端推导);rounds 每行带完整 graph(工作流DAG,很重)——
// 拉回即剔、绝不入 state(仓例:console 工具同款处理);新在前 → 转时间正序供渲染。
async function researchRuns(limit) {
  const API = (window.GUANLAN_BACKEND || ''); if (!API) return null;
  try {
    const res = await fetch(API + '/research/runs?limit=' + (limit || 20));
    if (!res.ok) return null;
    const j = await res.json();
    return j.ok ? (j.runs || []) : null;
  } catch (e) { return null; }
}
async function researchRounds(runId) {
  const API = (window.GUANLAN_BACKEND || ''); if (!API) return [];
  try {
    const res = await fetch(API + '/research/rounds?run_id=' + encodeURIComponent(runId) + '&limit=50');
    if (!res.ok) return [];
    const j = await res.json();
    if (!j.ok) return [];
    return (j.rounds || []).map(r => { const { graph, ...rest } = r; return rest; }).slice().reverse();
  } catch (e) { return []; }
}
```

- [ ] **Step 2: luozi-data.jsx 导出**

在导出块(:1590-1619)`lzRunsList: runsList, lzRunDecisions: runDecisions,` 所在行(:1604)后加一行:

```javascript
  lzResearchRuns: researchRuns, lzResearchRounds: researchRounds,
```

- [ ] **Step 3: luozi-panels.jsx 加 ResearchLoopCard 组件**

在文件尾 `Object.assign(window, {...})`(:1621)之前插入完整组件(视觉:头行照 RunPicker;指标/样本外映射照 AILoopModal 照抄——fIC/VL/VC 常量逐字来自 workflow.jsx:2010-2013;徽章两式照 LiveDecideFlow:972 实线 / fleet:97 虚线;hooks 裸用不解构):

```javascript
// ───────── 研究回路(P3:P2 后端研究回路的落子可视化;两模式通吃,默认折叠)─────────
// 数据=window.lzResearchRuns / lzResearchRounds(graph 已在 data 层剔除)。
// 视觉:头行/列表/选中展开照 RunPicker 范式;指标行与样本外中文映射照 workflow 页
// AILoopModal 照抄(跨页无 import 机制,照抄一份是仓例——先例 toast 四页各抄)。
// 状态四态全渲染(done/error/running/interrupted);「上画布」= 跳工作流页 ?load= 深链
// 载入研究回路存的图(绝不自动运行);跳转透传 embed/ws(防帷幄 iframe 跌回独立态)。
function ResearchLoopCard() {
  const [open, setOpen] = useState(false);
  const [runs, setRuns] = useState(null);          // null=未拉/后端不可达(诚实降级),[]=空档案
  const [selId, setSelId] = useState(null);
  const [rounds, setRounds] = useState([]);
  const fIC = v => (v == null || v !== v) ? '—' : (v >= 0 ? '+' : '') + (+v).toFixed(4);
  const VL = { robust: '稳健', degraded: '衰减', overfit: '疑似过拟合', insufficient: '期数不足', na: '不适用' };
  const VC = { robust: 'rgb(74,107,92)', degraded: '#b8860b', overfit: 'var(--zhu)' };
  const SC = { done: ['✓', 'var(--dai)'], error: ['✗', 'var(--zhu)'], running: ['⟳', 'var(--jin)'], interrupted: ['⚠', 'var(--ink-3)'] };
  useEffect(() => {
    if (!open) return;
    let dead = false;
    const pull = () => { (window.lzResearchRuns ? window.lzResearchRuns(20) : Promise.resolve(null)).then(rs => { if (!dead) setRuns(rs); }); };
    pull();
    const t = setInterval(pull, 60000);            // 展开时 60s 轮询(running run 可感知进度)
    return () => { dead = true; clearInterval(t); };
  }, [open]);
  useEffect(() => {
    if (!selId) { setRounds([]); return; }
    let dead = false;
    (window.lzResearchRounds ? window.lzResearchRounds(selId) : Promise.resolve([])).then(rs => { if (!dead) setRounds(rs || []); });
    return () => { dead = true; };
  }, [selId]);
  const list = runs || [];
  const promoBadge = (pr) => {
    if (!pr) return null;
    if (pr.status === 'draft') return <span className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--jin)', color: 'var(--jin)', flexShrink: 0 }}>draft·待人审</span>;
    if (pr.status === 'skipped_multi') return <span className="mono" style={{ fontSize: 8, padding: '1px 5px', borderRadius: 5, border: '1px dashed var(--line)', color: 'var(--ink-3)', flexShrink: 0 }}>多因子未入库</span>;
    if (pr.status === 'save_failed') return <span className="mono" title={pr.reason || ''} style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--zhu)', color: 'var(--zhu)', flexShrink: 0 }}>入库失败</span>;
    return null;
  };
  const goCanvas = (wid) => {
    const qs = new URLSearchParams(location.search);
    const extra = (qs.get('embed') === '1' ? '&embed=1' : '') + (qs.get('ws') ? '&ws=' + encodeURIComponent(qs.get('ws')) : '');
    location.href = '../factor/观澜 · AI 工作流.html?load=' + encodeURIComponent(wid) + extra;
  };
  return (
    <div style={{ flexShrink: 0, borderBottom: '1px solid var(--line)', display: 'flex', flexDirection: 'column' }}>
      <div onClick={() => setOpen(o => !o)} style={{ padding: '9px 13px', borderBottom: open ? '1px solid var(--line-soft)' : 'none', flexShrink: 0, display: 'flex', alignItems: 'baseline', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
        <span className="serif" style={{ fontSize: 12.5, fontWeight: 600 }}>研究回路 ✦</span>
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{runs === null ? (open ? '读取中…' : '') : list.length + ' 次研究'}</span>
        <span style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)' }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && <div style={{ maxHeight: 300, overflowY: 'auto' }}>
        {runs === null && <div className="mono" style={{ padding: 12, fontSize: 10, color: 'var(--ink-3)' }}>读取中…(后端不可达时保持空)</div>}
        {runs !== null && list.length === 0 && <div className="mono" style={{ padding: 12, fontSize: 10, color: 'var(--ink-3)' }}>暂无研究档案 — 让帷幄「研究一个因子」(ww_research_loop)即产生第一条</div>}
        {list.map(r => {
          const sc = SC[r.status] || ['·', 'var(--ink-3)'];
          const bm = r.best_metrics || {};
          const on = selId === r.run_id;
          return (
            <div key={r.run_id} style={{ borderBottom: '1px solid var(--line-soft)' }}>
              <div className="hover-row" onClick={() => setSelId(on ? null : r.run_id)}
                   style={{ padding: '7px 13px', cursor: 'pointer', borderLeft: '2px solid ' + (on ? 'var(--zhu)' : 'transparent'), background: on ? 'rgba(168,57,45,0.07)' : 'transparent' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <span className="mono" style={{ fontSize: 10, color: sc[1], flexShrink: 0 }} title={r.status + (r.error ? ':' + r.error : '')}>{sc[0]}</span>
                  <span className="serif" style={{ fontSize: 11.5, color: 'var(--ink-1)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.goal || ''}>{r.goal || '(无目标)'}</span>
                  {promoBadge(r.promoted)}
                </div>
                <div className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)', marginTop: 3, display: 'flex', gap: 8, alignItems: 'baseline' }}>
                  <span>{String(r.ts || '').slice(0, 16)}</span>
                  <span>{r.n_rounds != null ? r.n_rounds + ' 轮' : ''}</span>
                  <span>最佳 RankIC <b style={{ color: (bm.rank_ic >= 0 ? 'rgb(74,107,92)' : 'var(--zhu)') }}>{fIC(bm.rank_ic)}</b></span>
                  <span style={{ flex: 1 }} />
                  {r.workflow_saved && r.workflow_saved.ok && <span onClick={(e) => { e.stopPropagation(); goCanvas(r.workflow_saved.id); }} className="serif" style={{ color: 'var(--yin)', border: '1px solid var(--zhu-soft)', borderRadius: 5, padding: '0 6px', cursor: 'pointer', fontSize: 9, flexShrink: 0 }} title={'载入「' + (r.workflow_saved.name || '') + '」到工作流画布(绝不自动运行)'}>上画布</span>}
                </div>
              </div>
              {on && <div style={{ borderLeft: '2px solid var(--zhu)', background: 'rgba(168,57,45,0.04)', padding: '4px 0' }}>
                {rounds.length === 0 && <div className="mono" style={{ padding: '6px 13px', fontSize: 9, color: 'var(--ink-3)' }}>读取轮次…(或该 run 无轮次记录)</div>}
                {rounds.map(rd => {
                  const m = rd.metrics || {};
                  const mark = rd.failed ? '❌' : ((rd.gate || {}).passed ? '✅' : '·');
                  return (
                    <div key={rd.k} className="mono" style={{ padding: '4px 13px', fontSize: 9, color: 'var(--ink-2)', lineHeight: 1.5 }}>
                      <div style={{ display: 'flex', gap: 7, alignItems: 'baseline' }}>
                        <span style={{ flexShrink: 0 }}>{mark} 第{rd.k + 1}轮·{rd.stage === 'propose' ? '初始' : '改进'}</span>
                        <span>RankIC <b style={{ color: (m.rank_ic >= 0 ? 'rgb(74,107,92)' : 'var(--zhu)') }}>{fIC(m.rank_ic)}</b></span>
                        {m.oos_verdict && <span>样本外 <b style={{ color: VC[m.oos_verdict] || 'var(--ink-3)' }}>{VL[m.oos_verdict] || m.oos_verdict}</b></span>}
                      </div>
                      {rd.diag && <div style={{ color: 'var(--ink-3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={rd.diag}>{rd.diag}</div>}
                      {rd.failed && <div style={{ color: 'var(--zhu)' }}>⚠ {String(rd.error || '本轮未产出结果').slice(0, 60)}</div>}
                    </div>
                  );
                })}
              </div>}
            </div>
          );
        })}
      </div>}
    </div>
  );
}
```

并把 :1621 的 `Object.assign(window, { MiniLine, ...` 里(`LiveDecideFlow,` 之后)加 `ResearchLoopCard,`。

- [ ] **Step 4: luozi-app.jsx 挂载**

右栏容器内、`<OrderWatchPanel`(:750)那行之前插入一行:

```jsx
              <ResearchLoopCard />
```

- [ ] **Step 5: bump ?v(用 Edit 工具,观澜 · 落子.html :47-52)**

- `luozi-data.jsx?v=20260615h` → `luozi-data.jsx?v=20260702p3`
- `luozi-panels.jsx?v=20260615h` → `luozi-panels.jsx?v=20260702p3`
- `luozi-app.jsx?v=20260615i` → `luozi-app.jsx?v=20260702p3`

- [ ] **Step 6: 自审**

逐项核:①无 `const { useState } = React` 新解构;②graph 在 data 层剔除、组件 state 不存 graph;③rounds 已转时间正序、序号用 k+1;④徽章两式(draft·待人审=实线 --jin;多因子未入库=虚线);⑤goCanvas 透传 embed/ws;⑥不碰 :395-400 的 mode/code 重置 effect;⑦四态 SC 映射齐。

- [ ] **Step 7: Commit**

```bash
git add "ui/seats/luozi-data.jsx" "ui/seats/luozi-panels.jsx" "ui/seats/luozi-app.jsx" "ui/seats/观澜 · 落子.html"
git commit -m "feat(luozi): P3 T2 右栏「研究回路」卡(两模式默认折叠·四态·逐轮流水·上画布深链)"
```

---

### Task 3: 选股页「待审 draft」区

**Files:**
- Modify: `ui/screen/screen-app.jsx`(FactorLibrary :732 之前加 DraftFactorSection 组件;FactorLibrary 内挂载一行)
- Modify: `ui/screen/观澜 · 选股.html`(:43 bump ?v)

**Interfaces:**
- Consumes: `GET /factorlib/list?validate=false`(行含可选 `status:'draft'`);`POST /factorlib/promote {name}`;`window.xgLoadCatalog(API)`(转正后重拉因子目录,触发后端 refresh_factor_defs)。
- Produces: 组件 `DraftFactorSection`(无 props;API 组件内直读 window.GUANLAN_BACKEND——仓内先例 RegimeChips :60/DecisionPanel :1108,不动 ConstraintRail 挂载链。**spec §3 原文说传 API prop,此处以仓内既定惯例为准,显式记录该偏差供评审裁决**)。

- [ ] **Step 1: 加 DraftFactorSection 组件**

在 `// ───────── 因子库(选股页2.0…)─────────`(:732)注释行之前插入:

```javascript
// ───────── 待审 draft(P3:研究回路达标产物;人审转正后才上选股货架)─────────
// 数据必须另拉 /factorlib/list(f.status==='draft';正式因子无 status 键)——XG_FACTORS←
// /screen/factors 链路在后端 catalog 单点滤掉 draft,永远拿不到。转正(window.confirm 人审)
// 成功即重拉目录(xgLoadCatalog → 后端 refresh_factor_defs),新因子立即上货架可勾选;
// 实测 IC 待下次 regen 顺算(即刻显「—」,诚实降级)。空态整组不渲染,零噪音。
function DraftFactorSection() {
  const API = (typeof window !== 'undefined' && window.GUANLAN_BACKEND) || '';
  const [drafts, setDrafts] = useState([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState('');
  const [bump, setBump] = useState(0);
  useEffect(() => {
    if (!API) return;
    let dead = false;
    fetch(API + '/factorlib/list?validate=false').then(r => r.json()).then(j => {
      if (!dead && j && j.ok) setDrafts((j.factors || []).filter(f => f.status === 'draft'));
    }).catch(() => {});
    return () => { dead = true; };
  }, [bump]);
  if (!API || !drafts.length) return null;
  const doPromote = async (nm) => {
    if (!window.confirm('转正上架「' + nm + '」?转正后进入选股因子目录。')) return;
    setBusy(nm);
    try {
      const r = await fetch(API + '/factorlib/promote', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: nm }) });
      const j = await r.json();
      if (j && j.ok) { try { await window.xgLoadCatalog(API); } catch (e) {} setBump(x => x + 1); }
      else window.alert('转正失败:' + ((j && j.reason) || '未知原因'));
    } catch (e) { window.alert('转正调用失败:' + e); }
    setBusy('');
  };
  return (
    <div style={{ marginTop: 8, borderTop: '1px dashed var(--line)', paddingTop: 6 }}>
      <div onClick={() => setOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: 7, cursor: 'pointer', padding: '5px 2px', userSelect: 'none' }}>
        <span className="mono" style={{ fontSize: 8, padding: '1px 6px', borderRadius: 4, border: '1px solid var(--jin)', color: 'var(--jin)', flexShrink: 0 }}>draft</span>
        <span className="serif" style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ink-1)' }}>待审 draft(研究回路)</span>
        <span className="mono" style={{ fontSize: 8.5, color: 'var(--ink-3)' }}>{drafts.length}</span>
        <span className="mono" style={{ marginLeft: 'auto', fontSize: 9, color: 'var(--ink-3)' }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && drafts.map(f => (
        <div key={f.name} className="hover-row" title={(f.description || '') + (f.expr ? '\n' + f.expr : '')}
             style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '4px 6px 4px 17px', borderRadius: 6 }}>
          <span className="mono" style={{ fontSize: 10, color: 'var(--ink-1)', flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f.name}</span>
          <span className="mono" style={{ fontSize: 9, color: f.ic == null ? 'var(--ink-3)' : (f.ic >= 0 ? 'var(--zhu)' : 'var(--dai)'), flexShrink: 0 }}>{f.ic == null ? 'IC —' : 'IC ' + (f.ic >= 0 ? '+' : '') + (+f.ic).toFixed(3)}</span>
          <span onClick={() => busy !== f.name && doPromote(f.name)} className="serif"
                style={{ flexShrink: 0, fontSize: 10, color: 'var(--paper)', background: busy === f.name ? 'var(--ink-3)' : 'var(--yin)', borderRadius: 5, padding: '2px 8px', cursor: 'pointer' }}>{busy === f.name ? '…' : '转正'}</span>
        </div>
      ))}
      <div className="serif" style={{ fontSize: 9, color: 'var(--ink-3)', lineHeight: 1.5, marginTop: 3, paddingLeft: 2 }}>转正=人审动作;转正后立即进入上方因子目录(实测 IC 待下次 regen 顺算)。</div>
    </div>
  );
}
```

- [ ] **Step 2: FactorLibrary 内挂载**

在 `{dyn && window.XG_IC_NOTE && <div ...>{window.XG_IC_NOTE}</div>}`(:803)那行**之前**插入一行:

```jsx
      <DraftFactorSection />
```

- [ ] **Step 3: bump ?v**

观澜 · 选股.html :43 `screen-app.jsx?v=20260702b` → `screen-app.jsx?v=20260702p3`。

- [ ] **Step 4: 自审**

①`f.status==='draft'` 判断(不用 `!f.status`);②空态 return null;③window.confirm 人审;④转正成功 await xgLoadCatalog(即刻上货架)+重拉 draft 列表;⑤失败 alert 显形 reason;⑥无新 hook 解构。

- [ ] **Step 5: Commit**

```bash
git add "ui/screen/screen-app.jsx" "ui/screen/观澜 · 选股.html"
git commit -m "feat(screen): P3 T3 因子库面板「待审 draft」区(人审转正→即刻上货架)"
```

---

### Task 4: 工作流页三处小填充

**Files:**
- Modify: `ui/factor/workflow.jsx`(?load= effect + HistoryModal 一行修 + FactorLibModal status 合并+徽章)
- Modify: `ui/factor/观澜 · AI 工作流.html`(:37 `?v=90` → `?v=91`)

**Interfaces:**
- Consumes: 既有 `_get(path)->json|null`(:232-243)、`cloneGraph/setNodes/setEdges/setWfName/setSel/setShowRes/setRunState/wfSaveLast/setToast`、`prefilledRef`(:1234,深链必须**同步**立旗否则被「恢复上次会话」effect 覆盖——P0④ 坑,:1274 首行 `if (prefilledRef.current) return;`)、`GET /workflow/get/{wid}`→`{ok,graph,name,id,ts}`、`GET /factorlib/list`。
- Produces: URL 契约 `?load=<wid>`(落子卡 Task 2 goCanvas 已按此跳转)。

- [ ] **Step 1: ?load= 深链 effect**

在 ?q= effect(:1258-1261)之后、`/workflow/list` hydrate effect(:1264)之前插入:

```javascript
  // P3:?load=<wid> 深链 —— 落子页「研究回路」卡「上画布」跳转落点:从工作流库取全图铺画布。
  // 同步立旗 prefilledRef(否则「恢复上次会话」effect 抢画布,P0④ 坑);绝不自动运行。
  useEffect(() => {
    const wid = new URLSearchParams(location.search).get('load');
    if (!wid) return;
    prefilledRef.current = true;
    _get('/workflow/get/' + encodeURIComponent(wid)).then(r => {
      if (!r || !r.ok || !r.graph || !Array.isArray(r.graph.nodes) || !r.graph.nodes.length) {
        setToast({ title: '深链载入失败', build: '工作流 ' + wid + ' 不存在或无图数据' }); setTimeout(() => setToast(null), 6000); return;
      }
      const g = cloneGraph(r.graph.nodes, r.graph.edges || []);
      setNodes(g.nodes); setEdges(g.edges); if (r.name) setWfName(r.name); setSel(null); setShowRes(false); setRunState({});
      wfSaveLast({ name: r.name || '深链载入', nodes: g.nodes, edges: g.edges });
      setToast({ title: '已载入「' + (r.name || wid) + '」', build: '深链载入(研究回路存图可由此上画布)· 待审阅,不自动运行' }); setTimeout(() => setToast(null), 6000);
    });
  }, []);
```

- [ ] **Step 2: HistoryModal「0 节点」修**

:1473-1474 两处读法改嵌套兜底(载入功能不动,纯显示):

- `{wfAgo(w.ts)} · {(w.nodes || []).length} 节点 · {(w.edges || []).length} 连线` → `{wfAgo(w.ts)} · {((w.graph && w.graph.nodes) || w.nodes || []).length} 节点 · {((w.graph && w.graph.edges) || w.edges || []).length} 连线`
- 链路预览行 `{(w.nodes || []).slice()...}` → `{(((w.graph && w.graph.nodes) || w.nodes || [])).slice()...}`(其余管道原样)

- [ ] **Step 3: FactorLibModal draft 徽章**

3a. 数据 effect(:1639-1655)里,在 `const l = (await _get('/factor/list')) || (await _list());` 之前加:

```javascript
        const lib = await _get('/factorlib/list?validate=false');
        const st = {}; ((lib && lib.factors) || []).forEach(x => { if (x.status) st[x.name] = x.status; });
```

3b. `setFull([...])` 三段 map 各补 status(按 name 查表):

```javascript
        setFull([
          ...reg.map(s => ({ name: s.name, expr: s.formula || '', cat: s.family || 'zoo', status: st[s.name] })),
          ...usr.map(u => ({ name: u.name, expr: u.expr || u.formula || '', cat: u.family || 'user', status: st[u.name] })),
          ...fac.map(u => ({ name: u.name, expr: u.expr || u.formula || '', cat: u.family || 'library', status: st[u.name] || u.status })),
        ]);
```

3c. 行渲染(:1720 `{f.cat && <span ...>{f.cat}</span>}` 之后)插入:

```jsx
                    {f.status === 'draft' && <span className="mono" style={{ fontSize: 8.5, color: 'var(--zhu)', border: '1px solid var(--zhu-soft)', borderRadius: 4, padding: '0 5px' }}>draft·待审</span>}
```

- [ ] **Step 4: bump ?v**

观澜 · AI 工作流.html :37 `workflow.jsx?v=90` → `workflow.jsx?v=91`。

- [ ] **Step 5: 自审**

①?load= 同步立旗 prefilledRef(在 fetch 之前);②载入失败 toast 显形不静默;③HistoryModal 只改显示行不碰 onLoad/loadEntry;④draft 徽章只显形不加按钮;⑤_get 失败回 null 的分支全兜住。

- [ ] **Step 6: Commit**

```bash
git add "ui/factor/workflow.jsx" "ui/factor/观澜 · AI 工作流.html"
git commit -m "feat(workflow-ui): P3 T4 ?load= 深链+历史列表节点数修+因子弹窗 draft 徽章"
```

---

### Task 5: 全量回归 + 真机浏览器 e2e + 还原现场

**Files:** 无生产代码改动(暴露 bug 走 TDD 修+commit)。**铁律:亲手执行,绝不转包子代理。**

- [ ] **Step 1: 全量回归**

Run: `G:/financial-analyst/.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: ≥796 passed 0 failed(794 基线 + T1 新增 2;并行会话基线漂移的失败先判归属,非 P3 所致原样上报)。

- [ ] **Step 2: 起独立测试 server**

先探端口(9998 被占则改用 9997)。P2 教训:**PS5.1 的 Start-Process -c 内联代码被 argv 重切分,必须用脚本文件**——写 `<scratchpad>/run_server_p3.py`:

```python
import os
os.environ.setdefault("GUANLAN_PORT", "9998")
from guanlan_v2.server import main
main()
```

`$env:PYTHONIOENCODING="utf-8"; Start-Process -WindowStyle Hidden G:/financial-analyst/.venv/Scripts/python.exe -ArgumentList "<scratchpad>/run_server_p3.py" -WorkingDirectory G:\guanlan-v2`;轮询 `http://127.0.0.1:9998/screen/health` 到 200。

- [ ] **Step 3: 浏览器 e2e(浏览器自动化工具逐项核;每页打开先查 console 无 SyntaxError——babel 语法错=整文件组件不注册)**

1. **落子页** `http://127.0.0.1:9998/ui/seats/观澜 · 落子.html`:研究回路折叠头显形(实盘/复盘两模式都在)→ 点开列出 P2 真档案 run(≥1 条 done,rank_ic=+0.0539 那条)→ 选中展开逐轮流水(过门标/样本外中文/诊断)→ promoted 徽章照档案如实(该 run 的 draft 文件已在 P2 e2e 还原时删除,徽章仍显 draft·待人审——档案是历史快照,如实显示即诚实,不算 FAIL)→「上画布」跳工作流页且画布铺图(?load= 生效)
2. **临时注入验证四态**:往 `var/research_runs.jsonl` 追加一条只有 start 行的假 run(`{"run_id":"rr_e2etest99","kind":"start","goal":"e2e四态验证","ts":"<现在>"}`)→ 刷新卡 → 该行显 ⚠ 已中断;**测后精确删除该行**
3. **draft 链路(顺序敏感)**:造临时 draft(`POST /factorlib/save {"name":"lib_e2e_draft_p3","expr":"rank(-delta(close,7))","status":"draft"}`)→ ①工作流页因子弹窗:该因子带「draft·待审」徽章 → ②选股页:左栏出现「待审 draft(研究回路)」组(1 条)且上方正式目录**无**此因子 → ③点「转正」确认 → 待审区消失 + 目录「因子库」族出现该因子 → ④**还原:删 `guanlan_v2/factorlib/mined/lib_e2e_draft_p3.json`**
4. **工作流页**:历史列表服务端条目显真实节点数(非 0 节点)
5. **帷幄工具冒烟**:`GUANLAN_PORT=9998` 进程内调 `ct.factor_drafts_impl()`(draft 存在期间列出)与 `ct.factor_promote_impl(name="lib_e2e_draft_p3")`(转正后幂等 ok)
6. 核对三页 html ?v= 全部 bump(grep `20260702p3` 与 `v=91`)

- [ ] **Step 4: 还原现场**

删临时 draft JSON、精确删 research_runs.jsonl 注入行(别动真档案)、杀测试 server、`git status` 核工作树只剩预期改动、生产 9999 未碰。

- [ ] **Step 5: 报告**

Write `.superpowers/sdd/task-5-p3-report.md`:回归数字、e2e 各项 PASS/FAIL/SKIP、还原清单、并行会话干扰情况(如有)。

---

## 自审记录(writing-plans Self-Review)

1. **Spec 覆盖**:§1 落子卡=T2;§2 工作流三修=T4;§3 选股待审区=T3;§4 帷幄两工具+提示词+文案一致性=T1;§5 测试=T1(pytest)+T5(真机);§0 红线各任务内嵌。唯一偏差:§3 说给 FactorLibrary 传 API prop,计划改为组件内直读 window.GUANLAN_BACKEND(仓内既定惯例 RegimeChips/DecisionPanel,免动 ConstraintRail 链)——已在 T3 Interfaces 显式记录,评审可裁。
2. **占位符**:无 TBD/TODO;全部代码完整给出。
3. **类型一致性**:`lzResearchRuns(limit)->runs[]|null`/`lzResearchRounds(runId)->rounds[]` T2 定义 T2 消费;`?load=<wid>` T4 定义、T2 goCanvas 消费(跨任务契约=纯 URL 参,T2 先行 T4 后补页面侧,e2e 在 T5 验证全链);`factor_drafts_impl/factor_promote_impl` 签名与表条目 schema 对齐;html ?v= 统一 20260702p3(工作流页 v=91)。
