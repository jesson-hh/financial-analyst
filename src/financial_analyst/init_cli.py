"""``fa init`` — first-launch wizard.

After this step the user should be able to run ``fa report SH600519``. It covers:
  0. Pick language (zh / en)
  1. Pick **workspace root** — where to put data / .env / config / out.
     Stock data is large (155 MB demo · 3 GB lite · 14 GB full), so we let
     the user point this at e.g. ``D:\\fa-workspace`` instead of the
     system drive.
  2. Detect / collect LLM keys (DASHSCOPE recommended, OpenAI/Anthropic/DeepSeek optional)
  3. Optional Tushare token (with it: full daily_basic path; without it: direct connection)
  4. Pick data package demo/lite/full/skip (downloaded from HuggingFace)
  5. Write .env + config/loaders.yaml into the workspace
  6. Run fa data status to verify

Design principles:
  - Any step can be skipped via enter (uses default)
  - Existing .env / config get backed up rather than directly overwritten
  - On failure: clear error + next-step suggestion
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
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

console = Console()


# ──────────────────────── package presets (HF repo) ────────────────────────


HF_PACKAGES = {
    "demo": {
        "repo_id":      "yifishbossman/financial-analyst-data-demo",
        "size_hint":    "~155 MB",
        "n_stocks":     {"zh": "300 (当前 CSI300 by mv)", "en": "300 (current CSI300 by mv)"},
        "description":  {
            "zh": "演示包. 全历史日线 + 估值 + 核心 parquet. 无 5min.",
            "en": "Demo. Daily OHLCV + valuation + core parquet. No 5min.",
        },
        "eta":          "~3 min",
        "best_for":     {"zh": "试用 · 看看流程", "en": "Try it out · see flow"},
        "color":        "green",
    },
    "lite": {
        "repo_id":      "yifishbossman/financial-analyst-data-lite",
        "size_hint":    "~3 GB",
        "n_stocks":     {"zh": "800 (CSI800 ≈)", "en": "800 (CSI800 ≈)"},
        "description":  {
            "zh": "含 5min ~7 天 + 完整财务报表 (735 MB) + F10 文本.",
            "en": "5min (~7d) + full financials (735 MB) + F10 text.",
        },
        "eta":          "~30 min",
        "best_for":     {"zh": "日常 · 跑多股研报", "en": "Daily user · multi-stock"},
        "color":        "cyan",
    },
    "full": {
        "repo_id":      "yifishbossman/financial-analyst-data-full",
        "size_hint":    "~14 GB",
        "n_stocks":     {"zh": "全 A 股 5500+ (含退市)", "en": "5500+ all A-share (incl. delisted)"},
        "description":  {
            "zh": "量化研究员. 全 freq + 全 parquet + TDX 历年财报 zip (257 MB).",
            "en": "Researcher. All freq + all parquet + TDX archive zip (257 MB).",
        },
        "eta":          "1-2 hours",
        "best_for":     {"zh": "量化研究 · 因子挖掘", "en": "Quant research · factor mining"},
        "color":        "magenta",
    },
}


_LLM_PROVIDERS = [
    ("DASHSCOPE_API_KEY", "qwen", {
        "zh": "[bold]推荐[/bold] · 注册送 100w token · 国内最快",
        "en": "[bold]Recommended[/bold] · 1M free tokens on signup · fastest in CN",
    }, "green"),
    ("DEEPSEEK_API_KEY", "deepseek", {
        "zh": "推理强 · `deepseek-reasoner` 1/10 价格",
        "en": "Strong reasoning · `deepseek-reasoner` ~1/10 cost",
    }, "cyan"),
    ("OPENAI_API_KEY", "openai", {
        "zh": "通用 fallback · gpt-4o",
        "en": "Universal fallback · gpt-4o",
    }, "yellow"),
    ("ANTHROPIC_API_KEY", "anthropic", {
        "zh": "顶级质量 · claude-opus-4-7 (海外计费)",
        "en": "Top quality · claude-opus-4-7 (USD pricing)",
    }, "magenta"),
]


# ──────────────────────── i18n text bundle ────────────────────────


_T = {
    "welcome_title":      {"zh": "觀瀾 · financial-analyst",
                           "en": "GuanLan · financial-analyst"},
    "welcome_tagline":    {"zh": "个股深度研究 · 24 agent · MCP-ready",
                           "en": "Stock deep-dive research · 24 agents · MCP-ready"},
    "welcome_intro":      {"zh": "这个向导会帮你做 3 件事:",
                           "en": "This wizard will set up 3 things:"},
    "welcome_s1":         {"zh": "LLM API key       ", "en": "LLM API key       "},
    "welcome_s1_sub":     {"zh": "(必须 · 至少 1 个 provider)",
                           "en": "(required · at least one provider)"},
    "welcome_s2":         {"zh": "Tushare token     ", "en": "Tushare token     "},
    "welcome_s2_sub":     {"zh": "(可选 · 不填走 pytdx 直连免费)",
                           "en": "(optional · free pytdx fallback if blank)"},
    "welcome_s3":         {"zh": "数据包预设         ", "en": "Data package      "},
    "welcome_s3_sub":     {"zh": "(demo 155MB / lite 3GB / full 14GB)",
                           "en": "(demo 155MB / lite 3GB / full 14GB)"},
    "welcome_tail_pre":   {"zh": "走完后  ", "en": "After this, "},
    "welcome_tail_cmd":   {"zh": "fa report SH600519",
                           "en": "fa report SH600519"},
    "welcome_tail_post":  {"zh": "  应能跑出第一份研报.",
                           "en": "  should produce your first report."},
    "paths_project":      {"zh": "项目根",       "en": "project root"},
    "paths_env":          {"zh": ".env",         "en": ".env"},
    "paths_loaders":      {"zh": "loaders.yaml", "en": "loaders.yaml"},
    "paths_data":         {"zh": "data dir",     "en": "data dir"},

    "step_word":          {"zh": "步骤", "en": "Step"},

    "step_ws_title":      {"zh": "工作目录 (workspace)", "en": "Workspace location"},
    "step_ws_subtitle":   {"zh": "数据可能 155 MB ~ 14 GB · 选个磁盘空间大的位置",
                           "en": "Data is 155 MB ~ 14 GB · pick a disk with enough room"},
    "step_ws_default":    {"zh": "默认位置 (HOME)", "en": "Default (HOME)"},
    "step_ws_free":       {"zh": "剩余", "en": "free"},
    "step_ws_warn_tight": {"zh": "偏紧 ⚠ — 14 GB full 包可能装不下",
                           "en": "tight ⚠ — 14 GB full package may not fit"},
    "step_ws_recommend":  {"zh": "建议自定义到 D:/E: 盘 (有更多空间)",
                           "en": "Recommend customising to D:/E: drive (more room)"},
    "step_ws_prompt":     {"zh": "[bold]工作目录路径[/bold] [dim](回车用默认)[/dim]",
                           "en": "[bold]Workspace path[/bold] [dim](enter for default)[/dim]"},
    "step_ws_not_writable": {"zh": "✗ 路径不可写, 用默认",
                             "en": "✗ Path not writable, falling back to default"},
    "step_ws_picked":     {"zh": "工作目录已选", "en": "Workspace pinned"},
    "step_ws_kept_default": {"zh": "用默认 HOME 目录", "en": "Using default HOME"},
    "step_ws_old_data_warn": {
        "zh": "[dim]提示: 你旧默认位置可能有数据 (~/.financial-analyst/data/). "
              "切到新 workspace 后, 老数据**不会自动迁移**, 需手动 move 或重新下.[/dim]",
        "en": "[dim]Note: your old default may have data (~/.financial-analyst/data/). "
              "Switching workspace does **not** auto-migrate it; move it manually or re-download.[/dim]",
    },

    "step1_title":        {"zh": "LLM API key", "en": "LLM API key"},
    "step1_subtitle":     {"zh": "至少配一个 · 推荐 DashScope (阿里云百炼)",
                           "en": "At least one · DashScope (Aliyun Bailian) recommended"},
    "step1_col_provider": {"zh": "Provider", "en": "Provider"},
    "step1_col_envvar":   {"zh": "Env var",  "en": "Env var"},
    "step1_col_state":    {"zh": "状态",     "en": "Status"},
    "step1_col_desc":     {"zh": "说明",     "en": "Notes"},
    "step1_state_set":    {"zh": "已配置",   "en": "set"},
    "step1_state_unset":  {"zh": "未设置",   "en": "unset"},
    "step1_prompt_recommended": {"zh": "[bold red](强烈推荐填)[/bold red]",
                                  "en": "[bold red](strongly recommended)[/bold red]"},
    "step1_prompt_optional":    {"zh": "[dim](可选, 回车跳过)[/dim]",
                                  "en": "[dim](optional, enter to skip)[/dim]"},
    "step1_prompt_change":      {"zh": "[dim](回车保留, 粘贴新 key 替换, 输入 '-' 清空)[/dim]",
                                  "en": "[dim](Enter to keep, paste new to replace, '-' to clear)[/dim]"},
    "step1_kept":               {"zh": "保留原 key.", "en": "Kept existing key."},
    "step1_replaced":           {"zh": "替换为新 key.", "en": "Replaced with new key."},
    "step1_cleared":            {"zh": "已清空.", "en": "Cleared."},
    "step1_current_label":      {"zh": "当前:", "en": "current:"},
    "step1_no_key_warn_title":  {"zh": "⚠ 注意", "en": "⚠ Heads up"},
    "step1_no_key_warn_body":   {
        "zh": "[yellow]没有任何 LLM key — agent 跑不起来.[/yellow]\n"
              "[dim]你可以先跳过, 之后手动编辑 .env 加上再继续.[/dim]",
        "en": "[yellow]No LLM key set — agents won't run.[/yellow]\n"
              "[dim]You can skip now and edit .env manually later.[/dim]",
    },

    "step2_title":        {"zh": "Tushare token", "en": "Tushare token"},
    "step2_subtitle":     {"zh": "不必填 · 默认走免费数据源",
                           "en": "Optional · free data sources by default"},
    "step2_explainer":    {
        "zh": "[bold]Tushare 是个收费数据源, 你不需要它.[/bold]\n\n"
              "财报 / 行情 / 估值数据 fa 已经做好免费路径:\n"
              "  · 日线 / 5min → pytdx 主站直连 (免费, 国内可用, 数据更新更快)\n"
              "  · 当日 PE/PB/换手 → 腾讯实时报价 (免费, 0 token)\n"
              "  · 历史日线 + 财报 → HuggingFace 数据包 (fa init 下完就有)\n\n"
              "[dim]什么时候才需要 Tushare?[/dim]\n"
              "  你已经付费用 Tushare Pro Pro Max (≥5000 积分), 想拿\n"
              "  ps_ttm / dv_ttm / 北向资金 的完整 5 年历史. [bold]新用户直接回车跳过.[/bold]",
        "en": "[bold]Tushare is a paid data source. You don't need it.[/bold]\n\n"
              "fa already wires up free alternatives for everything:\n"
              "  · Daily / 5min OHLCV → pytdx main stations (free, CN-direct, fresher)\n"
              "  · Today's PE/PB/turnover → Tencent realtime quotes (free, 0 token)\n"
              "  · Historical daily + financials → HuggingFace data bundle (fa init pulls it)\n\n"
              "[dim]When DO you need Tushare?[/dim]\n"
              "  Only if you have a paid Tushare Pro Pro Max (≥5000 credits) and need\n"
              "  ps_ttm / dv_ttm / northbound full 5-year history. [bold]Otherwise press Enter.[/bold]",
    },
    "step2_current":      {"zh": "当前已填:", "en": "Currently set:"},
    "step2_kept":         {"zh": "保留原 token.", "en": "Kept existing token."},
    "step2_with_label":   {"zh": "[bold]填了[/bold]", "en": "[bold]With token[/bold]"},
    "step2_with_desc":    {"zh": "走完整 daily_basic 历史 (含 ps_ttm / dv_ttm / 北向资金)",
                           "en": "Full daily_basic history (incl. ps_ttm / dv_ttm / northbound)"},
    "step2_without_label": {"zh": "[bold]不填[/bold]", "en": "[bold]Without[/bold]"},
    "step2_without_desc": {"zh": "走 pytdx 主站 + 腾讯实时, 完全免费 0 配置",
                           "en": "pytdx main + Tencent realtime, free + zero-config"},
    "step2_prompt":       {"zh": "[dim](没有就回车跳过)[/dim]",
                           "en": "[dim](enter to skip if you don't have one)[/dim]"},

    "step3_title":        {"zh": "历史数据包", "en": "Historical data package"},
    "step3_subtitle":     {"zh": "从 HuggingFace 拉一份 Qlib + Parquet 数据",
                           "en": "Pull a Qlib + Parquet bundle from HuggingFace"},
    "step3_col_preset":   {"zh": "预设",     "en": "Preset"},
    "step3_col_size":     {"zh": "体量",     "en": "Size"},
    "step3_col_stocks":   {"zh": "股票池",   "en": "Stocks"},
    "step3_col_eta":      {"zh": "下载耗时", "en": "Download ETA"},
    "step3_col_bestfor":  {"zh": "适合",     "en": "Best for"},
    "step3_skip_desc":    {"zh": "已有数据 / 自己拉", "en": "Already have data / DIY"},
    "step3_choose":       {"zh": "选择", "en": "Choose"},

    "dl_panel_title":     {"zh": "📥 拉取 {preset} 数据包",
                           "en": "📥 Pulling {preset} package"},
    "dl_panel_eta":       {"zh": "约 {size} · 预计 {eta} · 进度由 huggingface_hub 输出",
                           "en": "~{size} · ETA {eta} · progress streamed by huggingface_hub"},
    "dl_done":            {"zh": "下载完成", "en": "Download complete"},
    "dl_fail":            {"zh": "下载失败", "en": "Download failed"},
    "dl_fail_hints":      {"zh": "可能原因: 1) 没网  2) HF 国内被墙 (set HF_ENDPOINT=https://hf-mirror.com 试镜像)  3) repo 还没 publish",
                           "en": "Possible causes: 1) no network  2) HF blocked in CN (try HF_ENDPOINT=https://hf-mirror.com)  3) repo not yet published"},
    "dl_fail_panel_title": {"zh": "⚠ 数据下载失败", "en": "⚠ Data download failed"},
    "dl_fail_panel_body": {
        "zh": "[yellow]数据包没下下来, 但 .env 仍会写.[/yellow]\n\n"
              "[dim]国内用户备选: 从网盘下 (阿里云盘 / 夸克 — 不走 hf.co):[/dim]\n"
              "  详细步骤见 [bold cyan]docs/setup/data_offline.md[/bold cyan]\n\n"
              "[dim]或者再试 HF:[/dim]\n"
              "  [cyan]set HF_ENDPOINT=https://hf-mirror.com[/cyan]   # 用 hf-mirror\n"
              "  [cyan]fa init --preset demo[/cyan]                  # 重试整个 wizard\n"
              "  [cyan]fa data bootstrap --preset demo[/cyan]        # 只重跑下载",
        "en": "[yellow]Package not downloaded, but .env will still be written.[/yellow]\n\n"
              "[dim]CN users alternative: download from a cloud drive (Aliyun / Quark — bypasses hf.co):[/dim]\n"
              "  see [bold cyan]docs/setup/data_offline.md[/bold cyan]\n\n"
              "[dim]Or retry HF with a mirror:[/dim]\n"
              "  [cyan]set HF_ENDPOINT=https://hf-mirror.com[/cyan]   # use hf-mirror\n"
              "  [cyan]fa init --preset demo[/cyan]                  # full wizard again\n"
              "  [cyan]fa data bootstrap --preset demo[/cyan]        # download only",
    },

    "step_opencli_title":     {"zh": "可选: OpenCLI", "en": "Optional: OpenCLI"},
    "step_opencli_subtitle":  {"zh": "装上才能搜雪球 / 同花顺 F10 / 新闻", "en": "Needed for xueqiu / THS / news collectors"},
    "step_opencli_have":      {"zh": "[green]✓[/green] 已检测到 OpenCLI 在 PATH 上, 完整功能可用.",
                                "en": "[green]✓[/green] OpenCLI detected on PATH — full features available."},
    "step_opencli_missing":   {
        "zh": "[yellow]未检测到 OpenCLI[/yellow] (这是个 Node.js 命令行, 给 agent 抓雪球 / 同花顺 / 新闻 用).\n\n"
              "[bold]没装也可以现在跑[/bold] — 基本研报 (估值 / 技术 / 量化) 全都用本地数据, 不依赖 OpenCLI.\n"
              "受影响的: 研报里的[bold]新闻段会空[/bold], `fa news-collect` 跑不起来, UI 里搜雪球 / F10 会报错.\n\n"
              "[dim]想装的话 (5 分钟):[/dim]\n"
              "  1. 装 Node.js ≥ 21:  [cyan]https://nodejs.org/en/download/[/cyan]\n"
              "  2. 装 opencli:        [cyan]npm install -g @jackwener/opencli[/cyan]\n"
              "  3. 验证:              [cyan]opencli --version[/cyan]\n\n"
              "[dim]详细 (含 Chrome 扩展 + ths-extra 插件) 见 [bold]docs/setup/beginner_zh.md[/bold] Step 8.[/dim]",
        "en": "[yellow]OpenCLI not detected[/yellow] (a Node.js CLI that lets agents fetch xueqiu / THS F10 / news).\n\n"
              "[bold]You can run without it[/bold] — basic reports (valuation / technical / quant) use local data only.\n"
              "What's affected: report [bold]news section will be empty[/bold], `fa news-collect` won't run, UI's xueqiu / F10 search will error.\n\n"
              "[dim]To install later (5 min):[/dim]\n"
              "  1. Node.js ≥ 21:    [cyan]https://nodejs.org/en/download/[/cyan]\n"
              "  2. opencli:         [cyan]npm install -g @jackwener/opencli[/cyan]\n"
              "  3. Verify:          [cyan]opencli --version[/cyan]\n\n"
              "[dim]Details (with Chrome ext + ths-extra plugin) in [bold]docs/setup/beginner_zh.md[/bold] Step 8.[/dim]",
    },

    "skip_msg":           {"zh": "⏭ 跳过数据包下载. 如已有数据, 编辑",
                           "en": "⏭ Skipping download. If you already have data, edit"},
    "skip_msg_tail":      {"zh": "指向你的目录.",
                           "en": "to point at your dir."},

    "verify_title":       {"zh": "验证", "en": "Verify"},
    "verify_fail":        {"zh": "验证失败", "en": "Verify failed"},
    "verify_inst":        {"zh": "只 instruments", "en": "instruments"},
    "verify_cal":         {"zh": "天日历",         "en": "calendar days"},
    "verify_range":       {"zh": "日线范围", "en": "Daily range"},

    "complete_left":      {"zh": "✓ 已配置", "en": "✓ Configured"},
    "complete_right":     {"zh": "🚀 下一步", "en": "🚀 Next steps"},
    "complete_envfile":   {"zh": "env file", "en": ".env"},
    "complete_loaders":   {"zh": "loaders",  "en": "loaders"},
    "complete_datadir":   {"zh": "data dir", "en": "data dir"},
    "complete_dled":      {"zh": "downloaded", "en": "downloaded"},
    "complete_dled_yes":  {"zh": "[green]✓ 已就绪[/green]", "en": "[green]✓ ready[/green]"},
    "complete_dled_no":   {"zh": "[yellow]⏭ 跳过 (无数据)[/yellow]",
                           "en": "[yellow]⏭ skipped (no data)[/yellow]"},
    "complete_cmd_launch": {"zh": "→ 一键起后端 + UI + 开浏览器",
                            "en": "→ One-command: backend + UI + open browser"},
    "complete_cmd_report": {"zh": "→ 跑第一份个股研报",
                            "en": "→ Generate your first stock deep-dive"},
    "complete_cmd_status": {"zh": "→ 查数据当前状态", "en": "→ Show data status"},
    "complete_cmd_update": {"zh": "→ 每日增量更新", "en": "→ Daily incremental update"},
    "complete_cmd_tui":   {"zh": "→ 终端 TUI 模式", "en": "→ Terminal TUI mode"},
    "complete_tail":      {"zh": "准备好了 · 期待你的第一份研报",
                           "en": "All set · looking forward to your first report"},
    "complete_keys":      {"zh": "{n} keys", "en": "{n} keys"},
    "complete_write_env": {"zh": "写", "en": "wrote"},

    "lang_panel_title":   {"zh": "语言 / Language", "en": "语言 / Language"},
    "lang_prompt":        {"zh": "[bold]选择 · Choose[/bold]",
                           "en": "[bold]选择 · Choose[/bold]"},
}


def _t(key: str, lang: str) -> str:
    """Lookup a translated text string."""
    entry = _T.get(key)
    if entry is None:
        return f"[missing:{key}]"
    return entry.get(lang, entry.get("zh", f"[missing-lang:{key}]"))


# ──────────────────────── helpers ────────────────────────


def _project_root() -> Path:
    """Locate where to write .env / config/loaders.yaml.

    Priority:
      1. Editable install (dev): repo root — for the maintainer working
         inside G:/financial-analyst, writes belong next to pyproject.toml.
      2. Pip install: the active workspace (honours ``FA_WORKSPACE`` env
         var / ``~/.financial-analyst/.workspace`` pointer file). This is
         what lets users put .env / loaders.yaml on a different drive.
      3. Pip install (no workspace pinned): ``~/.financial-analyst/``
         (legacy default — preserves pre-v1.0.3 behaviour).
    """
    src = Path(__file__).resolve().parent.parent.parent
    if (src / "pyproject.toml").exists():
        return src   # dev install — use repo root
    # pip install — honour the workspace
    from financial_analyst.workspace import get_workspace
    ws = get_workspace()
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _backup_if_exists(path: Path) -> None:
    if not path.exists():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, backup)
    console.print(f"  [dim]📦 backed up →[/dim] [dim cyan]{backup.name}[/dim cyan]")


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
        "FA_LANG", "FA_LOG_LEVEL", "FA_CACHE_DIR", "FA_DATA_DIR", "FA_MAINLINE_PANEL",
    ]
    lines = []
    written = set()
    for k in keys_order:
        if k in env:
            lines.append(f"{k}={env[k]}")
            written.add(k)
    for k, v in env.items():
        if k not in written:
            lines.append(f"{k}={v}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _step_header(n: int, total: int, title: str, subtitle: str = "",
                 lang: str = "zh") -> None:
    """A consistent step banner across the wizard."""
    console.print()
    word = _t("step_word", lang)
    badge = Text(f" {word} {n} / {total} ", style="bold reverse cyan")
    label = Text(f"  {title}", style="bold")
    console.print(Columns([badge, label], padding=(0, 1)))
    if subtitle:
        console.print(Text(f"   {subtitle}", style="dim"))
    console.print(Rule(style="dim cyan"))


def _mask(value: str, keep: int = 6) -> str:
    """Truncate secrets for display: 'sk-abcdef1234' → 'sk-abc•••(len=12)'."""
    if not value:
        return ""
    head = value[:keep]
    return f"{head}•••(len={len(value)})"


# ──────────────────────── steps ────────────────────────


def _step_workspace(non_interactive: bool, override: Optional[Path],
                    lang: str) -> Path:
    """Step 1: pick where data + config + out live.

    Honours ``--workspace`` override if given. Otherwise shows a panel
    with disk-free hints and lets the user paste an alternative path
    (e.g. ``D:\\fa-workspace`` to escape a cramped system drive). Persists
    via ``workspace.set_workspace(...)``.
    """
    from financial_analyst.workspace import (
        DEFAULT_WORKSPACE, get_workspace, set_workspace,
        disk_free_gb, is_writable,
    )

    _step_header(1, 4,
                 _t("step_ws_title", lang),
                 _t("step_ws_subtitle", lang),
                 lang=lang)

    if override:
        # explicit --workspace flag wins
        ws = set_workspace(override)
        free = disk_free_gb(ws)
        console.print(
            f"  [green]✓[/green] {_t('step_ws_picked', lang)}: "
            f"[cyan]{ws}[/cyan]  [dim]({_t('step_ws_free', lang)} {free:.0f} GB)[/dim]"
        )
        return ws

    # Show default + free space
    default_free = disk_free_gb(DEFAULT_WORKSPACE)
    tight = default_free < 20.0
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", justify="right")
    grid.add_column()
    free_text = f"{_t('step_ws_free', lang)} {default_free:.0f} GB"
    if tight:
        free_text += f"  [yellow]{_t('step_ws_warn_tight', lang)}[/yellow]"
    else:
        free_text = f"[dim]{free_text}[/dim]"
    grid.add_row(_t("step_ws_default", lang),
                 f"[cyan]{DEFAULT_WORKSPACE}[/cyan]  {free_text}")
    if tight:
        grid.add_row("", f"[yellow italic]{_t('step_ws_recommend', lang)}[/yellow italic]")
    console.print(grid)
    console.print()

    if non_interactive:
        ws = set_workspace(DEFAULT_WORKSPACE)
        return ws

    raw = Prompt.ask(_t("step_ws_prompt", lang),
                     default="", show_default=False)
    raw = raw.strip()

    if not raw:
        ws = set_workspace(DEFAULT_WORKSPACE)
        console.print(f"  [dim]{_t('step_ws_kept_default', lang)}: "
                      f"[cyan]{ws}[/cyan][/dim]")
        return ws

    target = Path(raw).expanduser()
    if not is_writable(target):
        console.print(f"  [red]{_t('step_ws_not_writable', lang)}[/red]: "
                      f"[dim]{target}[/dim]")
        ws = set_workspace(DEFAULT_WORKSPACE)
        return ws

    ws = set_workspace(target)
    free = disk_free_gb(ws)
    console.print(
        f"  [green]✓[/green] {_t('step_ws_picked', lang)}: "
        f"[cyan]{ws}[/cyan]  [dim]({_t('step_ws_free', lang)} {free:.0f} GB)[/dim]"
    )
    # Warn about non-migrated old data
    legacy_data = DEFAULT_WORKSPACE / "data"
    if ws != DEFAULT_WORKSPACE and legacy_data.exists() and any(legacy_data.iterdir()):
        console.print(f"  {_t('step_ws_old_data_warn', lang)}")
    return ws


def _pick_language(env: dict, non_interactive: bool) -> str:
    """Step 0 — pick UI language. Persists to .env as FA_LANG.

    Re-running ``fa init`` always shows the picker (so users can change
    language), but the existing FA_LANG (if any) becomes the default —
    pressing Enter keeps the current setting.
    """
    pre = (env.get("FA_LANG") or os.environ.get("FA_LANG", "")).strip().lower()
    current = pre if pre in ("zh", "en") else "zh"

    if non_interactive:
        return current

    console.print()
    body = Text.assemble(
        ("  ", ""), ("1", "bold cyan"),
        ("  中文" + ("  (current)" if current == "zh" else "") + "\n", "default"),
        ("  ", ""), ("2", "bold cyan"),
        ("  English" + ("  (current)" if current == "en" else ""), "default"),
    )
    console.print(Panel(body,
                        title="[bold]语言 · Language[/bold]",
                        border_style="cyan",
                        padding=(0, 4),
                        width=42))
    default_choice = "1" if current == "zh" else "2"
    choice = Prompt.ask("  [bold]选择 · Choose[/bold]",
                       default=default_choice,
                       choices=["1", "2"],
                       show_choices=False)
    lang = {"1": "zh", "2": "en"}[choice]
    env["FA_LANG"] = lang
    return lang


def _step_welcome(env_path: Path, config_path: Path, data_dir: Path,
                  lang: str) -> None:
    title = Text(_t("welcome_title", lang), style="bold cyan", justify="center")
    tagline = Text(_t("welcome_tagline", lang), style="dim italic", justify="center")
    body = Text.assemble(
        (_t("welcome_intro", lang) + "\n\n", "default"),
        ("  ", ""), ("1.", "bold cyan"), ("  ", ""),
        (_t("welcome_s1", lang), "default"),
        (_t("welcome_s1_sub", lang) + "\n", "dim"),
        ("  ", ""), ("2.", "bold cyan"), ("  ", ""),
        (_t("welcome_s2", lang), "default"),
        (_t("welcome_s2_sub", lang) + "\n", "dim"),
        ("  ", ""), ("3.", "bold cyan"), ("  ", ""),
        (_t("welcome_s3", lang), "default"),
        (_t("welcome_s3_sub", lang) + "\n", "dim"),
        ("\n", ""),
        (_t("welcome_tail_pre", lang), "default"),
        (_t("welcome_tail_cmd", lang), "bold yellow"),
        (_t("welcome_tail_post", lang), "default"),
    )
    console.print(Panel(
        Align.center(Group(title, tagline, Text(""), body)),
        border_style="cyan",
        padding=(1, 4),
        width=80,
    ))

    paths_table = Table.grid(padding=(0, 2))
    paths_table.add_column(style="dim", justify="right")
    paths_table.add_column(style="cyan")
    paths_table.add_row(_t("paths_project", lang), str(_project_root()))
    paths_table.add_row(_t("paths_env", lang),     str(env_path))
    paths_table.add_row(_t("paths_loaders", lang), str(config_path))
    paths_table.add_row(_t("paths_data", lang),    str(data_dir))
    console.print(Align.center(paths_table))


def _step_llm_keys(env: dict, non_interactive: bool, lang: str) -> dict:
    _step_header(2, 4,
                 _t("step1_title", lang),
                 _t("step1_subtitle", lang),
                 lang=lang)

    status = Table(show_header=True, header_style="bold", padding=(0, 1),
                   box=None, expand=False)
    status.add_column("", width=2)
    status.add_column(_t("step1_col_provider", lang), style="bold")
    status.add_column(_t("step1_col_envvar", lang), style="dim")
    status.add_column(_t("step1_col_state", lang), justify="center")
    status.add_column(_t("step1_col_desc", lang), style="dim")
    for env_var, prov, desc_bundle, color in _LLM_PROVIDERS:
        if env.get(env_var):
            icon = Text("✓", style="green bold")
            state = Text(_t("step1_state_set", lang), style="green")
        else:
            icon = Text("○", style="dim")
            state = Text(_t("step1_state_unset", lang), style="yellow dim")
        status.add_row(icon, Text(prov, style=color), env_var, state, desc_bundle[lang])
    console.print(status)
    console.print()

    if non_interactive:
        return env

    for env_var, prov, _desc, _color in _LLM_PROVIDERS:
        existing = env.get(env_var, "")
        if existing:
            # Already configured — show current, let user replace / keep / clear
            console.print(
                f"  [dim]{_t('step1_current_label', lang)}[/dim] "
                f"[green]{_mask(existing)}[/green]")
            suffix = " " + _t("step1_prompt_change", lang)
        else:
            # Empty slot — recommend or optional
            is_required_one = env_var == "DASHSCOPE_API_KEY" and not any(
                env.get(k) for k, *_ in _LLM_PROVIDERS
            )
            suffix = " " + (_t("step1_prompt_recommended", lang)
                            if is_required_one else _t("step1_prompt_optional", lang))

        v = Prompt.ask(f"  [bold]{prov}[/bold] · {env_var}{suffix}",
                       default="", show_default=False, password=False)
        s = v.strip()

        if not s:
            if existing:
                console.print(f"   [dim]{_t('step1_kept', lang)}[/dim]")
            # else: empty slot + skipped, no message
        elif s == "-":
            if existing:
                env.pop(env_var, None)
                console.print(f"   [yellow]✗[/yellow] {env_var} {_t('step1_cleared', lang)}")
            # else: skipped (nothing to clear)
        else:
            env[env_var] = s
            verb = _t("step1_replaced", lang) if existing else ""
            console.print(f"   [green]✓[/green] {env_var} = [dim]{_mask(s)}[/dim] "
                          f"[dim]{verb}[/dim]")

    has_any = any(env.get(k) for k, *_ in _LLM_PROVIDERS)
    if not has_any:
        console.print(Panel(
            _t("step1_no_key_warn_body", lang),
            border_style="yellow",
            title=f"[yellow]{_t('step1_no_key_warn_title', lang)}[/yellow]",
            padding=(0, 2),
        ))
    return env


def _step_tushare(env: dict, non_interactive: bool, lang: str) -> dict:
    """Step — Tushare token (always shown on re-run; existing token is the default)."""
    _step_header(3, 4,
                 _t("step2_title", lang),
                 _t("step2_subtitle", lang),
                 lang=lang)

    # Always show the explainer block — re-run users may have forgotten
    # what this token does, and the wording is the main reassurance that
    # "no token = totally fine".
    console.print(Panel(
        _t("step2_explainer", lang),
        border_style="dim cyan",
        padding=(0, 2),
        width=80,
    ))
    console.print()

    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="dim")
    grid.add_column()
    grid.add_row(_t("step2_with_label", lang),    _t("step2_with_desc", lang))
    grid.add_row(_t("step2_without_label", lang), _t("step2_without_desc", lang))
    console.print(grid)
    console.print()

    existing = env.get("TUSHARE_TOKEN", "")
    if existing:
        console.print(f"  [dim]{_t('step2_current', lang)}[/dim] [green]{_mask(existing)}[/green]")

    if non_interactive:
        return env

    v = Prompt.ask(f"  [bold]TUSHARE_TOKEN[/bold] {_t('step2_prompt', lang)}",
                   default="", show_default=False)
    if v.strip():
        # User typed something — overwrite
        env["TUSHARE_TOKEN"] = v.strip()
        console.print(f"   [green]✓[/green] TUSHARE_TOKEN [dim]= {_mask(v.strip())}[/dim]")
    elif existing:
        # Enter on a re-run with existing token = keep it
        console.print(f"   [dim]{_t('step2_kept', lang)}[/dim]")
    return env


def _step_pick_package(non_interactive: bool, preset: Optional[str],
                       lang: str) -> Optional[str]:
    _step_header(4, 4,
                 _t("step3_title", lang),
                 _t("step3_subtitle", lang),
                 lang=lang)

    table = Table(show_header=True, header_style="bold cyan", padding=(0, 1),
                  expand=False)
    table.add_column("#", justify="center", style="bold", width=3)
    table.add_column(_t("step3_col_preset", lang), style="bold")
    table.add_column(_t("step3_col_size", lang),   justify="right", style="dim")
    table.add_column(_t("step3_col_stocks", lang), style="dim")
    table.add_column(_t("step3_col_eta", lang),    justify="right", style="dim")
    table.add_column(_t("step3_col_bestfor", lang), style="dim italic")

    for i, key in enumerate(("demo", "lite", "full"), start=1):
        pkg = HF_PACKAGES[key]
        table.add_row(
            str(i),
            Text(key, style=pkg["color"]),
            pkg["size_hint"],
            pkg["n_stocks"][lang],
            pkg["eta"],
            pkg["best_for"][lang],
        )
    table.add_row("4", Text("skip", style="dim"), "—", "—", "—",
                  _t("step3_skip_desc", lang))
    console.print(table)

    if non_interactive:
        return preset
    if preset:
        return preset

    console.print()
    choice = Prompt.ask(f"  [bold]{_t('step3_choose', lang)}[/bold]",
                        default="1",
                        choices=["1", "2", "3", "4"],
                        show_choices=True)
    return {"1": "demo", "2": "lite", "3": "full", "4": "skip"}[choice]


def _download_package(preset: str, target: Path, lang: str) -> bool:
    """Download the data package from HuggingFace into target.

    Note: we deliberately do NOT wrap snapshot_download in a Rich Progress.
    huggingface_hub emits its own per-file tqdm output, and stacking two
    progress UIs makes the screen look glitchy. We just print a header Panel
    before, let HF's tqdm flow naturally, and print a done line after.
    """
    pkg = HF_PACKAGES[preset]
    console.print()
    title = _t("dl_panel_title", lang).format(preset=preset)
    eta_line = _t("dl_panel_eta", lang).format(size=pkg["size_hint"], eta=pkg["eta"])
    console.print(Panel.fit(
        f"[bold]{pkg['repo_id']}[/bold]  →  [cyan]{target}[/cyan]\n"
        f"[dim]{eta_line}[/dim]",
        title=f"[{pkg['color']}]{title}[/{pkg['color']}]",
        border_style=pkg["color"],
        padding=(0, 2),
    ))
    console.print()

    target.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        console.print("[red]✗ huggingface_hub missing. Run: pip install huggingface_hub[/red]")
        return False

    t0 = time.time()
    try:
        # HF's tqdm progress streams to stderr/stdout natively. Don't wrap it.
        snapshot_download(
            repo_id=pkg["repo_id"],
            repo_type="dataset",
            local_dir=str(target),
            local_dir_use_symlinks=False,
        )
    except Exception as e:
        console.print()
        console.print(f"  [red]✗ {_t('dl_fail', lang)}:[/red] [dim]{type(e).__name__}:[/dim] {e}")
        console.print(f"  [dim]{_t('dl_fail_hints', lang)}[/dim]")
        return False

    console.print()
    console.print(f"  [green]✓[/green] {_t('dl_done', lang)} "
                  f"[dim]({time.time() - t0:.0f}s)[/dim]")
    return True


def _write_loaders_config(data_dir: Path, config_path: Path, lang: str) -> None:
    """Write config/loaders.yaml pointing at the downloaded directory."""
    _backup_if_exists(config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""# Auto-generated by fa init {datetime.now():%Y-%m-%d %H:%M}
default: qlib_binary

loaders:
  qlib_binary:
    provider_uri:
      day: {data_dir}/cn_data
      5min: {data_dir}/cn_data_5min
    # Non-time-series data roots (read by financial_analyst.data.paths.get_data_paths)
    parquet_root: {data_dir}/parquet
    news_data_root: {data_dir}/news_data
"""
    config_path.write_text(text, encoding="utf-8")
    console.print(f"  [green]✓[/green] [cyan]{config_path}[/cyan]")


