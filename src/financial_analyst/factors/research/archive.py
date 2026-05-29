"""SP-E 研究档案: 把因子/合成评测运行 opt-in 持久化成 append-only 运行日志。

形态 = **评测运行日志** (不是独立结论卡 — wisdom store 已管人写经验)。每条
``RunRecord`` 是一次 ``factor_report`` / ``factor_compose`` 运行的快照 (配置 +
扁平指标 + note/tags), 以 JSONL 落盘。支持:

- ``list``   : 过滤 (kind / target 子串)。
- ``history``: 同一 target 的版本趋势 (时间序)。
- ``compare``: 两次运行的指标 diff (再评测后看增减)。

纯 stdlib (json/datetime/dataclasses/pathlib), 无新依赖。复用 forge ``store`` 的
"可写根 + 注入 root" 范式 (honor ``$FINANCIAL_ANALYST_HOME``)。

错误处理纪律 (仿 ``UserFactorStore`` / ``ComposeResult``): JSONL 缺文件 → []; 坏行
→ logger.warning 跳过, 永不抛; ``compare`` 缺 id → 结构化标错不抛; builder 在
子对象为 None 时不崩 (指标尽量空)。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


# 从结果对象抽取的扁平指标键 (None 子对象时跳过对应组)。
_IC_KEYS = ("ic_mean", "icir", "rank_ic_mean", "rank_icir", "ic_tstat")
_PF_KEYS = ("sharpe", "ann_return", "max_drawdown", "turnover", "win_rate")


@dataclass
class RunRecord:
    """一次评测运行的快照 (JSON-safe — 所有字段都可 json.dumps)。

    id / timestamp 留空交给 ``ResearchArchive.append`` 填充 (仿 wisdom next_id)。
    metrics 是扁平 dict: report → ic + portfolio + characteristics 数值;
    compose 另含 verdict(str) / members(list) / weights(dict)。
    """

    id: str
    timestamp: str
    kind: str  # "report" | "compose"
    target: str
    formula: str
    universe: str
    freq: str
    start: str
    end: str
    metrics: dict
    note: str = ""
    tags: list = field(default_factory=list)


def _default_research_root() -> Path:
    """可写根: ``$FINANCIAL_ANALYST_HOME/research`` else ``~/.financial-analyst/research``。"""
    home = os.environ.get("FINANCIAL_ANALYST_HOME")
    base = Path(home) if home else (Path.home() / ".financial-analyst")
    return base / "research"


def _num(x) -> float:
    """转 float, 不可转 → NaN (永不抛)。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _report_metrics(report) -> dict:
    """从 FactorReport 抽扁平数值指标 (guard None 子对象 → 跳过该组)。"""
    m: dict = {}
    ic = getattr(report, "ic", None)
    if ic is not None:
        for k in _IC_KEYS:
            m[k] = _num(getattr(ic, k, float("nan")))
    pf = getattr(report, "portfolio", None)
    if pf is not None:
        for k in _PF_KEYS:
            m[k] = _num(getattr(pf, k, float("nan")))
    ch = getattr(report, "characteristics", None)
    if ch is not None:
        m["coverage"] = _num(getattr(ch, "coverage", float("nan")))
    return m


