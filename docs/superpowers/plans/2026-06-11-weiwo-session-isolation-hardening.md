# 帷幄会话隔离加固 实现计划(体检 12 缺口收口)

> **状态:已执行完毕并验收(2026-06-12)** — H1-H4 四批全过两段审查(抓出并修复 1+4+4 处审查问题 + delete 路由信封),pytest 152 绿,9999 已重启拉新,/tools 全路径 0 ww_ 外露,浏览器冒烟(bus ws 命名空间/sessionStorage sid/iframe 带 ws)全过。挂账:meta.bg 终态条目裁剪(keep N)、delete 与 _spawn_bg 起跑微竞窗、/run 显式 tools 白名单仍可解析 ww_(localhost 信任缝,文档已注)、_SHOW_PAGES 补 seats(并入三期)、store 跨进程 pid 锁、_emit 吞吐优化。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. 本仓**无 git**——绝不 git init/commit;每任务以 pytest+控制端验证收口。

**Goal:** 修掉 2026-06-11 全面体检确认的 12 个缺口,使每个帷幄会话拥有真正互不串扰的 workspace(前端信箱/抽屉/多tab、后端记忆/重灌/后台任务、store 加固、越权面)。

**Architecture:** 四批:H1 前端隔离收口(纯 jsx+bus)→ H2 记忆持久(console api/tools)→ H3 后台任务韧性(console api/store)→ H4 加固(store 锁/引擎 profile)。后端改动须重启 9999(杀监听 PID 等看门狗 ~10s);jsx 改动必 bump `?v=`(用 Edit 非 sed)。

**Tech Stack:** no-build React 18 UMD+babel(ui/)、FastAPI(guanlan_v2/console/)、pytest 口径 `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`(当前 136 绿)。

**审计依据:** workflow wf_00d83c21-6c2(48 OK/29 raw→12 缺口),证据行号见各任务。

---

## H1 批 · 前端隔离收口(缺口 1-5)

### Task H1-1: handoff 信箱 ws 命名空间(缺口①最大结构性串扰)

**Files:**
- Modify: `ui/_shared/guanlan-bus.js`(handoff/take/peek 加可选 ws 参)
- Modify: `ui/console/console-bench.jsx`(drive 时传 state.sid)
- Modify: `ui/screen/screen-app.jsx`、`ui/factor/workflow.jsx`、`ui/cards/validation.jsx`(take/peek 带 WW_WS)
- Modify: 所有引用 `guanlan-bus.js?v=3` 的 html(bump v=4)+ 各 jsx ?v

**要点**:键形 `guanlan:handoff:<ch>[:<ws>]`;ws 缺省=全局键,独立页行为零变化。WW_WS 解析放宽为**不依赖 embed**(`?ws=` 即生效),为 H1-5 独立打开铺路。

- [ ] **Step 1: guanlan-bus.js 三方法加 ws**

```js
handoff(ch, payload, ws) { try { localStorage.setItem(HANDOFF + ch + (ws ? ':' + ws : ''), JSON.stringify({ payload, ts: Date.now() })); } catch (e) {} },
take(ch, ws) { try { const k = HANDOFF + ch + (ws ? ':' + ws : ''); const v = localStorage.getItem(k); if (!v) return null; localStorage.removeItem(k); return JSON.parse(v).payload; } catch (e) { return null; } },
peek(ch, ws) { try { const v = localStorage.getItem(HANDOFF + ch + (ws ? ':' + ws : '')); return v ? JSON.parse(v).payload : null; } catch (e) { return null; } },
```
(`go(href, ch, payload)` 不带 ws——独立页间交棒走全局键,语义不变)

- [ ] **Step 2: console-bench.jsx drive 传 sid**:`GL.handoff(art.channel, art.payload, state.sid)`(`wsArg` 已带 ws=sid 进 iframe URL,不动)
- [ ] **Step 3: 三个嵌入页解析 WW_WS 并用于 take/peek**
  - screen-app.jsx:顶部加 `const WW_WS = new URLSearchParams(location.search).get('ws') || '';`,`GL.take('screen')` → `GL.take('screen', WW_WS)`
  - workflow.jsx:`WW_WS` 定义去掉 `WW_EMBED ?` 前提(直接读 ?ws=);`GL.take('workflow')` → `GL.take('workflow', WW_WS)`
  - validation.jsx:加 WW_WS 常量;`GL.peek('validation')`/`GL.take('validation')` → 带 WW_WS