def _verify(data_dir: Path, lang: str) -> bool:
    """Verify the download is complete by sampling calendar + instruments."""
    console.print()
    console.print(Rule(f"[bold]{_t('verify_title', lang)}[/bold]", style="dim cyan"))
    try:
        from financial_analyst.data.bin_writer import (
            load_calendar, load_instruments,
        )
        day_uri = str(data_dir / "cn_data")
        inst = load_instruments(day_uri, market="all")
        cal = load_calendar(day_uri, freq="day")
        if not inst or not cal:
            console.print(f"  [red]✗[/red] {_t('verify_fail', lang)} — "
                          f"instruments={len(inst)} calendar={len(cal)}")
            return False
        console.print(f"  [green]✓[/green] [bold]{len(inst):,}[/bold] "
                      f"{_t('verify_inst', lang)}  ·  "
                      f"[bold]{len(cal):,}[/bold] {_t('verify_cal', lang)}")
        console.print(f"  [green]✓[/green] {_t('verify_range', lang)}: "
                      f"[cyan]{cal[0]}[/cyan] → [cyan]{cal[-1]}[/cyan]")
        return True
    except Exception as e:
        console.print(f"  [red]✗[/red] {_t('verify_fail', lang)}: "
                      f"[dim]{type(e).__name__}:[/dim] {e}")
        return False


