# 蒸馏 matured 门 + 开日跑攒样本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ①`ww_rerank_distill` 加 matured 门——无完整成熟 A/B 对时拒绝蒸馏(未熟数字不入永久记忆,防经召回反哺固化);②开 `GUANLAN_RERANK_DAILY=1` 日跑攒 A/B 样本(纯配置,控制器亲手)。

**Architecture:** 门加在 `rerank_distill_impl`(console/tools.py:1093)内、`memory_write_impl` 之前:经既有桥 `_rerank_perf_fetch(limit=20)` 读 A/B 成绩单,至少一对完整成熟(两臂 `ok:true` 且 `n>0` 且 `matured_n==n`)才放行;读取失败/端点失败/无成熟对一律 `ok:False` 拒绝显形,无 override。日跑=var/secrets.env 加一行(触发链已在:REGEN_DAILY 调度 daemon → 18 点后 tick → `_maybe_daily_rerank` → `start_rescore_bg(top_n=50)`)。

**Tech Stack:** Python/pytest(零新依赖;不新增工具,守护计数不动)。

## Global Constraints

- **诚实红线**:无法核实成熟度(档案读取失败)= 拒绝写,绝不默认放行;拒绝信息显形给 agent。
- **key 必填校验保持在门之前**(便宜校验先行,空 key 不触发 HTTP)。
- **确认门保持**:`confirm: True` 不动;matured 门是叠加的第二道闸。
- 不改守护计数(无新工具);工具表只改 `ww_rerank_distill` 的 description 文案。
- **TDD**;**逐文件 git add 绝不 -A**;尾注 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **secrets.env / 重启 9999 / 真机 e2e = 控制器亲手(Task 2)**,implementer 绝不碰 `var/`、绝不碰 9999。
- 分支:`distill-matured-gate`(base main)。

---

### Task 1: matured 门 + 工具表文案 + 测试(单 implementer,一次提交)

**Files:**
- Modify: `guanlan_v2/console/tools.py:1093-1100`(`rerank_distill_impl` 加门)、`:2644-2648`(工具表 description 补一句)
- Test: `tests/test_console_tools.py:1788-1799`(现有 distill 测试补桩)+ 新增 `test_rerank_distill_matured_gate`

**Interfaces:**
- Consumes(不变):`_rerank_perf_fetch(limit)`(tools.py:1056,返回 `/seats/basket_perf?kind=rerank_ab` 响应:`{ok, pairs:[{run_id, arms:{data:{ok,n,matured_n,…}, rerank:{…}}, …}]}`)、`memory_write_impl(text, scope, key)`。
- Produces:`rerank_distill_impl` 新增三条拒绝路径,content 均含 `matured 门` 字样;放行路径行为不变。

---

- [ ] **Step 1: 写失败测试(新门行为)**

在 `tests/test_console_tools.py` 的 `test_rerank_distill_enforces_prefix`(:1788)之后新增:

```python
def test_rerank_distill_matured_gate(monkeypatch):
    import guanlan_v2.console.tools as ct
    wrote = {}
    monkeypatch.setattr(ct, "memory_write_impl",
                        lambda text, scope, key: wrote.update(k=key) or {"ok": True})
    # ① 只有未成熟对 → 拒绝,不写记忆
    unmat = {"ok": True, "pairs": [{"run_id": "rs_u", "arms": {
        "data": {"ok": True, "n": 5, "matured_n": 0},
        "rerank": {"ok": True, "n": 5, "matured_n": 0}}}]}
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: unmat)
    r = ct.rerank_distill_impl(key="x", text="t")
    assert r["ok"] is False and "matured 门" in r["content"] and not wrote
    # ② 桥抛异常(端点不可达)→ 拒绝(无法核实=不写)
    monkeypatch.setattr(ct, "_rerank_perf_fetch",
                        lambda limit: (_ for _ in ()).throw(RuntimeError("boom")))
    r2 = ct.rerank_distill_impl(key="x", text="t")
    assert r2["ok"] is False and "拒绝蒸馏" in r2["content"] and not wrote
    # ③ 端点 ok:False → 拒绝
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: {"ok": False, "reason": "档案坏"})
    r3 = ct.rerank_distill_impl(key="x", text="t")
    assert r3["ok"] is False and not wrote
    # ④ 混合档案里存在一对完整成熟(两臂 ok 且 matured_n==n>0)→ 放行写入
    mat = {"ok": True, "pairs": [
        {"run_id": "rs_u", "arms": {"data": {"ok": True, "n": 5, "matured_n": 0},
                                    "rerank": {"ok": True, "n": 5, "matured_n": 0}}},
        {"run_id": "rs_m", "arms": {"data": {"ok": True, "n": 3, "matured_n": 3},
                                    "rerank": {"ok": True, "n": 3, "matured_n": 3}}}]}
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: mat)
    r4 = ct.rerank_distill_impl(key="x", text="t")
    assert r4["ok"] is True and wrote["k"] == "行业·x"
    # ⑤ 臂失败(ok:False)的对不算成熟
    armfail = {"ok": True, "pairs": [{"run_id": "rs_f", "arms": {
        "data": {"ok": False, "reason": "无任何可算票"},
        "rerank": {"ok": True, "n": 3, "matured_n": 3}}}]}
    wrote.clear()
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: armfail)
    r5 = ct.rerank_distill_impl(key="x", text="t")
    assert r5["ok"] is False and not wrote
```

同时给现有 `test_rerank_distill_enforces_prefix`(:1788-1799)在 `seen = {}` 之后补一行成熟桩(否则加门后其 r1/r2 会真发 HTTP 被拒):

```python
    monkeypatch.setattr(ct, "_rerank_perf_fetch", lambda limit: {"ok": True, "pairs": [
        {"run_id": "rs_m", "arms": {"data": {"ok": True, "n": 3, "matured_n": 3},
                                    "rerank": {"ok": True, "n": 3, "matured_n": 3}}}]})
```

(其 r3 空 key 用例不受影响——key 校验在门之前。)

- [ ] **Step 2: 跑新测试确认 RED**

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_console_tools.py::test_rerank_distill_matured_gate -q`
Expected: **FAIL**(旧实现无门:①未成熟也 `ok:True`、`wrote` 非空)。

- [ ] **Step 3: 实现门**

`guanlan_v2/console/tools.py` 把 `rerank_distill_impl`(:1093-1100)整体替换为:

```python
def rerank_distill_impl(key: str = "", text: str = "") -> Dict[str, Any]:
    """A/B 结论蒸馏为行业教训入帷幄记忆(key 强制加「行业·」前缀,scope=global)。
    matured 门(2026-07-10):须存在至少一对完整成熟(两臂 ok 且 matured_n==n>0)的
    A/B 档案才放行——horizon 未走完的是未实现收益,蒸馏进永久记忆会经召回反哺固化。"""
    key = (key or "").strip()
    if not key:
        return {"ok": False, "content": "key 必填(蒸馏教训需可检索的主题 key)", "artifact": None}
    if not key.startswith("行业·"):
        key = f"行业·{key}"
    try:
        perf = _rerank_perf_fetch(limit=20)
    except Exception as e:  # noqa: BLE001 — 成熟度无法核实=不写,拒绝显形
        return {"ok": False, "artifact": None,
                "content": f"matured 门:A/B 档案读取失败({e}),无法核实成熟度,拒绝蒸馏"}
    if not perf.get("ok"):
        return {"ok": False, "artifact": None,
                "content": f"matured 门:A/B 档案读取失败({perf.get('reason')}),拒绝蒸馏"}

    def _pair_matured(p: Dict[str, Any]) -> bool:
        arms = p.get("arms") or {}
        return all(bool(a.get("ok")) and isinstance(a.get("n"), int) and a.get("n") > 0
                   and a.get("matured_n") == a.get("n")
                   for a in ((arms.get("data") or {}), (arms.get("rerank") or {})))

    if not any(_pair_matured(p) for p in (perf.get("pairs") or [])):
        return {"ok": False, "artifact": None,
                "content": "matured 门:暂无完整成熟的 A/B 对(horizon 未走完),"
                           "拒绝蒸馏——未熟数字不入永久记忆"}
    return memory_write_impl(text=text, scope="global", key=key)
