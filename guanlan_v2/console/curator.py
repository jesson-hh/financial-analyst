"""离线记忆 Curator + 共享行分类。

行分类(is_keyed_line/classify_lines)是注入(api._select_memory_lines)与收敛
(consolidate_memory)的共用基元,落在本中性模块杜绝两处正则漂移;本模块不依赖
tools/api(tools 惰性 import 本模块触发收敛,故反向 import 会成环)。

consolidate_memory 常驻感知:带 (key) 的常驻行永不归档/永远保留;只把溢出的最旧
易逝(无 key)行归档(不物理删,可恢复)。纯函数、无 LLM、不持锁(调用方在
_MEMORY_LOCK 内调)。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

_KEYED_RE = re.compile(r"^- \[\d{4}-\d{2}-\d{2}\] \([^)]+\) ")


def is_keyed_line(line: str) -> bool:
    """行是否带 (key) 标签 = 常驻偏好(永不归档、每轮全量注入)。"""
    return bool(_KEYED_RE.match(line))


def classify_lines(text: str) -> Tuple[List[str], List[str]]:
    """按行分类 → (keyed 常驻, unkeyed 易逝),保序、跳过空行。"""
    keyed: List[str] = []
    unkeyed: List[str] = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        (keyed if is_keyed_line(ln) else unkeyed).append(ln)
    return keyed, unkeyed


def consolidate_memory(mem_path: Path, archive_path: Path, max_lines: int = 120,
                       protected_lines: Iterable[str] = ()) -> Dict[str, Any]:
    """常驻感知收敛:keyed 行永不归档;溢出的最旧 unkeyed 行归档(不物理删,可恢复)。
    保留行按原始顺序写回。注:合并时顺带剔除空行(压紧格式)。无溢出则不动文件、不建 archive。"""
    try:
        if not mem_path.exists():
            return {"ok": True, "archived": 0, "kept": 0, "reason": "无记忆文件"}
        lines = [ln for ln in mem_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) <= max_lines:
            return {"ok": True, "archived": 0, "kept": len(lines)}
        protected = {str(ln).strip() for ln in protected_lines if str(ln).strip()}
        # UI pin 与主题 key 是两个概念，但二者都属于“不可被自动收敛”的常驻集合。
        # protected_lines 来自治理 sidecar；sidecar 不可用时仍退化为原有 keyed 规则。
        archiveable_idx = [
            i for i, ln in enumerate(lines)
            if not is_keyed_line(ln) and ln.strip() not in protected
        ]
        resident_n = len(lines) - len(archiveable_idx)
        keep_archiveable_n = max(0, max_lines - resident_n)
        archived_idx = set(
            archiveable_idx[:max(0, len(archiveable_idx) - keep_archiveable_n)]
        )
        if not archived_idx:
            return {"ok": True, "archived": 0, "kept": len(lines)}
        archived = [lines[i] for i in sorted(archived_idx)]
        kept = [ln for i, ln in enumerate(lines) if i not in archived_idx]
        stamp = datetime.now().isoformat(timespec="seconds")
        with archive_path.open("a", encoding="utf-8") as f:
            f.write(f"\n## 归档于 {stamp}\n" + "\n".join(archived) + "\n")
        mem_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        return {"ok": True, "archived": len(archived), "kept": len(kept)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
