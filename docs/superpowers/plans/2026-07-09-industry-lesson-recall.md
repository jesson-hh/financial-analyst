# 经验反哺按行业相关性召回(方案a)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `read_industry_lessons` 从"全局取尾部 k 条教训"改为"按今天盘面行业双向子串过滤后取近邻",让重排上下文只注入与盘面相关的教训。

**Architecture:** 纯确定性字符串子串匹配,零新依赖、零向量。改一个纯函数 + 其唯一生产调用点 + 同步 5 处单测。函数返回从 `List[str]` 变 `Tuple[List[str], List[str]]`(第二元素 `matched_segs` 供归档透明度)。`run_rerank` 成功 dict 增 `matched_segs` 字段(纯加性)。

**Tech Stack:** Python 3.9+(用 `str.removeprefix`)、pytest、FastAPI(不改路由)。

## Global Constraints

- **展示型红线**:重排结论只进数据榜/A-B 篮,绝不进 picks/正式选股信号/blend/seats。本次只改教训召回过滤,不碰这条边界(血缘审计已 CONFIRMED)。
- **诚实降级**:`board_segs` 空 / 无记忆 / 不可读 / 无命中 → 返回 `([], [])`,不编造、不回填不相关教训(方案 A:严格相关)。
- **UI 只填充**:选股页名次对照列不动;本次不改任何 UI/前端文件。
- **零新依赖**:纯 Python 字符串子串,无 embedding/向量库/新包。
- **TDD**:每个实现步骤前先有失败测试。**频繁提交**。
- **逐文件 git add**:`git add <具体文件>`,**绝不 `git add -A`**。
- **提交尾注**:`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **真机 e2e**:控制器亲手执行、9998 隔离,**绝不碰生产 9999**;不派 subagent 跑 e2e。
- 分支已开:`industry-lesson-recall`(base main,spec 已提交 d3f60bd)。

---

### Task 1: read_industry_lessons 相关性召回 + run_rerank 接线 + 5 处测试同步

单一原子 diff——签名 `List→Tuple` 与新增位置参 `board_segs` 会同时打断唯一调用者和 5 处测试,必须同步落地。TDD 顺序:先写新纯函数测试(红)→ 实现纯函数(绿)→ 改调用者(旧桩红)→ 改测试桩(绿)→ 全量。

**Files:**
- Modify: `guanlan_v2/screen/rerank.py:12`(typing 增 `Set`)、`:55-67`(纯函数重写)、`:122-124`(调用点)、`:141-145`(成功 dict 增字段)
- Test: `tests/test_screen_rerank.py:30-47`(纯函数测试重写/更新 + 新增一条)、`:83/92/111`(monkeypatch 桩)、`:105-122`(成功 schema 断言)

**Interfaces:**
- Produces:
  - `read_industry_lessons(board_segs: Set[str], k: int = 5) -> Tuple[List[str], List[str]]` — 返回 `(lessons, matched_segs)`;`lessons` 元素格式 `"(行业·XXX) 正文"`;`matched_segs` 去重升序;任何空/不可读/无命中 → `([], [])`。
  - `run_rerank(rows, market)` 成功 dict 键集从 `{ok, model, overall, lessons_injected, board_snapshot, elapsed_sec, rows}` 增为并含 `matched_segs`(`List[str]`,升序)。
- Consumes(不变):`_LESSON_PAT`、`_MEMORY_PATH`(lazy import,call 时读=可被 monkeypatch)、`build_context_pack(ranked, board, market, lessons)`。

---

- [ ] **Step 1: 写失败测试(纯函数,新签名/新行为)**

在 `tests/test_screen_rerank.py`:**替换**现有 `test_read_lessons_filters_prefix_and_tail`(第 30-41 行)为下方语义重写版(召回算法本身变了,期望值按新过滤逻辑重推),**紧接其后新增** `test_read_lessons_bidirectional_and_strict`,并**替换** `test_read_lessons_missing_file_returns_empty`(第 44-47 行):

```python
def test_read_lessons_filters_by_board_and_tail(tmp_path, monkeypatch):
    p = tmp_path / "memory.md"
    lines = ["- [2026-07-01] (研究·某目标) 因子教训",
             "- [2026-07-02] (行业·光芯片) 教训A",
             "普通行不带key",
             "- [2026-07-03] (行业·情绪) 教训B",
             "- [2026-07-04] (行业·风格) 教训C"]
    p.write_text("\n".join(lines), encoding="utf-8")
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", p)
    # 盘面含 光芯片/情绪/风格;研究· 前缀非行业教训被排除;命中三条取尾部 k=2 保序
    lessons, matched = rk.read_industry_lessons({"光芯片", "情绪", "风格"}, k=2)
    assert lessons == ["(行业·情绪) 教训B", "(行业·风格) 教训C"]
    assert set(matched) == {"情绪", "风格"} and matched == sorted(matched)  # 只反映保留的 k 条,去重升序


