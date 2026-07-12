# tests/test_report_spawn_env.py
"""glmcp/console 后台研报 spawn env 对齐 + config 路由探针(Task 1,TDD)。

背景:glmcp 后台研报 spawn(guanlan_v2/glmcp/server.py `_spawn_background_detached`)
原 env 只补 PYTHONIOENCODING,缺 PYTHONPATH → 子进程吃 venv 里 pinned 旧引擎(缺
news-sentiment 注册)→ 起跑即崩;console 路径(`_call_buddy_report`)本已注入
PYTHONPATH,但 FA_CONFIG_DIR 只靠父进程(9999 server.py `_CONFIG_DIR` setdefault)
继承,未显式钉死。本文件钉死两处构造出的 env 逐项对齐,并真起子进程验证
find_config('llm.yaml') 解析到仓内 config/llm.yaml(而非 pinned workspace)。
"""
import os
import subprocess
import sys
from pathlib import Path

import guanlan_v2.console.api as capi
import guanlan_v2.glmcp.server as gsrv

_REPO = Path(__file__).resolve().parent.parent


def test_glmcp_spawn_env_defaults_when_ambient_unset(monkeypatch):
    """无外部覆盖时:PYTHONPATH 挂仓内 engine/、FA_CONFIG_DIR 落仓内 config/。"""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("FA_CONFIG_DIR", raising=False)
    env = gsrv._report_spawn_env()
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(_REPO / "engine")
    assert Path(env["FA_CONFIG_DIR"]).resolve() == (_REPO / "config").resolve()


def test_glmcp_spawn_env_preserves_existing_pythonpath_prepends_engine(monkeypatch, tmp_path):
    """已有 PYTHONPATH/FA_CONFIG_DIR 时:engine/ 前插(不丢旧条目),FA_CONFIG_DIR 尊重显式覆盖。"""
    other = str(tmp_path / "other")
    pinned_cfg = str(tmp_path / "pinned_config")
    monkeypatch.setenv("PYTHONPATH", other)
    monkeypatch.setenv("FA_CONFIG_DIR", pinned_cfg)
    env = gsrv._report_spawn_env()
    parts = env["PYTHONPATH"].split(os.pathsep)
    assert parts[0] == str(_REPO / "engine")
    assert other in parts
    assert env["FA_CONFIG_DIR"] == pinned_cfg


def test_console_buddy_report_env_aligns_with_glmcp(monkeypatch):
    """两处 spawn env 构造逐项对齐:PYTHONPATH 首段、FA_CONFIG_DIR 解析结果一致。"""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("FA_CONFIG_DIR", raising=False)
    env_console = capi._buddy_report_env()
    env_glmcp = gsrv._report_spawn_env()
    assert env_console["PYTHONPATH"].split(os.pathsep)[0] == env_glmcp["PYTHONPATH"].split(os.pathsep)[0]
    assert Path(env_console["FA_CONFIG_DIR"]).resolve() == Path(env_glmcp["FA_CONFIG_DIR"]).resolve()
    assert capi._REPO_DIR.resolve() == _REPO.resolve()
    assert capi._ENGINE_DIR.resolve() == (_REPO / "engine").resolve()


def test_config_routing_probe_resolves_to_repo_config(monkeypatch):
    """探针:真起子进程用修后 env 跑 find_config('llm.yaml'),必须解析到仓内
    config/llm.yaml,不被 pinned workspace(如 G:\\financial-analyst\\config)遮蔽。
    engine 经 PYTHONPATH 注入,子进程自身 cwd/sys.path 均无需预置 engine。"""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("FA_CONFIG_DIR", raising=False)
    env = gsrv._report_spawn_env()
    proc = subprocess.run(
        [sys.executable, "-c",
         "from financial_analyst._config import find_config; print(find_config('llm.yaml'))"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, cwd=str(_REPO), env=env,
    )
    assert proc.returncode == 0, f"探针子进程失败: {proc.stderr}"
    resolved = Path(proc.stdout.strip()).resolve()
    assert resolved == (_REPO / "config" / "llm.yaml").resolve()
