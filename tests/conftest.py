import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest


@pytest.fixture(autouse=True)
def _ci_safe_defaults(tmp_path_factory, monkeypatch):
    """CI / fresh-machine safety net — applied to EVERY test.

    Local dev has `DASHSCOPE_API_KEY` set, `G:/stocks/stock_data/` on disk,
    and an interactive TTY. CI runners have none of these, which used to
    break ~10 tests at random. Set sane defaults so tests are env-agnostic:

    - `NO_COLOR=1` + `COLUMNS=200`: disable Rich ANSI rendering + widen the
      virtual terminal so typer help text doesn't word-wrap. Fixes the
      `assert "--source" in result.stdout` family of failures where rich
      panel borders chop option names mid-string.
    - `FA_QLIB_URI` -> tmp path: prevents `qlib.init(provider_uri=...)`
      from trying the dev fallback `G:/stocks/stock_data/cn_data` on Linux
      runners and raising `ValueError: provider_uri does not exist`.
    - `DASHSCOPE_API_KEY=fake`: the `/models` SSE endpoint filters out
      providers without `*_API_KEY` set. Tests that assert `qwen` appears
      in the list would otherwise fail when run without local creds.

    Individual tests can monkeypatch their own values on top — autouse
    fixtures run before per-test fixtures.
    """
    import os
    # Kill rich/typer's fancy panel rendering in CliRunner — without this,
    # `--help` output gets wrapped in a Rich Panel whose option names land in
    # truncated content cells, so `assert "--trace" in result.stdout` fails
    # despite the option existing. We're aggressive about disabling it
    # because rich/typer/click each look at different env vars:
    #   - NO_COLOR (XDG-style, rich, modern click)
    #   - TERM=dumb (legacy terminals — rich treats this as no-tty)
    #   - CLICOLOR=0, CLICOLOR_FORCE=0, FORCE_COLOR=0 (click / colorama / rich)
    #   - _TYPER_STANDARD_TRACEBACK=1 (typer-specific: plain tracebacks)
    #   - COLUMNS=200 (rich/click panel width — wide enough to prevent wrap)
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("CLICOLOR", "0")
    monkeypatch.setenv("CLICOLOR_FORCE", "0")
    monkeypatch.setenv("FORCE_COLOR", "0")
    monkeypatch.setenv("_TYPER_STANDARD_TRACEBACK", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("FA_E2E", "0")
    # Force-disable rich's terminal detection at the lib level — covers the
    # case where typer constructs its own Console with auto-detected width
    # that doesn't honour the COLUMNS env var.
    try:
        import rich.console as _rich_console
        original_init = _rich_console.Console.__init__

        def _patched_init(self, *args, **kwargs):
            kwargs.setdefault("force_terminal", False)
            kwargs.setdefault("no_color", True)
            kwargs.setdefault("width", 200)
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(_rich_console.Console, "__init__", _patched_init)
    except Exception:
        pass

    # Build a fake Qlib root (existing dir tree so QlibBinaryLoader's
    # `provider_uri.exists()` check passes — see qlib_binary.py:112).
    fake_qlib_root = tmp_path_factory.mktemp("ci_fake_qlib")
    (fake_qlib_root / "calendars").mkdir()
    (fake_qlib_root / "calendars" / "day.txt").write_text("2026-05-01\n", encoding="utf-8")
    (fake_qlib_root / "instruments").mkdir()
    (fake_qlib_root / "instruments" / "all.txt").write_text("", encoding="utf-8")
    (fake_qlib_root / "features").mkdir()
    # NOTE: deliberately NOT setting FA_QLIB_URI — env var would take priority
    # over yaml in get_data_paths() and break tests that exercise yaml-based
    # resolution. The find_config patch below routes loader_factory et al to
    # the fake yaml; that's enough.

    # Override `find_config("loaders.yaml")` so loader_factory + the dream CLI
    # never resolve to the bundled default (which hardcodes G:/stocks/... and
    # crashes Linux CI on QlibBinaryLoader init).
    fake_cfg_dir = tmp_path_factory.mktemp("ci_fake_config")
    fake_loaders_yaml = fake_cfg_dir / "loaders.yaml"
    fake_loaders_yaml.write_text(
        "default: qlib_binary\n"
        "loaders:\n"
        "  qlib_binary:\n"
        f"    provider_uri:\n      day: {fake_qlib_root}\n"
        f"    parquet_root: {fake_qlib_root}\n"
        f"    news_data_root: {fake_qlib_root}\n",
        encoding="utf-8",
    )
    try:
        import financial_analyst._config as _cfg_mod
        original_find = _cfg_mod.find_config

        def _fake_find_config(name, explicit=None):
            if explicit is not None:
                return original_find(name, explicit=explicit)  # honour test-supplied path
            if name == "loaders.yaml":
                return fake_loaders_yaml
            return original_find(name)

        monkeypatch.setattr(_cfg_mod, "find_config", _fake_find_config)
        # Also patch the binding inside loader_factory which imports `find_config`
        # directly (the `from ... import find_config` binding is independent).
        try:
            import financial_analyst.data.loader_factory as _lf_mod
            monkeypatch.setattr(_lf_mod, "find_config", _fake_find_config)
        except Exception:
            pass
        try:
            import financial_analyst.data.paths as _paths_mod
            monkeypatch.setattr(_paths_mod, "find_config", _fake_find_config)
        except Exception:
            pass
    except Exception:
        pass

    for k in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(k):
            monkeypatch.setenv(k, "fake-for-tests")
    yield


@pytest.fixture(autouse=True)
def _isolate_buddy_prefs(tmp_path, monkeypatch):
    """v1.7.5: BuddyApp persists permission_mode + model to
    ~/.financial-analyst/buddy.yaml. Redirect that to a per-test tmp file
    so tests neither read the developer's real prefs nor clobber them."""
    prefs = tmp_path / "buddy.yaml"
    try:
        from financial_analyst.buddy.app import BuddyApp
        monkeypatch.setattr(BuddyApp, "_prefs_path", staticmethod(lambda: prefs))
    except Exception:
        pass
    yield


@pytest.fixture(autouse=True)
def _clear_net_caches():
    """v1.9.6: net.py @rate_limited 给每个 source 配了短 TTL 缓存
    (xueqiu 30s, xueqiu_hot 60s, tencent 2s). 同 session 多个 test 用同 args
    调同一 collector 会命中 cache, mock 的 fake response 不生效.
    每个 test 跑前清 source cache 保证独立."""
    try:
        from financial_analyst.data.net import _clear_all_caches
        _clear_all_caches()
    except Exception:
        pass
    yield
