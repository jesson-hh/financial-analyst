# report_v2 语义检索经验注入 (SP-1 接入) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 stocks `report_v2._load_knowledge_pack` 的 4 个大文件全文注入改成"子进程调 fa `knowledge search --json` 语义检索 top-K", 让 sub-agent 拿精准相关经验而非整本书, 每股省 ~30K token, 失败自动 fallback 全文。

**Architecture:** fa 端给 `knowledge search` CLI 加 `--json` 输出 (纯 JSON 到 stdout)。stocks 端 report_v2 用 subprocess 调 base 环境的 fa CLI (零代码耦合, 不 import fa / 不装 chromadb)。检索失败任何环节 → 退回现有全文逻辑。analyst_playbook + rating_system 保留全文。

**Tech Stack:** Python, typer (fa CLI), subprocess (跨环境跨仓), chromadb+BGE (fa 内部, stocks 不直接碰), pytest (fa 端) + 独立烟测脚本 (stocks 端无 pytest)。

**Spec:** `docs/superpowers/specs/2026-06-02-report-v2-semantic-knowledge-injection-design.md`

---

## File Structure

| 文件 | 仓库 | 改动 |
|---|---|---|
| `src/financial_analyst/data/knowledge_index/cli.py` | fa | search_cmd 加 `--json` flag (Task 1) |
| `tests/test_knowledge_cli.py` | fa | 加 `--json` 输出测试 (Task 1) |
| `G:/stocks/strategy/report_v2.py` | stocks | 加 `_kb_build_incremental` / `_kb_retrieve` + 改 `_load_knowledge_pack` + 调用处 (Task 2,4,5) |
| `G:/stocks/strategy/test_kb_retrieve.py` | stocks | 新建独立烟测 (Task 3) |
| `G:/stocks/strategy/agent_prompts.py` | stocks | knowledge_pack 说明加一句 (Task 5) |
| `G:/stocks/strategy/log.md` | stocks | 追加一条 (Task 6) |

**执行顺序**: Task 1 (fa CLI --json, 前置) → Task 2 (_kb_retrieve) → Task 3 (烟测) → Task 4 (_load_knowledge_pack 改造 + 调用处) → Task 5 (build 提到 main + agent_prompts) → Task 6 (集成验证 + log)。

**环境**: fa 测试用 `D:/app/miniconda/python.exe -m pytest`; stocks 脚本用 `D:/app/miniconda/envs/stocks/python.exe`。

---

## Task 1: fa CLI search 加 --json 输出

**Files:**
- Modify: `src/financial_analyst/data/knowledge_index/cli.py` (search_cmd, line 63-88)
- Test: `tests/test_knowledge_cli.py`

- [ ] **Step 1: Write the failing test**

