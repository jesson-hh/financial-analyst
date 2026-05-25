# Release Workflow Design Spec
*Date: 2026-05-25 | Status: Approved*

## Summary

A Claude Code Workflow (`.workflow.mjs`) that automates the entire release last-mile process for `financial-analyst`, from local validation through PyPI publish to Git tagging. Fully automatic, fail-fast, outputs a single final report.

**Trigger:** `ultrawork` (requires `CLAUDE_CODE_WORKFLOWS=1`)  
**File:** `G:/financial-analyst/release.workflow.mjs`  
**Scope:** Release last-mile + user-perspective validation  
**Mode:** Fully automatic, no mid-workflow prompts, single final report

---

## Problem Statement

Releasing financial-analyst manually is error-prone:
- Version numbers in `pyproject.toml`, `CHANGELOG.md`, and `src-tauri/tauri.conf.json` can drift
- No automated user-perspective install validation before the irreversible PyPI push
- TestPyPI step is often skipped under time pressure
- No clear reversible/irreversible checkpoint
- Post-release report is manual

---

## Architecture: 8 Phases

```
Phase 0  Preflight          git clean + version 3-way consistency check
Phase 1  Local Validation   pytest + ruff + build + twine check
Phase 2  Local Smoke        serve + smoke_test_serve.sh + CLI smoke
Phase 3  Changelog Gate     CHANGELOG entry verified, pre-release commit
Phase 4  TestPyPI Round     upload testpypi → clean venv → install → fa version/agents
──────── IRREVERSIBLE BOUNDARY ────────────────────────────────────────────────
Phase 5  Real PyPI          twine upload → verify pypi.org/{version} exists
Phase 6  Git Tag + Push     tag v{ver} → push → release.yml triggered
Phase 7  HF Data Package    git diff detection → conditional publish demo/lite
Phase 8  Final Report       ✓/⚠/❌ per phase + action items table
```

Phases 0–4 are fully reversible (no external state changed). The workflow pauses for a structural reason only if a phase returns an error — it never prompts for intermediate decisions.

---

## Phase Designs

### Phase 0: Preflight

**Purpose:** Ensure we're starting from a clean, consistent state.

**Checks:**
1. `git status --porcelain` → must be empty (no uncommitted changes)
2. `pyproject.toml` version field  
3. `CHANGELOG.md` latest `## [x.y.z]` heading → must match pyproject version  
4. `packaging/src-tauri/tauri.conf.json` `.package.version` → must match (or warn if Tauri build not in scope)
5. `dist/financial_analyst-{version}*.whl` present (build artifacts from last `python -m build`)

