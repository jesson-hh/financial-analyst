# 挂账三修 Implementation Plan(看门狗代际化 · DL 新鲜度显形 · MCP 研报真执行)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修三个挂账:9999 守护换「代际自轮换检查器」(根治常驻冻死);DL 预测新鲜度容忍窗 + staleness 显形(修静默退纯 LGB);glmcp 研报 background 信封真执行(修假成功红线)。

**Architecture:** 修1 = 短代际 PowerShell 检查器(≤5min 自轮换·WMI 派生继任·Run key 登录引导·server 侧心跳互拉);修2 = `_load_dl_for_date` 加 `max_stale_days` 容忍窗 + `stale_days` 透传 provenance + 徽章显形;修3 = `dispatch_tool` 检测 background 信封 → detached 子进程真跑(report=CLI `financial-analyst report`;etf=python -c 调引擎函数)→ 诚实受理凭证。

**Tech Stack:** PowerShell 5.1(WMI Win32_Process.Create·named mutex)、Python 3.13 + pandas、pytest、React(browser-Babel JSX)。**前置参考**:`docs/superpowers/specs/2026-07-02-ops-hardening-three-fixes-design.md`(§1 机制已按本机实证修正)。

## Global Constraints

- **绝不用计划任务**:本机 Schedule 服务派生进程冻死在 loader init(2026-06-10 实证,见 `scripts/register_watchdog_9999.ps1` 头注);进程派生只用 WMI `Win32_Process.Create` / detached Popen。
- **不打架红线**:不碰并行 WIP 文件(`console/api.py`、`console/tools.py`、`screen/api.py`、`screen/catalog.py`、`cpcv.py`、`model_workflow.py`、`tests/test_console_tools.py`、`tests/test_guanlan_mcp.py`、`tests/test_model_workflow_promote.py`、`tests/test_screen_api.py`)——读可以,写绝不;每次 `git commit` 前 `git branch --show-current` 确认 `main`,不是则**停**。
- **诚实合约**:MCP 返回受理凭证绝不谎称完成;DL stale 全程显形(provenance `stale_days` + 徽章「旧n日」);当日命中路径行为与旧版一致(既有测试全绿守护)。
- **PIT**:容忍窗只取 `eval_date < ld` 的过去预测(零前视);lookahead 判定沿用 cutoff 语义。
- **测试**:从仓根 `G:/guanlan-v2` 跑 `python -m pytest`;引擎路径 = 测试顶 prepend 仓内 `engine/`。修3 新测试放**新文件** `tests/test_glmcp_background.py`(旧 `test_guanlan_mcp.py` 在并行 WIP,只跑不改)。
- **GateGuard**:每文件首次 Write/Edit + 每会话首次 Bash 前报 facts。

---

### Task 1: DL 新鲜度容忍窗(`_load_dl_for_date` + `apply_dl_ensemble`)· TDD

**Files:**
- Modify: `guanlan_v2/strategy/compute/dl_ensemble.py:24-31`(DLSource 加字段)、`:67-97`(_load_dl_for_date)、`:112-156`(apply_dl_ensemble)
- Test: `tests/test_dl_ensemble.py`(改 169-178 行既有 4 元组解包 + 追加 4 测)

**Interfaces:**
- Produces: `_load_dl_for_date(path, ld, score_col="pred_ret_5d", max_stale_days=4) -> (series|None, df|None, cutoff|None, stale_days|None, fail|None)`(**5 元组**,当日命中 `stale_days=0`);`DLSource` 新字段 `max_stale_days: int = 4`;provenance `sources[]` 每源新键 `stale_days`。
- Consumes: 现有 `dl_mix_scores`(不改)。

- [ ] **Step 1: 改既有解包 + 写失败测试** —— `tests/test_dl_ensemble.py`:169-178 的 `test_load_dl_for_date_cutoff_is_scored_dates_own` 中两处 `_, _, cutoff_late, fail = _load_dl_for_date(...)` / `_, _, cutoff_early, _ = ...` 各加一个 `_stale` 位改成 5 元组解包;文件末尾追加:

```python
def _mk_pred(tmp_path, rows):
    """rows: list[(eval_date_str, instrument, pred)] → parquet 路径。"""
    import pandas as pd
    p = str(tmp_path / "dl_pred_x.parquet")
    df = pd.DataFrame(rows, columns=["eval_date", "instrument", "pred_ret_5d"])
    df["eval_date"] = pd.to_datetime(df["eval_date"])
    df.to_parquet(p, index=False)
    return p


def test_load_dl_stale_within_window(tmp_path):
    import pandas as pd
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-06-30", "SH600000", 0.01), ("2026-06-30", "SZ000001", -0.02)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-07-02"))
    assert fail is None and stale == 2                     # 旧 2 自然日,窗内(≤4)
    assert abs(float(s["SH600000"]) - 0.01) < 1e-9 and len(s) == 2   # 用的是最近一期截面


def test_load_dl_stale_beyond_window(tmp_path):
    import pandas as pd
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-06-25", "SH600000", 0.01)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-07-02"))
    assert s is None and "断供" in fail and "7" in fail     # 旧 7 日 > 4 → 诚实断供


def test_load_dl_same_day_stale_zero(tmp_path):
    import pandas as pd
    from guanlan_v2.strategy.compute.dl_ensemble import _load_dl_for_date
    p = _mk_pred(tmp_path, [("2026-07-02", "SH600000", 0.03)])
    s, df, cutoff, stale, fail = _load_dl_for_date(p, pd.Timestamp("2026-07-02"))
    assert fail is None and stale == 0 and abs(float(s["SH600000"]) - 0.03) < 1e-9


def test_apply_dl_ensemble_stale_days_in_sources(tmp_path):
    import pandas as pd
    from guanlan_v2.strategy.compute.dl_ensemble import apply_dl_ensemble, DLSource
    p = _mk_pred(tmp_path, [("2026-06-30", f"SH{600000+k}", 0.001 * k) for k in range(60)])  # 60 只 ≥ MIN_MATCH
    idx = pd.MultiIndex.from_tuples([(f"SH{600000+k}", pd.Timestamp("2026-07-02")) for k in range(60)],
                                    names=["instrument", "datetime"])
    pred = pd.DataFrame({"score": [float(k) for k in range(60)]}, index=idx)
    info = apply_dl_ensemble(pred, pd.Timestamp("2026-07-02"),
                             [DLSource(model_id="x", path=p, weight_mode="fixed", fixed_w=0.3)])
    src = next(s for s in info["sources"] if s["model_id"] == "x")
    assert src["active"] is True and src["stale_days"] == 2 and "旧2日" in src["reason"]
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v`
  Expected: 新 4 测 + 改过的既有测 FAIL(解包 ValueError / 缺 stale_days 键)。

- [ ] **Step 3: 实现** —— `guanlan_v2/strategy/compute/dl_ensemble.py`:

(3a)`DLSource` 加字段:

```python
@dataclass
class DLSource:
    model_id: str
    path: str
    score_col: str = "pred_ret_5d"
    weight_mode: str = "adaptive"          # "adaptive"(按近期 ICIR)| "fixed"
    fixed_w: Optional[float] = None
    max_stale_days: int = 4                # 新鲜度容忍窗(自然日);超窗诚实断供退出
```

(3b)`_load_dl_for_date` 整体替换(签名 + 容忍窗 + 5 元组):

```python
def _load_dl_for_date(path: str, ld: pd.Timestamp, score_col: str = "pred_ret_5d",
                      max_stale_days: int = 4):
    """读 DL 预测 parquet → (当日或容忍窗内最近一期 series, 全表 df, train_cutoff, stale_days, fail)。
    新鲜度容忍:当日缺 → 取窗内(自然日 ≤ max_stale_days)最近一期(过去预测,零前视),
    stale_days 显形;超窗 → 诚实断供退出。当日命中 stale_days=0(行为与旧版一致)。"""
    if not path or not os.path.exists(path):
        return None, None, None, None, "预测文件不存在,退出(离线产出:见 scripts/fincast_predict.py 同款工具)"
    try:
        df = pd.read_parquet(path)
    except Exception as e:  # noqa: BLE001
        return None, None, None, None, f"预测 parquet 读取失败({type(e).__name__}),退出"
    need = {"eval_date", "instrument", score_col}
    if not need.issubset(df.columns):
        try:
            df = df.reset_index()
        except Exception:  # noqa: BLE001
            pass
    if not need.issubset(df.columns):
        return None, None, None, None, f"预测 parquet 缺 {need} 列,退出"
    cutoff = None
    if "train_cutoff" in df.columns and len(df):
        try:
            cutoff = str(pd.Timestamp(df["train_cutoff"].iloc[0]).date())
        except Exception:  # noqa: BLE001
            cutoff = None
    ev = pd.to_datetime(df["eval_date"]).dt.normalize()
    today = pd.Timestamp(ld).normalize()
    sub = df[ev == today]
    stale_days = 0
    if sub.empty:
        past = ev[ev < today]                              # 只看过去(零前视)
        if past.empty:
            return None, df, cutoff, None, f"无 {today.date()} 预测且无更早预测,退出"
        latest = past.max()
        stale_days = int((today - latest).days)
        if stale_days > max_stale_days:
            return None, df, cutoff, None, f"预测断供 {stale_days} 日(>{max_stale_days}),退出"
        sub = df[ev == latest]
    s = sub.set_index("instrument")[score_col]
    s = s[~s.index.duplicated(keep="last")]
    return s, df, cutoff, stale_days, None
```