加到 `tests/test_knowledge_cli.py` 末尾:
```python
def test_search_json_output_is_clean_json(tmp_path: Path):
    """--json 输出必须是可解析的纯 JSON 数组 (stdout 不含警告/人类可读文本)."""
    import json
    strat = tmp_path / "strategy"
    strat.mkdir()
    (strat / "factor_insights.md").write_text(
        "## 反转因子\nrev_20 是 A 股最强 alpha, ICIR 0.51.\n", encoding="utf-8")
    idx_root = tmp_path / "idx"
    runner = CliRunner()
    rb = runner.invoke(knowledge_app, ["build", "--strategy-root", str(strat),
                                       "--index-root", str(idx_root)])
    assert rb.exit_code == 0, rb.output
    rs = runner.invoke(knowledge_app, ["search", "反转因子", "--k", "3", "--json",
                                       "--strategy-root", str(strat),
                                       "--index-root", str(idx_root)])
    assert rs.exit_code == 0, rs.output
    data = json.loads(rs.stdout.strip())
    assert isinstance(data, list)
    assert len(data) >= 1
    assert set(data[0].keys()) == {"source", "section", "text", "score"}
    assert "反转因子" in data[0]["section"] or "反转" in data[0]["text"]
```
注: 本测试用真 BgeEmbedder (build + search), 首次需模型已缓存。若本测试环境无模型, 可 `@pytest.mark.slow` 标记 (本仓 addopts 默认 skip slow); 但既然索引已 build 过模型在缓存, 优先不标 slow 直接跑。沿用本文件已有 import (CliRunner / knowledge_app / Path)。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && D:/app/miniconda/python.exe -m pytest tests/test_knowledge_cli.py::test_search_json_output_is_clean_json -v`
Expected: FAIL — `--json` 不是已知 option (typer 报 "No such option") 或 stdout 非 JSON。

- [ ] **Step 3: Add --json flag to search_cmd**

把 `cli.py` 的 `search_cmd` (line 63-88) 整体替换为:
```python
@app.command("search")
def search_cmd(
    query: str = typer.Argument(..., help="Natural-language query (e.g. '反转因子 失效场景')."),
    k: int = typer.Option(5, "--k", help="Number of top results to return."),
    strategy_root: Optional[Path] = typer.Option(
        None, "--strategy-root", help="Override strategy MD root."
    ),
    index_root: Optional[Path] = typer.Option(
        None, "--index-root", help="Override chroma store root."
    ),
    preview: int = typer.Option(240, "--preview", help="Chars of chunk text to show inline."),
    json_out: bool = typer.Option(
        False, "--json", help="Emit machine-readable JSON array to stdout (for programmatic callers)."
    ),
):
    """Search the index. Returns top-K matching chunks with scores."""
    idx = _build_index(strategy_root, index_root)
    results = idx.search(query, k=k)
    if json_out:
        import json as _json
        # 纯 JSON 到 stdout — 调用方 (stocks report_v2) json.loads 解析.
        # 警告/日志走 stderr (BgeEmbedder FutureWarning 等), 不污染 stdout.
        typer.echo(_json.dumps(
            [{"source": r.source, "section": r.section, "text": r.text, "score": r.score}
             for r in results],
            ensure_ascii=False,
        ))
        return
    if not results:
        typer.echo("(no results — try `fa knowledge build` first, or rephrase the query)")
        return
    typer.echo(f"Top {len(results)} results for {query!r}:\n")
    for i, r in enumerate(results, 1):
        body = r.text.strip().replace("\n", " ")
        if len(body) > preview:
            body = body[:preview] + "..."
        typer.echo(f"  {i:>2}. [score={r.score:.4f}]  {r.source}  §{r.section}")
        typer.echo(f"      {body}")
        typer.echo("")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && D:/app/miniconda/python.exe -m pytest tests/test_knowledge_cli.py -v`
Expected: PASS (现有 + 新 test 全过)

- [ ] **Step 5: Verify stdout cleanliness on the real index (manual)**

Run: `cd G:/financial-analyst && D:/app/miniconda/python.exe -m financial_analyst.cli knowledge search "rev_20" --k 3 --json 2>/dev/null | D:/app/miniconda/python.exe -c "import sys,json; d=json.load(sys.stdin); print('OK', len(d), 'hits, keys:', list(d[0].keys()))"`
Expected: `OK 3 hits, keys: ['source', 'section', 'text', 'score']` — 证明 `2>/dev/null` 丢 stderr 后 stdout 是干净 JSON (验证 spec 风险 #3)。
若失败 (stdout 含警告): 查 embedder.py warning 是否走了 print/stdout, 改成 `warnings.warn` 或 logging (走 stderr)。

- [ ] **Step 6: Commit (fa 端)**

```bash
cd G:/financial-analyst && git status --short   # 先确认只动这两文件
git add src/financial_analyst/data/knowledge_index/cli.py tests/test_knowledge_cli.py
git commit -m "feat(knowledge-index): search --json 输出供 report_v2 子进程调用"
```
注: 当前分支可能是 main 或 feat 分支; 只 add 这两文件, 不带别窗口工作 (config/llm.yaml 等)。

---

## Task 2: stocks report_v2 加 _kb_retrieve + _kb_build_incremental

**Files:**
- Modify: `G:/stocks/strategy/report_v2.py` (加 2 函数 + 模块级常量, 插在 `_load_knowledge_pack` 定义 line 999 之前)

> stocks 无 pytest, 本 task 只加函数, Task 3 用独立烟测验证。

- [ ] **Step 1: 加模块级常量 + 两个函数**

确认 `report_v2.py` import 区有 `import os` (有则跳过)。在 `def _load_knowledge_pack` (line 999) **之前**插入:
```python
import subprocess as _subprocess

