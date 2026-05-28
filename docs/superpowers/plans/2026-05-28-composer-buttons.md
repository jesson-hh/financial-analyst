# Composer Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the three dead composer toolbar buttons (`⊟上传 / @引用 / ⌗板块`) in the 觀瀾 web UI to real functions: insert a stock/§N reference, insert a 同花顺 concept board, and attach a document whose extracted text is prepended to the prompt.

**Architecture:** Each button at `app.jsx:2108-2110` gets an `onClick` that opens a small popover (mirroring the existing `SlashMenu` at `app.jsx:2129`). `@引用` is frontend-only (reads existing React state). `⌗板块` fetches a new backend `GET /concepts` (reads `concept_ths_index.parquet`). `⊟上传` POSTs the file to a new `POST /upload` (extracts text; csv/txt/md decode, pdf via pypdf), shows an attachment chip, and `send()` prepends the extracted text to the message.

**Tech Stack:** React (babel-standalone in-browser JSX — no JS test framework, frontend verified manually), FastAPI (`buddy/server.py`), pandas/pyarrow (parquet read), `pypdf` (PDF text), `python-multipart` (multipart upload), pytest (backend tests).

**Testing convention:** Backend routes are TDD'd with pytest in `tests/`. Frontend `.jsx` changes have **no automated test harness** (babel compiles in-browser) — each frontend task ends with a manual-verify step + **bumping the `index.html` cache-buster** (else browsers serve stale jsx).

**Branch/commit note:** Per the maintainer, the uncommitted UI layout fixes (header/footer/ToolRow + cache-buster `-3`) ship **together** with this feature — do NOT commit them separately; they ride along in this feature's commits. Never add a `Co-Authored-By` trailer.

---

## File Structure

- **Modify** `src/financial_analyst/ui/app.jsx`
  - `~2108-2110` — replace the static 3-button `.map` with three real buttons (onClick → popovers).
  - `Composer` fn (`~1985`) — add `attachments` state, three popover states, the file `<input>`, the attachment-chip row, and the `send()` prepend logic.
  - Add one `ComposerPopover` helper component (near `SlashMenu`, `~2129`).
- **Modify** `src/financial_analyst/ui/index.html` — bump `?v=` cache-buster (3 script tags).
- **Modify** `src/financial_analyst/buddy/server.py` — add `GET /concepts` and `POST /upload` inside `build_app()`.
- **Modify** `pyproject.toml` — add `pypdf` + `python-multipart` to core deps.
- **Create** `tests/test_concepts_endpoint.py`, `tests/test_upload_endpoint.py`.

Build order: @引用 (Task 1) → 板块 (Tasks 2-3) → 上传 (Tasks 4-5) → deps/verify (Task 6).

---

## Task 1: @引用 popover + shared ComposerPopover (frontend)

**Files:**
- Modify: `src/financial_analyst/ui/app.jsx` (add `ComposerPopover`; wire `@ 引用` button; add popover state in `Composer`)

- [ ] **Step 1: Add the `ComposerPopover` helper component** (place just above `function SlashMenu` at `app.jsx:2129`)

```jsx
// 复用的输入框上方小面板 (镜像 SlashMenu 的视觉). items: [{key,label,sub,onPick}]
function ComposerPopover({ title, items, onClose, emptyHint }) {
  useEffect(() => {
    const h = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 70 }}>
      <div onClick={(e) => e.stopPropagation()} style={{
        position: 'absolute', left: 56, right: 56, bottom: 96, maxHeight: 280, overflowY: 'auto',
        background: 'var(--paper)', border: '1px solid var(--ink)', boxShadow: '0 12px 40px rgba(0,0,0,0.2)'
      }}>
        <div className="mono" style={{ padding: '8px 14px', fontSize: 10, color: 'var(--ink-3)', letterSpacing: '0.2em', borderBottom: '1px solid var(--line-soft)' }}>{title}</div>
        {items.length === 0 && <div className="serif" style={{ padding: 16, fontSize: 12, color: 'var(--ink-3)', fontStyle: 'italic' }}>{emptyHint || '暂无'}</div>}
        {items.map((it) => (
          <div key={it.key} className="hover-row" onClick={() => { it.onPick(); onClose(); }}
            style={{ padding: '8px 14px', display: 'flex', alignItems: 'baseline', gap: 8, cursor: 'pointer', borderBottom: '1px solid var(--line-soft)', whiteSpace: 'nowrap' }}>
            <span className="serif" style={{ fontSize: 13, color: 'var(--ink)' }}>{it.label}</span>
            {it.sub && <span className="mono" style={{ fontSize: 10, color: 'var(--ink-3)' }}>{it.sub}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Add popover state in `Composer`** (after `const [val, setVal] = useState('');` at `app.jsx:1986`)

```jsx
  const [popover, setPopover] = useState(null); // null | 'ref' | 'board' | (upload uses no popover)
  const fileInputRef = useRef(null); // 上传按钮用; 实际 <input> 在 Task 5 渲染 (此前 .click() 安全 no-op)
  const insertText = (t) => { setVal(v => (v ? v.replace(/\s*$/, '') + ' ' : '') + t + ' '); inputRef.current?.focus(); };
