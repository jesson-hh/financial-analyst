# 帷幄记忆子系统加固 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让帷幄全局记忆「能记忆长上下文」名副其实——常驻(带 key)偏好永远注入/永不归档,易逝(无 key)笔记走近期窗 + 超阈值自动归档且可经工具召回,并消灭不同 key 被消毒折叠成同一个的跨主题误删。

**Architecture:** 全局记忆按 `(key)` 标签分常驻/易逝两类,**注入优先级 == curator 收敛优先级**。共享行分类逻辑落在中性模块 `curator.py`(`is_keyed_line`/`classify_lines`),由注入(`api._select_memory_lines`)与收敛(`curator.consolidate_memory`)共用,杜绝两处正则漂移。`tools.py` 惰性 import curator 触发收敛(在 `_MEMORY_LOCK` 内,curator 自身不持锁)。

**Tech Stack:** Python 3.13;FastAPI(console 路由);pytest;引擎 fork 经 `tests/conftest.py` 已置于 `sys.path`。

**仓库注记:** 本仓**不是 git 仓库**——所有任务的「Commit」步骤一律替换为「跑全量 `pytest tests/ -q` 确认全绿」的检查点;后端改动经**杀 9999 监听 PID(看门狗 ~10s 自动拉新代码)**生效,真机验证在最后一个任务做。规格见 `docs/superpowers/specs/2026-06-17-weiwo-memory-hardening-design.md`。

---

## File Structure

| 文件 | 职责 | 本计划改动 |
|---|---|---|
| `guanlan_v2/console/curator.py` | 离线记忆收敛 + **共享行分类**(中性,不依赖 tools/api) | 加 `_KEYED_RE`/`is_keyed_line`/`classify_lines`;`consolidate_memory` 改常驻感知 |
| `guanlan_v2/console/tools.py` | 工具 impl + 记忆写/读 | #2 收窄 key 消毒;`memory_write_impl` 惰性触发 curator;`memory_read_impl` 读 archive;常量 `_ARCHIVE_PATH`/`_CURATOR_TRIGGER_LINES` |
| `guanlan_v2/console/api.py` | 轮编排 + 记忆注入 | `_memory_block` 重写为结构化注入 + `_select_memory_lines`/`_tail_lines` + 注入预算常量 |
| `tests/test_curator.py` | curator 单测 | 加常驻感知用例 |
| `tests/test_console_tools.py` | 工具单测 | 加 #2、触发收敛、读 archive 用例 |
| `tests/test_console_api.py` | 路由/注入单测 | 加结构化注入用例 |

