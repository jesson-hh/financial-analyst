# Reusable Agent-Project Release Workflow — Design Spec v3
*Date: 2026-05-25 (revised 2026-05-26) | Status: Approved | Target: any agent project, financial-analyst as reference impl*

## Revision history
- **v1** (2026-05-25): 8 phases, build + publish only, financial-analyst specific.
- **v2** (2026-05-26): expanded to 17 phases including user-journey + doc consistency, still financial-analyst specific.
- **v3** (2026-05-26): **refactored as reusable template** — workflow body is project-agnostic, all specifics moved into a per-project `release.workflow.config.yaml`. Drop-in for any agent project that ships a CLI + (optional) wizard + (optional) web UI to PyPI.

---

## Summary

A Claude Code Workflow (`.workflow.mjs`) **template** that automates whole-product release validation for agent projects. The workflow itself is project-agnostic. Each consuming project supplies a `release.workflow.config.yaml` that fills in the project name, CLI commands, ports, providers, etc.

**Trigger:** `ultrawork`  ·  **File pattern:** `<project>/release.workflow.mjs` + `<project>/release.workflow.config.yaml`  ·  **Mode:** fully automatic, fail-fast, single final report

---

## Target consumers

This workflow is designed for the class of projects that look like:

- Python package shipped to PyPI
- Has a CLI (one or more entry-point binaries via `[project.scripts]`)
- Optionally has a first-run wizard (key collection, data preset, etc.)
- Optionally has a local web UI launcher
- Optionally has data/asset packages on HuggingFace or similar
- Bilingual or monolingual

Examples in this template family:
- **financial-analyst** (the reference impl — A-share research agent)
- Other future agent projects with the same shape

Each consumer drops in the unchanged `release.workflow.mjs` + writes a `release.workflow.config.yaml` describing its own surface.

---

## Architecture: 17 phases / 4 tracks (generic)

```
TRACK A — Build & Publish (the irreversible spine)
────────────────────────────────────────────────────────────────────
Phase 0   Preflight            git clean · version N-way · env baseline
Phase 1   Static & lint        configured linter (ruff/black/pylint/...)
Phase 2   Test suite           configured test runner (pytest/unittest/...)
Phase 3   Build artifacts      python -m build wheel+sdist · twine check
                                · wheel contents whitelist check

TRACK B — User Journey Validation
────────────────────────────────────────────────────────────────────
Phase 4   CLI surface smoke    every entry point in config responds (--help / version / list)
Phase 5   Wizard simulation    isolated /tmp workspace · non-interactive init
                                · re-run produces backup · language switch works
Phase 6   Launcher preflight   required imports · port availability ·
                                env/data path resolution across candidate locations
Phase 7   Fast-path verification 2nd launcher invocation skips wizard
Phase 8   Update mechanism     update --check probes PyPI · editable install refusal

TRACK C — Documentation & Consistency
────────────────────────────────────────────────────────────────────
Phase 9   Doc cross-check      configured doc paths · version refs current ·
                                forbidden strings absent · CHANGELOG entry non-stub
Phase 10  i18n smoke           each declared language passes wizard with no missing-key markers
Phase 11  Changelog gate       git commit "chore: prepare release v{version}"

TRACK A continued — Publish & Notify
────────────────────────────────────────────────────────────────────
Phase 12  TestPyPI publish     twine --skip-existing
Phase 13  TestPyPI replay      clean /tmp venv · install · wizard --preset skip · version match
──────── IRREVERSIBLE BOUNDARY ────────────────────────────────────
Phase 14  Real PyPI publish    twine upload · poll index until version visible
Phase 15  Git tag + push       tag · push main + tag · trigger CI/release workflows
Phase 16  Asset package        configured asset publishers (HF / S3 / custom)
Phase 17  Final report         per-phase ✓/⚠/❌ · action items · write release_reports/v{ver}.md
```

**17 phases**, but per-project the user can disable phases that don't apply (e.g. project without web UI disables Phase 6/7).

---

## Config schema: `release.workflow.config.yaml`

This is the contract between consuming projects and the generic workflow. The schema in full:

```yaml
# release.workflow.config.yaml
# All values are project-specific. Workflow code reads this; never the other way around.

project:
  name: "financial-analyst"              # human-readable
  package_name: "financial-analyst"      # PyPI dist name (used in `pip install <name>`)
  module_name: "financial_analyst"       # importable module (`python -m <module>`)
  cli_aliases: ["fa", "financial-analyst"]    # entry-point names — Phase 4 hits each
  repo_url: "https://github.com/jesson-hh/financial-analyst"

version_sources:
  # Phase 0 verifies all sources agree
  - path: "pyproject.toml"
    extractor: "toml:project.version"
  - path: "CHANGELOG.md"
    extractor: "regex:## \\[(\\d+\\.\\d+\\.\\d+)\\]"
    take: "first"                        # first match = "current version"
  - path: "packaging/src-tauri/tauri.conf.json"
    extractor: "json:.package.version"
    optional: true                       # warn-only if missing

linter:
  enabled: true
  command: "ruff check src/"
  format_check: "ruff format --check src/"

tests:
  enabled: true
  command: "pytest tests/ -q"
  max_seconds: 60                        # if exceeded → probably hit real LLM (bad)
  mock_required: true

build:
  command: "python -m build --wheel --sdist --outdir dist/"
  twine_check: true
  wheel_must_contain:                    # Phase 3 sanity (catches missing package_data)
    - "_resources/"
    - "config/"

cli_smoke:
  # Phase 4 — each command must exit 0 and produce ≥1 line stdout
  # {cli} gets substituted by each entry in project.cli_aliases
  commands:
    - "{cli} version"
    - "{cli} --help"
    - "{cli} agents"
    - "{cli} data --help"
    - "{cli} init --help"
    - "{cli} update --check"
    - "{cli} doctor"
    - "{cli} report --help"
    - "{cli} launch --help"
  version_command: "{cli} version"        # special — output must contain {version}

workspace:
  # Phase 5/6/13 — workspace concept (skip whole phase if disabled)
  enabled: true
  default_path: "~/.{project.module_name}"        # template uses {project.*}
  pointer_file: "~/.{project.module_name}/.workspace"
  env_var: "FA_WORKSPACE"                # the override env var
  data_subdir: "data"
  config_subdir: "config"

wizard:
  # Phase 5/10 — init wizard simulation
  enabled: true
  command: "{cli} init"
  flags:
    non_interactive: "--yes"
    skip_assets: "--preset skip"         # avoid real downloads in tests
    workspace: "--workspace"
    language: "--lang"
  expected_outputs:                      # files the wizard should create
    - ".env"
    - "config/loaders.yaml"
  backup_pattern: ".env.bak.*"           # Phase 5 verifies re-run creates this

launcher:
  # Phase 6/7 — fast-path launcher (skip whole phase if disabled)
  enabled: true
  command: "{cli} start"
  ports:
    - name: "backend"
      port: 9999
      health_url: "http://127.0.0.1:9999/health"
    - name: "ui"
      port: 5173
      health_url: "http://127.0.0.1:5173/"
  required_imports: ["fastapi", "uvicorn"]    # Phase 6 preflight check
  fast_path_markers:                     # Phase 7 — at least one must appear in 2nd-invocation stdout
    - "Already running"
    - "已在运行"

update_mechanism:
  # Phase 8 — fa update / equivalent
  enabled: true
  check_command: "{cli} update --check"
  editable_refusal_check: true
  cache_file: "~/.{project.module_name}/.update_check.json"

llm_providers:
  # Informational — used by Phase 0 baseline + Phase 5 fake-key insertion
  - env_var: "DASHSCOPE_API_KEY"
    name: "qwen"
    required: false                      # at least one of `required=true` providers must be set during preflight
  - env_var: "DEEPSEEK_API_KEY"
    name: "deepseek"
  - env_var: "OPENAI_API_KEY"
    name: "openai"
  - env_var: "ANTHROPIC_API_KEY"
    name: "anthropic"
  at_least_one_required: true
  fake_key_for_tests: "sk-test-fake-key-for-wizard-smoke-only"

i18n:
  # Phase 10 — full wizard run per language
  enabled: true
  languages: ["zh", "en"]
  missing_key_marker: "[missing:"        # if found in stdout → fail
  env_var: "FA_LANG"

doc_cross_check:
  # Phase 9 — version drift + forbidden-string sweeps
  enabled: true
  paths:
    - "README.md"
    - "README_zh.md"
    - "docs/setup/install.md"
    - "docs/setup/zero_to_report.md"
    - "docs/setup/beginner_zh.md"
    - "docs/setup/release_checklist.md"
  forbidden_strings:                     # if any path contains these → warn
    - "pip install {project.package_name}[serve]"   # for projects that retired this extra
  changelog_path: "CHANGELOG.md"
  changelog_min_chars: 200
  changelog_min_sections: 3              # min ### Added/Changed/Fixed sections

testpypi:
  enabled: true
  repository: "testpypi"                 # passes to twine --repository
  index_url: "https://test.pypi.org/simple/"
  skip_existing: true

pypi:
  enabled: true
  repository: "pypi"
  index_url: "https://pypi.org/simple/"
  verify_url_template: "https://pypi.org/pypi/{project.package_name}/json"
  verify_timeout_seconds: 45

git:
  main_branch: "main"
  tag_format: "v{version}"
  release_actions_url_template: "{project.repo_url}/actions"

asset_packages:
  # Phase 16 — pluggable asset publishers, run sequentially
  - kind: "huggingface"
    enabled: true
    repo: "yifishbossman/financial-analyst-data-demo"
    publish_command: "python scripts/publish_hf_dataset.py --preset demo --repo {repo}"
    trigger:                             # only runs if matching changes detected
      type: "git_diff"
      since: "HEAD~2"
      paths: ["src/{project.module_name}/data/", "src/{project.module_name}/_resources/"]
    required_env: "HUGGINGFACE_TOKEN"
    warn_only_if_no_env: true

final_report:
  output_path: "docs/release_reports/v{version}.md"
  status_emoji:
    pass: "✓"
    warn: "⚠"
    fail: "✗"

phases:
  # Master enable/disable switches — false skips with status ⚠
  enabled:
    preflight: true
    lint: true
    tests: true
    build: true
    cli_smoke: true
    wizard_sim: true                     # set false if no wizard
    launcher_preflight: true             # set false if no web UI
    fast_path: true                      # set false if launcher doesn't have fast-path
    update_mechanism: true               # set false if no update command
    doc_cross_check: true
    i18n_smoke: true                     # set false if monolingual
    changelog_gate: true
    testpypi_publish: true
    testpypi_replay: true
    pypi_publish: true
    git_tag_push: true
    asset_package: true
    final_report: true                   # always recommended
```