def test_read_lessons_bidirectional_and_strict(tmp_path, monkeypatch):
    p = tmp_path / "memory.md"
    lines = ["- [2026-07-01] (行业·光芯片顺风) 教训X",   # seg 光芯片 ⊂ key(seg-in-key)
             "- [2026-07-02] (行业·半导体) 教训Y",         # key 半导体 ⊂ seg 半导体材料(key-in-seg)
             "- [2026-07-03] (行业·消费) 教训Z",           # 无重叠 → 严格不命中
             "- [2026-07-04] (行业·) 空key正文"]          # 空 key → 跳过,不空串全命中
    p.write_text("\n".join(lines), encoding="utf-8")
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", p)
    lessons, matched = rk.read_industry_lessons({"光芯片", "半导体材料"}, k=5)
    assert lessons == ["(行业·光芯片顺风) 教训X", "(行业·半导体) 教训Y"]  # 双向子串命中,消费/严格未命中
    assert set(matched) == {"光芯片", "半导体材料"} and matched == sorted(matched)
    assert rk.read_industry_lessons(set(), k=5) == ([], [])            # 空 board → 严格空


def test_read_lessons_missing_file_returns_empty(tmp_path, monkeypatch):
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "nope.md")
    assert rk.read_industry_lessons({"光芯片"}) == ([], [])            # 不可读 → ([], [])
```

- [ ] **Step 2: 跑新纯函数测试,确认失败**

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_screen_rerank.py::test_read_lessons_filters_by_board_and_tail tests/test_screen_rerank.py::test_read_lessons_bidirectional_and_strict tests/test_screen_rerank.py::test_read_lessons_missing_file_returns_empty -q`
Expected: FAIL —— 旧 `read_industry_lessons(k=5)->List` 收到位置参 `board_segs` 或被断言成元组,`TypeError`/`AssertionError`。

- [ ] **Step 3: 实现新纯函数 + Set import**

在 `guanlan_v2/screen/rerank.py` 第 12 行 typing import 增 `Set`:

```python
from typing import Any, Dict, List, Optional, Set, Tuple
```

**替换**第 55-67 行整个 `read_industry_lessons` 为:

```python
def read_industry_lessons(board_segs: Set[str], k: int = 5) -> Tuple[List[str], List[str]]:
    """按今天盘面行业相关性召回帷幄「行业·」keyed 教训。

    命中 = 某盘面 seg 与教训 key(去『行业·』前缀、非空)双向子串相含(容 seg⊂key 与 key⊂seg)。
    返回 (lessons, matched_segs);board_segs 空/无记忆/不可读/无命中 → ([], []) 诚实降级不回填。
    lessons 保持既有格式 "(行业·XXX) 正文";matched_segs = 被保留的 k 条命中到的盘面 seg 去重升序。"""
    segs = {s.strip() for s in (board_segs or set()) if s and s.strip()}
    if not segs:
        return [], []
    try:
        from guanlan_v2.console.tools import _MEMORY_PATH
        lines = _MEMORY_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001 — 无/不可读记忆诚实降级,绝不挡重排
        return [], []
    hits: List[Tuple[str, str]] = []          # (key_去前缀, 格式化行)
    for ln in lines:
        m = _LESSON_PAT.match(ln.strip())
        if not m:
            continue
        key = m.group(1).removeprefix("行业·").strip()
        if not key:                            # 空 key(如「行业·」)跳过,防空串全命中
            continue
        if any((s in key) or (key in s) for s in segs):
            hits.append((key, f"({m.group(1)}) {m.group(2)}"))
    kept = hits[-max(0, int(k)):] if k else []
    lessons = [line for _, line in kept]
    matched = sorted({s for key, _ in kept for s in segs if (s in key) or (key in s)})
    return lessons, matched
```