任务顺序:Task 1(curator 分类+常驻感知,被后续依赖)→ Task 2(#2 独立小修)→ Task 3(触发接线,依赖 1)→ Task 4(读 archive,用 Task 3 常量)→ Task 5(结构化注入,依赖 1)→ Task 6(全量+真机证据)。

---

### Task 1: curator 共享行分类 + 常驻感知收敛

**Files:**
- Modify: `guanlan_v2/console/curator.py`(整文件替换)
- Test: `tests/test_curator.py`(追加 2 个新用例;现有 4 个保持绿)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_curator.py` 末尾)

```python
def test_consolidate_keeps_all_keyed(tmp_path):
    """常驻(带 key)行永不归档;只归档最旧的易逝行,保留行按原始顺序。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text(
        "- [2026-06-01] (pool) 常驻A\n"
        "- [2026-06-02] 易逝1\n"
        "- [2026-06-03] (freq) 常驻B\n"
        "- [2026-06-04] 易逝2\n"
        "- [2026-06-05] 易逝3\n", encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=3)
    assert r["ok"] is True and r["archived"] == 2
    body = mem.read_text(encoding="utf-8")
    assert "常驻A" in body and "常驻B" in body          # 两条 keyed 全留
    assert "易逝3" in body                               # 最新易逝留
    arch_body = arch.read_text(encoding="utf-8")
    assert "易逝1" in arch_body and "易逝2" in arch_body  # 最旧两条易逝归档
    assert "常驻A" not in arch_body                       # keyed 绝不进归档


def test_consolidate_keyed_only_never_archives(tmp_path):
    """边界:全是常驻行且超 max_lines → 不归档(keyed 永不丢),archived=0、archive 不创建。"""
    from guanlan_v2.console.curator import consolidate_memory
    mem = tmp_path / "memory.md"; arch = tmp_path / "memory.archive.md"
    mem.write_text(
        "- [2026-06-01] (k1) a\n- [2026-06-02] (k2) b\n- [2026-06-03] (k3) c\n",
        encoding="utf-8")
    r = consolidate_memory(mem, arch, max_lines=2)
    assert r["ok"] is True and r["archived"] == 0
    body = mem.read_text(encoding="utf-8")
    assert "(k1)" in body and "(k2)" in body and "(k3)" in body
    assert not arch.exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_curator.py -v`
Expected: 新两条 FAIL(现 `consolidate_memory` 非常驻感知,会把 keyed 行也归档/或 archived 计数不符);现有 4 条仍 PASS。

- [ ] **Step 3: 整文件替换 `guanlan_v2/console/curator.py`**

```python
"""离线记忆 Curator + 共享行分类。

行分类(is_keyed_line/classify_lines)是注入(api._select_memory_lines)与收敛
(consolidate_memory)的共用基元,落在本中性模块杜绝两处正则漂移;本模块不依赖
tools/api(tools 惰性 import 本模块触发收敛,故反向 import 会成环)。

consolidate_memory 常驻感知:带 (key) 的常驻行永不归档/永远保留;只把溢出的最旧
易逝(无 key)行归档(不物理删,可恢复)。纯函数、无 LLM、不持锁(调用方在
_MEMORY_LOCK 内调)。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

_KEYED_RE = re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \([^)]+\) ")


def is_keyed_line(line: str) -> bool:
    """行是否带 (key) 标签 = 常驻偏好(永不归档、每轮全量注入)。"""
    return bool(_KEYED_RE.match(line))


def classify_lines(text: str) -> Tuple[List[str], List[str]]:
    """按行分类 → (keyed 常驻, unkeyed 易逝),保序、跳过空行。"""
    keyed: List[str] = []
    unkeyed: List[str] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        (keyed if is_keyed_line(ln) else unkeyed).append(ln)
    return keyed, unkeyed


def consolidate_memory(mem_path: Path, archive_path: Path, max_lines: int = 120) -> Dict[str, Any]:
    """常驻感知收敛:keyed 行永不归档;溢出的最旧 unkeyed 行归档(不物理删,可恢复)。
    保留行按原始顺序写回。注:合并时顺带剔除空行(压紧格式)。无溢出则不动文件、不建 archive。"""
    try:
        if not mem_path.exists():
            return {"ok": True, "archived": 0, "kept": 0, "reason": "无记忆文件"}
        lines = [ln for ln in mem_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) <= max_lines:
            return {"ok": True, "archived": 0, "kept": len(lines)}
        unkeyed_idx = [i for i, ln in enumerate(lines) if not is_keyed_line(ln)]
        keyed_n = len(lines) - len(unkeyed_idx)
        keep_unkeyed_n = max(0, max_lines - keyed_n)
        archived_idx = set(unkeyed_idx[:max(0, len(unkeyed_idx) - keep_unkeyed_n)])
        if not archived_idx:                       # 无可归档(如全 keyed)→ noop,不动文件
            return {"ok": True, "archived": 0, "kept": len(lines)}
        archived = [lines[i] for i in sorted(archived_idx)]
        kept = [ln for i, ln in enumerate(lines) if i not in archived_idx]
        stamp = datetime.now().isoformat(timespec="seconds")
        with archive_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## 归档于 {stamp}\n" + "\n".join(archived) + "\n")
        mem_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return {"ok": True, "archived": len(archived), "kept": len(kept)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_curator.py -v`
Expected: 全部 PASS(新 2 条 + 旧 4 条;旧 4 条因 keyed_n=0 退化为原行为故不变)。

- [ ] **Step 5: 检查点**

Run: `pytest tests/test_curator.py tests/test_console_tools.py tests/test_console_api.py -q`
Expected: 全绿(本任务未碰 tools/api,确认无连带破坏)。

---

### Task 2: #2 收窄 key 消毒(消灭跨主题碰撞误删)

**Files:**
- Modify: `guanlan_v2/console/tools.py:480-481`(`memory_write_impl` 内 key 消毒)
- Test: `tests/test_console_tools.py`(追加 3 个用例)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_tools.py` 末尾)

