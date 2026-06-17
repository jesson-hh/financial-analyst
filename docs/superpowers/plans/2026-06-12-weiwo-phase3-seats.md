# 帷幄三期 3+4:落子嵌右栏 + 哨兵研判回流(附:图谱卡死排查)

> **状态:已执行完毕并验收(2026-06-12)** — S1-S4 两段审查全过(修 3 Minor:code 校验+quote、WW_TOOL_CN、哨兵条目点对点聚焦 onSentryFocus),pytest 155 绿,9999 已拉新;真机验收:呼出器 5 项含落子、哨兵徽章「哨·20」→面板全局分区(立昂微/宁德真历史)→点条目落子页嵌右栏并聚焦立昂微(toast「已接收帷幄研判交棒」)、身份区隐/agent 全在、信箱读后即焚、已读水位生效。S5 图谱卡死:**未复现,页面无死循环**——最可能是 unpkg/Google Fonts 经代理黑洞致 document 停 loading(harness 误判),修法=vendor 自托管(挂账);顺手发现:看门狗开机自启失效(已手动拉起,另起任务芯片跟进)、var/archive 75→5 件待向用户确认。

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。本仓**无 git**;改 jsx 必 bump `?v=`(Edit 非 sed);改 python 须重启 9999(controller 收口统一做);pytest 口径 `G:\financial-analyst\.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`(基线 152 绿)。

**Goal:** 帷幄能调出落子页(右栏 iframe),哨兵研判结果回流帷幄(顶栏通知 + agent 可查询),统帅-哨兵双向闭环;顺手查图谱页渲染卡死根因。

**红线:落子页的哨兵 agent 窗口/能力全保留**——嵌入态只隐导航重复件,绝不隐 agent。

**已知事实(controller 已核)**:
- `ww_seats_decide` 的 artifact 已是 `artifact("seat_decision", page="seats", channel="cockpit", payload={code,name})`(tools.py:255-256)——前端 WW_PAGES 没注册 seats 所以进不了 bench;
- luozi-app.jsx:69 `GL.take('cockpit')`(裸键,mount 一次),现只处理 `{fromScreen, basket}` 形(选股→扩池);
- `GET /seats/decisions?code=&kind=&limit=`(seats/api.py:177-)读 var/seats_decisions.jsonl 逆序,恒 200,条目 `{id, ts(ISO秒), kind, code?, name?, direction?, confidence?, rationale?…}`;
- nav.js 在 `?embed=1` 全局早退(不注 nav);bench iframe src 已统一带 `&ws=<sid>`;GL.handoff/take 已支持 ws 第三/二参;
- H4 子进程测试硬编断言 CONSOLE_ALLOWED=19、explicit ww=12(tests/test_console_tools.py:288-337)——S4 加工具后**必须**同步改 20/13。

---

## S1: 落子页嵌入卫生 + cockpit 通道收 ws 与新 payload 形

**Files:** Modify `ui/seats/luozi-app.jsx`(或 masthead 所在的 luozi-*.jsx,Grep 定位)、`ui/seats/观澜 · 落子.html`(bump)

- [ ] 顶部加旗(对齐 screen-app 同款):`const WW_EMBED = new URLSearchParams(location.search).get('embed') === '1';` 与 `const WW_WS = new URLSearchParams(location.search).get('ws') || '';`
- [ ] **embed 卫生**:Grep 落子页自有页头/masthead(顶栏身份区:印章+「观澜 · 落子」类),包 `{!WW_EMBED && (...)}` 隐藏;**agent 窗口、席位、研判抽屉等一概不动**
- [ ] `GL.take('cockpit')` → `GL.take('cockpit', WW_WS)`(嵌入态吃会话键;独立态 WW_WS='' 裸键如旧)
- [ ] take 处理器加新 payload 形:`else if (h.code)`(无 fromScreen)→ `window.lzPoolAdd && lzPoolAdd({ code: bare(h.code), name: h.name })` 入盯盘池;若现有代码里有廉价的「切换当前票」入口(读 take 处理器下文与 lzPool* 帮助函数)就顺带聚焦该票,没有就入池为止(注释注明)
- [ ] bump:luozi-app.jsx `?v=20260611f` → `20260612a`(html 内);若动了别的 luozi-*.jsx 同步 bump
- [ ] 验证留给 controller(浏览器统一做);本任务跑 pytest 全量确认没碰后端(152 绿)

## S2: 帷幄注册落子页

**Files:** Modify `ui/console/console-data.jsx`(WW_PAGES)、`guanlan_v2/console/tools.py`(_SHOW_PAGES + ww_show_page spec)、`ui/console/观澜 · 帷幄.html`(bump data)

- [ ] console-data.jsx `WW_PAGES` 加:`seats: { label: '落子', file: '../seats/观澜 · 落子.html', channel: 'cockpit' },`(呼出器/bench tab/artifact 驱动自动生效)
- [ ] tools.py `_SHOW_PAGES` 集合加 `"seats"`;`ww_show_page` 注册 spec 的 page 参数枚举/描述同步加 seats(写明「落子=盯盘/席位/研判」)
- [ ] console-data.jsx `?v=20260613g` → `20260613l`(帷幄 html)
- [ ] 测试:test_console_tools.py 若有 show_page 页面枚举断言则更新;跑全量

## S3: 哨兵研判回流(平台级通知)

**Files:** Modify `ui/console/console-data.jsx`(API)、`ui/console/console-app.jsx`(poll+state)、`ui/console/console-thread.jsx`(WwSessBar 徽章+面板分区)、帷幄 html(bump 三件)

设计:研判是**平台级**事实(不属于某个会话)→ 不进会话事件流;帷幄轮询 `/seats/decisions`,新条目在**会话栏任务芯片旁**亮「哨」徽章,任务面板尾部加「哨兵研判 · 全局」分区;点条目 `onOpenPage('seats')` 调出落子页;打开面板即记已读。