# SP-1 语义检索: 子进程调 base 环境 fa CLI (stocks 环境无 chromadb/fa, 走子进程零耦合).
_FA_PYTHON = os.environ.get("FA_PYTHON", "D:/app/miniconda/python.exe")
_KB_ENABLED = os.environ.get("REPORT_KB_SEMANTIC", "1") != "0"   # 总开关; =0 退回全文


def _kb_build_incremental() -> None:
    """跑前增量刷新知识索引 (一轮研报由 main() 调 1 次). 失败静默 → search 自然 fallback."""
    if not _KB_ENABLED:
        return
    try:
        _subprocess.run(
            [_FA_PYTHON, "-m", "financial_analyst.cli", "knowledge", "build"],
            capture_output=True, timeout=180,
            env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
        )
    except Exception:
        pass


def _kb_retrieve(query: str, k: int = 8):
    """语义检索 strategy 知识库. 返回 list[dict] | None.

    任何异常 (非0退出 / 坏JSON / 超时 / base python 不存在) → None → 调用方 fallback 全文.
    """
    if not _KB_ENABLED or not query.strip():
        return None
    try:
        r = _subprocess.run(
            [_FA_PYTHON, "-m", "financial_analyst.cli", "knowledge",
             "search", query, "--k", str(k), "--json"],
            capture_output=True, text=True, encoding="utf-8",
            timeout=120, env={**os.environ, "NO_PROXY": "*", "no_proxy": "*"},
        )
        if r.returncode != 0:
            return None
        import json as _json
        hits = _json.loads(r.stdout.strip())
        return hits or None
    except Exception:
        return None
```

- [ ] **Step 2: 语法自检**

Run: `cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe -c "import ast; ast.parse(open('strategy/report_v2.py',encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 3: 不 commit (stocks 非 git, Task 6 统一处理)**

---

## Task 3: stocks 烟测 test_kb_retrieve.py

**Files:**
- Create: `G:/stocks/strategy/test_kb_retrieve.py`

- [ ] **Step 1: 写独立烟测 (mock subprocess, 不真起子进程)**

```python
# G:/stocks/strategy/test_kb_retrieve.py
"""_kb_retrieve 烟测 (独立脚本, 符合 stocks 无 pytest 惯例). 跑: python strategy/test_kb_retrieve.py"""
import os, sys, types, importlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _m():
    return importlib.import_module("strategy.report_v2")


def test_valid_json():
    m = _m()
    fake = types.SimpleNamespace(returncode=0,
        stdout='[{"source":"factor_insights.md","section":"反转","text":"rev_20 强","score":0.49}]')
    with mock.patch.object(m._subprocess, "run", return_value=fake):
        m._KB_ENABLED = True
        hits = m._kb_retrieve("反转因子 风险", k=8)
    assert hits is not None and len(hits) == 1 and hits[0]["source"] == "factor_insights.md", hits
    print("[OK] valid_json")


def test_nonzero_exit_returns_none():
    m = _m()
    fake = types.SimpleNamespace(returncode=1, stdout="")
    with mock.patch.object(m._subprocess, "run", return_value=fake):
        m._KB_ENABLED = True
        assert m._kb_retrieve("x", k=8) is None
    print("[OK] nonzero_exit")


def test_bad_json_returns_none():
    m = _m()
    fake = types.SimpleNamespace(returncode=0, stdout="NOT JSON {{{")
    with mock.patch.object(m._subprocess, "run", return_value=fake):
        m._KB_ENABLED = True
        assert m._kb_retrieve("x", k=8) is None
    print("[OK] bad_json")


def test_timeout_returns_none():
    m = _m()
    def _boom(*a, **k):
        raise m._subprocess.TimeoutExpired(cmd="x", timeout=120)
    with mock.patch.object(m._subprocess, "run", side_effect=_boom):
        m._KB_ENABLED = True
        assert m._kb_retrieve("x", k=8) is None
    print("[OK] timeout")


def test_disabled_returns_none():
    m = _m()
    m._KB_ENABLED = False
    assert m._kb_retrieve("x", k=8) is None
    print("[OK] disabled")


if __name__ == "__main__":
    test_valid_json()
    test_nonzero_exit_returns_none()
    test_bad_json_returns_none()
    test_timeout_returns_none()
    test_disabled_returns_none()
    print("ALL PASS")
```