```

- [ ] **Step 4: 跑测试确认 GREEN**

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_console_tools.py -k rerank_distill -q`
Expected: PASS(新门测试 + 既有前缀测试全绿)。

- [ ] **Step 5: 工具表文案补门说明**

`guanlan_v2/console/tools.py` `ww_rerank_distill` 条目 description(:2645-2648)末尾补一句,改为:

```python
     "description":
         "把 ww_rerank_perf 的真实 A/B 数字蒸馏为行业教训,写入帷幄长期记忆(key 强制加"
         "「行业·」前缀,scope=global,供后续重排上下文注入引用)。绝不凭印象编教训,"
         "必须先调 ww_rerank_perf 看到真实数字再蒸馏;需用户确认。"
         "matured 门:无完整成熟 A/B 对(两臂 matured_n==n)时拒绝蒸馏,未熟数字不入记忆。",
```

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_console_tools.py tests/test_guanlan_mcp.py -q`
Expected: PASS(守护计数不动——无新工具,只改文案)。

- [ ] **Step 6: 全量回归**

Run: `cd /g/guanlan-v2 && python -m pytest -q`
Expected: 基线 1089 + 1 新测试 = **1090 passed**(非本改动既有红如有,诚实标注)。

- [ ] **Step 7: 提交(逐文件 add)**

```bash
cd /g/guanlan-v2
git add guanlan_v2/console/tools.py tests/test_console_tools.py
git commit -m "feat(distill): ww_rerank_distill 加 matured 门——无完整成熟 A/B 对拒绝蒸馏

门=_rerank_perf_fetch(limit=20) 里至少一对两臂 ok 且 matured_n==n>0 才放行写记忆;
档案读取失败/端点失败/无成熟对一律 ok:False 显形拒绝,无 override。防未熟数字
经蒸馏+行业召回反哺固化成永久记忆。key 校验保持门前,confirm 门不动,零新工具。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 开日跑 + 重启 9999 + 真机 e2e(控制器亲手,不派 subagent)

1. **secrets.env 加行**(照 GUANLAN_REGEN_DAILY 先例带注释):`GUANLAN_RERANK_DAILY=1` —— setx 对看门狗代际链无效,本文件是唯一可靠开关位;要停=删行+重启。
2. **重启 9999**(吃 matured 门代码 + 新 env):杀进程,看门狗自愈,验 `/health` 200。
3. **真机 e2e**:
   - matured 门真拒:in-process 调 `rerank_distill_impl(key="测试", text="t")` 对真 9999 → 今天 3 对全未成熟 → **必须被拒**(content 含 `matured 门`),并验 `var/console/memory.md` 未被写入。
   - 日跑链活性:若当前时刻 ≥18 点,重启后 ≤10 分钟 tick 应触发(`_REGEN_SCHED` 随进程重置)→ 等一个 tick 周期,验 `/screen/rescore/latest` 出现 `note="daily-scheduler"` 新 run + picks 档案新增一对 rerank_ab;若 <18 点则验调度器 enabled 状态即可,首对今晚落。
4. 台账/记忆收尾;合 main。

## Self-Review

**1. 覆盖**:门条件/三拒绝路径/放行/臂失败不算成熟 → Step 1 五用例;key 门前 → 既有 r3 不补桩仍绿;工具文案 → Step 5;守护计数不动 → Step 5 跑 MCP 测;日跑+e2e → Task 2。
**2. 占位符**:无,代码可直接粘贴。
**3. 类型一致**:`_pair_matured` 读 `arms.data/rerank` 的 `ok/n/matured_n` 与 `/seats/basket_perf` rerank_ab 分支实际返回键(compute_basket_perf:56 `n/matured_n`)一致;测试 fake 用同键。