class ResearchArchive:
    """JSONL-backed append-only 运行日志。

    ``root`` 可注入 (测试用 tmp_path); 默认 ``_default_research_root()``。
    单文件 ``runs.jsonl`` (一行一条记录, ensure_ascii=False)。
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_research_root()
        self.path = self.root / "runs.jsonl"

    def load(self) -> List[RunRecord]:
        """读 JSONL → list[RunRecord]。缺文件 → []; 坏行 → warning 跳过, 不抛。"""
        if not self.path.exists():
            return []
        records: List[RunRecord] = []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except Exception as e:  # 读文件本身失败也当空, 不拖垮调用方。
            logger.warning("runs.jsonl 读取失败, 当空处理: %s", e)
            return []
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                records.append(RunRecord(**d))
            except Exception as e:  # 坏行: JSON 解析失败 / 字段不匹配 → 跳过。
                logger.warning("runs.jsonl 第 %d 行损坏, 跳过: %s", i, e)
                continue
        return records

    def append(self, record: RunRecord) -> RunRecord:
        """追加一条记录。未填 id → ``r{N+1:04d}``; 未填 timestamp → now().isoformat()。

        返回实际落盘的记录 (id/timestamp 已补全)。
        """
        existing = self.load()
        if not record.id:
            record.id = f"r{len(existing) + 1:04d}"
        if not record.timestamp:
            record.timestamp = datetime.now().isoformat()
        self.root.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def list(self, kind: Optional[str] = None, target: Optional[str] = None) -> List[RunRecord]:
        """过滤运行: kind 精确等; target 子串匹配。"""
        out = self.load()
        if kind:
            out = [r for r in out if r.kind == kind]
        if target:
            out = [r for r in out if target in r.target]
        return out

    def history(self, target: str) -> List[RunRecord]:
        """同一 target 的运行历史, 按 (timestamp, id) 升序 (看指标版本趋势)。"""
        recs = [r for r in self.load() if r.target == target]
        recs.sort(key=lambda r: (r.timestamp, r.id))
        return recs

    def compare(self, id_a: str, id_b: str) -> dict:
        """对比两条运行的指标 diff (b - a)。缺 id → {"error": ...} 不抛。"""
        recs = {r.id: r for r in self.load()}
        a = recs.get(id_a)
        b = recs.get(id_b)
        if a is None or b is None:
            missing = [i for i, r in ((id_a, a), (id_b, b)) if r is None]
            return {"error": f"运行未找到: {', '.join(missing)}"}
        diffs: dict = {}
        for k in set(a.metrics) & set(b.metrics):
            av, bv = a.metrics[k], b.metrics[k]
            if isinstance(av, bool) or isinstance(bv, bool):
                continue  # bool 是 int 子类, 不当数值 diff。
            if isinstance(av, (int, float)) and isinstance(bv, (int, float)):
                diffs[k] = bv - av
        return {
            "a": asdict(a),
            "b": asdict(b),
            "metric_diffs": diffs,
            "targets": (a.target, b.target),
        }


def record_from_report(report, *, note: str = "", tags=()) -> RunRecord:
    """FactorReport → RunRecord (id/timestamp 留空, 由 append 填)。

    target/formula = 因子名 (报告无独立 formula 字段); 配置取自 report.meta;
    metrics 从 ic + portfolio + characteristics 抽扁平 (guard None)。
    """
    meta = getattr(report, "meta", None)
    factor = getattr(meta, "factor", "") if meta is not None else ""
    return RunRecord(
        id="",
        timestamp="",
        kind="report",
        target=factor,
        formula=factor,
        universe=getattr(meta, "universe", "") if meta is not None else "",
        freq=getattr(meta, "freq", "") if meta is not None else "",
        start=getattr(meta, "start", "") if meta is not None else "",
        end=getattr(meta, "end", "") if meta is not None else "",
        metrics=_report_metrics(report),
        note=note,
        tags=list(tags),
    )


def record_from_compose(res, *, note: str = "", tags=()) -> RunRecord:
    """ComposeResult → RunRecord (id/timestamp 留空, 由 append 填)。

    target/formula = ``f"{method}:[m1,m2,...]"``; 配置取自 res.composite.meta
    (若 composite 非 None); metrics 从 composite 抽 + verdict/members/weights。
    composite 为 None (失败) 时 metrics 仅 verdict/members/weights。永不抛。
    """
    members = list(getattr(res, "members", []) or [])
    method = getattr(res, "method", "")
    target = f"{method}:[{','.join(str(m) for m in members)}]"

    composite = getattr(res, "composite", None)
    if composite is not None:
        metrics = _report_metrics(composite)
        cmeta = getattr(composite, "meta", None)
        universe = getattr(cmeta, "universe", "") if cmeta is not None else ""
        freq = getattr(cmeta, "freq", "") if cmeta is not None else ""
        start = getattr(cmeta, "start", "") if cmeta is not None else ""
        end = getattr(cmeta, "end", "") if cmeta is not None else ""
    else:
        metrics = {}
        universe = freq = start = end = ""

    metrics["verdict"] = getattr(res, "verdict", "")
    metrics["members"] = members
    weights = getattr(res, "weights", {}) or {}
    metrics["weights"] = dict(weights)

    return RunRecord(
        id="",
        timestamp="",
        kind="compose",
        target=target,
        formula=target,
        universe=universe,
        freq=freq,
        start=start,
        end=end,
        metrics=metrics,
        note=note,
        tags=list(tags),
    )