```

> NOTE: `fileInputRef` is declared here so the `⊟ 上传` button wired in Step 4 is a safe no-op (`fileInputRef.current?.click()` → null) until Task 5 renders the actual `<input>`. The `@ 引用` popover is functional this task; the `⌗ 板块` popover renders in Task 3 (clicking it before then just toggles state — harmless).

- [ ] **Step 3: Build the @引用 items** (compute inside `Composer`, before `return`)

```jsx
  // @引用 候选: 自选股 (watch quotes) + 当前个股 (context) + 本轮工具结果 §N
  const refItems = (() => {
    const out = [];
    if (context && context.code) out.push({ key: 'ctx', label: context.name, sub: context.code, onPick: () => insertText(`${context.name}（${context.code}）`) });
    (s.watch?.quotes || []).forEach(q => out.push({ key: 'w_' + q.code, label: q.name || q.code, sub: q.code, onPick: () => insertText(`${q.name || q.code}（${q.code}）`) }));
    const sess = s.sessions.find(x => x.id === s.currentSessionId);
    const chain = (sess?.messages || []).filter(m => m.kind === 'chain').flatMap(m => m.chain || []).filter(c => c.status === 'done');
    chain.forEach((c, i) => out.push({ key: 'tool_' + i, label: `§${i + 1} ${c.name}`, sub: (c.cn || ''), onPick: () => insertText(`§${i + 1}`) }));
    return out;
  })();
```

> NOTE: `s.watch.quotes` is the watchlist quote array the UI already polls (see the `/quotes?codes=` poller). If the exact property differs, use whatever array backs the monitoring wall; the row only needs `{name, code}`.

- [ ] **Step 4: Replace the dead button row** at `app.jsx:2108-2110` with real buttons

```jsx
            <span className="hover-pill" onClick={() => fileInputRef.current?.click()}
              style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 7px', border: '1px solid var(--line)', cursor: 'pointer' }}>⊟ 上传</span>
            <span className="hover-pill" onClick={() => setPopover(popover === 'ref' ? null : 'ref')}
              style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 7px', border: '1px solid var(--line)', cursor: 'pointer' }}>@ 引用</span>
            <span className="hover-pill" onClick={() => s.backendUrl && setPopover(popover === 'board' ? null : 'board')}
              title={s.backendUrl ? '选概念板块' : '需连后端'}
              style={{ fontSize: 10, color: 'var(--ink-2)', padding: '3px 7px', border: '1px solid var(--line)', cursor: 'pointer', opacity: s.backendUrl ? 1 : 0.5 }}>⌗ 板块</span>
```

- [ ] **Step 5: Render the @引用 popover** (inside `Composer`'s return, right after the `{showSlash && <SlashMenu .../>}` line at `app.jsx:2082`)

```jsx
      {popover === 'ref' && (
        <ComposerPopover title="引用 · 自选 / 当前股 / 工具结果" items={refItems}
          emptyHint="无可引用项 (先加自选或问一只股)" onClose={() => setPopover(null)} />
      )}
```

- [ ] **Step 6: Manual verify + cache-buster**

Bump `?v=` in `ui/index.html` (e.g. `20260528-3` → `20260528-4`, all 3 script tags). Then: reload `http://127.0.0.1:5173/`, click `@ 引用` → popover lists current stock / watchlist / §N → clicking inserts e.g. `宁德时代（SZ300750）` into the textarea. ESC / click-outside closes.

- [ ] **Step 7: Commit**

```bash
git add src/financial_analyst/ui/app.jsx src/financial_analyst/ui/index.html
git commit -m "feat(ui): @引用 button — insert stock/§N reference into composer"
```

---

## Task 2: GET /concepts backend endpoint (TDD)