(3c)`apply_dl_ensemble`:源循环解包 + missing 分支 + meta + 回填,四处:

```python
    for src in sources:
        s, df, cutoff, stale_days, fail = _load_dl_for_date(
            src.path, ld, src.score_col, max_stale_days=getattr(src, "max_stale_days", 4))
        if fail is not None:
            missing.append({"model_id": src.model_id, "active": False, "weight": 0.0,
                            "n_has": 0, "lookahead": None, "stale_days": None, "reason": fail})
            continue
```

meta 行(现有 `meta[src.model_id] = {...}` 处):

```python
        meta[src.model_id] = {"lookahead": look, "fc_icir_recent": icir, "stale_days": stale_days}
```

混合后回填循环(现有 `for s in mix["sources"]:` 块)改为:

```python
    for s in mix["sources"]:
        m = meta.get(s["model_id"], {})
        s["lookahead"] = m.get("lookahead")
        s["fc_icir_recent"] = m.get("fc_icir_recent")
        s["stale_days"] = m.get("stale_days")
        if s.get("active") and (m.get("stale_days") or 0) > 0:
            s["reason"] = f"{s['reason']}·旧{m['stale_days']}日"
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py -v`
  Expected: PASS(原测含改解包 + 新 4 测;当日路径等价由原字节等价测继续守护)。