**Fail condition:** git dirty OR version mismatch across pyproject/CHANGELOG  
**Warn condition:** Tauri version mismatch (don't block — Tauri build is optional)

**Agent output schema:**
```json
{
  "version": "string",
  "git_clean": "boolean",
  "pyproject_version": "string",
  "changelog_version": "string",
  "tauri_version": "string | null",
  "version_consistent": "boolean",
  "dist_exists": "boolean",
  "issues": ["string"],
  "warnings": ["string"]
}
```

---

### Phase 1: Local Validation

**Purpose:** Verify tests pass, code is lint-clean, and wheel/sdist are valid.

**Steps (sequential, fail-fast):**
1. `pytest tests/ -q` — must exit 0
2. `ruff check src/` — must exit 0
3. `python -m build --wheel --sdist --outdir dist/` — rebuild artifacts
4. `twine check dist/financial_analyst-{version}*` — both whl + tar.gz

**Note:** If `dist_exists=true` from Phase 0, skip step 3 (reuse existing artifacts). The agent decides based on Phase 0 output.

**Agent output schema:**
```json
{
  "pytest_passed": "boolean",
  "pytest_summary": "string",
  "ruff_passed": "boolean",
  "build_passed": "boolean",
  "twine_check_passed": "boolean",
  "artifacts": ["string"]
}
```

---

### Phase 2: Local Smoke

**Purpose:** Verify the built package actually runs — serve + CLI golden paths.

**Steps:**
1. Start `financial-analyst serve --port 9999` in background
2. Poll `GET http://127.0.0.1:9999/health` until 200 or 15s timeout
3. Run `bash scripts/smoke_test_serve.sh` (covers /health, /diag, /tools, /models, /quotes, /comments, /resolve, /alerts, /run SSE, /report-progress)
4. Run CLI smoke: `fa version` → must contain `{version}`; `fa agents` → must show agents list; `fa data --help` → exit 0; `fa dream --help` → exit 0
5. Kill serve process

**Agent output schema:**
```json
{
  "serve_started": "boolean",
  "serve_health": "boolean",
  "smoke_test_passed": "boolean",
  "smoke_test_output": "string",
  "cli_version_ok": "boolean",
  "cli_agents_ok": "boolean",
  "cli_help_ok": "boolean"
}
```

---

### Phase 3: Changelog Gate

**Purpose:** Ensure CHANGELOG entry is final and commit everything before the irreversible steps.

**Steps:**
1. Read `CHANGELOG.md` — find `## [{version}]` section, verify it's non-empty (≥3 bullet points)
2. If CHANGELOG section looks like a stub (< 50 chars), warn but don't block
3. `git add pyproject.toml CHANGELOG.md README.md docs/` 
4. `git commit -m "chore: prepare release v{version}"` (skip if nothing staged)
5. Verify commit hash

**Note:** This commit goes to main. It is technically reversible (git reset) but treated as the point of no return for the release.

**Agent output schema:**
```json
{
  "changelog_entry_found": "boolean",
  "changelog_entry_chars": "number",
  "commit_made": "boolean",
  "commit_hash": "string | null",
  "warnings": ["string"]
}
```

---

### Phase 4: TestPyPI Validation (User-Perspective)

**Purpose:** Validate the actual install experience from a clean environment before touching real PyPI.

**Steps:**
1. `twine upload --repository testpypi dist/financial_analyst-{version}*` (requires `~/.pypirc` testpypi token)
2. Create temp venv: `python -m venv /tmp/fa_test_venv`
3. Install from TestPyPI:
   ```
   /tmp/fa_test_venv/Scripts/pip install \
     -i https://test.pypi.org/simple/ \
     --extra-index-url https://pypi.org/simple/ \
     financial-analyst=={version}
   ```
4. `/tmp/fa_test_venv/Scripts/financial-analyst version` → must print `{version}`
5. `/tmp/fa_test_venv/Scripts/financial-analyst agents` → must list agents
6. Cleanup temp venv

**Fail conditions:**
- twine upload fails (e.g. version already exists on testpypi — need `--skip-existing`)
- version mismatch after install
- agents command fails

**Agent output schema:**
```json
{
  "testpypi_upload_ok": "boolean",
  "testpypi_install_ok": "boolean",
  "version_reported": "string",
  "version_match": "boolean",
  "agents_ok": "boolean",
  "user_experience_score": "string"
}
```

---

### Phase 5: Real PyPI Upload ⚠ IRREVERSIBLE

**Purpose:** Publish to the real PyPI. No going back — only yanking is possible.

**Steps:**
1. `twine upload dist/financial_analyst-{version}*` (uses `~/.pypirc` pypi token)
2. Wait 10s for CDN propagation
3. `pip index versions financial-analyst` → confirm `{version}` appears
4. Record pypi URL

**Fail condition:** Upload fails or version not found in index after 30s

**Agent output schema:**
```json
{
  "pypi_upload_ok": "boolean",
  "pypi_version_confirmed": "boolean",
  "pypi_url": "string"
}
```

---

### Phase 6: Git Tag + Push ⚠ IRREVERSIBLE

**Purpose:** Tag the release and push to origin, triggering `release.yml` (Tauri bundles + GitHub Release draft).

**Steps:**
1. `git tag v{version}`
2. `git push origin main`
3. `git push origin v{version}`
4. Confirm tag pushed (not rejected)

**Note:** This triggers `release.yml` which builds .msi/.dmg/.AppImage cross-platform. The workflow does NOT wait for that CI run — it just confirms the trigger.

**Agent output schema:**
```json
{
  "tag_created": "boolean",
  "main_pushed": "boolean",
  "tag_pushed": "boolean",
  "release_yml_url": "string"
}
```

---

### Phase 7: HuggingFace Data Package (Conditional)

**Purpose:** Auto-publish HF data packages if data files changed.

**Steps:**
1. `git diff HEAD~2 -- src/financial_analyst/data/ src/financial_analyst/_resources/` → check if any data files changed
2. If changed: `python scripts/publish_hf_dataset.py --preset demo --repo jesson-hh/financial-analyst-data-demo`
3. Lite preset only if demo succeeds and `HUGGINGFACE_TOKEN` env is set with write access

**Note:** HF publish is optional — if `HUGGINGFACE_TOKEN` not set, phase is skipped with ⚠ (not ❌).

**Agent output schema:**
```json
{
  "data_changed": "boolean",
  "hf_needed": "boolean",
  "hf_demo_published": "boolean",
  "hf_lite_published": "boolean",
  "skipped_reason": "string | null"
}
```

---

### Phase 8: Final Report

**Purpose:** Aggregate all results into a single human-readable release report.

**Output format:**
```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 financial-analyst v{version} Release Report
 {date}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Phase 0  Preflight          ✓ git clean, versions consistent
 Phase 1  Local Validation   ✓ 52 passed, ruff clean, artifacts OK
 Phase 2  Local Smoke        ✓ serve + smoke + CLI all green
 Phase 3  Changelog Gate     ✓ committed d8a3f2c
 Phase 4  TestPyPI           ✓ installed, fa version = {version}, agents listed
 Phase 5  Real PyPI          ✓ https://pypi.org/project/financial-analyst/{version}/
 Phase 6  Git Tag + Push     ✓ v{version} pushed, release.yml triggered
 Phase 7  HF Data Package    ⚠ skipped (data unchanged)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Status: RELEASED ✓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Action items:
  - Check GitHub Actions for release.yml Tauri bundle status
  - Monitor PyPI downloads: https://pypistats.org/packages/financial-analyst
```

The report is written to `docs/release_reports/v{version}.md` and printed to console.

---

## Data Flow Between Phases

```javascript
const preflight  = await agent("...", { schema: preflight_schema })
                                       // ↓ version, dist_exists
const validation = await agent("...", { schema: validation_schema })
                                       // ↓ artifacts[]
const [smoke, changelog] = await Promise.all([
  agent("smoke test...", { schema: smoke_schema }),
  agent("changelog...", { schema: changelog_schema }),
])
                                       // ↓ all previous results
const testpypi   = await agent("...", { schema: testpypi_schema })
// ── BOUNDARY ──
const pypi       = await agent("...", { schema: pypi_schema })
const tag        = await agent("...", { schema: tag_schema })
const hf         = await agent("...", { schema: hf_schema })
// ── REPORT ──
return await agent("...", { schema: report_schema })
```

**Parallelism:** Phase 2 (smoke) and Phase 3 (changelog) can run in parallel since they have no shared state.

---

## Error Handling

Fail-fast rule: any phase returning a false on a required field immediately assembles a partial report and returns. No phase after the failure is attempted.

```javascript
if (!preflight.version_consistent) {
  return buildReport({ ...allPhaseResults, aborted_at: "Phase 0" })
}
```

---

## Prerequisites for the Workflow to Run

| Requirement | Where | Notes |
|-------------|-------|-------|
| `~/.pypirc` with `[testpypi]` and `[pypi]` tokens | Local | Must have write access to financial-analyst package |
| `git remote origin` configured | Repo | `git remote get-url origin` must work |
| `HUGGINGFACE_TOKEN` env var | Optional | Only needed if HF publish enabled |
| Python venv with `financial-analyst[dev]` installed | Active venv | For running tests + build |
| `bash` available | PATH | For smoke_test_serve.sh |
| Port 9999 free | Local | For serve smoke test |

---

## File Layout

```
G:/financial-analyst/
├── release.workflow.mjs          ← the workflow (to be created)
├── docs/
│   ├── superpowers/
│   │   └── specs/
│   │       └── 2026-05-25-release-workflow-design.md  ← this file
│   └── release_reports/
│       └── v{version}.md         ← auto-generated per release
```

---

## Out of Scope

- 24h post-release monitoring (manual — check pypistats.org)
- Community announcement (Weibo / Zhihu / V2EX — manual)
- Docker image build (separate `docker build` pipeline)
- Full CI pass verification (GitHub Actions runs async — we just trigger and note URL)
- Rolling back a release (manual — follow `docs/setup/release_checklist.md §6`)

---

## Self-Review Checklist

- [x] No TBD/TODO sections
- [x] Phases are internally consistent (data flows correctly)
- [x] Reversible/irreversible boundary clearly marked
- [x] Error handling defined for each phase
- [x] Schema fields are complete and typed
- [x] Prerequisites documented
- [x] Scope is focused (single workflow, no scope creep)
- [x] Phase 2 + Phase 3 parallelism is safe (no shared state)