- [ ] **Step 4: bump 全部引用方**:Grep `guanlan-bus.js?v=3` 的 html 全改 v=4;workflow.jsx v74→75、screen-app/validation/console-bench/console-app 各 bump
- [ ] **Step 5: 验证**:9998 preview 或 Chrome JS——①bench 驱动后 localStorage 出现 `guanlan:handoff:screen:cs_xxx` 键(而非裸键)②iframe 内页面真取到 payload ③独立打开(无 ws)写裸键行为如旧

### Task H1-2: attach 清研报抽屉(缺口②)

**Files:** Modify `ui/console/console-app.jsx`

- [ ] attach() 里 `setManual([]); setBenchFocus(null);` 后加 `setDrawer(null);`
- [ ] 验证:开抽屉→切会话→抽屉消失

### Task H1-3: 多 tab sid 改 sessionStorage 优先(缺口③)

**Files:** Modify `ui/console/console-app.jsx`

- [ ] attach() 写:`sessionStorage.setItem('guanlan:ww:sid', sid)` + 仍写 localStorage(新 tab 兜底);mount 读:sessionStorage 优先,无则 localStorage
- [ ] 验证:JS 断言两存储各自有值;刷新当前 tab 回到本 tab 会话

### Task H1-4: cards 会话态 MEM_KEY 接 ws(缺口④)

**Files:** Modify `ui/cards/validation.jsx`

- [ ] 用 H1-1 的 WW_WS:`const MEM_KEY = 'guanlan:cards:mem' + (WW_WS ? ':' + WW_WS : '');`
- [ ] 验证:embed+ws 下写入键带 sid 后缀;独立页键不变

### Task H1-5: ↗ 独立打开带 ws(缺口⑤)

**Files:** Modify `ui/console/console-bench.jsx`

- [ ] `window.open(PAGES[cur].file + '?ws=' + encodeURIComponent(state.sid || ''), '_blank')`(H1-1 已让 WW_WS 不依赖 embed → 独立页进入本会话工作区,且不误食裸键信箱)
- [ ] 验证:点 ↗ 新页 URL 带 ws;workflow 独立页读到会话级 wf:last 键

**H1 收口**:pytest 全绿(纯前端不应动测试)+ 控制端全链验真 + bump 清单核对。

---

## H2 批 · 记忆持久(缺口 6-8)

### Task H2-1: _reseed 读 condensation 摘要打底(缺口⑥ P1)

**Files:** Modify `guanlan_v2/console/api.py:_reseed`;Test `tests/test_console_api.py`

- [ ] **实现**(替换 _reseed 主体):

```python
def _reseed(agent, events: List[Dict[str, Any]], max_msgs: int = 16, max_chars: int = 8000) -> None:
    """进程重启后从事件日志重灌:最后一条 condensation 摘要打底 + 其后对话(对齐 compact 口径)。"""
    if getattr(agent, "messages", None):
        return
    base, idx = None, 0
    for i in range(len(events) - 1, -1, -1):
        if events[i].get("type") == "condensation" and events[i].get("summary"):
            base, idx = str(events[i]["summary"]), i + 1
            break
    msgs: List[Dict[str, str]] = []
    for ev in events[idx:]:
        if ev.get("type") == "user_msg" and ev.get("text"):
            msgs.append({"role": "user", "content": str(ev["text"])})
        elif ev.get("type") == "agent_delta" and ev.get("text"):
            if msgs and msgs[-1]["role"] == "assistant":
                msgs[-1]["content"] += "\n" + str(ev["text"])
            else:
                msgs.append({"role": "assistant", "content": str(ev["text"])})
    msgs = msgs[-max_msgs:]
    while len(msgs) > 1 and sum(len(m["content"]) for m in msgs) > max_chars:
        msgs.pop(0)
    if base:
        msgs.insert(0, {"role": "user", "content": "（前情摘要——更早对话已压缩）\n" + base[:4000]})
    try:
        from financial_analyst.buddy.agent import Message
        for m in msgs:
            agent.messages.append(Message(role=m["role"], content=m["content"]))
    except Exception:
        pass  # FakeAgent / 测试路径:reseed 是增强项,不阻塞
```

- [ ] **测试**:造事件流 [user,delta,condensation(summary=S),user,delta] → 带 messages=[] 的假 agent reseed 后 messages[0].content 含 S、其后只有 idx 之后的对话(注意假 agent 需让 `from financial_analyst...` 导入失败路径仍可断言——用真实 import 可用的环境,messages 列表收 Message 对象,断言 .content)
- [ ] pytest 该文件绿

