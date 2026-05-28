# Composer Buttons — Empower 上传 / @引用 / 板块

**Date:** 2026-05-28
**Status:** Approved design, pending implementation plan
**Scope:** Wire up the three currently-dead composer toolbar buttons in the 觀瀾 web UI.

## Problem

`src/financial_analyst/ui/app.jsx:2108-2110` renders three toolbar buttons as pure
decoration — a `.map` over `['⊟ 上传', '@ 引用', '⌗ 板块']` producing `<span>` elements
with `cursor: pointer` but **no `onClick`**. They look interactive but do nothing.

```jsx
{['⊟ 上传', '@ 引用', '⌗ 板块'].map((x, i) => (
  <span key={i} style={{ ...cursor: 'pointer' }}>{x}</span>
))}
```

Goal: give each button a real, useful function that fits the A-share research workflow.

## Scope

This spec covers three independent features, built in order of value/risk:

1. **@引用** — insert a reference (stock / current tool result) into the prompt. Frontend-only.
2. **⌗板块** — pick a 同花顺 concept board → insert into the prompt. Adds one backend endpoint.
3. **⊟上传 (documents)** — attach a PDF/CSV/TXT/MD → backend extracts text → prepended to the prompt as context. Adds one backend endpoint.

### Non-goals (this spec)

- **Image upload / vision analysis.** Deferred to a follow-up spec — it requires a vision-capable
  model (gpt-4o / claude / qwen-vl) plus image-content-block support in `llm/client.py` (today text-only),
  which is a materially larger, riskier change.
- **"最近研报" in @引用.** Would need a past-report list endpoint; deferred. @引用 stays frontend-only this round.
- **Auto-triggering board analysis** when a board is picked. Picking inserts text; the user edits and sends. (A one-click "analyze" affordance can come later.)

## Current architecture (relevant pieces)

- **Composer** (`app.jsx`, the `Composer` function ~line 1953+): a `<textarea>` bound to `val` state,
  a toolbar row (the 3 dead buttons), and a send button. `onPick`/`prefill` already insert text into the composer.
- **Command palette** (`CommandPalette` ~line 2590+) and **SlashMenu** (~line 2129): existing popover patterns to mirror.
- **Refs / citations:** the UI already has a `§N` tool-result citation system and a `@ 引用此股` affordance (line 2086) + `当前任务引用` panel (line 2499).
- **Backend:** FastAPI app in `buddy/server.py` (`build_app()`), many `@app.get/post` routes; the UI talks to it at `s.backendUrl` (`http://127.0.0.1:9999`).
- **Concepts data:** `fa data update --include-concepts` (adata 同花顺 concept list + constituents) writes a concepts parquet under the parquet dir; `ths_concept_board` tool also exists.
- **UI cache-buster:** `ui/index.html` carries `?v=YYYYMMDD-N` on the three `.jsx` script tags; **must be bumped on every jsx change** or browsers serve the stale version (babel compiles in-browser).

## Design

### Shared: ComposerPopover

A small reusable popover component anchored above the composer toolbar (mirrors `SlashMenu`'s
style: absolutely-positioned panel, `var(--paper)` bg, `var(--line)` border, keyboard-dismiss on ESC,
click-outside to close). Each of the three buttons toggles its own popover. Selecting an item calls a
callback that either inserts text into `val` (via the existing prefill/setVal path) or, for upload,
pushes onto an `attachments` array.

The three buttons (app.jsx:2108-2110) change from a static `.map` to three real `<span>`/`<button>`
elements with `onClick` toggling popover state.

### Feature 1 — @引用 (frontend-only)

**Behavior:** Click `@ 引用` → popover with up to three sections (only render non-empty ones):
- **自选股** — from `watchCodes` / the quotes the UI already polls. Each row: name + code.
- **本轮工具结果** — the current session's `chain` items that produced a `§N` (if a chain message exists).
- **当前个股** — if a stock quick-view card / report drawer is open for a specific symbol.

