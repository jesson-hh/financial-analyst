"""``fa init`` — 第一次启动引导.

走完这步用户应该能 ``fa report SH600519``. 涉及:
  1. 检测/接收 LLM key (DASHSCOPE 必填, OpenAI/Anthropic 可选)
  2. 可选 Tushare token (有了走完整 daily_basic 路径, 没有走直连)
  3. 选数据包 demo/lite/full/skip (从 HuggingFace 下)
  4. 写 .env + config/loaders.yaml 到正确位置
  5. 跑 fa data status 验证

设计原则:
  - 任何一步都可以 enter 跳过 (走 default)
  - 已存在的 .env / config 备份, 不直接覆盖
  - 失败有清晰错误 + 下一步建议
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


# ──────────────────────── 包预设 (HF repo) ────────────────────────


HF_PACKAGES = {
    "demo": {
        "repo_id":      "jesson-hh/financial-analyst-data-demo",
        "size_hint":    "~500 MB",
        "n_stocks":     "~900 (csi300 累积成份)",
        "description":  "单股研报演示足够. 日线全历史 + 当日 PE/PB/MV. 无 5min.",
    },
    "lite": {
        "repo_id":      "jesson-hh/financial-analyst-data-lite",
        "size_hint":    "~5 GB",
        "n_stocks":     "~1500 (csi800 累积成份)",
        "description":  "中等用户. 含 5min ~7 天 + financials.",
    },
    "full": {
        "repo_id":      "jesson-hh/financial-analyst-data-full",
        "size_hint":    "~50 GB",
        "n_stocks":     "全 A 股 5500+",
        "description":  "量化研究员 / 重度. 全 freq 全历史.",
    },
}


# ──────────────────────── 提示 helpers ────────────────────────


def _project_root() -> Path:
    """启发式找 financial-analyst 安装根. dev: 项目根; pip install: 用户家目录."""
    src = Path(__file__).resolve().parent.parent.parent   # …/src/financial_analyst → …
    if (src / "pyproject.toml").exists():
        return src
    home = Path.home() / ".financial-analyst"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _backup_if_exists(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, backup)
    console.print(f"  📦 backed up existing → [dim]{backup.name}[/dim]")


def _read_env(path: Path) -> dict:
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env(path: Path, env: dict) -> None:
    _backup_if_exists(path)
    keys_order = [
        "DASHSCOPE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY", "TUSHARE_TOKEN", "HUGGINGFACE_TOKEN",
        "FA_LOG_LEVEL", "FA_CACHE_DIR", "FA_DATA_DIR", "FA_MAINLINE_PANEL",
    ]
    lines = []
    written = set()
    for k in keys_order:
        if k in env:
            lines.append(f"{k}={env[k]}")
            written.add(k)
    # any keys not in order: append at end
    for k, v in env.items():
        if k not in written:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ──────────────────────── 步骤 ────────────────────────


def _step_welcome() -> None:
    console.print(Panel.fit(
        "[bold cyan]financial-analyst 首次启动向导[/bold cyan]\n\n"
        "这个向导会引导你完成 3 件事:\n"
        "  1. 填 LLM API key (必须)\n"
        "  2. 选数据包 (推荐) 或自己来 (高级)\n"
        "  3. 写 .env + config/loaders.yaml\n\n"
        "走完后 [bold]fa report SH600519[/bold] 应该能跑出第一份研报.",
        title="🚀 fa init",
        border_style="cyan",
    ))


def _step_llm_keys(env: dict, non_interactive: bool) -> dict:
    console.print("\n[bold]Step 1/3 — LLM API key[/bold]")
    console.print("[dim]至少填一个. 推荐 DashScope (阿里云百炼, 注册送 100w token).[/dim]")
    if not non_interactive:
        if not env.get("DASHSCOPE_API_KEY"):
            v = Prompt.ask("  DASHSCOPE_API_KEY (回车跳过)", default="", show_default=False)
            if v.strip():
                env["DASHSCOPE_API_KEY"] = v.strip()
        else:
            console.print(f"  ✓ DASHSCOPE_API_KEY 已存在 ({env['DASHSCOPE_API_KEY'][:8]}...)")
        if not env.get("OPENAI_API_KEY"):
            v = Prompt.ask("  OPENAI_API_KEY (可选, 回车跳过)", default="", show_default=False)
            if v.strip():
                env["OPENAI_API_KEY"] = v.strip()
        if not env.get("ANTHROPIC_API_KEY"):
            v = Prompt.ask("  ANTHROPIC_API_KEY (可选, 回车跳过)", default="", show_default=False)
            if v.strip():
                env["ANTHROPIC_API_KEY"] = v.strip()

    has_any = any(env.get(k) for k in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY",
                                       "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"))
    if not has_any:
        console.print("[red]⚠ 没有任何 LLM key. agent 跑不起来.[/red]")
        console.print("[dim]  你可以现在跳过, 之后手动编辑 .env 再继续.[/dim]")
    return env


def _step_tushare(env: dict, non_interactive: bool) -> dict:
    console.print("\n[bold]Step 2/3 — Tushare token (可选)[/bold]")
    console.print("[dim]没填: 走 pytdx + 腾讯直连, 完全免费, 0 配置.[/dim]")
    console.print("[dim]填了: 走完整 daily_basic 历史 (含 ps_ttm/dv_ttm).[/dim]")
    if not non_interactive:
        if not env.get("TUSHARE_TOKEN"):
            v = Prompt.ask("  TUSHARE_TOKEN (没有就回车)", default="", show_default=False)
            if v.strip():
                env["TUSHARE_TOKEN"] = v.strip()
        else:
            console.print(f"  ✓ TUSHARE_TOKEN 已存在 ({env['TUSHARE_TOKEN'][:8]}...)")
    return env


def _step_pick_package(non_interactive: bool, preset: Optional[str]) -> Optional[str]:
    console.print("\n[bold]Step 3/3 — 历史数据包[/bold]")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("选项")
    table.add_column("包名")
    table.add_column("大小")
    table.add_column("说明")
    table.add_row("1", "demo", HF_PACKAGES["demo"]["size_hint"], HF_PACKAGES["demo"]["description"])
    table.add_row("2", "lite", HF_PACKAGES["lite"]["size_hint"], HF_PACKAGES["lite"]["description"])
    table.add_row("3", "full", HF_PACKAGES["full"]["size_hint"], HF_PACKAGES["full"]["description"])
    table.add_row("4", "skip", "—", "已经有数据 / 自己跑 fa data update 从零拉")
    console.print(table)

    if non_interactive:
        return preset

    if preset:
        return preset

    choice = Prompt.ask("  选择 [1/2/3/4]", default="1", choices=["1", "2", "3", "4"])
    return {"1": "demo", "2": "lite", "3": "full", "4": "skip"}[choice]


def _download_package(preset: str, target: Path) -> bool:
    """从 HuggingFace 下载数据包到 target."""
    pkg = HF_PACKAGES[preset]
    console.print(f"\n  📥 下载 [cyan]{pkg['repo_id']}[/cyan] → {target}")
    console.print(f"  [dim]大约 {pkg['size_hint']}, 看网速 1-30 min[/dim]")

    target.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        console.print("[red]✗ 没装 huggingface_hub. 跑: pip install huggingface_hub[/red]")
        return False

    t0 = time.time()
    try:
        snapshot_download(
            repo_id=pkg["repo_id"],
            repo_type="dataset",
            local_dir=str(target),
            local_dir_use_symlinks=False,
        )
        console.print(f"  ✓ 下载完成 ({time.time() - t0:.0f}s)")
        return True
    except Exception as e:
        console.print(f"[red]✗ 下载失败: {type(e).__name__}: {e}[/red]")
        console.print("[dim]  可能原因: 1) 没网 2) HF 国内偶尔需要代理 3) repo 不存在 (我们还没 publish)[/dim]")
        return False


def _write_loaders_config(data_dir: Path, config_path: Path) -> None:
    """写 config/loaders.yaml 指向下载的目录."""
    _backup_if_exists(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Auto-generated by fa init {datetime.now():%Y-%m-%d %H:%M}
default: qlib_binary

loaders:
  qlib_binary:
    provider_uri:
      day: {data_dir}/cn_data
      5min: {data_dir}/cn_data_5min
"""
    config_path.write_text(text, encoding="utf-8")
    console.print(f"  ✓ 写 {config_path}")