```python
def test_memory_write_distinct_punctuation_keys_no_collision(tmp_path, monkeypatch):
    """#2:仅标点不同的 key 不再被消毒折叠成同一个 → 两主题各留各的,不互删。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "m.md")
    ct.memory_write_impl(text="主题A", scope="global", key="a.b")
    ct.memory_write_impl(text="主题B", scope="global", key="a/b")
    body = (tmp_path / "m.md").read_text(encoding="utf-8")
    assert "主题A" in body and "主题B" in body
    assert "(a.b)" in body and "(a/b)" in body


def test_memory_write_key_strips_only_format_breakers(tmp_path, monkeypatch):
    """#2:只剔除会破坏 (key) 标签的字符(括号/方括号/换行),其余保留。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "m.md")
    ct.memory_write_impl(text="t", scope="global", key="risk(x)[y]")
    body = (tmp_path / "m.md").read_text(encoding="utf-8")
    assert "(riskxy)" in body          # 括号方括号被剔,其余字符保留


def test_memory_write_empty_sanitized_key_falls_back_to_no_key(tmp_path, monkeypatch):
    """#2 边界:key 全是被剔字符 → 消毒后为空 → 当作无 key(纯追加不收敛)。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "m.md")
    res = ct.memory_write_impl(text="内容", scope="global", key="()[]")
    assert res["ok"] is True
    body = (tmp_path / "m.md").read_text(encoding="utf-8")
    assert "内容" in body and "() " not in body and "(  ) " not in body
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_tools.py -k "punctuation_keys_no_collision or strips_only_format or empty_sanitized_key" -v`
Expected: `no_collision` FAIL(现 `a.b`/`a/b` 都消毒成 `ab` → 第二条删掉第一条);其余两条以实跑为准,作为回归锁保留。

- [ ] **Step 3: 改 key 消毒**(`guanlan_v2/console/tools.py:480-481`)

把:

```python
    # key 消毒:只留中日文/词字符/连字符下划线 → 杜绝 ')' 等致畸形标签/跨 key 碰撞误删。
    if key:
        key = _re.sub(r"[^\w一-鿿\-]", "", key)
```

改为:

```python
    # key 消毒:只剔除会破坏 `(key)` 标签格式/行匹配的字符(圆括号/方括号/换行),其余
    # (. / : 空格 ; 等)一律保留 → 仅标点不同的 key 不再被折叠成同一个而跨主题误删(#2)。
    # 匹配侧 re.escape(key) 已足够中和残余正则元字符;消毒后为空则下方按无 key 处理。
    if key:
        key = _re.sub(r"[\[\]()\r\n]", "", key).strip()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_console_tools.py -k "memory_write" -v`
Expected: 全部 PASS,含新 3 条与既有 `test_memory_write_replace_key_converges`(key=`池子偏好` 无格式破坏字符,不受影响)、`test_memory_write_key_replace_anchored_no_false_delete`(key=`测试键`,不受影响)。

- [ ] **Step 5: 检查点**

Run: `pytest tests/test_console_tools.py -q`
Expected: 全绿。

---