def _step_opencli_check(lang: str) -> None:
    """Step 4.5 — Detect OpenCLI on PATH; print a friendly hint if missing.

    Non-blocking: never aborts wizard. Just informs the user whether they
    have full vs basic capability after init.

    Why this lives here (not as a `fa` subcommand or `doctor` check):
    onboarding is the one moment the user is patient + reading carefully.
    If they hit a missing-opencli error 3 days later when clicking "搜雪球",
    they're frustrated.
    """
    import shutil as _shutil
    _step_header(5, 5,
                 _t("step_opencli_title", lang),
                 _t("step_opencli_subtitle", lang),
                 lang=lang)
    if _shutil.which("opencli"):
        console.print(f"  {_t('step_opencli_have', lang)}")
    else:
        console.print(Panel(
            _t("step_opencli_missing", lang),
            border_style="yellow",
            padding=(0, 2),
            width=88,
        ))


def _step_completion(env: dict, env_path: Path, config_path: Path,
                     data_dir: Path, downloaded: bool, lang: str) -> None:
    console.print()
    console.print(Rule(style="dim cyan"))

    cfg_lines = Table.grid(padding=(0, 2))
    cfg_lines.add_column(style="dim", justify="right")
    cfg_lines.add_column()
    n_keys = sum(1 for k in env if env[k])
    cfg_lines.add_row(_t("complete_envfile", lang),
                      f"[cyan]{env_path}[/cyan]  [dim]({_t('complete_keys', lang).format(n=n_keys)})[/dim]")
    cfg_lines.add_row(_t("complete_loaders", lang), f"[cyan]{config_path}[/cyan]")
    cfg_lines.add_row(_t("complete_datadir", lang), f"[cyan]{data_dir}[/cyan]")
    cfg_lines.add_row(_t("complete_dled", lang),
                      _t("complete_dled_yes", lang) if downloaded else _t("complete_dled_no", lang))
    left = Panel(cfg_lines,
                 title=f"[green]{_t('complete_left', lang)}[/green]",
                 border_style="green",
                 padding=(1, 2), width=46)

    next_cmds = Table.grid(padding=(0, 1))
    next_cmds.add_column(style="bold cyan")
    next_cmds.add_column(style="dim")
    next_cmds.add_row("fa launch",         _t("complete_cmd_launch", lang))
    next_cmds.add_row("fa report SH600519", _t("complete_cmd_report", lang))
    next_cmds.add_row("fa data status",     _t("complete_cmd_status", lang))
    next_cmds.add_row("fa data update",     _t("complete_cmd_update", lang))
    next_cmds.add_row("fa --tui",           _t("complete_cmd_tui", lang))
    right = Panel(next_cmds,
                  title=f"[cyan]{_t('complete_right', lang)}[/cyan]",
                  border_style="cyan",
                  padding=(1, 2), width=46)

    console.print(Columns([left, right], padding=(0, 1), equal=True, expand=False))
    console.print()
    console.print(Align.center(
        Text(_t("complete_tail", lang), style="dim italic")
    ))
    console.print()