- [ ] **Step 4: 跑纯函数测试,确认通过**

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_screen_rerank.py::test_read_lessons_filters_by_board_and_tail tests/test_screen_rerank.py::test_read_lessons_bidirectional_and_strict tests/test_screen_rerank.py::test_read_lessons_missing_file_returns_empty -q`
Expected: PASS(3 passed)。

- [ ] **Step 5: 改唯一生产调用者 run_rerank**

在 `guanlan_v2/screen/rerank.py` `run_rerank` 内,**替换**现第 122-124 行:

```python
        lessons = read_industry_lessons(k=5)
        ranked = [dict(r, rank=i + 1) for i, r in enumerate(rows)]
        pack = build_context_pack(ranked, board, market, lessons)
```

为(把 `ranked` 提到召回前,从 `chain.seg_name` 拼 `board_segs`,`chain=None` 票不贡献,解包元组):

```python
        ranked = [dict(r, rank=i + 1) for i, r in enumerate(rows)]
        board_segs = {r["chain"]["seg_name"] for r in ranked
                      if isinstance(r.get("chain"), dict) and r["chain"].get("seg_name")}
        lessons, matched_segs = read_industry_lessons(board_segs, k=5)
        pack = build_context_pack(ranked, board, market, lessons)
```

再在成功返回 dict(现第 141-145 行)于 `lessons_injected` 之后**增一行** `matched_segs`:

```python
        return {"ok": True, "model": resp.get("model"),
                "overall": str(data.get("overall") or "")[:200],
                "lessons_injected": len(lessons),
                "matched_segs": sorted(matched_segs),
                "board_snapshot": dict(board.get("snapshot") or {}),
                "elapsed_sec": round(time.time() - t0, 1), "rows": out_rows}
```

- [ ] **Step 6: 跑 run_rerank 相关测试,确认旧桩失败**

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_screen_rerank.py -k "run_rerank" -q`
Expected: FAIL —— `test_run_rerank_llm_fail_is_honest`/`_invalid_order_whole_fail`/`_success_block_schema` 崩:旧桩 `lambda k=5:` 被 `run_rerank` 以 `(board_segs, k=5)` 位置调用 → `TypeError: multiple values for argument 'k'`,且裸 list 无法解包成 `(lessons, matched_segs)`。(`_board_down_refuses` 不涉召回,仍 PASS。)

- [ ] **Step 7: 同步 3 处 monkeypatch 桩 + 成功 schema 断言**

在 `tests/test_screen_rerank.py`:

第 83 行:`monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: [])`
→ `monkeypatch.setattr(rk, "read_industry_lessons", lambda board_segs, k=5: ([], []))`

第 92 行:同上,`lambda k=5: []` → `lambda board_segs, k=5: ([], [])`

第 111 行:`monkeypatch.setattr(rk, "read_industry_lessons", lambda k=5: ["(行业·x) 教训"])`
→ `monkeypatch.setattr(rk, "read_industry_lessons", lambda board_segs, k=5: (["(行业·x) 教训"], ["x"]))`

并在 `test_run_rerank_success_block_schema` 内,现第 117 行 `assert out["lessons_injected"] == 1` 之后**加一行**(桩返回 matched=`["x"]`,`run_rerank` 出 `sorted(["x"])==["x"]`):