### Task 3: curator 触发接线(写入超阈值自动收敛,锁内)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(`_MEMORY_PATH` 附近加常量;`memory_write_impl` 落盘块内加触发)
- Test: `tests/test_console_tools.py`(追加 2 个用例)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_tools.py` 末尾)

```python
def test_memory_write_triggers_curator_over_threshold(tmp_path, monkeypatch):
    """写到超阈值 → 自动收敛:主文件有界、archive 生成,最新留存、最旧归档。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "m.md")
    monkeypatch.setattr(ct, "_ARCHIVE_PATH", tmp_path / "m.archive.md")
    monkeypatch.setattr(ct, "_CURATOR_TRIGGER_LINES", 5)
    for i in range(7):
        assert ct.memory_write_impl(text=f"note-{i}", scope="global")["ok"]
    mem = (tmp_path / "m.md").read_text(encoding="utf-8")
    arch = (tmp_path / "m.archive.md")
    assert len([l for l in mem.splitlines() if l.strip()]) <= 5   # 主文件收敛到阈值内
    assert "note-6" in mem                                        # 最新留存
    assert arch.exists() and "note-0" in arch.read_text(encoding="utf-8")  # 最旧归档


def test_memory_write_curator_concurrent_no_loss(tmp_path, monkeypatch):
    """锁内触发 + 完整性:并发写 60 条且持续超阈值 → 主文件 + archive 合计无丢失。"""
    import threading
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "m.md")
    monkeypatch.setattr(ct, "_ARCHIVE_PATH", tmp_path / "m.archive.md")
    monkeypatch.setattr(ct, "_CURATOR_TRIGGER_LINES", 10)

    def w(tag):
        for i in range(30):
            assert ct.memory_write_impl(text=f"{tag}-{i}", scope="global")["ok"]

    t1 = threading.Thread(target=w, args=("a",)); t2 = threading.Thread(target=w, args=("b",))
    t1.start(); t2.start(); t1.join(); t2.join()
    mem_lines = [l for l in (tmp_path / "m.md").read_text(encoding="utf-8").splitlines() if l.strip()]
    arch_path = (tmp_path / "m.archive.md")
    arch_notes = [l for l in arch_path.read_text(encoding="utf-8").splitlines()
                  if l.startswith("- ")] if arch_path.exists() else []
    assert len(mem_lines) + len(arch_notes) == 60   # 零丢失(锁串行 + 归档不删)
    assert len(mem_lines) <= 10                      # 主文件始终有界
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_tools.py -k "triggers_curator or curator_concurrent_no_loss" -v`
Expected: FAIL(`_ARCHIVE_PATH`/`_CURATOR_TRIGGER_LINES` 属性不存在 → AttributeError;触发逻辑未接)。

- [ ] **Step 3: 加常量**(`guanlan_v2/console/tools.py`,在 `_MEMORY_MAX_LINE = 280`(:42)下方)

```python
# 阶段2 收敛接线:全局记忆归档文件 + 自动收敛触发阈值(行数)。memory_write_impl 写 global
# 超此行数即在 _MEMORY_LOCK 内调 curator.consolidate_memory(常驻 keyed 永不归档)。
_ARCHIVE_PATH = _MEMORY_PATH.parent / "memory.archive.md"
_CURATOR_TRIGGER_LINES = 120
```

- [ ] **Step 4: 在落盘块内加触发**(`guanlan_v2/console/tools.py`,`memory_write_impl` 的 `with path.open("a", ...)` 之后、仍在 `with _MEMORY_LOCK:` 块内)

当前落盘块(tools.py:496-502)为:

```python
            if key and scope == "global" and path.exists():
                _pat = _re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \(" + _re.escape(key) + r"\) ")
                old = path.read_text(encoding="utf-8").splitlines()
                kept = [ln for ln in old if not _pat.match(ln)]
                path.write_text(("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
```

在 `f.write(line)` 之后、`with _MEMORY_LOCK:` 块仍持锁处追加:

```python
            with path.open("a", encoding="utf-8") as f:
                f.write(line)
            # 阶段2 收敛接线:仅 global,超阈值在同一 _MEMORY_LOCK 内调 curator(其自身不持锁,
            # 单次持锁安全;杜绝与复盘 fork 竞争)。收敛失败不影响写入成败(写已落盘)。
            if scope == "global":
                try:
                    n = sum(1 for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
                    if n > _CURATOR_TRIGGER_LINES:
                        from guanlan_v2.console.curator import consolidate_memory
                        consolidate_memory(path, _ARCHIVE_PATH, max_lines=_CURATOR_TRIGGER_LINES)
                except Exception:  # noqa: BLE001 — 收敛是增强项,失败不挡写入
                    pass
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_console_tools.py -k "triggers_curator or curator_concurrent_no_loss" -v`
Expected: PASS。

- [ ] **Step 6: 检查点**

Run: `pytest tests/test_console_tools.py -q`
Expected: 全绿(含既有 `test_memory_concurrent_append_no_loss` 写 100 条 < 默认 120 不触发 → 仍 100 行)。

---

### Task 4: `memory_read_impl` 读回归档(归档≠失忆)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(`memory_read_impl`,:848-872)
- Test: `tests/test_console_tools.py`(追加 1 个用例)

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_tools.py` 末尾)

```python
def test_memory_read_global_includes_archive(tmp_path, monkeypatch):
    """归档可召回:ww_memory_read(global)正文后附 archive 尾部,标注归档。"""
    import guanlan_v2.console.tools as ct
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "m.md")
    monkeypatch.setattr(ct, "_ARCHIVE_PATH", tmp_path / "m.archive.md")
    (tmp_path / "m.md").write_text("- [2026-06-17] 现存笔记\n", encoding="utf-8")
    (tmp_path / "m.archive.md").write_text(
        "\n## 归档于 2026-06-10T00:00:00\n- [2026-06-01] 已归档笔记\n", encoding="utf-8")
    res = ct.memory_read_impl(scope="global")
    assert res["ok"] is True
    assert "现存笔记" in res["content"] and "已归档笔记" in res["content"]
    assert "归档" in res["content"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_tools.py -k "memory_read_global_includes_archive" -v`
Expected: FAIL(现 `memory_read_impl` 不读 `_ARCHIVE_PATH` → "已归档笔记" 不在 content)。

- [ ] **Step 3: 加 archive 读取**(`guanlan_v2/console/tools.py`,在 `memory_read_impl` 定义前加 helper,并在 global/all 分支拼接)

在 `def memory_read_impl(...)` 上方(`_read_memory_file` 之后)加:

```python
def _archive_tail() -> str:
    """全局归档尾部(_ARCHIVE_PATH 尾 4000),供 memory_read 召回已归档的易逝笔记;无则空串。"""
    try:
        if _ARCHIVE_PATH.exists():
            a = _ARCHIVE_PATH.read_text(encoding="utf-8")
            if a.strip():
                return "\n\n归档(更早易逝笔记,可恢复):\n" + a[-4000:]
    except Exception:  # noqa: BLE001
        pass
    return ""
```

把 global 分支(tools.py:851-856)的 return 改为:

```python
    if scope == "global":
        try:
            body = _MEMORY_PATH.read_text(encoding="utf-8") if _MEMORY_PATH.exists() else ""
        except Exception as e:
            return {"ok": False, "content": f"记忆读取失败: {e}", "artifact": None}
        arch = _archive_tail()
        content = (("帷幄记忆:\n" + body[-4000:]) if body.strip() else "记忆为空。") + arch
        return {"ok": True, "content": content, "artifact": None}
```

把 all 分支(tools.py:864-872)改为(全局段附归档尾部):

```python
    # all:全局 + 本会话两段拼接,各截尾 4000;全局段附归档尾部
    parts: List[str] = []
    g = _read_memory_file(_MEMORY_PATH)
    arch = _archive_tail()
    if g.strip() or arch:
        parts.append((("帷幄记忆(全局):\n" + g[-4000:]) if g.strip() else "帷幄记忆(全局):(空)") + arch)
    s = _read_memory_file(_session_notes_path(sid)) if sid else ""
    if s.strip():
        parts.append("本会话笔记:\n" + s[-4000:])
    return {"ok": True, "content": "\n\n".join(parts) if parts else "记忆为空。", "artifact": None}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_console_tools.py -k "memory_read" -v`
Expected: PASS(新用例 + 既有 memory_read 用例)。

- [ ] **Step 5: 检查点**

Run: `pytest tests/test_console_tools.py -q`
Expected: 全绿。

---

### Task 5: 结构化注入(重写 `_memory_block`)

**Files:**
- Modify: `guanlan_v2/console/api.py`(`_CONDENSE_*`(:182-183)下方加常量;新增 `_select_memory_lines`/`_tail_lines`;重写 `_memory_block`,:186-203)
- Test: `tests/test_console_api.py`(追加 4 个用例);既有 `tests/test_console_tools.py::test_memory_block_contains_both_sections` 保持绿

- [ ] **Step 1: 写失败测试**(追加到 `tests/test_console_api.py` 末尾)

```python
def test_select_memory_lines_keyed_always_present():
    """超大文件:全部常驻(keyed)行必现;易逝只取最近 N 条。"""
    from guanlan_v2.console.api import _select_memory_lines, _INJECT_N_UNKEYED
    text = "- [2026-06-01] (pool) 只看沪深300、月频\n"
    for i in range(40):
        text += f"- [2026-06-02] 临时笔记{i}\n"
    out = _select_memory_lines(text)
    assert "只看沪深300" in out                          # 常驻永远在,文件再大也不掉
    assert "临时笔记39" in out                            # 最新易逝在
    assert "临时笔记0" not in out                         # 最旧易逝掉出近期窗
    kept_unkeyed = [l for l in out.splitlines() if "临时笔记" in l]
    assert len(kept_unkeyed) == _INJECT_N_UNKEYED         # 易逝恰好近期 N 条


def test_select_memory_lines_no_midline_cut():
    """整行截断:输出每行都以 '- ' 开头(无从行中间切出的半行)。"""
    from guanlan_v2.console.api import _select_memory_lines
    text = "".join(f"- [2026-06-02] 笔记{i} {'x'*200}\n" for i in range(40))
    out = _select_memory_lines(text)
    for ln in out.splitlines():
        assert ln.startswith("- "), ln


def test_select_memory_lines_keyed_budget_clamp():
    """常驻总量超预算才丢最旧常驻,并加诚实标注(罕见路径)。"""
    from guanlan_v2.console.api import _select_memory_lines, _INJECT_KEYED_MAX_CHARS
    text = "".join(f"- [2026-06-02] (k{i}) {'y'*270}\n" for i in range(40))  # 40*~290>4000
    out = _select_memory_lines(text)
    assert len(out) <= _INJECT_KEYED_MAX_CHARS + 200      # 受预算钳约束
    assert "超注入预算" in out                            # 诚实标注
    assert "(k39)" in out and "(k0)" not in out           # 留最新常驻、丢最旧


def test_memory_block_large_file_recalls_keyed(tmp_path, monkeypatch):
    """端到端:memory.md 远超旧 2000 窗口时,_memory_block 仍注入老的常驻偏好。"""
    import guanlan_v2.console.tools as ct
    from guanlan_v2.console.api import _memory_block
    monkeypatch.setattr(ct, "_MEMORY_PATH", tmp_path / "memory.md")
    body = "- [2026-06-01] (pool) 只看沪深300、月频\n"
    for i in range(60):
        body += f"- [2026-06-02] 噪声笔记{i} {'z'*40}\n"   # 远超 2000 字符
    (tmp_path / "memory.md").write_text(body, encoding="utf-8")
    blk = _memory_block("cs_none")
    assert "[帷幄记忆·全局]" in blk and "只看沪深300" in blk   # 老的常驻偏好仍被召回
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_console_api.py -k "select_memory_lines or memory_block_large_file" -v`
Expected: FAIL(`_select_memory_lines`/`_INJECT_*` 不存在;旧 `_memory_block` 尾-2000 盲切会丢掉靠前的常驻 `(pool)` 行)。

- [ ] **Step 3: 加注入常量**(`guanlan_v2/console/api.py`,`_CONDENSE_MSGS = ...`(:183)下方)

```python
# 结构化记忆注入预算(组件1):常驻(keyed)行永远全量注入;易逝(unkeyed)取最近 N 条;
# 整行截断绝不从行中间切。预算是最终安全钳(常规不触发)。
_INJECT_N_UNKEYED = 6
_INJECT_N_SESSION = 12
_INJECT_KEYED_MAX_CHARS = 4000
_INJECT_UNKEYED_MAX_CHARS = 1500
```

- [ ] **Step 4: 重写 `_memory_block` 并加两个 helper**(`guanlan_v2/console/api.py:186-203` 整段替换)

```python
def _select_memory_lines(text: str) -> str:
    """全局记忆选择:全部常驻(keyed)+ 最近 _INJECT_N_UNKEYED 条易逝(unkeyed),整行拼接。
    易逝超 _INJECT_UNKEYED_MAX_CHARS 丢最旧易逝;常驻超 _INJECT_KEYED_MAX_CHARS 才丢最旧常驻
    并加诚实标注(常规常驻数远小于此,不触发)。"""
    from guanlan_v2.console.curator import classify_lines
    keyed, unkeyed = classify_lines(text)
    sel_unkeyed = unkeyed[-_INJECT_N_UNKEYED:]
    while len(sel_unkeyed) > 1 and sum(len(l) + 1 for l in sel_unkeyed) > _INJECT_UNKEYED_MAX_CHARS:
        sel_unkeyed.pop(0)
    sel_keyed = list(keyed)
    clamped = False
    while len(sel_keyed) > 1 and sum(len(l) + 1 for l in sel_keyed) > _INJECT_KEYED_MAX_CHARS:
        sel_keyed.pop(0)
        clamped = True
    out: List[str] = list(sel_keyed)
    if clamped:
        out.append("- (更早常驻偏好已超注入预算,可用 ww_memory_read 查看全部)")
    out += sel_unkeyed
    return "\n".join(out)


def _tail_lines(text: str, n: int) -> str:
    """取最近 n 条非空整行(会话笔记用,行级近期窗,不从行中间切)。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _memory_block(sid: str) -> str:
    """轮注入记忆 = 全局(常驻全量 + 易逝近期窗,结构化整行)+ 本会话笔记(近期窗,无文件省略整段)。"""
    from guanlan_v2.console.tools import _MEMORY_PATH, _session_notes_path

    def _read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8") if p.exists() else ""
        except Exception:
            return ""

    parts: List[str] = []
    g = _read(_MEMORY_PATH)
    if g.strip():
        sel = _select_memory_lines(g)
        if sel:
            parts.append(f"[帷幄记忆·全局]\n{sel}")
    s = _read(_session_notes_path(sid))
    if s.strip():
        sel_s = _tail_lines(s, _INJECT_N_SESSION)
        if sel_s:
            parts.append(f"[本会话笔记]\n{sel_s}")
    return ("\n\n".join(parts) + "\n\n") if parts else ""