- [ ] console-data.jsx 加 `wwSeatsDecisions(limit=20)`:`fetch(WW_API + '/seats/decisions?limit=' + limit)` 返回 items(失败返回 []);挂 window.WW.seatsDecisions
- [ ] console-app.jsx:`const [sentry, setSentry] = React.useState({ items: [], unread: 0 });` 复用现有 8s 轮询 effect(refreshSessions 同节拍)拉 decisions;已读水位 `localStorage['guanlan:ww:sentryseen']`(存最新已读 ts 字符串);unread = items 中 ts > 水位 的条数;`markSentrySeen()` 把最新 ts 写水位并清 unread。把 `sentry`、`markSentrySeen` 传给 WwThread → WwSessBar(连同已有 onOpenPage)
- [ ] console-thread.jsx WwSessBar:
  - 任务芯片左侧加哨兵徽章(仅 unread>0 时显形):`● 哨 · N` 朱砂小 pill,点它 = 打开任务面板
  - 面板(open 时)调 `markSentrySeen()`;面板尾部新分区标题「哨兵研判 · 全局」(标注非本会话),列 `sentry.items.slice(0,8)`:`{kind==='decide'?'研判':'条件单'} · {name||code} · {direction||''} {confidence?('置信'+confidence):''}` + 右侧时间(ts.slice(5,16));每条 onClick={() => { setOpen(false); onOpenPage && onOpenPage('seats'); }};空态「暂无研判——落子哨兵出手后挂在这里」
  - WwThread 把 onOpenPage 透传给 WwSessBar(签名加 sentry/markSentrySeen/onOpenPage)
- [ ] bump:console-app/thread → `20260613l`(与 data 同号)
- [ ] 纯前端,跑全量确认 152 绿

## S4: ww_seats_history 工具(agent 可查哨兵研判)

**Files:** Modify `guanlan_v2/console/tools.py`、`guanlan_v2/console/api.py`(_SYSTEM_PROMPT 一句)、`tests/test_console_tools.py`

- [ ] impl:

```python
def seats_history_impl(code: str = "", limit: int = 10) -> Dict[str, Any]:
    try:
        lim = max(1, min(int(limit or 10), 50))
        r = _self_get(f"/seats/decisions?code={code}&limit={lim}")
    except Exception as e:
        return {"ok": False, "content": f"研判历史查询失败: {e}", "artifact": None}
    items = r.get("items") or r.get("decisions") or []   # 以真实响应键为准(先读 seats/api.py 返回形状)
    if not items:
        return {"ok": True, "content": "暂无哨兵研判记录。", "artifact": None, "raw": r}
    lines = [f"{it.get('ts','')[:16]} {it.get('kind','')} {it.get('name') or it.get('code','')} "
             f"{it.get('direction','')} 置信{it.get('confidence','-')}" for it in items[:lim]]
    return {"ok": True, "content": f"哨兵研判最近 {len(lines)} 条:\n" + "\n".join(lines),
            "artifact": None, "raw": r}
```
(**先 Read seats/api.py 的 /decisions 响应形状**——items 键名/字段以真实代码为准,上面是骨架)

- [ ] 注册 specs:`("ww_seats_history", "查询落子哨兵的研判/条件单历史(全局,跨会话)。", {code?, limit? default 10}, _wrap(seats_history_impl), "instant", False)`;`CONSOLE_ALLOWED` 加 `ww_seats_history`(19→20)
- [ ] api.py `_SYSTEM_PROMPT` 工具清单句加「哨兵研判历史 ww_seats_history」
- [ ] **同步改 H4 子进程测试**(tests/test_console_tools.py:288-337):CONSOLE_ALLOWED 计数 19→20、explicit ww 12→13(读清断言再改)
- [ ] 新测试:monkeypatch `_self_get` 返回 `{"ok":True,"items":[{"ts":"2026-06-12T10:00:00","kind":"decide","code":"SZ000001","name":"平安银行","direction":"buy","confidence":0.7}]}` → content 含「平安银行」与「buy」;空 items → 「暂无」
- [ ] 全量 pytest ≥152 绿

## S5: 图谱页渲染卡死排查(诊断,独立并行)

**Files:** 只读为主;若根因便宜(<20 行)修 `ui/graph/graph.jsx` + bump,否则交根因报告

- [ ] 现象:2026-06-11 preview 面板窗口停在 ui/graph/观澜 · 研究图谱.html 时渲染进程整体挂起(eval/截图全部 30s 超时),archive/list 返回 75 件后发生;同页更早(物料 23 件时段)可正常 eval
- [ ] 读 graph.jsx:找同步死循环候选(力导向布局 while 不收敛?useEffect 互相触发?O(n²) 同步计算随 75 件物料爆炸?)与 archive/list 合并逻辑(guanlan-bus.js:142-162 影子库回填 → persist → emit → 订阅者 setState 风暴?bus.on 订阅 + GL.put 回写循环?)
- [ ] 重现:Chrome 开图谱页(独立 tab),JS 注入探针或直接观察 eval 是否可用;**别用 preview 面板**(就是它卡的)
- [ ] 产出:根因(文件:行号+触发条件)+ 修法;便宜就修(bump graph.jsx ?v=4→5)并验证图谱页 75 件物料下可交互

## S6 收口(controller)

- [ ] 全量 pytest;杀 9999 等看门狗拉新;浏览器验真:呼出器有「落子」、调出后落子页嵌右栏(agent 窗口仍在)、任务面板「哨兵研判」分区与徽章(可向 seats_decisions.jsonl 追加一条合成记录触发);文档(console+seats README、spec 三期状态)与 memory 收口。