- [ ] **Step 2: Run smoke test**

Run: `cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe strategy/test_kb_retrieve.py`
Expected: 5 行 `[OK]` + `ALL PASS`
若 import report_v2 因重依赖 (qlib) 失败: 优先确认 stocks 环境 qlib 已装 (一般装了)。仍失败则报 BLOCKED 让控制端定 (可能需把 _kb_retrieve 抽到独立小模块)。

- [ ] **Step 3: 不 commit (Task 6 统一)**

---

## Task 4: 改造 _load_knowledge_pack + 调用处

**Files:**
- Modify: `G:/stocks/strategy/report_v2.py` (`_load_knowledge_pack` line 999-1023; 调用处 line 1632)

- [ ] **Step 1: 改 _load_knowledge_pack 签名 + 检索分支**

把 `_load_knowledge_pack` (line 999-1023) 整体替换为:
```python
def _load_knowledge_pack(code, target_date=None, stock_name="", factor_signals="", extra_query=""):
    """经验注入. SP-1: 4 大杂烩 (pitfalls/factor_insights/sentiment_summary/rules_learned)
    走语义检索 top-8; analyst_playbook + rating_system 保留全文 (思维范本+评级规则必须完整).
    检索失败 → 退回全文 (向后兼容).

    analyst_playbook 九视角思维范本; mainlines_today 主线雷达; retrieved_experience
    是语义检索到的最相关经验片段 (带 source/section 出处)."""
    query = f"{stock_name} {factor_signals} {extra_query} 风险 失效 经验".strip()
    hits = _kb_retrieve(query, k=8)
    base = {
        '_usage': ('判断前必读. analyst_playbook=九视角思维范本 (V1-V9, 必按序扫 + 论点挂锚点); '
                    'morning_brief_today=今日晨会简报 (大盘背景, 直接引用避免重复 WebSearch); '
                    'mainlines_today=今日主线 top 5 + 龙头 + 阶段 (V3/V6 校准); '
                    'retrieved_experience=语义检索到的最相关经验片段 (带 source/section 出处, '
                    '涵盖 pitfalls/factor_insights/sentiment/rules; semantic 模式); '
                    'pitfalls/factor_insights/sentiment_summary/rules_learned=全文 (fallback 模式); '
                    'rating_system=v4评级规则; history=上次研报.'),
        'analyst_playbook':    _safe_read(STRATEGY_DIR / 'analyst_playbook.md', max_chars=22000),
        'rating_system':       _safe_read(STRATEGY_DIR / 'rating_system.md'),
        'morning_brief_today': _load_morning_brief(target_date),
        'mainlines_today':     _load_mainlines_today(target_date),
        'previous_history':    _extract_stock_history(code),
    }
    if hits is None:
        base.update({
            'sentiment_summary': _safe_read(STRATEGY_DIR / 'research' / 'sentiment_summary.md', max_chars=12000),
            'pitfalls':          _safe_read(STRATEGY_DIR / 'pitfalls.md'),
            'factor_insights':   _safe_read(STRATEGY_DIR / 'factor_insights.md'),
            'rules_learned':     _safe_read(STRATEGY_DIR / 'rules_learned.md', max_chars=6000),
            '_kb_mode':          'fulltext_fallback',
        })
    else:
        base['retrieved_experience'] = hits
        base['_kb_mode'] = 'semantic'
    return base
```

- [ ] **Step 2: 改调用处传 stock_name + factor_signals**

`analyze_stock` 内调用处 (line 1632) `analysis_ctx['knowledge_pack'] = _load_knowledge_pack(TARGET)` 改为:
```python
    analysis_ctx['knowledge_pack'] = _load_knowledge_pack(
        TARGET, target_date=None,
        stock_name=analysis_ctx.get('name', name_map.get(TARGET, '')),
        factor_signals=str(analysis_ctx.get('factor_signals', ''))[:200],
    )
```
注: `analysis_ctx` 在此处已填充 (name/factor_signals 都在); `name_map.get(TARGET)` 是 analyze_stock 入参一定可用。若 `analysis_ctx` 无 'factor_signals' 键则取空串 (检索 query 仍含 name + 风险词, 不影响 fallback)。