- [ ] **Step 5: 提交**(先 `git branch --show-current` = main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/strategy/compute/dl_ensemble.py tests/test_dl_ensemble.py
git commit -m "fix(dl-freshness): 预测容忍窗≤4自然日+stale_days显形进provenance(修静默退纯LGB)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: 徽章 staleness 显形 · screen-app.jsx

**Files:**
- Modify: `ui/screen/screen-app.jsx:564-583`(多源徽章路径)+ 选股页 HTML `?v` bump(先 `grep -n "screen-app.jsx?v=" ui/screen/*.html` 取实际文件与当前值)

**Interfaces:**
- Consumes: Task 1 的 provenance `sources[].stale_days`(int|None)与 reason 含「断供」语义(gat 类无文件源 reason=「预测文件不存在」→ 不亮 ⚠,spec §5 决策)。

- [ ] **Step 1: 改多源徽章** —— [screen-app.jsx:567-583] 的 `if (Array.isArray(p.sources))` 块替换为(以现场实际行逐字对齐,未涉 stale 的既有措辞保留):

```jsx
            if (Array.isArray(p.sources)) {
              const act = p.sources.filter(s => s.active);
              if (!act.length) {
                const cut = p.sources.filter(s => /断供/.test(s.reason || ''));   // 曾供过数才算断供
                const why = (p.sources[0] && p.sources[0].reason) || '无当日 DL 预测';
                const label = cut.length ? 'v4 · 纯 LGB ⚠DL断供' : 'v4 · 纯 LGB';
                const tip = '排名口径:纯 LGB(' + why + ')。'
                  + (cut.length ? ' 断供源:' + cut.map(s => s.model_id + '(' + (s.reason || '') + ')').join('、') + '。' : '')
                  + '混入 DL 需离线产出当日预测 parquet。';
                return <span className="mono" title={tip}
                  style={{ fontSize: 10, color: cut.length ? 'var(--paper)' : 'var(--ink-3)', background: cut.length ? 'var(--yin)' : 'transparent', border: cut.length ? 'none' : '1px dashed var(--line)', borderRadius: 5, padding: '2px 7px' }}>{label}</span>;
              }
              const anyLa = act.some(s => s.lookahead === true);
              const srcTxt = s => s.model_id + '(' + (+s.weight).toFixed(2) + ((s.stale_days || 0) > 0 ? '·旧' + s.stale_days + '日' : '') + ')';
              const tip = '排名口径:LGB + DL 多源混合 · w_LGB=' + (+p.w_lgb).toFixed(2)
                + act.map(s => ' + ' + s.model_id + ' w=' + (+s.weight).toFixed(2)
                    + '(' + s.n_has + ' 只匹配'
                    + ((s.stale_days || 0) > 0 ? ' · 预测旧 ' + s.stale_days + ' 自然日(容忍窗内·过去预测零前视)' : '')
                    + (s.lookahead === true ? ' · ⚠含前视' : '') + ')').join('')
                + (anyLa ? ' · ⚠ 该日含模型 look-ahead' : '');
              return <span className="mono" title={tip}
                style={{ fontSize: 10, color: 'var(--paper)', background: 'var(--yin)', borderRadius: 5, padding: '2px 7px' }}>
                v4 · LGB+{act.map(srcTxt).join('+')}{anyLa ? ' ⚠前视' : ''}</span>;
            }
```

- [ ] **Step 2: bump ?v** —— `grep -n "screen-app.jsx?v=" ui/screen/*.html` 找加载行,`?v=N` → `N+1`(Edit)。

- [ ] **Step 3: 冒烟** —— `node -e "const s=require('fs').readFileSync('ui/screen/screen-app.jsx','utf8');console.log('stale render:', s.includes('stale_days'), '| 断供 badge:', s.includes('DL断供'))"`
  Expected: 两个 `true`(真编译 Task 6 served 字节验)。

- [ ] **Step 4: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add ui/screen/screen-app.jsx ui/screen/*.html
git commit -m "feat(dl-freshness): 徽章显形 stale(源·旧n日/⚠DL断供)+bump ?v

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: glmcp 研报 background 真执行 · TDD

**Files:**
- Modify: `guanlan_v2/glmcp/server.py`(加 `_spawn_background_detached` + `dispatch_tool` 后处理)
- Test: Create `tests/test_glmcp_background.py`(新文件·零碰并行 WIP 的 test_guanlan_mcp.py)

**Interfaces:**
- Produces: `_spawn_background_detached(bg: dict) -> str`(受理凭证文本或「暂不支持」;Popen 异常上抛由 dispatch 捕);`dispatch_tool` 对含 `background` 信封的 dict 结果 → content + 凭证。
- Consumes: console impl 信封形 `{"kind": "report"|"etf_report", "code", "name", "asof"}`(console/tools.py:640/1041 只读参照);etf 执行体 = `bt.get_tool("run_etf_report").run(code=, asof=)`(console/api.py:594-598 只读参照)。

- [ ] **Step 1: 写失败测试** —— 新建 `tests/test_glmcp_background.py`:

```python
# tests/test_glmcp_background.py
# glmcp background 信封真执行门禁:detached 子进程真起 + 诚实受理凭证(绝不谎称完成)。
# 新文件(不动并行 WIP 中的 test_guanlan_mcp.py)。
import asyncio
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_ENGINE = _REPO / "engine"
if _ENGINE.is_dir() and "financial_analyst" not in sys.modules:
    sys.path.insert(0, str(_ENGINE))

import guanlan_v2.glmcp.server as G  # noqa: E402


class _FakeProc:
    pid = 12345


def test_spawn_detached_report_builds_cli(monkeypatch):
    calls = {}

    def fake_popen(cmd, **kw):
        calls["cmd"], calls["kw"] = cmd, kw
        return _FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    txt = G._spawn_background_detached({"kind": "report", "code": "SH600000", "name": "x", "asof": None})
    assert "已真启动" in txt and "mcpbg_" in txt and "5-8" in txt
    assert "完成" not in txt                                   # 受理凭证,绝不谎称完成
    assert calls["cmd"][1:3] == ["report", "SH600000"]         # financial-analyst report <code>
    assert "--asof" not in calls["cmd"]
    assert calls["kw"]["creationflags"] & 0x00000008           # DETACHED_PROCESS
    assert str(calls["kw"]["cwd"]).lower().endswith("guanlan-v2")


def test_spawn_detached_report_with_asof(monkeypatch):
    calls = {}
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: calls.update(cmd=cmd) or _FakeProc())
    G._spawn_background_detached({"kind": "report", "code": "SZ000001", "asof": "2026-07-01"})
    assert calls["cmd"][-2:] == ["--asof", "2026-07-01"]


def test_spawn_detached_unknown_kind(monkeypatch):
    monkeypatch.setattr("subprocess.Popen",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应派生")))
    txt = G._spawn_background_detached({"kind": "review", "code": "x"})
    assert "暂不支持" in txt


def test_dispatch_appends_receipt_and_honest_failure(monkeypatch):
    decl = {"name": "ww_report_run", "gated": False, "engine": False,
            "description": "", "inputSchema": {}, "read_only": False, "destructive": False}
    monkeypatch.setattr(G, "_by_name", lambda: {"ww_report_run": decl})
    monkeypatch.setattr(G, "_resolve_impl", lambda d, n: (
        lambda **kw: {"ok": True, "content": "已受理",
                      "background": {"kind": "report", "code": "SH600000", "name": "", "asof": None}}))
    # ① Popen 成功 → content + 凭证
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: _FakeProc())
    out = asyncio.run(G.dispatch_tool("ww_report_run", {}))
    assert "已受理" in out[0].text and "已真启动" in out[0].text
    # ② Popen 失败 → 诚实报错(不吞、不假成功)
    def boom(cmd, **kw):
        raise OSError("exe 不在")
    monkeypatch.setattr("subprocess.Popen", boom)
    out2 = asyncio.run(G.dispatch_tool("ww_report_run", {}))
    assert "后台任务启动失败" in out2[0].text and "已真启动" not in out2[0].text
```

- [ ] **Step 2: 跑确认失败** —— `cd G:/guanlan-v2 && python -m pytest tests/test_glmcp_background.py -v`
  Expected: FAIL(`_spawn_background_detached` 不存在 / dispatch 无凭证)。

- [ ] **Step 3: 实现** —— `guanlan_v2/glmcp/server.py`:

(3a)模块级加(`dispatch_tool` 之前):

```python
def _spawn_background_detached(bg: dict) -> str:
    """background 信封 → detached 子进程真跑(不随 MCP 客户端退出而死)→ 诚实受理凭证。
    console 事件循环外的 MCP 通道没有 _spawn_bg 跑道 —— 此处补齐真执行,修假成功红线。
    kind=report → console 同款 CLI `financial-analyst report`;etf_report → 引擎 run_etf_report。"""
    import shutil
    import subprocess
    import sys as _sys
    import uuid
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    kind = str((bg or {}).get("kind") or "")
    code = str((bg or {}).get("code") or "")
    asof = (bg or {}).get("asof")
    if kind == "report":
        exe = shutil.which("financial-analyst") or r"G:\financial-analyst\.venv\Scripts\financial-analyst.exe"
        cmd = [exe, "report", code] + (["--asof", str(asof)] if asof else [])
    elif kind == "etf_report":
        py = ("import sys; sys.path.insert(0, r'{eng}');"
              "import financial_analyst.buddy.tools as bt;"
              "t = bt.get_tool('run_etf_report');"
              "r = t.run(code={code!r}, asof={asof!r});"
              "sys.exit(0 if not getattr(r, 'is_error', False) else 1)").format(
                  eng=str(repo / "engine"), code=code, asof=asof)
        cmd = [_sys.executable, "-c", py]
    else:
        return f"该后台任务类型 MCP 通道暂不支持:{kind or '(空)'}(请经帷幄 console 执行)"
    job = "mcpbg_" + uuid.uuid4().hex[:8]
    log = repo / "var" / f"mcp_bg_{job}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    with open(log, "ab") as lf:
        subprocess.Popen(cmd, cwd=str(repo), stdout=lf, stderr=subprocess.STDOUT,
                         creationflags=flags)
    return (f"已真启动后台研报(job {job} · {code} · 预计 5-8 分钟 · "
            f"产物落 reports store · 日志 {log})")
```

(3b)`dispatch_tool` 末尾 `return [TextContent(type="text", text=_to_text(result))]` 之前插入:

```python
    if isinstance(result, dict) and result.get("background"):
        try:
            receipt = _spawn_background_detached(result["background"])
        except Exception as e:  # noqa: BLE001 — 诚实失败显形,绝不假成功
            return [TextContent(type="text", text=json.dumps(
                {"error": f"后台任务启动失败: {type(e).__name__}: {e}"}, ensure_ascii=False))]
        base = str(result.get("content") or "")
        return [TextContent(type="text", text=(base + "\n" + receipt).strip())]
```

- [ ] **Step 4: 跑确认通过** —— `cd G:/guanlan-v2 && python -m pytest tests/test_glmcp_background.py -v`
  Expected: PASS。再 `python -m pytest tests/test_guanlan_mcp.py -q`(**只跑不改**)确认不破;若其断言与新行为冲突 → 停报用户,不改 WIP 文件。

- [ ] **Step 5: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/glmcp/server.py tests/test_glmcp_background.py
git commit -m "fix(glmcp): background 信封真执行(detached子进程+诚实受理凭证·修假成功红线)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `check_9999.ps1` 代际检查器 + 注册 + 退役旧看门狗

**Files:**
- Create: `scripts/check_9999.ps1`、`scripts/register_check_9999.ps1`
- Modify: `scripts/watchdog_9999.ps1`(仅头部 deprecated 注记)

**Interfaces:**
- Produces: `var/check_9999.heartbeat`(每循环 touch·Task 5 互拉信号)、`var/check_9999.state`(JSON `{"fails": n}`)、日志 `var/watchdog-9999.log` 带 `[check]` 前缀。
- Consumes: 旧 watchdog 的 server 启动命令行(`G:\financial-analyst\.venv\Scripts\python.exe guanlan_v2\server.py`)与健康口径(`GET /workflow/list`)。

- [ ] **Step 1: 写 `scripts/check_9999.ps1`**:

```powershell
# check_9999.ps1 -- guanlan-v2 backend (:9999) generational health checker.
#
# One GENERATION = up to $GenMinutes of $CycleSec-interval checks, then:
#   release mutex -> spawn successor via WMI Win32_Process.Create -> exit.
# No process lives longer than $GenMinutes => no long-resident freeze surface.
# NEVER Task Scheduler: on this box every Schedule-service child freezes at loader
# init (verified 2026-06-10, see register_watchdog_9999.ps1). WMI-spawned processes
# (parented to WmiPrvSE) are the verified-alive mechanism.
# Bootstrap: HKCU Run key (logon) + server-side heartbeat revive (server.py lifespan).
# State: var\check_9999.state {"fails":n}; heartbeat: var\check_9999.heartbeat.
param([switch]$Once)   # -Once: single check pass, no successor (for tests)

$ErrorActionPreference = 'Continue'
$Repo     = 'G:\guanlan-v2'
$Python   = 'G:\financial-analyst\.venv\Scripts\python.exe'
$ServerPy = Join-Path $Repo 'guanlan_v2\server.py'
$VarDir   = Join-Path $Repo 'var'
$Log      = Join-Path $VarDir 'watchdog-9999.log'
$State    = Join-Path $VarDir 'check_9999.state'
$Heart    = Join-Path $VarDir 'check_9999.heartbeat'
$SrvLog   = Join-Path $VarDir 'server-9999.log'
$Port     = 9999
$Health   = "http://127.0.0.1:$Port/workflow/list"
$GenMinutes = 5; $CycleSec = 30; $HangLimit = 6   # 连败6次(~3分钟)强重启

if (-not (Test-Path $VarDir)) { New-Item -ItemType Directory -Force -Path $VarDir | Out-Null }
function Log([string]$m) {
    try {
        if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 5MB)) {
            $b = "$Log.1"; if (Test-Path $b) { Remove-Item $b -Force }; Move-Item $Log $b -Force }
        Add-Content -Path $Log -Value ('{0} [check] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
    } catch { }
}
function Get-Fails { try { [int](Get-Content $State -Raw | ConvertFrom-Json).fails } catch { 0 } }
function Set-Fails([int]$n) { try { ('{"fails":' + $n + '}') | Set-Content -Path $State -Encoding ascii } catch { } }
function Get-ListenerPids {
    @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique) }
function Test-Health {
    try { $q = [System.Net.WebRequest]::Create($Health); $q.Timeout = 5000; $q.Proxy = $null
          $r = $q.GetResponse(); $c = [int]$r.StatusCode; $r.Close(); return ($c -eq 200)
    } catch { return $false } }
function Stop-Listeners {
    foreach ($p in Get-ListenerPids) {
        try { Log ("killing pid {0} on {1}" -f $p, $Port); Stop-Process -Id $p -Force -ErrorAction Stop }
        catch { Log ("kill {0} failed: {1}" -f $p, $_.Exception.Message) } }
    $dl = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $dl) { if (-not (Get-ListenerPids)) { return $true }; Start-Sleep -Milliseconds 500 }
    return $false }
function Start-Server {
    $cl = '/S /C ""{0}" "{1}" >> "{2}" 2>&1"' -f $Python, $ServerPy, $SrvLog
    $p = Start-Process -FilePath $env:ComSpec -ArgumentList $cl -WorkingDirectory $Repo -WindowStyle Hidden -PassThru
    Log ("server start requested (wrapper pid {0})" -f $p.Id) }

# 代际互斥:拿不到 = 上一代还活着 → 本实例退出(双引导/互拉重复派生无害)
$mtx = New-Object System.Threading.Mutex($false, 'Global\guanlan_v2_9999_check')
if (-not $mtx.WaitOne(0)) { exit 0 }
Log ("generation start (pid {0})" -f $PID)
$deadline = (Get-Date).AddMinutes($GenMinutes)
do {
    try {
        New-Item -ItemType File -Path $Heart -Force | Out-Null       # 心跳(server 侧互拉信号)
        $fails = Get-Fails
        if (-not (Get-ListenerPids)) {
            Log 'no listener -> start server'
            Stop-Listeners | Out-Null; Start-Server; Set-Fails 0
        } elseif (Test-Health) {
            if ($fails -gt 0) { Log 'health recovered' }
            Set-Fails 0
        } else {
            $fails++; Set-Fails $fails
            Log ("health fail {0}/{1}" -f $fails, $HangLimit)
            if ($fails -ge $HangLimit) {
                Log 'hang limit -> force restart'
                Stop-Listeners | Out-Null; Start-Server; Set-Fails 0
            }
        }
    } catch { Log ("check error: {0}" -f $_.Exception.Message) }
    if ($Once) { break }
    Start-Sleep -Seconds $CycleSec
} while ((Get-Date) -lt $deadline)

$mtx.ReleaseMutex()   # 命门:先放锁再派生,否则继任者抢锁失败断链
if (-not $Once) {
    $cl = 'C:\Windows\System32\conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + $PSCommandPath
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $cl }
    Log ("successor spawned rv={0} pid={1}" -f $r.ReturnValue, $r.ProcessId)
}
```

- [ ] **Step 2: 写 `scripts/register_check_9999.ps1`**(镜像旧 register 形制):

```powershell
# register_check_9999.ps1 -- switch guardianship to the generational checker.
# Idempotent. Removes the old resident watchdog (Run key + running instances),
# sets Run key for check_9999.ps1, and starts the first generation via WMI now.
$CheckPs1  = 'G:\guanlan-v2\scripts\check_9999.ps1'
$LaunchCmd = 'C:\Windows\System32\conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File ' + $CheckPs1

# 1) retire old resident watchdog: Run key + live instances (frozen ones hold the old mutex)
try { Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
        -Name 'guanlan-v2-9999-watchdog' -ErrorAction Stop; Write-Output 'old watchdog Run key removed' } catch { }
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*watchdog_9999*' } |
    ForEach-Object { Write-Output ("killing old watchdog pid={0}" -f $_.ProcessId)
                     Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# 2) Run key for the checker (logon bootstrap)
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' `
    -Name 'guanlan-v2-9999-check' -Value $LaunchCmd -Type String
Write-Output 'Run key set (checker starts at user logon)'

# 3) start first generation now via WMI (survives this shell; mutex makes doubles harmless)
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $LaunchCmd }
Write-Output ("checker start via WMI: rv={0} pid={1}" -f $r.ReturnValue, $r.ProcessId)
```

- [ ] **Step 3: 旧 watchdog 标 deprecated** —— `scripts/watchdog_9999.ps1` 注释块顶部加:

```powershell
# [DEPRECATED 2026-07-02] 已被 check_9999.ps1(代际自轮换)+ register_check_9999.ps1 取代:
# 本脚本为常驻循环,在本机必冻死且持全局 mutex 挡新实例(2026-07-02 双端全断现场)。保留仅作历史/回退。
```

- [ ] **Step 4: 单趟冒烟(-Once·真机)**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File G:\guanlan-v2\scripts\check_9999.ps1 -Once
Get-Content G:\guanlan-v2\var\watchdog-9999.log -Tail 3
Test-Path G:\guanlan-v2\var\check_9999.heartbeat
```
Expected: 日志见 `[check] generation start` + 健康分支(9999 在跑则零动作);heartbeat True;`-Once` 不派生继任。

- [ ] **Step 5: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add scripts/check_9999.ps1 scripts/register_check_9999.ps1 scripts/watchdog_9999.ps1
git commit -m "feat(watchdog): 代际自轮换检查器(≤5min代际·WMI派生·零schtasks·旧常驻退役)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: server 侧互拉守望(server.py lifespan)

**Files:**
- Modify: `guanlan_v2/server.py`(启动段加异步守望;先 `grep -n "lifespan\|asynccontextmanager\|add_event_handler\|on_event\|create_task" guanlan_v2/server.py` 定位,跟既有形制——该文件不在并行 WIP)

**Interfaces:**
- Consumes: Task 4 的 `var/check_9999.heartbeat`。
- Produces: 心跳陈旧 >600s → detached 拉起新代际(互拉闭环)。

- [ ] **Step 1: 定位启动段** —— grep 上式,确认 lifespan/startup 形制(已有 gl-mcp lifespan 叠加,跟其挂法)。

- [ ] **Step 2: 加守望** —— 模块级函数 + 启动处 `create_task`:

```python
async def _checker_revive_loop() -> None:
    """互拉守望:检查器心跳(var/check_9999.heartbeat)陈旧 >600s → detached 拉起新代际。
    检查器守 server、server 守检查器;双死才需登录 Run key/人工。绝不用 schtasks(本机冻死)。"""
    import asyncio as _aio
    import subprocess
    import time
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    heart = repo / "var" / "check_9999.heartbeat"
    script = repo / "scripts" / "check_9999.ps1"
    cmd = ["C:\\Windows\\System32\\conhost.exe", "--headless", "powershell.exe",
           "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    while True:
        try:
            stale = (not heart.exists()) or (time.time() - heart.stat().st_mtime > 600)
            if stale and script.exists():
                subprocess.Popen(cmd, creationflags=0x00000008 | 0x00000200)  # DETACHED
                print("[revive] check_9999 心跳陈旧,已拉起新代际", flush=True)
                await _aio.sleep(300)     # 给新代际时间写心跳,防派生风暴
        except Exception:  # noqa: BLE001 — 守望绝不拖垮 server
            pass
        await _aio.sleep(60)
```

- [ ] **Step 3: 冒烟** —— `python -c "import ast; ast.parse(open('guanlan_v2/server.py',encoding='utf-8').read()); print('ast ok')"`;真机:删 heartbeat → 重启 9999 → ≤2 分钟 server 日志见 `[revive]` 且 `var/watchdog-9999.log` 出现新 `generation start`。

- [ ] **Step 4: 提交**(先确认 main)

```bash
cd /g/guanlan-v2 && git branch --show-current
git add guanlan_v2/server.py
git commit -m "feat(watchdog): server 侧互拉守望(检查器心跳陈旧→detached拉新代际)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: 真机集成验证

**Files:** 无(验证)。

- [ ] **Step 1: 单元回归** —— `cd G:/guanlan-v2 && python -m pytest tests/test_dl_ensemble.py tests/test_glmcp_background.py tests/test_lstm_io.py tests/test_fincast_io.py -q` 全绿;`python -m pytest tests/test_guanlan_mcp.py tests/test_screen_api.py -q`(只跑;失败若源于并行 WIP 自身则如实记录不修)。

- [ ] **Step 2: 守护切换真机验** —— 跑 `register_check_9999.ps1` → 杀 9999 监听 → **≤1 分钟自动回来**(日志 `[check] no listener -> start server`);等 ≥6 分钟确认代际轮换(两条 `generation start` + `successor spawned rv=0`);健康期 10 分钟无误杀。

- [ ] **Step 3: DL 显形真机验** —— `python -m guanlan_v2.strategy.compute.regen 2026-07-01` → provenance `sources[].stale_days` 存在(当日已刷 → 0 或 1);POST `/screen/run` → `v4_provenance.sources[]` 含 `stale_days`;served `screen-app.jsx?v=<new>` 含 stale 渲染(HTTP 取字节验)。

- [ ] **Step 4: MCP 真执行真机验** —— 经 `/gl-mcp` 调 `ww_report_run`(小票)→ 文本含「已真启动 · job mcpbg_」;`var/mcp_bg_*.log` 有内容;5-8 分钟后 reports store 出新研报 md。注意该工具若 gated 需 `GUANLAN_MCP_WRITE=1` 环境(不绕门)。

- [ ] **Step 5: 清理 + 记忆** —— 删验证临时文件;更新记忆(watchdog-9999.md 指新机制;dl-ensemble-layer.md 记 stale 显形;guanlan-mcp-server.md 记真执行)+ MEMORY.md 索引。

---

## Self-Review(已对 spec 核对)

- **Spec §1 修1**:Task 4(代际检查器+注册+退役)+ Task 5(互拉守望)——机制按本机实证修正(零 schtasks·WMI 派生·Run key·互拉),spec 已同步。✓
- **Spec §2 修2**:Task 1(容忍窗+5 元组+stale 透传·4 新测+既有解包更新)+ Task 2(徽章·⚠ 仅对 reason 含「断供」的源 → gat 无文件不误报,spec §5 决策落地)。✓
- **Spec §3 修3**:Task 3(_spawn_background_detached+dispatch 后处理·新测试文件·etf 执行体按 console/api.py:594-598 `bt.get_tool("run_etf_report").run(code=, asof=)` 核对)。✓
- **Spec §4 验证**:Task 6。✓
- **不打架**:全部触点不在并行 WIP;WIP 测试只跑不改;每提交前验 branch=main。✓
- **占位扫描**:无 TBD;PS/Python/JSX 全给完整代码;两处 grep 定位(screen html ?v、server.py lifespan 形制)为现场对齐点非占位。✓
- **类型一致**:`_load_dl_for_date → (s, df, cutoff, stale_days, fail)` 5 元组在 Task 1 定义、apply/测试同款;`DLSource.max_stale_days:int=4`;`_spawn_background_detached(bg)->str` Task 3 定义与测试一致;`var/check_9999.heartbeat`/`.state` 路径 Task 4↔5 一致;mutex 名 `Global\guanlan_v2_9999_check` 单一。✓
