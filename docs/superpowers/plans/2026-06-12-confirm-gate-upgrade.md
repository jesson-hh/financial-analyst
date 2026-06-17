# 确认门升级 Implementation Plan(0612演习修复#4)

> **状态:已执行完毕并验收(2026-06-12)** — CG1/CG2 全过,pytest **197 绿**(195+2),9999 已拉新,?v=20260613n;真机验收四项全过:①确认门带「机器核数·实时真值」块(现价303.92+五因子渲染行)与「⚠ 预检 2 处」朱砂块——合成 creed「动量强劲20日+20%,追涨」被当场拆穿(方向矛盾+20%无出处)②点「同意·一次」一次生效,modal 随 confirm_resolved 2 秒内关闭 ③刷新页面快照重放 modal **不复活**(残留 bug 根治)④无 JS 报错。**注**:验收时发现浏览器自动化(CDP 合成点击)对 textarea 聚焦失效(active=BODY,click 事件正常仅 focus 副作用丢)——非本批回归(JS 层与真人路径正常),自动化绕过=JS focus+原生 setter+POST /console/send。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 确认门从「授权门」升级为「事实门」——决策类工具的门上并排显示**机器核数**(实时真值经语义字典渲染)与 **creed 预检**(claim_audit 提前到门上),人一眼对出矛盾;同时根治演习暴露的两个 modal bug:①后端 confirm 后无 `confirm_resolved` 事件 → SSE 300s 分段重连快照重放 confirm_request 时 modal 复活(残留之谜);②前端 `WW.confirm` 返回值被忽略 → 失败静默(点击无效之谜),且点遮罩=拒绝(误触风险)。

**Architecture:** 后端 console/api.py:`pending` 改存 `(sid, fut)`;confirm 端点与超时路径都 `_emit("confirm_resolved")`;新增 `_confirm_extras(tool, args)`(ww_seats_decide → live_eval 真值+semantics 渲染+audit_claims 预检 creed;ww_cards_save → unsourced_percents 预检 insight;失败静默 `{}`)注入 confirm_request 事件。前端:wwApply 收 `confirm_resolved` 清 confirm;ConfirmModal 渲染 facts/precheck 块 + 点击进「已发送」态 + 失败显形可重试 + 遮罩点击不再拒绝。

**Tech Stack:** Python 3.13 / FastAPI / pytest;前端 no-build React(改 jsx 必 bump `?v=`,**用 Edit 非 sed**)

**硬约束:**
- **本仓无 git——禁止 git 命令,"提交"=跑 pytest**
- pytest 口径同前(基线 **195 绿**);9999 重启 controller 收口做;GateGuard 四事实照做;用户指令原话:「好的 开始4」
- **协程内严禁同步自 HTTP**——`_self_get` 必须包 `asyncio.to_thread`(历史坑:堵 loop→看门狗杀 9999)

**已核事实:**
- confirm 机制:console/api.py:214(`pending: Dict[str, Future]`)、:410-420(confirm_cb:emit confirm_request → wait fut → TimeoutError 默认 'n')、:478-486(POST /console/confirm:`fut.set_result(choice)`,**无 emit**)
- 前端:console-data.jsx:41(`confirm_request → n.confirm = ev`)、:36(仅 task_update done/error 清 confirm)、:119-124(`wwConfirm` 返回 `r.json()`);console-app.jsx:121(`onConfirm=(c)=>WW.confirm(state.confirm.turn_id,c)` 返回值被丢);console-thread.jsx:249-288(ConfirmModal:遮罩 onClick=resolve('n'),args JSON 直显,Y/N 键盘)
- live_eval 响应键(seats/api.py:725-727 docstring):`price/asof/asofDate/rsi14/maDiff20/rev20/mom60/turnover20` 等(camelCase)
- 帷幄 html 当前版本:data `?v=20260613m` / thread `?v=20260613m` / app `?v=20260613m`(rail g、bench k、drawer b 本次不动)
- 现有确认流测试:tests/test_console_api.py:128 `test_confirm_flow`(FakeAgent 调 confirm_callback → 轮询 confirm_request → POST /console/confirm)