**Files:**
- Create: `tests/test_concepts_endpoint.py`
- Modify: `src/financial_analyst/buddy/server.py` (add route in `build_app()`, near `/quotes` ~line 773)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_concepts_endpoint.py
import pandas as pd
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


def _client(monkeypatch, tmp_path, with_data=True):
    if with_data:
        pd.DataFrame({"concept_code": ["886001", "886002"],
                      "concept_name": ["CPO", "机器人"]}).to_parquet(tmp_path / "concept_ths_index.parquet")

    class _P:
        parquet_root = tmp_path
    monkeypatch.setattr("financial_analyst.data.paths.get_data_paths", lambda: _P())
    return TestClient(build_app())


def test_concepts_lists_boards(monkeypatch, tmp_path):
    r = _client(monkeypatch, tmp_path).get("/concepts")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    names = [b["name"] for b in body["boards"]]
    assert "CPO" in names and "机器人" in names


def test_concepts_absent_data_is_graceful(monkeypatch, tmp_path):
    r = _client(monkeypatch, tmp_path, with_data=False).get("/concepts")
    assert r.status_code == 200
    assert r.json() == {"available": False, "boards": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_concepts_endpoint.py -v`
Expected: FAIL — 404 (route not defined) / KeyError.

- [ ] **Step 3: Add the route** in `buddy/server.py` inside `build_app()` (after the `/quotes` handler)

```python
    @app.get("/concepts")
    async def concepts():
        """List 同花顺 concept boards for the UI 板块 picker.

        Reads ``concept_ths_index.parquet`` written by
        ``fa data update --include-concepts``. Returns
        ``{available: bool, boards: [{name, code}]}``.
        """
        def _load():
            import pandas as pd
            from financial_analyst.data.paths import get_data_paths
            path = get_data_paths().parquet_root / "concept_ths_index.parquet"
            if not path.exists():
                return None
            df = pd.read_parquet(path)
            name_col = next((c for c in df.columns if "name" in c.lower()), df.columns[0])
            code_col = next((c for c in df.columns if "code" in c.lower()), None)
            out = []
            for _, row in df.iterrows():
                nm = str(row[name_col]).strip()
                if nm and nm.lower() != "nan":
                    out.append({"name": nm, "code": str(row[code_col]) if code_col else None})
            return out

        try:
            boards = await asyncio.to_thread(_load)
        except Exception as exc:
            return JSONResponse({"available": False, "boards": [], "error": str(exc)}, status_code=200)
        if boards is None:
            return JSONResponse({"available": False, "boards": []})
        return JSONResponse({"available": True, "boards": boards})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_concepts_endpoint.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add tests/test_concepts_endpoint.py src/financial_analyst/buddy/server.py
git commit -m "feat(buddy): GET /concepts — list THS concept boards for 板块 picker"
```

---

## Task 3: ⌗板块 popover (frontend)

**Files:**
- Modify: `src/financial_analyst/ui/app.jsx` (`Composer`: fetch + cache boards, render popover)

- [ ] **Step 1: Add board-fetch state in `Composer`** (after the `popover` state from Task 1 Step 2)

```jsx
  const [boards, setBoards] = useState(null); // null=未取, []=空
  useEffect(() => {
    if (popover !== 'board' || boards !== null || !s.backendUrl) return;
    fetch(`${s.backendUrl}/concepts`).then(r => r.json())
      .then(d => setBoards(d.available ? (d.boards || []) : []))
      .catch(() => setBoards([]));
  }, [popover, boards, s.backendUrl]);
  const [boardQ, setBoardQ] = useState('');
```

- [ ] **Step 2: Build board items + render popover** (add after the `popover === 'ref'` block from Task 1 Step 5)

```jsx
      {popover === 'board' && (
        <ComposerPopover title="概念板块 · 同花顺"
          items={(boards || [])
            .filter(b => !boardQ || b.name.includes(boardQ))
            .slice(0, 60)
            .map(b => ({ key: b.code || b.name, label: b.name, sub: b.code || '', onPick: () => insertText(`${b.name}板块`) }))}
          emptyHint={boards === null ? '加载中…' : '无板块数据 (先跑 fa data update --include-concepts)'}
          onClose={() => { setPopover(null); setBoardQ(''); }} />
      )}
```

> NOTE: a search box can be added inside `ComposerPopover` later; for v1 the list is capped at 60 and `boardQ` stays empty (the popover shows the full capped list). Keep `boardQ` state so adding the box is a one-liner.

- [ ] **Step 3: Manual verify + cache-buster**

Bump `?v=` in `index.html`. Reload, ensure concepts data exists (`fa data update --include-concepts` once), click `⌗ 板块` → popover lists boards → click inserts e.g. `CPO板块`. With no backend, button is disabled (opacity 0.5).

- [ ] **Step 4: Commit**

```bash
git add src/financial_analyst/ui/app.jsx src/financial_analyst/ui/index.html
git commit -m "feat(ui): ⌗板块 button — pick a THS concept board into the composer"
```

---

## Task 4: POST /upload backend endpoint (TDD)

**Files:**
- Create: `tests/test_upload_endpoint.py`
- Modify: `src/financial_analyst/buddy/server.py` (add route in `build_app()`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_upload_endpoint.py
import io
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app

client = TestClient(build_app())


def test_upload_txt_extracts_text():
    r = client.post("/upload", files={"file": ("note.txt", io.BytesIO("贵州茅台 PE 偏高".encode("utf-8")), "text/plain")})
    assert r.status_code == 200
    b = r.json()
    assert b["name"] == "note.txt"
    assert "贵州茅台" in b["text"]
    assert b["chars"] > 0


def test_upload_csv_extracts_text():
    r = client.post("/upload", files={"file": ("h.csv", io.BytesIO(b"code,pct\nSH600519,2.1\n"), "text/csv")})
    assert r.status_code == 200
    assert "SH600519" in r.json()["text"]


def test_upload_rejects_unsupported_type():
    r = client.post("/upload", files={"file": ("a.exe", io.BytesIO(b"MZ..."), "application/octet-stream")})
    assert r.status_code == 400


def test_upload_rejects_oversized():
    big = io.BytesIO(b"x" * (10 * 1024 * 1024 + 1))
    r = client.post("/upload", files={"file": ("big.txt", big, "text/plain")})
    assert r.status_code == 413
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_upload_endpoint.py -v`
Expected: FAIL — 404 (route undefined). (If `python-multipart` missing, FastAPI raises at TestClient POST — Task 6 adds the dep; install it now with `pip install python-multipart pypdf` to run this task.)

- [ ] **Step 3: Add the route** in `buddy/server.py` inside `build_app()`

```python
    @app.post("/upload")
    async def upload(file: UploadFile = FastAPIFile(...)):
        """Extract text from an uploaded doc for the composer 上传 button.

        csv/txt/md → utf-8 decode; pdf → pypdf page text. Caps raw size at
        10 MB and extracted text at 20k chars. Returns {id, name, chars, truncated, text}.
        """
        MAX_BYTES = 10 * 1024 * 1024
        MAX_CHARS = 20_000
        name = file.filename or "upload"
        ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
        if ext not in (".txt", ".md", ".csv", ".pdf"):
            return JSONResponse({"error": f"不支持的文件类型: {ext or '无扩展名'}"}, status_code=400)
        raw = await file.read()
        if len(raw) > MAX_BYTES:
            return JSONResponse({"error": f"文件过大 (>{MAX_BYTES // 1024 // 1024}MB)"}, status_code=413)

        def _extract():
            if ext == ".pdf":
                import pypdf
                reader = pypdf.PdfReader(io.BytesIO(raw))
                return "\n".join((p.extract_text() or "") for p in reader.pages)
            return raw.decode("utf-8", errors="replace")

        try:
            text = await asyncio.to_thread(_extract)
        except Exception as exc:
            return JSONResponse({"error": f"解析失败: {exc}"}, status_code=422)
        truncated = len(text) > MAX_CHARS
        text = text[:MAX_CHARS]
        return JSONResponse({"id": uuid.uuid4().hex, "name": name, "chars": len(text),
                             "truncated": truncated, "text": text})
```

- [ ] **Step 4: Add imports** at the top of `buddy/server.py` (near the existing `import io`/`uuid` — verify; add what's missing)

```python
import io
import uuid
from fastapi import UploadFile, File as FastAPIFile
```

> NOTE: `uuid` is already imported (`server.py:28`). Add `io` and the `fastapi` UploadFile/File import if not present.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_upload_endpoint.py -v`
Expected: PASS (all 4).

- [ ] **Step 6: Commit**

```bash
git add tests/test_upload_endpoint.py src/financial_analyst/buddy/server.py
git commit -m "feat(buddy): POST /upload — extract text from pdf/csv/txt/md for composer attach"
```

---

## Task 5: ⊟上传 file input + attachment chip + send() prepend (frontend)

**Files:**
- Modify: `src/financial_analyst/ui/app.jsx` (`Composer`: file input, attachments state, chip row, send() prepend)

- [ ] **Step 1: Add attachments state + upload handler** (after the `popover`/`fileInputRef` state from Task 1; `fileInputRef` is already declared in Task 1 — do NOT redeclare it)

```jsx
  const [attachments, setAttachments] = useState([]); // [{id,name,chars,text}]
  const onFilePicked = (e) => {
    const f = e.target.files?.[0];
    e.target.value = '';
    if (!f || !s.backendUrl) return;
    const fd = new FormData(); fd.append('file', f);
    dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: `⏳ 解析附件 ${f.name}…` } });
    fetch(`${s.backendUrl}/upload`, { method: 'POST', body: fd })
      .then(async r => { const d = await r.json(); if (!r.ok) throw new Error(d.error || '上传失败'); return d; })
      .then(d => setAttachments(a => [...a, { id: d.id, name: d.name, chars: d.chars, text: d.text }]))
      .catch(err => dispatch({ type: 'inject_message', message: { id: 'sys_'+Date.now(), role: 'ai', kind: 'answer', text: `⚠ 附件失败: ${err.message}` } }));
  };