- [ ] **Step 3: 语法自检**

Run: `cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe -c "import ast; ast.parse(open('strategy/report_v2.py',encoding='utf-8').read()); print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 4: 不 commit (Task 6 统一)**

---

## Task 5: main() 开头调 build + agent_prompts 认 retrieved_experience

**Files:**
- Modify: `G:/stocks/strategy/report_v2.py` (main() 开头)
- Modify: `G:/stocks/strategy/agent_prompts.py` (knowledge_pack 说明段)

- [ ] **Step 1: 定位 main() 并在开头跑一次 build**

Run 定位: `cd G:/stocks && grep -nE "def main|if __name__" strategy/report_v2.py`
在 `main()` 函数开头 (Qlib init 之后、逐股循环之前) 加:
```python
    # SP-1: 一轮研报跑前刷新一次知识索引 (增量, mtime 驱动). 失败静默 → 检索阶段 fallback.
    _kb_build_incremental()
```
若无清晰 main(), 加在 `if __name__ == '__main__':` 块逐股循环之前。

- [ ] **Step 2: 定位 agent_prompts 的 knowledge_pack 说明并加一句**

Run 定位: `cd G:/stocks && grep -nE "knowledge_pack|pitfalls|factor_insights|_KNOWLEDGE" strategy/agent_prompts.py | head`
在该说明条款里加 (随现有风格调措辞):
```
- retrieved_experience: 语义检索到的最相关经验片段 (带 source/section 出处), 优先级等同 pitfalls/factor_insights。
  semantic 模式有此字段; fulltext_fallback 模式则是 pitfalls/factor_insights/sentiment_summary/rules_learned 全文。两种都要会读。