---

## Task 1: 后端 confirm_resolved + 机器核数 extras(TDD)

**Files:**
- Modify: `guanlan_v2/console/api.py`(pending 结构、confirm_cb、confirm 端点、新 `_confirm_extras`)
- Modify: `tests/test_console_api.py`(扩展 test_confirm_flow + 新增 extras 单测)

- [ ] **Step 1: 先扩测试** — test_console_api.py 的 `test_confirm_flow` 内,在 `r = c.post("/console/confirm", ...)` 断言 ok 之后追加(`_wait`/`store`/`sid` 等沿用该测试现有局部变量名——**先 Read :120-160 对齐真实变量名**再写):

```python
            evs2 = _wait(store, sid, lambda es: any(e["type"] == "confirm_resolved" for e in es))
            rs = [e for e in evs2 if e["type"] == "confirm_resolved"]
            assert rs and rs[0]["turn_id"] == req[0]["turn_id"] and rs[0]["choice"] == choice
```

再在文件末尾追加 extras 单测:

```python
def test_confirm_extras_seats_decide(monkeypatch):
    import asyncio
    from guanlan_v2.console import api as capi
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_self_get", lambda path: {
        "ok": True, "price": 303.12, "asofDate": "2026-06-11",
        "rev20": 0.2170881, "mom60": -0.0313489, "rsi14": 22.79383,
        "maDiff20": -0.1907521, "turnover20": 8.8468891})
    ex = asyncio.run(capi._confirm_extras("ww_seats_decide",
                                          {"code": "SH688012", "creed": "动量最强(20日+20%)"}))
    assert any("下跌21.7%" in f for f in ex["facts"])
    assert any("方向矛盾" in p for p in ex["precheck"])


def test_confirm_extras_cards_save_and_fallback():
    import asyncio
    from guanlan_v2.console import api as capi
    ex = asyncio.run(capi._confirm_extras("ww_cards_save",
                                          {"title": "卡", "insight": "动量20日+20%", "ic": "RankIC 4.80%"}))
    assert ex.get("precheck") and "未注明出处" in ex["precheck"][0]
    assert asyncio.run(capi._confirm_extras("ww_plan_update", {})) == {}
```

- [ ] **Step 2: 跑新测试确认失败**(`pytest tests/test_console_api.py -q` → confirm_resolved 不出现/`_confirm_extras` 不存在而红)

- [ ] **Step 3: 实现** — console/api.py 四处:

(a) `pending` 注释与结构(:214 附近):`pending: Dict[str, tuple] = {}`,注释改 `# turn_id → (sid, confirm future)`

(b) 模块级(便于单测)加:

```python
async def _confirm_extras(tool_name: str, args: dict) -> dict:
    """确认门升级(修复#4):决策类工具的门上附「机器核数」facts 与 precheck 预检。
    失败静默返回 {}(核数挂了不挡门);自 HTTP 走 to_thread(协程内禁同步 IO)。"""
    try:
        a = args or {}
        if tool_name == "ww_seats_decide":
            import guanlan_v2.console.tools as _ct
            from guanlan_v2.factorlib.claim_audit import audit_claims
            from guanlan_v2.factorlib.semantics import render_factors
            code = str(a.get("code") or "")
            if not code:
                return {}
            le = await asyncio.to_thread(_ct._self_get, f"/seats/live_eval?code={code}")
            if not (le or {}).get("ok"):
                return {}
            fac = {"rev_20": le.get("rev20"), "mom_60": le.get("mom60"),
                   "rsi_14": le.get("rsi14"), "ma_diff_20": le.get("maDiff20"),
                   "turnover_20": le.get("turnover20")}
            facts = [f"现价 {le.get('price')}(asof {le.get('asofDate') or le.get('asof') or '—'})",
                     render_factors(fac, ("rev_20", "mom_60", "rsi_14", "ma_diff_20", "turnover_20"))]
            precheck = audit_claims(str(a.get("creed") or ""), fac, "\n".join(facts))
            return {"facts": facts, "precheck": precheck}
        if tool_name == "ww_cards_save":
            from guanlan_v2.factorlib.claim_audit import unsourced_percents
            rogue = unsourced_percents(str(a.get("insight") or ""),
                                       " ".join([str(a.get("title") or ""), str(a.get("expr") or ""),
                                                 str(a.get("ic") or "")]))
            if rogue:
                return {"precheck": ["insight 含 " + str(len(rogue)) + " 个未注明出处的数字断言: "
                                     + ", ".join(f"{x:g}%" for x in rogue[:3])]}
            return {}
    except Exception:  # noqa: BLE001 — 核数失败不挡确认门
        return {}
    return {}
```