```

- [ ] **Step 5: 跑测试确认通过**

Run: `pytest tests/test_console_api.py -k "select_memory_lines or memory_block_large_file" -v`
Expected: PASS。

- [ ] **Step 6: 确认既有注入测试不退化**

Run: `pytest tests/test_console_tools.py::test_memory_block_contains_both_sections tests/test_console_api.py -q`
Expected: 全绿(既有用例写 1 条无 key 全局 + 1 条 session,新注入仍含 `csi300`/`300750` 两段)。

---

### Task 6: 全量回归 + 真机证据 + 还原现场

**Files:**
- 无代码改动;验证 + 取证 + 清理。

- [ ] **Step 1: 全量回归**

Run: `pytest tests/ -q`
Expected: 全绿(应 ≥ 之前的 511 + 本计划新增用例数;无新增/删 ww_ 工具,守护计数 26/44 不变)。

- [ ] **Step 2: 重启后端让改动生效**

杀 9999 监听 PID(看门狗 ~10s 自动拉新代码),再 `curl -s -m8 http://127.0.0.1:9999/console/sessions` 确认服务回来。
Expected: 服务恢复、PID 变化。

- [ ] **Step 3: 真机 T1 — 召回天花板已破**

把 `var/console/memory.md` 临时灌入 1 条 keyed 偏好 + 数十条 unkeyed(远超旧 2000 窗口);起全新会话经 `POST /console/send` 问「我设过默认看哪个池子/频率吗」。
Expected: 0 工具调用即答出 keyed 偏好(对比修复前:靠前 keyed 行会被尾-2000 挤出而答不出)。读 `var/console/sessions/<sid>/events.jsonl` 取证。