```

- [ ] **Step 3: 语法自检 (两文件)**

Run: `cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe -c "import ast; [ast.parse(open(f,encoding='utf-8').read()) for f in ['strategy/report_v2.py','strategy/agent_prompts.py']]; print('syntax OK')"`
Expected: `syntax OK`

- [ ] **Step 4: 不 commit (Task 6 统一)**

---

## Task 6: 集成验证 + 跨仓合规 + log

**Files:**
- Verify: 真实跑一只股票
- Modify: `G:/stocks/strategy/log.md`

- [ ] **Step 1: 跨仓合规 grep (硬约束)**

Run: `cd G:/stocks && grep -nE "import financial_analyst|from financial_analyst" strategy/report_v2.py strategy/agent_prompts.py strategy/test_kb_retrieve.py || echo "OK 无 fa import (合规)"`
Expected: `OK 无 fa import (合规)` — 证明零代码耦合 (只 subprocess 调 CLI)。
若有命中: 违约, 必须改回 subprocess。

- [ ] **Step 2: 烟测过**

Run: `cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe strategy/test_kb_retrieve.py`
Expected: `ALL PASS`

- [ ] **Step 3: 真实集成验证 — semantic 模式**

Run (跑一只股票, 实际触发子进程检索, 耗时分钟级):
`cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe strategy/report_v2.py SH600519 2>&1 | tail -5`
然后验 _agent_ctx (路径若不同按实际调整):
`D:/app/miniconda/envs/stocks/python.exe -c "import json,io; d=json.load(io.open('strategy/reports/_agent_ctx/SH600519.json',encoding='utf-8')); kp=d['knowledge_pack']; print('_kb_mode=', kp.get('_kb_mode')); re=kp.get('retrieved_experience'); print('retrieved n=', len(re) if re else 0); print('sample=', (re[0]['source'], re[0]['section']) if re else 'N/A')"`
Expected: `_kb_mode= semantic` + `retrieved n= 8` + 合理 source/section。
若 `fulltext_fallback`: 子进程检索失败 — 手动跑 `D:/app/miniconda/python.exe -m financial_analyst.cli knowledge search "贵州茅台 风险" --k 8 --json` 看报错 (多半 base python 路径 / stdout 污染)。

- [ ] **Step 4: 逃生舱验证 — fallback 模式**

Run: `cd G:/stocks && REPORT_KB_SEMANTIC=0 D:/app/miniconda/envs/stocks/python.exe strategy/report_v2.py SH600519 2>&1 | tail -3`
然后验 `_kb_mode= fulltext_fallback` + 有 pitfalls/factor_insights 字段 + 研报正常产出。
Expected: 研报正常 — 证明逃生舱有效 (出问题可一键 REPORT_KB_SEMANTIC=0 退回)。

- [ ] **Step 5: fa 全量回归 (确认 Task 1 没破坏)**

Run: `cd G:/financial-analyst && D:/app/miniconda/python.exe -m pytest tests/test_knowledge_cli.py tests/test_knowledge_chunker.py tests/test_knowledge_indexer.py -q`
Expected: 全 PASS。

- [ ] **Step 6: 追加 strategy/log.md (项目硬规则, 原子前插避多窗口竞争)**

Run:
```bash
cd G:/stocks && D:/app/miniconda/envs/stocks/python.exe -c "
import io
p='strategy/log.md'
entry='- 2026-06-02 **feat | report_v2 接入 SP-1 语义检索经验注入 (knowledge_pack 全文塞→检索top8)**: report_v2._load_knowledge_pack 4大杂烩(pitfalls/factor_insights/sentiment_summary/rules_learned)改子进程调 fa knowledge search --json 语义检索 top-8, playbook+rating保留全文. 每股省~30K token. 跨仓零耦合(subprocess调base环境fa CLI, 不import fa/不装chromadb). 失败分层fallback全文+REPORT_KB_SEMANTIC=0总开关逃生舱. main()开头跑1次增量build保新鲜. fa端cli.py加--json. agent_prompts认retrieved_experience字段. → strategy/report_v2.py / financial-analyst knowledge_index/cli.py'
c=io.open(p,encoding=\"utf-8\").read()
io.open(p,\"w\",encoding=\"utf-8\",newline=\"\").write(entry+chr(10)+c)
print(\"log appended\")
"
```
Expected: `log appended`

- [ ] **Step 7: Commit (fa 端 spec+plan; stocks 非 git 只改文件)**

```bash
cd G:/financial-analyst && git add docs/superpowers/specs/2026-06-02-report-v2-semantic-knowledge-injection-design.md docs/superpowers/plans/2026-06-02-report-v2-semantic-knowledge-injection.md
git commit -m "docs: report_v2 语义检索接入 spec + plan"
```
stocks 端非 git, 改的文件 (report_v2.py / agent_prompts.py / test_kb_retrieve.py / log.md) 留工作区, 不 commit。

---

## Self-Review Checklist (已核对)

- **Spec 覆盖**: --json CLI (Task1) / _kb_retrieve (Task2) / 烟测 (Task3) / _load_knowledge_pack 改造+调用处 (Task4) / build 提到 main + agent_prompts (Task5) / 集成验证 + 逃生舱 + 合规 grep + log (Task6) — 全覆盖 spec 目标与 DoD。
- **类型/签名一致**: `_kb_retrieve(query, k=8) -> list[dict]|None` Task2 定义 / Task3 测 / Task4 调一致; `_load_knowledge_pack(code, target_date, stock_name, factor_signals, extra_query)` 新签名 Task4 定义 + 调用处一致; `_kb_build_incremental()` Task2 定义 Task5 调一致; `_FA_PYTHON`/`_KB_ENABLED`/`_subprocess` Task2 建后续引用一致。
- **占位扫描**: 无 TBD; 每 code step 有完整代码; 每 test step 有完整断言。spec 风险 #5 (_name_of 占位) 在 Task4 解决 (改 stock_name 参数 + name_map.get)。
- **执行注意**: Task1 提交前 `git status` 确认只 add 2 文件 (别窗口 workflow-lab-v2 工作不能带); Task3 若 import report_v2 因 qlib 重依赖失败报 BLOCKED; Task5 main()/agent_prompts 精确位置用 plan 给的 grep 命令定位。