(c) confirm_cb(:410-420)改为:

```python
        async def confirm_cb(tool_name: str, args: dict) -> bool:
            fut: "asyncio.Future[str]" = asyncio.get_running_loop().create_future()
            try:
                pending[turn_id] = (sid, fut)  # 与 emit 同在 try 内:emit 抛也走 finally pop,不泄漏
                extras = await _confirm_extras(tool_name, args)
                _emit(sid, "confirm_request", turn_id=turn_id, tool=tool_name,
                      args=_safe(args), **extras)
                choice = await asyncio.wait_for(fut, timeout=_CONFIRM_TIMEOUT)
            except asyncio.TimeoutError:
                choice = "n"
                _emit(sid, "confirm_resolved", turn_id=turn_id, choice="timeout")
            finally:
                pending.pop(turn_id, None)
            return choice in ("y", "a", "yes", "always")
```

(d) confirm 端点(:478-486)改为:

```python
    @router.post("/confirm")
    async def confirm(body: dict = Body(default={})):
        # async:与 confirm_cb 同在 loop 线程,check-then-set 原子(threadpool 会 TOCTOU)
        turn_id = str(body.get("turn_id") or "")
        ent = pending.get(turn_id)
        if ent is None or ent[1].done():
            return JSONResponse({"ok": False, "reason": "no pending confirm"})
        sid_, fut = ent
        choice = str(body.get("choice") or "n")
        fut.set_result(choice)
        _emit(sid_, "confirm_resolved", turn_id=turn_id, choice=choice)
        return {"ok": True}
```

注意:Grep 全文件 `pending[` 与 `pending.get` 确认没有第三处使用旧结构。

- [ ] **Step 4: 跑全量**

Run: `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`
Expected: **197 passed, 0 failed**(195+2 新测试;confirm_flow 扩展不加计数,以 pytest 实际收集为准,关键 0 failed)

## Task 2: 前端三文件(facts/precheck 块 + resolved 清除 + 点击显形)+ bump

**Files:**
- Modify: `ui/console/console-data.jsx`(wwApply)
- Modify: `ui/console/console-thread.jsx`(ConfirmModal)
- Modify: `ui/console/console-app.jsx`(核对 onConfirm 返回值透传,大概率不用动)
- Modify: `ui/console/观澜 · 帷幄.html`(bump data/thread/app → `?v=20260613n`,**用 Edit**)

- [ ] **Step 1: console-data.jsx** — wwApply 的 `if (ev.type === 'confirm_request') n.confirm = ev;` 之后加一行:

```jsx
  if (ev.type === 'confirm_resolved') n.confirm = null;   // 修复#4:已决确认门即关(快照按序重放,不再复活)
```

- [ ] **Step 2: console-thread.jsx ConfirmModal 改造**(保留现有视觉 token):