### Task H2-2: LRU 逐出避开 running(缺口⑦)

**Files:** Modify `guanlan_v2/console/api.py:_agent_for`;Test 同文件

- [ ] 替换逐出循环:

```python
        while len(agents) > 12:
            victim = next((k for k in agents if k not in running), None)
            if victim is None:
                break          # 全在跑(理论不可达):宁可超限不丢在跑会话的史
            agents.pop(victim)
```

- [ ] 测试:经 send(FakeAgent)建 13 个会话,把最旧 sid 手动放进 router 闭包的 running 不可达——改为直接单测 `_agent_for` 等价逻辑:用 build_console_router 暴露不便时,以「13 会话连发,最旧者在跑」难造,可降级为逻辑单测:构造 OrderedDict+running set 走同款循环断言victim选择(实现者可把循环抽成模块级 `_evict_lru(agents, running)` 以便测试)
- [ ] pytest 全绿

### Task H2-3: 记忆双层(全局偏好 + 会话笔记)(缺口⑧ P1)

**Files:** Modify `guanlan_v2/console/tools.py`(memory 工具)、`guanlan_v2/console/api.py`(_memory_block+_SYSTEM_PROMPT);Test `tests/test_console_tools.py`

- [ ] **tools.py**:模块级 `_MEMORY_LOCK = threading.Lock()`;`_session_notes_path(sid)` = `_MEMORY_PATH.parent / "sessions" / sid / "notes.md"`;memory_write_impl 加 `scope`("global" 缺省|"session"):session 时经 CTX_SID 取 sid(无 sid 诚实拒绝),目录 mkdir(parents=True, exist_ok=True),append 持锁;memory_read_impl 加 scope("all" 缺省=两段拼接,各截尾 4000)。spec 描述写明:「稳定偏好(池子/频率/风格)→ scope=global;仅本会话相关的任务笔记 → scope=session」
- [ ] **api.py**:`_memory_block(sid)` 返回 `[帷幄记忆·全局]` 段(尾2000)+ `[本会话笔记]` 段(notes.md 尾2000,无则省);`_run_turn` 调用处传 sid;_SYSTEM_PROMPT 第 6 条纪律改:「稳定偏好用 ww_memory_write(scope=global) 记;本会话任务笔记用 scope=session,不污染其他会话」
- [ ] **测试**:①scope=session 写入落 sessions/<sid>/notes.md 且全局文件不变 ②无 CTX_SID 时 session 写诚实失败 ③_memory_block 含两段 ④并发 append 不炸(两线程各写 50 行,行数=100)
- [ ] pytest 全绿

**H2 收口**:重启 9999,控制端实测:会话 A 记 scope=session 笔记 → 会话 B 的轮注入不含它、A 自己含。

---

## H3 批 · 后台任务韧性(缺口 9-10)

### Task H3-1: meta.bg 起跑即写 + 启动扫描标中断(缺口⑨ P1)

**Files:** Modify `guanlan_v2/console/store.py`(锁内子键合并 + RLock)、`guanlan_v2/console/api.py`(_run_report_bg + build_console_router 启动扫描);Test `tests/test_console_api.py`

- [ ] **store.py**:`self._lock = threading.RLock()`(为下方方法重入 get_meta 铺路);加方法:

```python
    def merge_meta_sub(self, sid: str, key: str, sub_id: str, fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """锁内对 meta[key][sub_id] 合并(后台任务留档等嵌套字典,避免锁外读改写竞态)。"""
        with self._lock:
            meta = self.get_meta(sid)
            if meta is None:
                return None
            sub = dict(meta.get(key) or {})
            sub[sub_id] = {**(sub.get(sub_id) or {}), **fields}
            meta[key] = sub
            meta["updated"] = _now()
            self._save_meta(meta)
            return meta
```

- [ ] **api.py _run_report_bg**:过 dedup 后、起 executor 前 `st.merge_meta_sub(sid, "bg", bg_id, {"kind": "report", "code": code, "status": "running", "started": datetime.now().isoformat(timespec="seconds")})`;finally 改用 `st.merge_meta_sub(sid, "bg", bg_id, {"status": status, "ok": ok, "ended": ...})` 替换原锁外 get_meta+merge_meta 整 dict 路径
- [ ] **api.py build_console_router 内(routes 定义后、return 前)加启动扫描**:

```python
    # 启动扫描:上次进程死亡时停在 running 的后台任务 → 标中断并补事件(孤儿子进程最多 900s 自灭)
    try:
        for m in st.list_sessions():
            for bg_id, b in (m.get("bg") or {}).items():
                if b.get("status") == "running":
                    st.merge_meta_sub(m["id"], "bg", bg_id, {"status": "error", "ok": False, "note": "服务重启,任务中断"})
                    st.append_event(m["id"], "task_update", task_id=bg_id, kind=b.get("kind", "report"),
                                    code=b.get("code"), status="error", ok=False, note="服务重启,任务中断(可重新发起)")
    except Exception:
        pass  # 扫描失败不挡服务启动
```

- [ ] **测试**:①预置 meta.bg={x:{status:"running",code:"SZ000001"}} → build_console_router → meta 标 error 且 events 尾部出现 task_update error ②merge_meta_sub 不丢兄弟条目
- [ ] pytest 全绿

### Task H3-2: 撞车改搭车 + delete 挡 in-flight 研报(缺口⑩)

**Files:** Modify `guanlan_v2/console/api.py`;Test `tests/test_console_api.py`

- [ ] `_bg_inflight` 从 set 改 dict:`key → {"sid": 发起者, "watchers": set()}`;撞车分支:同 sid 重复→维持拒绝口径;异 sid→`watchers.add(sid)` 并 `_emit(sid, "task_update", task_id=bg_id, kind="report", code=code, status="running", note=f"{code} 已在另一会话生成中,完成后此处同步通知")`(**非 error**),然后 return
- [ ] 完成/失败收尾:对发起者照旧;对每个 watcher 也 `_emit` task_update done/error +(成功时)tool_result report_md artifact(各自 sid 各落各的 jsonl)
- [ ] `sessions_delete`:加 `if any(sid == v["sid"] or sid in v["watchers"] for v in _bg_inflight.values()): return JSONResponse({"ok": False, "reason": "该会话有后台研报在跑,先等其完成"})`
- [ ] **测试**(monkeypatch `_call_buddy_report` 短路):两会话同 code:B 收 running 搭车事件非 error;完成后 A、B 各有 done 事件与 report_md artifact;in-flight 期间 delete A 与 delete B 均被拒
- [ ] pytest 全绿

**H3 收口**:重启 9999;控制端冒烟(monkeypatch 不真跑 5 分钟)。

---

## H4 批 · 加固(缺口 11-12)

### Task H4-1: store 读持锁 + Windows replace 重试(缺口⑪)

**Files:** Modify `guanlan_v2/console/store.py`;Test `tests/test_console_store.py`

- [ ] get_meta/read_events/list_sessions 包 `with self._lock:`(H3-1 已换 RLock,内部互调安全);`_save_meta` 的 `tmp.replace(p)` 包 3 次重试(PermissionError 时 `time.sleep(0.05)`)
- [ ] delete_session:`for f in d.iterdir(): f.unlink()` + rmdir → `shutil.rmtree(d)` 包 PermissionError 重试一次
- [ ] 测试:两线程各 50 次 append_event 同会话 + 主线程循环 get_meta:无异常且 read_events 总数=100、next_event_id 单调
- [ ] pytest 全绿

### Task H4-2: 引擎 profile 过滤 ww_ 工具堵 /run 越权(缺口⑫)

**Files:** Modify `engine/financial_analyst/buddy/tools.py`(profile 解析处;engine fork 改动在授权豁免范围);Test `tests/test_console_tools.py`

- [ ] Grep `profile` 于 engine/financial_analyst/buddy/tools.py 定位 profile→工具集合的函数,在「非 console 显式白名单」的所有路径(缺省/all/research)统一剔除 `name.startswith("ww_")`;console 路径不受影响(api.py 显式传 CONSOLE_ALLOWED)
- [ ] 测试:register_console_tools() 后,该 profile 函数对 'research' 与缺省返回的名字集合不含任何 ww_*;CONSOLE_ALLOWED 仍 19 个全可解析
- [ ] pytest 全绿;重启 9999 后验证引擎工具清单端点不外露 ww_*

**H4 收口**:pytest 全绿 + 9999 重启 + 控制端复测 H1-H3 主链不回归 + README/memory 收口。

**挂账(不在本计划)**:_SHOW_PAGES 补 seats(并入三期落子嵌右栏)、store 跨进程 pid 锁(info)、_emit 每事件重写 meta 的吞吐优化(info)。
