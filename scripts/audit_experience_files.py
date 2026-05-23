r"""审计两套经验文件: G:\stocks/strategy/*.md vs G:\financial-analyst/memories/*/*.md.

为什么要这个: 历史上两个项目各自演化, 同一份"踩坑/规则"出现在两处, 漂移
后报告 (report_v2) vs 13-agent stock-deep-dive 可能用不同版本的经验做研判.

输出: 哪些文件**主题对应**, 各自 size/mtime, 是否漂移 (mtime 差>1天), 哪个更新.

**只读, 不改文件**. 看完用户决定哪边是 source-of-truth 后, 手工 merge.

用法:
    python G:/financial-analyst/scripts/audit_experience_files.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Windows 控制台默认 GBK, ⚠/✓ 等 Unicode 字符会炸. 强制 stdout/stderr UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

STRATEGY = Path("G:/stocks/strategy")
MEMORIES = Path("G:/financial-analyst/memories")

# 主题对应表 — strategy/ 那份 vs memories/<agent>/ 那份
CANONICAL_MAP = [
    ("pitfalls.md",         "bear-advocate/pitfalls.md",
     "踩坑库 (bear-advocate 用 FTS5 retrieval 读)"),
    ("rules_learned.md",    "quant-analyst/rules_learned.md",
     "策略规则 (quant-analyst 全读)"),
    ("rating_system.md",    "fundamental-analyst/rating_system.md",
     "v4 评级规则 (fundamental + report-writer 都读)"),
    ("rating_system.md",    "report-writer/rating_system.md",
     "v4 评级规则 (writer 端)"),
    ("factor_insights.md",  "technical-analyst/factor_insights.md",
     "因子经验 (technical 全读)"),
    ("factor_insights.md",  "bull-advocate/factor_insights_long_side.md",
     "因子经验 (bull 抽取多头视角, 通常文件名不同)"),
    ("analyst_playbook.md", "_shared/playbook_V1_V10.md",
     "V1-V10 视角 playbook (跨 agent 共享)"),
    ("research/sentiment_summary.md",
                            "whale-analyst/sentiment_signals_R7_R20.md",
     "R7-R20 情绪信号库 (whale-analyst)"),
]


def _stat(p: Path) -> dict:
    if not p.exists():
        return {"exists": False}
    s = p.stat()
    return {
        "exists": True,
        "size": s.st_size,
        "mtime": datetime.fromtimestamp(s.st_mtime),
    }


def _drift_marker(a_mt, b_mt) -> str:
    if a_mt is None or b_mt is None:
        return "—"
    delta = abs((a_mt - b_mt).total_seconds())
    if delta < 60:
        return "✓ 同步"
    if delta < 86400:
        return f"~ 差 {delta/3600:.1f}h"
    return f"⚠ 差 {delta/86400:.1f} 天"


def main() -> int:
    drift_count = 0
    missing_count = 0
    print(f"=== 经验文件审计 ({datetime.now():%Y-%m-%d %H:%M}) ===")
    print(f"strategy: {STRATEGY}")
    print(f"memories: {MEMORIES}")
    print()
    print(f"{'strategy 文件':<35} {'memories 对应':<48} {'状态':<18} 备注")
    print("-" * 130)

    for strat_rel, mem_rel, note in CANONICAL_MAP:
        sp = STRATEGY / strat_rel
        mp = MEMORIES / mem_rel
        ss = _stat(sp)
        ms = _stat(mp)

        if not ss["exists"] and not ms["exists"]:
            status = "✗ 两边都缺"
            missing_count += 1
        elif not ss["exists"]:
            status = "✗ strategy 缺"
            missing_count += 1
        elif not ms["exists"]:
            status = "✗ memories 缺"
            missing_count += 1
        else:
            status = _drift_marker(ss["mtime"], ms["mtime"])
            if "差" in status:
                drift_count += 1

        size_diff = ""
        if ss.get("exists") and ms.get("exists"):
            sz_s, sz_m = ss["size"], ms["size"]
            if sz_s != sz_m:
                size_diff = f" (size {sz_s} vs {sz_m})"

        print(f"{strat_rel:<35} {mem_rel:<48} {status:<18} {note}{size_diff}")

    print()
    print(f"漂移项: {drift_count}    缺失项: {missing_count}    总条目: {len(CANONICAL_MAP)}")
    if drift_count or missing_count:
        print()
        print("→ 建议:")
        print("  1. memories/ 是 13-agent 系统的 canonical source (per-agent 粒度更细)")
        print("  2. strategy/ 是 report_v2.py + 文档/research 的 source")
        print("  3. 主题对应文件出现漂移时, 决定哪边是 truth, 手工 merge 或单向 sync")
        print("  4. 不建议双向自动 sync (会冲突). 建议挑一个方向做主, 另一个定期 rsync")
        return 1
    print("✓ 全部同步")
    return 0


if __name__ == "__main__":
    sys.exit(main())