**Template variables:** `{version}`, `{project.name}`, `{project.package_name}`, `{project.module_name}`, `{cli}`, `{repo}` — workflow expands these at execution time. `{cli}` iterates over `project.cli_aliases`.

---

## Per-phase detail (project-agnostic)

### Phase 0 — Preflight

**Generic intent:** Verify the repo is in a releasable state. No code changes, just observation.

**Checks:**
- git working tree clean (`git status --porcelain` empty)
- All `version_sources` agree on a single version string
- Not running inside an editable install (would publish stale wheel)
- Optional health probe (configured command, e.g. `{cli} doctor`) captures baseline
- Maintainer's workspace pointer (if `workspace.enabled`) snapshotted for restoration

**Pluggability:** Add additional `version_sources` for project-specific files (README badges, Tauri config, etc.). Doctor command is optional.

---

### Phase 1 — Static & lint

Runs `linter.command`. Fails on non-zero exit. Format check (`linter.format_check`) runs separately, warns only.

---

### Phase 2 — Test suite

Runs `tests.command`. Fails on non-zero exit. If total wall time exceeds `tests.max_seconds`, warns: "tests may have hit real network/LLM — confirm mocks intact".

---

### Phase 3 — Build artifacts

`build.command` produces wheel + sdist. `twine check` validates. Wheel content sanity: every prefix in `build.wheel_must_contain` must appear in `zipfile.namelist()`. Catches missing `package_data` / `MANIFEST.in` regressions.

---

### Phase 4 — CLI surface smoke

For each `cli_alias × command` pair, run and assert exit 0 and ≥1 non-empty line of stdout. The `version_command` additionally asserts stdout contains the resolved `{version}`.