- [ ] **Step 4: 真机 T2 — curator 触发 + 归档可读**

直调或经会话把 global 记忆写过 120 行;确认 `memory.md` 行数有界、`memory.archive.md` 生成、keyed 行仍在 `memory.md`;再经会话让 agent 调 `ww_memory_read` 确认能读回归档。
Expected: 文件有界 + archive 含被归档的最旧 unkeyed + read 输出含「归档」段。

- [ ] **Step 5: 真机 T3/T4 — 常规召回不退化 + #2**

T3:常规小 `memory.md` 跨会话召回仍正常(对照基线)。T4:经会话或直调写两个标点差异 key(`a.b`/`a/b`),确认 `memory.md` 两条各留各的。
Expected: 召回正常;两 key 不互删。

- [ ] **Step 6: 性能证据**

对比修复前后:主 turn 延迟、注入块字符数(从 events.jsonl 时间戳 / `_memory_block` 输出长度)。
Expected: 结构化注入不显著增加主 turn 延迟(注入块体积与旧 2000 同量级或受预算钳约束)。

- [ ] **Step 7: 还原现场(零残留)**

删除真机测试产生的 session(`DELETE /console/sessions/<sid>`)、还原 `var/console/memory.md` 到测试前的原始内容、删除测试灌入产生的 `var/console/memory.archive.md`(若为测试产物)。确认生产记忆未被测试污染。