```python
    assert out["matched_segs"] == ["x"]
```

- [ ] **Step 8: 跑整文件,确认全通过**

Run: `cd /g/guanlan-v2 && python -m pytest tests/test_screen_rerank.py -q`
Expected: PASS(全绿,含未改的 rescore 级测试——它们经 `_run_rerank_bridge`/fake_rk 绕过真 `run_rerank`,不受本改动影响)。

- [ ] **Step 9: 全量回归**

Run: `cd /g/guanlan-v2 && python -m pytest -q`
Expected: PASS(与改动前同基线绿;重点扫 `tests/test_console_tools.py` 的 rescore 渲染测试仍绿——`matched_segs` 为加性字段,`_rescore_lines` 只按子串/命名键取值)。若有非本改动的既有红(环境漂移),记录并诚实标注,不误算入本任务。

- [ ] **Step 10: 提交(逐文件 add,绝不 -A)**

```bash
cd /g/guanlan-v2
git add guanlan_v2/screen/rerank.py tests/test_screen_rerank.py
git commit -m "feat(rerank): 教训召回按盘面行业双向子串过滤(方案a)+ matched_segs 归档

read_industry_lessons(board_segs,k)->(lessons,matched_segs):只召回与今天盘面
seg 双向子串相含的『行业·』教训、取近邻;空/不可读/无命中→([],[]) 严格不回填。
run_rerank 拼 board_segs 并解包、成功 dict 增 matched_segs(纯加性)。test 同步 5 处。
展示型红线不变、零新依赖。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 真机 e2e(控制器亲手,9998 隔离)

**非 subagent 任务——由控制器本人执行**(标准红线:真机 e2e 亲手、绝不转包、绝不碰生产 9999)。Task 1 合入后由控制器跑,不派实现 subagent。

**验证目标:**
- 9998 隔离端口起后端,`POST /screen/rescore`(带 rerank phase,top_n=5 控 LLM 成本)跑一次。
- 读 `var/rescore_runs.jsonl` 最新 run 的 `rerank` 块:确认含 `matched_segs` 字段、`lessons_injected` 与之口径一致(当下 0 教训 → 两者应为 0/`[]`,即 no-op 现场坐实)。
- 确认重排结论集合校验照旧(rows 票集合 == 输入)、`GET /screen/picks` 默认不含 rerank_ab、选股页名次对照列渲染不变。
- 收尾:杀 9998、还原现场。

**说明:** 因 `var/console/memory.md` 现有 0 条「行业·」教训,e2e 预期 `matched_segs==[]`、`lessons_injected==0`——这正是"当下 no-op"的真机证据;若要额外证明过滤逻辑真的生效,可临时在 9998 隔离记忆里塞一条 `(行业·<某盘面seg>)` 教训跑一次、验其被召回,再撤销(不落生产记忆)。

---

## Self-Review

**1. Spec 覆盖**:①纯函数签名/双向子串/严格不回填/matched_segs 反推 → Task1 Step 3;②调用点拼 board_segs + 解包 + 加字段 → Step 5;③5 处测试(40 语义重写/47 直调/83·92·111 桩)→ Step 1 + Step 7;透明度 matched_segs 入档 → Step 5(字段)+ Task2(真机验档);红线/降级/零依赖 → Global Constraints + 测试覆盖;回归+真机 e2e → Step 9 + Task2。无遗漏。

**2. 占位符扫描**:无 TBD/TODO;每个代码步骤含完整可粘贴代码与精确命令/预期。

**3. 类型一致**:`read_industry_lessons(board_segs:Set[str],k=5)->Tuple[List[str],List[str]]` 在 Step 3 定义,Step 5 调用点解包 `lessons, matched_segs = ...` 一致;桩(Step 7)签名 `lambda board_segs, k=5: (…, …)` 与之匹配;`run_rerank` 成功 dict 增 `matched_segs`(Step 5)被 Step 7 断言 `out["matched_segs"]`——名字/类型贯通一致。`removeprefix` 需 Python 3.9+(仓内现代栈满足)。