```

- [ ] **Step 2: Render hidden input + attachment chips** (inside return, right after the `<div style={{ border... }}>` composer box open at `app.jsx:2083`)

```jsx
        <input ref={fileInputRef} type="file" accept=".pdf,.csv,.txt,.md" onChange={onFilePicked} style={{ display: 'none' }} />
        {attachments.length > 0 && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, padding: '6px 14px 0' }}>
            {attachments.map(a => (
              <span key={a.id} className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 10, padding: '2px 8px', border: '1px solid var(--yin)', color: 'var(--ink-1)', whiteSpace: 'nowrap' }}>
                ⊟ {a.name} <span style={{ color: 'var(--ink-3)' }}>{a.chars}字</span>
                <span onClick={() => setAttachments(x => x.filter(y => y.id !== a.id))} style={{ cursor: 'pointer', color: 'var(--yin)' }}>×</span>
              </span>
            ))}
          </div>
        )}
```

- [ ] **Step 3: Prepend attachment text in `send()`** — change the normal-text branch (`app.jsx:2060-2062`)

```jsx
    const attachText = attachments.map(a => `【附件 ${a.name}】\n${a.text}`).join('\n\n');
    const finalText = attachText ? `${attachText}\n\n${text}` : text;
    if (s.status === 'idle') startAgent(finalText);
    else dispatch({ type: 'queue', text: finalText });
    setAttachments([]);
    setVal('');