1. 组件头加本地态与新 resolve(原 resolve 实现替换;键盘 Y/N 监听沿用,依赖数组加 sending):
```jsx
  const [sending, setSending] = React.useState('');
  const [err, setErr] = React.useState('');
  const resolve = async (ch) => {
    if (sending) return;
    setSending(ch); setErr('');
    try {
      const r = await onChoice(ch);
      if (!r || r.ok !== true) { setSending(''); setErr((r && r.reason) || '确认透传失败,请重试'); }
      // 成功后不本地关门:等 confirm_resolved 事件(单一事实源);sending 保持防双击
    } catch (e) { setSending(''); setErr('网络错误: ' + e); }
  };
```

2. **遮罩点击不再拒绝**:外层 `onClick={() => resolve('n')}` 改 `onClick={() => {}}`。

3. args JSON 块之后插入两块:

```jsx
        {Array.isArray(c.facts) && c.facts.length > 0 && (
          <div style={{ margin: '0 24px 10px', padding: '10px 12px', background: 'var(--paper-2)', border: '1px solid var(--line)' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.15em', marginBottom: 6 }}>机器核数 · 实时真值</div>
            {c.facts.map((f, i) => (
              <div key={i} className="mono" style={{ fontSize: 11.5, color: 'var(--ink-1)', lineHeight: 1.7 }}>{f}</div>
            ))}
          </div>
        )}
        {Array.isArray(c.precheck) && c.precheck.length > 0 && (
          <div style={{ margin: '0 24px 10px', padding: '10px 12px', border: '1px solid var(--yin)', background: 'rgba(140,30,20,0.05)' }}>
            <div className="mono" style={{ fontSize: 10, color: 'var(--yin)', letterSpacing: '0.15em', marginBottom: 6 }}>⚠ 预检 {c.precheck.length} 处 · 叙述与数据矛盾</div>
            {c.precheck.map((p, i) => (
              <div key={i} className="serif" style={{ fontSize: 12.5, color: 'var(--yin)', lineHeight: 1.7 }}>{p}</div>
            ))}
          </div>
        )}
        {err && <div className="serif" style={{ margin: '0 24px 8px', color: 'var(--yin)', fontSize: 12 }}>✗ {err}</div>}
```

4. 按钮 sending 态:`<span>{sending === 'y' ? '已发送…' : '同意 · 一次'}</span>`(拒绝同理);sending 非空时 opacity 0.6、cursor default。

- [ ] **Step 3: console-app.jsx** — 核对 :121:`WW.confirm` 返回 Promise<json>(wwConfirm `return r.json()`)→ onChoice await 即拿到,**确认后保持原样**;若 wwConfirm 没 return,补 return。

- [ ] **Step 4: bump** — `ui/console/观澜 · 帷幄.html`:data/thread/app 三处 `?v=20260613m` → `?v=20260613n`(Edit 三次)

- [ ] **Step 5: 全量 pytest** — 与 Task 1 收尾相同,0 failed

## Task 3: 收口(controller 亲自做)

- [ ] 全量 pytest 终验
- [ ] 重启 9999 + 探活
- [ ] 真机验收(Chrome 驱动帷幄):
  1. 新会话发「对中微公司 SH688012 做一次快速研判」→ 确认门弹出 → 门上有「机器核数·实时真值」块(现价+五因子渲染行),creed 有矛盾时显预检块
  2. 点「同意 · 一次」→ 按钮「已发送…」→ modal 随 confirm_resolved 关闭不残留
  3. 刷新页面(快照重放)→ modal 不复活
  4. read_console_messages pattern 'error' 无报错
- [ ] memory 收口(live-drill 修复#4 行 + MEMORY.md;待修③确认门 modal 残留标已修)

---

## Self-Review(已执行)

- 覆盖:#4 两半(事实门、modal 双 bug)全有任务;超时路径也 emit resolved;遮罩误触拒绝顺手关闭。
- 占位符扫描:无。
- 类型一致:`_confirm_extras -> dict`(facts/precheck 可缺省);事件 `confirm_resolved{turn_id, choice}` 前后端一致;`pending: turn_id → (sid, fut)` 全部使用处同步改。