def _verify(data_dir: Path) -> bool:
    """跑 fa data status 验证下载完整."""
    console.print("\n[bold]验证[/bold]")
    try:
        from financial_analyst.data.bin_writer import (
            load_calendar, load_instruments,
        )
        day_uri = str(data_dir / "cn_data")
        inst = load_instruments(day_uri, market="all")
        cal = load_calendar(day_uri, freq="day")
        if not inst or not cal:
            console.print(f"[red]✗ 验证失败 — instruments={len(inst)} calendar={len(cal)}[/red]")
            return False
        console.print(f"  ✓ {len(inst)} 只 instruments, {len(cal)} 天日历")
        console.print(f"  ✓ 日线范围: {cal[0]} → {cal[-1]}")
        return True
    except Exception as e:
        console.print(f"[red]✗ 验证报错: {type(e).__name__}: {e}[/red]")
        return False


# ──────────────────────── CLI 入口 ────────────────────────


def init_cmd(
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="非交互模式, 走全部 default (跳过任何 prompt)"),
    preset: Optional[str] = typer.Option(
        None, "--preset",
        help="数据包预设 demo/lite/full/skip (非交互模式必填)"),
    target: Optional[Path] = typer.Option(
        None, "--target",
        help="数据目标目录 (默认 ~/.financial-analyst/data/)"),
):
    """首次启动向导 — 配 LLM + 数据 + 验证.

    Examples:
      fa init                                # 交互
      fa init --yes --preset demo            # 全自动 demo
      fa init --target /mnt/data --preset full
    """
    _step_welcome()

    root = _project_root()
    env_path = root / ".env"
    config_path = root / "config" / "loaders.yaml"
    data_dir = target or (Path.home() / ".financial-analyst" / "data")

    console.print(f"\n[dim]项目根:  {root}[/dim]")
    console.print(f"[dim].env:    {env_path}[/dim]")
    console.print(f"[dim]loaders: {config_path}[/dim]")
    console.print(f"[dim]data:    {data_dir}[/dim]")

    # 读已有 env
    env = _read_env(env_path)

    # Steps
    env = _step_llm_keys(env, non_interactive=yes)
    env = _step_tushare(env, non_interactive=yes)

    chosen_preset = _step_pick_package(non_interactive=yes, preset=preset)

    if chosen_preset and chosen_preset != "skip":
        ok = _download_package(chosen_preset, data_dir)
        if ok:
            _write_loaders_config(data_dir, config_path)
            _verify(data_dir)
        else:
            console.print("[yellow]⚠ 数据包没下下来, 但 .env 仍会写. 你可以之后再:[/yellow]")
            console.print("  fa init --preset demo  # 重试下载")
            console.print("  fa data bootstrap --preset demo  # 单独跑下载")
    elif chosen_preset == "skip":
        console.print("\n  ⏭ 跳过数据包下载. 如已有数据, 编辑 config/loaders.yaml 指向你的目录.")

    # write .env
    _write_env(env_path, env)
    console.print(f"\n  ✓ 写 .env ({len(env)} 个 key)")

    # 完成
    console.print(Panel.fit(
        "[bold green]✓ fa init 完成[/bold green]\n\n"
        "[bold]下一步:[/bold]\n"
        "  [cyan]fa data status[/cyan]                 — 看数据当前状态\n"
        "  [cyan]fa report SH600519[/cyan]             — 跑第一份研报\n"
        "  [cyan]fa serve --port 9999[/cyan]           — 起 GuanLan UI 后端\n"
        "  [cyan]fa data update[/cyan]                 — 每日增量更新数据",
        title="🎉 完成",
        border_style="green",
    ))