```

Also add `attachments` to the `send` useCallback dep array (`app.jsx:2063`).

- [ ] **Step 4: Manual verify + cache-buster**

Bump `?v=` in `index.html`. Reload. Click `⊟ 上传` → pick a `.txt`/`.csv`/small `.pdf` → chip appears (`⊟ name 〈chars〉字`). Type a question, send → the agent's prompt includes the extracted text (verify via the answer referencing the doc, or the backend log). `×` removes a chip. Oversized/unsupported → red toast, no chip.

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/ui/app.jsx src/financial_analyst/ui/index.html
git commit -m "feat(ui): ⊟上传 button — attach a doc, prepend extracted text to the prompt"
```

---

## Task 6: Dependencies + final verification

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add deps** to `[project].dependencies` in `pyproject.toml`

```toml
    "pypdf>=4",            # composer 上传: PDF text extraction
    "python-multipart>=0.0.9",  # FastAPI multipart for POST /upload
```

- [ ] **Step 2: Install + full backend test run**

Run: `pip install -e ".[dev]" && python -m pytest tests/test_concepts_endpoint.py tests/test_upload_endpoint.py -v`
Expected: all PASS.

- [ ] **Step 3: Regression — full suite + ruff**

Run: `python -m pytest tests/ -q -p no:cacheprovider` (expected: prior 787 + new tests pass, 1 skipped) and `ruff check src/financial_analyst/buddy/server.py`
Expected: green / clean.

- [ ] **Step 4: Final manual UI pass** — all three buttons work at narrow + wide widths (chips/popovers don't go vertical — they inherit the header/footer nowrap fixes). Confirm cache-buster bumped to the final value.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "build: add pypdf + python-multipart for composer upload"
```

---

## Out of scope (follow-up spec)

⊟上传 **images**: vision-model routing (gpt-4o / claude / qwen-vl) + image content blocks in `llm/client.py` + auto model-switch when an image is attached. Tracked in a separate spec.