This is structural — does not need LLM keys, data, or network.

---

### Phase 5 — Wizard simulation (if `wizard.enabled`)

**Setup:**
1. Snapshot maintainer's `workspace.pointer_file` (per `workspace.env_var`)
2. Create isolated workspace: `/tmp/{module_name}-test-{timestamp}/`
3. Pre-populate workspace `.env` with `llm_providers.fake_key_for_tests` against the first provider
4. Export `workspace.env_var = <isolated path>`

**Run:**
```
{cli} {wizard.command} {flags.non_interactive} {flags.skip_assets} {flags.language} zh
```

**Verify:**
- All `wizard.expected_outputs` exist in the isolated workspace
- workspace pointer file now contains the isolated path
- Re-run with `--lang en`: backup file matching `wizard.backup_pattern` appears
- Re-run output contains the second language's strings (not `missing_key_marker`)

**Teardown:**
- Restore maintainer's pointer
- `rm -rf` the isolated workspace

---

### Phase 6 — Launcher preflight (if `launcher.enabled`)

**Checks:**
- Every entry in `launcher.required_imports` is `import`-able
- Every entry in `launcher.ports` has `_port_free` == True
- Workspace probe: simulating `_env_has_llm_key` from cwd/workspace/repo finds the key correctly (verifies multi-path resolution implementation)

---

### Phase 7 — Fast-path verification (if `launcher.enabled`)

1. Run `launcher.command --no-browser` in background (or equivalent; workflow detects platform)
2. Wait for each `launcher.ports[*].health_url` to return 2xx (timeout 30s)
3. Spawn 2nd invocation, capture first 5s stdout
4. Assert ≥1 of `launcher.fast_path_markers` appears in stdout
5. Kill both processes

---

### Phase 8 — Update mechanism (if `update_mechanism.enabled`)

1. `update_mechanism.check_command` exits 0 (no install attempted)
2. If `editable_refusal_check`: inside editable install context, full update command refuses with exit 1
3. `update_mechanism.cache_file` exists after step 1

---

### Phase 9 — Doc cross-check