# ──────────────────────── CLI entry point ────────────────────────


def init_cmd(
    yes: bool = typer.Option(False, "--yes", "-y",
                             help="Non-interactive mode, accept all defaults"),
    preset: Optional[str] = typer.Option(
        None, "--preset",
        help="Data preset: demo / lite / full / skip (required with --yes)"),
    target: Optional[Path] = typer.Option(
        None, "--target",
        help="Data target directory (overrides workspace/data/, kept for back-compat)"),
    workspace: Optional[Path] = typer.Option(
        None, "--workspace",
        help="Workspace root — where .env / config / data / out live "
             "(default: ~/.financial-analyst/, or set persistently via the wizard)"),
    lang: Optional[str] = typer.Option(
        None, "--lang",
        help="UI language: zh / en (skips the picker)"),
):
    """First-launch wizard — configure workspace + LLM + data + verify.

    Examples:
      fa init                                       # interactive
      fa init --yes --preset demo                   # fully automated demo
      fa init --workspace D:/fa-workspace --preset full   # data on D: drive
      fa init --lang en                             # English wizard
    """
    # Read existing env early so we can show pre-existing config in welcome
    # NOTE: env_path / config_path / data_dir get re-resolved AFTER the
    # workspace step in case the user repoints to a different root.
    initial_env_path = _project_root() / ".env"
    env = _read_env(initial_env_path)

    # Step 0 — language
    if lang in ("zh", "en"):
        env["FA_LANG"] = lang
        chosen_lang = lang
    else:
        chosen_lang = _pick_language(env, non_interactive=yes)

    # Step 1 — workspace (NEW). Persists via workspace.set_workspace.
    # After this call, _project_root() / data_dir / config_path will
    # all resolve to the chosen workspace for pip-installed users.
    _step_workspace(non_interactive=yes, override=workspace, lang=chosen_lang)

    # Now re-resolve paths against the (possibly new) workspace
    root = _project_root()
    env_path = root / ".env"
    config_path = root / "config" / "loaders.yaml"
    # Data dir: --target wins (back-compat), else workspace/data/
    from financial_analyst.workspace import data_dir as _ws_data_dir
    data_dir = target if target else _ws_data_dir()

    # Re-read env if path moved
    if env_path != initial_env_path:
        existing = _read_env(env_path)
        # Merge — workspace .env takes priority, but keep any keys the user
        # entered in the workspace step (none yet, but be safe)
        env = {**env, **existing}

    _step_welcome(env_path, config_path, data_dir, chosen_lang)

    # Steps 2-4
    env = _step_llm_keys(env, non_interactive=yes, lang=chosen_lang)
    env = _step_tushare(env, non_interactive=yes, lang=chosen_lang)
    chosen_preset = _step_pick_package(non_interactive=yes, preset=preset,
                                       lang=chosen_lang)

    downloaded = False
    if chosen_preset and chosen_preset != "skip":
        ok = _download_package(chosen_preset, data_dir, chosen_lang)
        if ok:
            _write_loaders_config(data_dir, config_path, chosen_lang)
            _verify(data_dir, chosen_lang)
            downloaded = True
        else:
            console.print()
            console.print(Panel(
                _t("dl_fail_panel_body", chosen_lang),
                border_style="yellow",
                title=f"[yellow]{_t('dl_fail_panel_title', chosen_lang)}[/yellow]",
                padding=(0, 2),
            ))
    elif chosen_preset == "skip":
        console.print()
        console.print(
            f"  [dim]{_t('skip_msg', chosen_lang)}[/dim] "
            f"[cyan]{config_path}[/cyan] [dim]{_t('skip_msg_tail', chosen_lang)}[/dim]"
        )
        _write_loaders_config(data_dir, config_path, chosen_lang)

    # Step 4.5 — OpenCLI detection (optional, non-blocking)
    _step_opencli_check(chosen_lang)

    # Write .env (last, so all collected keys land)
    console.print()
    _write_env(env_path, env)
    n_keys = sum(1 for k in env if env[k])
    write_word = _t("complete_write_env", chosen_lang)
    keys_label = _t("complete_keys", chosen_lang).format(n=n_keys)
    console.print(f"  [green]✓[/green] {write_word} [cyan]{env_path}[/cyan] "
                  f"[dim]({keys_label})[/dim]")

    _step_completion(env, env_path, config_path, data_dir, downloaded, chosen_lang)