**On select:**
- Stock → insert `<name>（<CODE>）` (e.g. `宁德时代（SZ300750）`) at the cursor in `val`.
- Tool result → insert the `§N` citation token the UI already understands.

**Data:** all sourced from existing client-side React state (`watchCodes`, `quotes`, current session
`messages`, open drawer). **No backend change.**

### Feature 2 — ⌗板块 (adds GET /concepts)

**Backend:** new `GET /concepts` in `buddy/server.py` → returns a JSON list of concept boards
`[{ name, code?, n_constituents? }]`, read from the concepts parquet (the same data
`fa data update --include-concepts` writes). If the concepts data is absent, return `{ boards: [], available: false }`
so the UI can show an empty-state hint ("先跑 fa data update --include-concepts").

**Frontend:** Click `⌗ 板块` → popover with a search box + the board list (fetched once from
`${backendUrl}/concepts`, cached in state). Selecting a board inserts `<board>板块` (e.g. `CPO板块`)
into `val`. When not connected to a backend (`mock` mode), the button is disabled with a tooltip.

### Feature 3 — ⊟上传 documents (adds POST /upload)

**Frontend:**
- Click `⊟ 上传` → hidden `<input type="file" accept=".pdf,.csv,.txt,.md">` opens the OS picker.
- On file select → POST the file (multipart) to `${backendUrl}/upload`.
- Show an **attachment chip** in the composer (filename + size + a `×` to remove) backed by an
  `attachments` state array `[{ id, name, chars, text }]`.
- **On send:** prepend each attachment's extracted text to the outgoing message as a fenced context block:
  ```
  【附件 <name>】
  <extracted text, already truncated by backend>

  <user's question>
  ```
  Then clear `attachments`.

**Backend:** new `POST /upload` (multipart) in `buddy/server.py`:
- Accept one file; cap raw size (e.g. **10 MB**) — reject larger with a clear error.
- Extract text by type: `.csv/.txt/.md` → decode UTF-8 (errors=replace); `.pdf` → `pypdf` page text.
- Truncate extracted text to a budget (e.g. **20k chars**) and note truncation.
- Return `{ id, name, chars, truncated, text }`.

**Dependencies:** `python-multipart` (FastAPI multipart parsing) and `pypdf` (PDF text). Both small;
add to `pyproject` core deps. CSV/TXT/MD need no extra dep.

## Data flow

```
@引用:   button → popover (client state) → insert ref into val
板块:    button → popover → GET /concepts (cached) → insert "<board>板块" into val
上传:    button → file picker → POST /upload (multipart) → {text} → attachments[] → chip
         send → prepend attachment text to message → existing agent/SSE path
```

## Error handling

- **No backend (mock mode):** 板块 + 上传 buttons disabled with tooltip; @引用 still works (client-only).
- **/concepts empty/missing data:** popover shows the "run fa data update --include-concepts" hint.
- **/upload too large / unsupported type / extraction failure:** backend returns a 4xx with a message;
  UI shows it as a transient toast, no chip added.
- **PDF with no extractable text (scanned image):** return `chars: 0` + a note; UI warns "未提取到文字（可能是扫描件）".

## Testing

- **Backend (pytest, `tests/`):**
  - `GET /concepts` returns a list when concepts data present; `available: false` when absent.
  - `POST /upload` extracts text from sample `.csv`, `.txt`, `.md`, and a tiny `.pdf` fixture; rejects oversized + unsupported types.
- **Frontend:** no JS test framework (babel-in-browser) → manual visual verification at narrow + wide widths;
  bump the `index.html` cache-buster and hard-reload.

## Build order

1. @引用 (frontend-only) — fastest, immediate value.
2. ⌗板块 — `GET /concepts` + popover.
3. ⊟上传 documents — `POST /upload` + extraction + attachment chip + prompt assembly.

## Follow-up (separate spec)

⊟上传 **images**: vision-model routing (gpt-4o / claude / qwen-vl), image content blocks in
`llm/client.py`, auto-switch model when an image is attached.