---

## Self-Review(对照 spec)

**1. Spec 覆盖**:
- 组件1 结构化注入 → Task 5(`_select_memory_lines`/`_memory_block` + 常量)✓
- 组件2 curator 常驻感知 + 触发 + 归档可读 → Task 1(常驻感知)+ Task 3(触发,锁内)+ Task 4(读 archive)✓
- 组件3 收窄 key 消毒 → Task 2 ✓
- 模块边界(防循环导入:classify_lines 在 curator,tools 惰性 import)→ Task 1 定义 + Task 3 惰性 import + Task 5 从 curator import ✓
- 测试策略(单测 + 真机 + 性能 + 还原)→ Task 1-5 单测 + Task 6 真机/性能/清理 ✓
- 红线(jsonl 原文不改、归档不物理删、_MEMORY_LOCK 串行、诚实标注、26/44 不变)→ 各 Task 代码与 Task 6 守护体现 ✓

**2. Placeholder 扫描**:无 TBD/TODO;每个代码步骤含完整可粘贴代码与确切命令/预期。✓

**3. 类型/命名一致性**:`is_keyed_line`/`classify_lines`(curator,Task 1)被 `consolidate_memory`(Task 1)、`_select_memory_lines`(Task 5)一致引用;`_ARCHIVE_PATH`/`_CURATOR_TRIGGER_LINES`(Task 3 定义)被 `memory_write_impl`(Task 3)、`_archive_tail`(Task 4)一致引用;`_INJECT_*`(Task 5)命名贯穿。✓