For each path in `doc_cross_check.paths`:
- Grep for `forbidden_strings` (template-expanded) — any hit → warning
- Grep for old version strings that should be `{version}` now (heuristic: any `\d+\.\d+\.\d+` that's NOT current version)

For `doc_cross_check.changelog_path`:
- Top-level section heading matches `## [{version}]`
- Section body length ≥ `changelog_min_chars`
- Section contains ≥ `changelog_min_sections` `### …` sub-headings

---

### Phase 10 — i18n smoke (if `i18n.enabled`)

For each language in `i18n.languages`:
- Run `{cli} {wizard.command} {flags.non_interactive} {flags.skip_assets} {flags.language} {lang}` in isolated workspace
- Capture stdout
- Assert no occurrence of `i18n.missing_key_marker`

Workspace isolation same as Phase 5; each language gets its own subdir.

---

### Phase 11 — Changelog gate

Stage all configured `doc_cross_check.paths` + pyproject.toml + tauri.conf.json (if present) + CHANGELOG.md. Commit with message `chore: prepare release v{version}`. Skip if nothing staged. Record commit hash.

---

### Phase 12 — TestPyPI publish (if `testpypi.enabled`)

`twine upload --repository {testpypi.repository} --skip-existing dist/{project.package_name}-{version}*`

`--skip-existing` ensures workflow re-runs after partial failure are safe.

---

### Phase 13 — TestPyPI replay (full user journey)

**The heart of the workflow.** Simulates "fresh user installs from TestPyPI and runs the wizard".

1. Create temp venv: `python -m venv /tmp/{module_name}_replay_venv`
2. Install:
   ```
   pip install \
     --index-url {testpypi.index_url} \
     --extra-index-url {pypi.index_url} \
     {project.package_name}=={version}
   ```
3. From completely clean state:
   - `python -m {project.module_name} version` → must contain `{version}`
   - `python -m {project.module_name} init --yes --preset skip --workspace /tmp/{module_name}_replay_ws --lang {i18n.languages[0]}`
   - Optionally `python -m {project.module_name} doctor` if configured
4. Verify expected files in replay workspace
5. Teardown both temp dirs

This catches: missing `__version__` bump, wheel missing `package_data`, wizard imports unbundled module, etc.

---

### Phase 14 — Real PyPI publish ⚠ IRREVERSIBLE

`twine upload dist/{project.package_name}-{version}*`. Wait 15s. Poll `{pypi.verify_url_template}` until response JSON `info.version == {version}`, max `pypi.verify_timeout_seconds`.

---

### Phase 15 — Git tag + push ⚠ IRREVERSIBLE

```
git tag {git.tag_format}
git push origin {git.main_branch}
git push origin {git.tag_format}
```

Record `{git.release_actions_url_template}` (does not wait for CI).

---

### Phase 16 — Asset packages (if `asset_package` enabled, per item)

For each entry in `asset_packages`:
- Evaluate `trigger`:
  - `git_diff`: run `git diff {since} -- {paths…}`, proceed if non-empty
  - (future) other trigger types
- If `required_env` not set: warn only, skip publish
- Otherwise run `publish_command` with template expansion
- Capture exit code + output

Currently supports `kind: huggingface`; pluggable for `s3`, `gcs`, `custom_script` in future.

---

### Phase 17 — Final report

Write to `final_report.output_path` (template-expanded) **and** print to stdout. Format:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 {project.name} v{version} Release Report — {timestamp}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 TRACK A — Build & Publish
   Phase  0  Preflight             {emoji}
   …
 Status: {RELEASED | RELEASED with N warnings | ABORTED at Phase X}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Action items:
   {emoji} Phase X: <detail>
Next steps (manual):
   …
```

---

## How a new project adopts this workflow

```
1. Copy release.workflow.mjs   →   <new-project>/release.workflow.mjs
2. Write release.workflow.config.yaml against the schema above
3. Verify ~/.pypirc has [testpypi] + [pypi] tokens for the new project
4. Run: ultrawork
```

That's it. No editing the `.mjs`. If the new project lacks (say) a wizard or web UI, set the relevant `phases.enabled.*` to `false`.

---

## Data flow between phases

```javascript
// All phases read from the same config object loaded once at start
const cfg = loadConfig("release.workflow.config.yaml")

// TRACK A — sequential, fail-fast
const preflight  = await phaseAgent("preflight", cfg, { schema: ... })
if (!preflight.ok) return earlyReport(preflight)

const lint   = await phaseAgent("lint", cfg, { schema: ... })
const tests  = await phaseAgent("tests", cfg, { schema: ... })
const build  = await phaseAgent("build", cfg, { schema: ... })

// TRACK B — parallel (each agent gets its own workspace dir)
const [cliS, wizS, launchS, fastpS, updS] = await Promise.all([
  phaseAgent("cli_smoke",          cfg, { ... }),
  phaseAgent("wizard_sim",         cfg, { ... }),
  phaseAgent("launcher_preflight", cfg, { ... }),
  phaseAgent("fast_path",          cfg, { ... }),
  phaseAgent("update_mechanism",   cfg, { ... }),
])

// TRACK C — parallel
const [docs, i18n] = await Promise.all([
  phaseAgent("doc_cross_check", cfg, { ... }),
  phaseAgent("i18n_smoke",      cfg, { ... }),
])

// Sequential to release
const changelog = await phaseAgent("changelog_gate",   cfg, { ... })
const testpypi  = await phaseAgent("testpypi_publish", cfg, { ... })
const replay    = await phaseAgent("testpypi_replay",  cfg, { ... })
// ── IRREVERSIBLE BOUNDARY ──
const pypi      = await phaseAgent("pypi_publish",     cfg, { ... })
const tag       = await phaseAgent("git_tag_push",     cfg, { ... })
const assets    = await phaseAgent("asset_package",    cfg, { ... })

return await phaseAgent("final_report", { ...allResults, cfg })
```

Each phase agent receives `cfg` + previous phase outputs and returns a schema-conforming JSON. The `.mjs` does NOT contain project names, paths, ports, or commands — only the orchestration logic.

---

## Error handling & fail-fast

- Any phase returning `success: false` on a non-optional field → workflow exits with partial report
- Phases 0–13 reversible — workflow can be safely re-run
- Phase 12 uses `--skip-existing` so re-runs past TestPyPI don't error
- Phase 5/13 use unique `/tmp/{module_name}-test-{timestamp}` paths — concurrent runs and re-runs don't collide
- Phase 0 snapshots maintainer state (workspace pointer); Phase 17 restores it even on failure (try/finally semantics)

---

## Prerequisites (per consuming project)

| Requirement | Failure mode |
|-------------|--------------|
| `~/.pypirc` with `[{testpypi.repository}]` and `[{pypi.repository}]` tokens | Phase 12/14 fail |
| `git remote get-url origin` resolves | Phase 15 fail |
| `{asset_packages[*].required_env}` env (if asset publish desired) | Phase 16 warns |
| Active venv with `dev` extras (linter, test, build, twine) | Phase 1/2/3 fail |
| `bash` in PATH (or workflow falls back to platform shell) | Phase 5/7/13 degraded |
| Ports in `launcher.ports` free | Phase 7 fails |
| `/tmp/` (or `%TEMP%`) writable | Phase 5/13 fail |

---

## Out of scope

- Post-release 24h monitoring (manual)
- Social announcement (manual)
- Docker image / container build (separate pipeline)
- `release.yml`/equivalent CI completion wait (async by design — we trigger, don't wait)
- Rollback procedures (manual, per project's docs)
- Real end-to-end LLM-driven smoke (would need real API key, too expensive per release)

---

## Confirmed decisions (2026-05-26)

1. **Phase 13 install command** → bare `pip install {package_name}` (no extras). Validates that any "promoted-to-core" deps actually work without flags.
2. **Phase 16 asset publishing** → only the asset items marked `enabled: true` AND `trigger` satisfied. Lite/full/large assets configured but not enabled by default.
3. **Phase 10 i18n smoke depth** → full wizard run per declared language with `--preset skip`.
4. **Preflight health probe** → `warn only`. Project's diagnostic command (`fa doctor` for financial-analyst) captures baseline but never blocks release.

---

## Reference impl: financial-analyst config

The financial-analyst project's `release.workflow.config.yaml` populates the schema as follows (abbreviated — full file lives in the repo):

| Field | financial-analyst value |
|-------|-------------------------|
| `project.name` | `financial-analyst` |
| `project.module_name` | `financial_analyst` |
| `project.cli_aliases` | `[fa, financial-analyst]` |
| `workspace.default_path` | `~/.financial-analyst` |
| `workspace.env_var` | `FA_WORKSPACE` |
| `launcher.ports` | backend :9999, ui :5173 |
| `launcher.required_imports` | `[fastapi, uvicorn]` |
| `launcher.fast_path_markers` | `["Already running", "已在运行"]` |
| `llm_providers[0].env_var` | `DASHSCOPE_API_KEY` (qwen, domestic) |
| `llm_providers[1].env_var` | `DEEPSEEK_API_KEY` |
| `llm_providers[2].env_var` | `OPENAI_API_KEY` |
| `llm_providers[3].env_var` | `ANTHROPIC_API_KEY` |
| `i18n.languages` | `[zh, en]` |
| `i18n.env_var` | `FA_LANG` |
| `asset_packages[0].repo` | `yifishbossman/financial-analyst-data-demo` |
| `git.tag_format` | `v{version}` |

A second project (e.g. a future agent) drops in different values for all of these and reuses the same `release.workflow.mjs` unchanged.

---

## Self-review checklist

- [x] No project-specific names hardcoded in the `.mjs` design (only in the example config)
- [x] All 17 phases parameterized through config
- [x] Phases can be individually disabled via `phases.enabled.*`
- [x] Reversible/irreversible boundary clearly marked
- [x] Template variables documented (`{version}`, `{project.*}`, `{cli}`)
- [x] Cross-platform paths considered (`/tmp/` ↔ `%TEMP%`)
- [x] Phase 5/13 workspace isolation prevents cross-project collision
- [x] Asset publisher is pluggable (`kind: huggingface | s3 | custom_script`)
- [x] Reference impl (financial-analyst) maps cleanly to schema
- [x] New-project adoption is 1 file + 0 code edits
