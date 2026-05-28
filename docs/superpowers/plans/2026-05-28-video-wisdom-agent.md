# video-wisdom Agent (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 financial-analyst 里新增 `wisdom/` 模块, 把视频转写文本经 LLM 抽取成结构化投资经验卡 (草稿), 经人工过闸后进入可检索的知识库; 同时给 buddy 加 `wisdom_review` / `wisdom_search` 两个工具。

**Architecture:** 独立 `wisdom/` 模块 (方案 B), 四个单一职责文件 (card / store / extractor / prompts) + cli 入口。卡片 one-per-file, 状态机 = 目录位置 (`draft/` → `approved/` → `rejected/`)。检索复用 `knowledge/LocalMarkdownKB` 指向 `approved/`。抽取走 `llm/LLMClient.for_agent("wisdom")`, 不依赖 G:/stocks。

**Tech Stack:** Python 3.10+, dataclass, PyYAML (frontmatter), pytest (mock LLM, 不调真实 API), `financial_analyst.llm.client.LLMClient`, `financial_analyst.knowledge.local_markdown.LocalMarkdownKB`。

**Spec:** `docs/superpowers/specs/2026-05-28-video-wisdom-agent-design.md`

---

## File Structure

| 文件 | 职责 |
|---|---|
| `src/financial_analyst/wisdom/__init__.py` | 包标记, 导出 WisdomCard / WisdomStore |
| `src/financial_analyst/wisdom/card.py` | WisdomCard dataclass + markdown ⇄ 对象 序列化 |
| `src/financial_analyst/wisdom/store.py` | WisdomStore: 状态机 (目录) + CRUD + next_id |
| `src/financial_analyst/wisdom/prompts.py` | 抽取 prompt 构造 (质量标准 + few-shot + 互证) |
| `src/financial_analyst/wisdom/extractor.py` | extract_cards() async, 调 LLM, 质量过滤 |
| `src/financial_analyst/wisdom/cli.py` | `python -m financial_analyst.wisdom.cli extract ...` |
| `src/financial_analyst/wisdom/migrate.py` | 一次性: bilibili_notes.md → approved 卡 |
| `src/financial_analyst/buddy/tools.py` (改) | 扩 `wisdom_review` + `wisdom_search` 两个 Tool |
| `tests/test_wisdom_card.py` | card round-trip |
| `tests/test_wisdom_store.py` | store 状态机 |
| `tests/test_wisdom_extractor.py` | extractor (mock LLM) |
| `tests/test_wisdom_cli.py` | cli (mock extractor) |
| `tests/test_wisdom_tools.py` | 两工具 (mock store/KB) |
| `tests/test_wisdom_migrate.py` | 迁移解析 |

**执行顺序**: Task 1 (card) → 2 (store) → 3 (prompts) → 4 (extractor) → 5 (cli) → 6 (tools) → 7 (migrate)。后者依赖前者的类型。

---

## Task 1: WisdomCard dataclass + markdown 序列化

**Files:**
- Create: `src/financial_analyst/wisdom/__init__.py`
- Create: `src/financial_analyst/wisdom/card.py`
- Test: `tests/test_wisdom_card.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_card.py
from financial_analyst.wisdom.card import WisdomCard


def _sample() -> WisdomCard:
    return WisdomCard(
        id="EV-008",
        title="证券板块作为科技兑现/抄底切换信号",
        status="draft",
        quality_score=0.82,
        confidence="高",
        tags=["板块组合", "择时", "证券"],
        source={"platform": "bilibili", "up": "来去由心",
                "bvid": "BV1F3Gy6oEMx", "date": "2026-05-27", "segments": "71-167"},
        body="## 经验\n证券和主流题材高开组合判断兑现/抄底.\n\n"
             "## 适用条件\n日内/隔日, 有明确主流题材抱团时.\n\n"
             "## 操作建议\n科技多日高潮+证券同步走强 → 当日减仓.\n\n"
             "## 反例 / 边界\n证券有独立行情时信号失真.",
        corroborates=["EV-002"],
        conflicts=[],
        created="2026-05-28",
        reviewed_by=None,
    )


def test_roundtrip_preserves_all_fields():
    card = _sample()
    text = card.to_markdown()
    back = WisdomCard.from_markdown(text)
    assert back.id == "EV-008"
    assert back.title == card.title
    assert back.status == "draft"
    assert back.quality_score == 0.82
    assert back.confidence == "高"
    assert back.tags == ["板块组合", "择时", "证券"]
    assert back.source["bvid"] == "BV1F3Gy6oEMx"
    assert back.corroborates == ["EV-002"]
    assert back.conflicts == []
    assert back.reviewed_by is None
    assert "## 经验" in back.body
    assert "## 反例 / 边界" in back.body


def test_to_markdown_starts_with_frontmatter():
    text = _sample().to_markdown()
    assert text.startswith("---\n")
    assert text.count("---") >= 2


def test_from_markdown_missing_frontmatter_raises():
    import pytest
    with pytest.raises(ValueError):
        WisdomCard.from_markdown("no frontmatter here")


def test_from_markdown_tolerates_null_optional_fields():
    text = (
        "---\n"
        "id: EV-001\n"
        "title: t\n"
        "status: draft\n"
        "tags:\n"
        "source:\n"
        "corroborates:\n"
        "conflicts:\n"
        "reviewed_by:\n"
        "---\n\n## 经验\nx"
    )
    card = WisdomCard.from_markdown(text)
    assert card.tags == []
    assert card.source == {}
    assert card.corroborates == []
    assert card.reviewed_by is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_card.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.wisdom'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/wisdom/__init__.py
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore

__all__ = ["WisdomCard", "WisdomStore"]
```

```python
# src/financial_analyst/wisdom/card.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

# 正文固定 4 段式 (与 strategy/wisdom/bilibili_notes.md 的 PoC 12 条一致)
BODY_SECTIONS = ["经验", "适用条件", "操作建议", "反例 / 边界"]


@dataclass
class WisdomCard:
    id: str
    title: str
    status: str = "draft"            # draft | approved | rejected
    quality_score: float = 0.0       # LLM 自评 0-1, 仅排序待审
    confidence: str = "中"            # 高/中/低, 经验置信
    tags: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict)
    body: str = ""                   # 4 段式正文 markdown
    corroborates: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    created: str = ""
    reviewed_by: Optional[str] = None

    def to_markdown(self) -> str:
        fm = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "quality_score": self.quality_score,
            "confidence": self.confidence,
            "tags": self.tags,
            "source": self.source,
            "corroborates": self.corroborates,
            "conflicts": self.conflicts,
            "created": self.created,
            "reviewed_by": self.reviewed_by,
        }
        fm_yaml = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{fm_yaml}\n---\n\n{self.body.strip()}\n"

    @classmethod
    def from_markdown(cls, text: str) -> "WisdomCard":
        if not text.lstrip().startswith("---"):
            raise ValueError("WisdomCard markdown missing YAML frontmatter")
        # split into ['', frontmatter, body...]
        parts = text.split("---", 2)
        if len(parts) < 3:
            raise ValueError("WisdomCard markdown malformed frontmatter fences")
        fm = yaml.safe_load(parts[1]) or {}
        return cls(
            id=fm.get("id", ""),
            title=fm.get("title", ""),
            status=fm.get("status", "draft"),
            quality_score=float(fm.get("quality_score") or 0.0),
            confidence=fm.get("confidence", "中"),
            tags=fm.get("tags") or [],
            source=fm.get("source") or {},
            body=parts[2].strip(),
            corroborates=fm.get("corroborates") or [],
            conflicts=fm.get("conflicts") or [],
            created=fm.get("created", ""),
            reviewed_by=fm.get("reviewed_by"),
        )
```

> Note: `__init__.py` imports `WisdomStore` (Task 2). Until Task 2 lands, temporarily make the store import lazy — comment the `WisdomStore` line and re-add it in Task 2. (Or implement Task 2 first; order is a suggestion.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_card.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/wisdom/__init__.py src/financial_analyst/wisdom/card.py tests/test_wisdom_card.py
git commit -m "feat(wisdom): WisdomCard dataclass + markdown serialization"
```

---

## Task 2: WisdomStore 状态机

**Files:**
- Create: `src/financial_analyst/wisdom/store.py`
- Test: `tests/test_wisdom_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_store.py
import pytest
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore


def _card(card_id: str, status: str = "draft", score: float = 0.5) -> WisdomCard:
    return WisdomCard(id=card_id, title=f"t-{card_id}", status=status,
                      quality_score=score, body="## 经验\nx")


def test_creates_status_dirs(tmp_path):
    store = WisdomStore(root=tmp_path)
    for s in ("draft", "approved", "rejected"):
        assert (tmp_path / s).is_dir()


def test_save_and_load(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001"))
    loaded = store.load("EV-001")
    assert loaded.id == "EV-001"
    assert loaded.status == "draft"


def test_save_writes_to_status_subdir(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    assert (tmp_path / "draft" / "EV-001.md").exists()


def test_list_by_status(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    store.save(_card("EV-002", status="draft"))
    store.save(_card("EV-003", status="approved"))
    drafts = store.list_by_status("draft")
    assert {c.id for c in drafts} == {"EV-001", "EV-002"}
    assert len(store.list_by_status("approved")) == 1


def test_set_status_moves_file(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001", status="draft"))
    store.set_status("EV-001", "approved", reviewed_by="xuyi")
    assert not (tmp_path / "draft" / "EV-001.md").exists()
    assert (tmp_path / "approved" / "EV-001.md").exists()
    reloaded = store.load("EV-001")
    assert reloaded.status == "approved"
    assert reloaded.reviewed_by == "xuyi"


def test_set_status_invalid_raises(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(_card("EV-001"))
    with pytest.raises(ValueError):
        store.set_status("EV-001", "bogus")


def test_load_missing_raises_keyerror(tmp_path):
    store = WisdomStore(root=tmp_path)
    with pytest.raises(KeyError):
        store.load("EV-999")


def test_next_id_sequence(tmp_path):
    store = WisdomStore(root=tmp_path)
    assert store.next_id() == "EV-001"
    store.save(_card("EV-001"))
    store.save(_card("EV-012", status="approved"))
    assert store.next_id() == "EV-013"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.wisdom.store'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/wisdom/store.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from financial_analyst.wisdom.card import WisdomCard

_STATUSES = ("draft", "approved", "rejected")
_ID_RE = re.compile(r"EV-(\d+)")


class WisdomStore:
    """经验卡存储. 状态即目录: draft/ approved/ rejected/ 各放对应 status 的卡."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else (
            Path.home() / ".financial-analyst" / "wisdom"
        )
        for s in _STATUSES:
            (self.root / s).mkdir(parents=True, exist_ok=True)

    def _path_for(self, card: WisdomCard) -> Path:
        return self.root / card.status / f"{card.id}.md"

    def save(self, card: WisdomCard) -> Path:
        p = self._path_for(card)
        p.write_text(card.to_markdown(), encoding="utf-8")
        return p

    def load(self, card_id: str) -> WisdomCard:
        for s in _STATUSES:
            p = self.root / s / f"{card_id}.md"
            if p.exists():
                return WisdomCard.from_markdown(p.read_text(encoding="utf-8"))
        raise KeyError(card_id)

    def list_by_status(self, status: str) -> list[WisdomCard]:
        d = self.root / status
        if not d.is_dir():
            return []
        return [WisdomCard.from_markdown(p.read_text(encoding="utf-8"))
                for p in sorted(d.glob("*.md"))]

    def set_status(self, card_id: str, status: str,
                   reviewed_by: Optional[str] = None) -> None:
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status}")
        card = self.load(card_id)
        old_path = self.root / card.status / f"{card_id}.md"
        card.status = status
        if reviewed_by is not None:
            card.reviewed_by = reviewed_by
        new_path = self.save(card)
        if old_path.exists() and old_path != new_path:
            old_path.unlink()

    def next_id(self) -> str:
        mx = 0
        for s in _STATUSES:
            for p in (self.root / s).glob("EV-*.md"):
                m = _ID_RE.match(p.stem)
                if m:
                    mx = max(mx, int(m.group(1)))
        return f"EV-{mx + 1:03d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_store.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/wisdom/store.py tests/test_wisdom_store.py
git commit -m "feat(wisdom): WisdomStore status-machine (draft/approved/rejected dirs)"
```

---

## Task 3: 抽取 prompt 构造

**Files:**
- Create: `src/financial_analyst/wisdom/prompts.py`
- Test: `tests/test_wisdom_prompts.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_prompts.py
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.prompts import build_extraction_messages


def test_messages_shape():
    msgs = build_extraction_messages("今天大盘跌了4200家.", {"up": "老徐"}, [])
    assert isinstance(msgs, list)
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["role"] == "user"


def test_system_prompt_has_quality_gate_and_schema():
    msgs = build_extraction_messages("x", {}, [])
    system = msgs[0]["content"]
    # 质量门: 必须要求具体数字/阈值 或 可操作动作 + 反例
    assert "反例" in system
    assert "数字" in system or "阈值" in system
    # JSON schema 约定: cards 数组 + 关键字段
    assert "cards" in system
    assert "quality_score" in system
    assert "corroborates" in system


def test_user_prompt_embeds_transcript_and_source():
    msgs = build_extraction_messages("特定转写内容ABC", {"up": "来去由心", "bvid": "BVxxx"}, [])
    user = msgs[-1]["content"]
    assert "特定转写内容ABC" in user
    assert "来去由心" in user


def test_existing_cards_summarized_for_corroboration():
    existing = [WisdomCard(id="EV-002", title="单板块虹吸警示", tags=["流动性"])]
    msgs = build_extraction_messages("x", {}, existing)
    joined = "\n".join(m["content"] for m in msgs)
    assert "EV-002" in joined
    assert "单板块虹吸警示" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_prompts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.wisdom.prompts'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/wisdom/prompts.py
from __future__ import annotations

import json
from typing import Any

from financial_analyst.wisdom.card import WisdomCard

_SYSTEM = """你是 A 股投资经验提炼专家. 输入是某视频博主的口语化盘面复盘转写文本.
你的任务: 提炼出**有信息量、可操作、分析师能看懂**的经验条, 输出严格的 JSON.

# 硬质量门 (不满足就不要产出该条)
每条经验必须同时满足:
1. 含**具体数字/阈值** (如 "占比>15%") 或**明确可操作动作** (如 "压力位附近减仓")
2. 必须有**反例 / 边界** (什么时候这条不成立)
绝不输出"要保持好心态""顺势而为"这类无信息量的水文.

# 输出 JSON 格式 (只输出 JSON, 不要其他文字)
{
  "cards": [
    {
      "title": "一句话标题",
      "quality_score": 0.0-1.0,         // 你对该条信息量的自评
      "confidence": "高|中|低",
      "tags": ["标签1", "标签2"],
      "body": "## 经验\\n...\\n\\n## 适用条件\\n...\\n\\n## 操作建议\\n...\\n\\n## 反例 / 边界\\n...",
      "corroborates": ["EV-002"],        // 与下方已有经验互证的 id, 没有则空数组
      "conflicts": []                    // 与已有经验冲突的 id
    }
  ]
}
body 必须是 4 段式: 经验 / 适用条件 / 操作建议 / 反例 / 边界."""


def _summarize_existing(existing: list[WisdomCard]) -> str:
    if not existing:
        return "(无已有经验)"
    lines = [f"- {c.id} | {c.title} | tags={','.join(c.tags)}" for c in existing]
    return "\n".join(lines)


def build_extraction_messages(
    transcript: str,
    source: dict[str, Any],
    existing: list[WisdomCard],
) -> list[dict[str, str]]:
    """构造抽取用 messages. system=任务+质量门+schema, user=转写+来源+已有经验摘要."""
    src_line = json.dumps(source, ensure_ascii=False)
    user = (
        f"# 视频来源\n{src_line}\n\n"
        f"# 已有经验 (判断 corroborates/conflicts 用, 不要重复产出同一条)\n"
        f"{_summarize_existing(existing)}\n\n"
        f"# 转写文本\n{transcript}\n\n"
        f"请按 system 的 JSON 格式输出经验卡."
    )
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_prompts.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/wisdom/prompts.py tests/test_wisdom_prompts.py
git commit -m "feat(wisdom): extraction prompt with quality gate + corroboration"
```

---

## Task 4: extractor (async, mock LLM)

**Files:**
- Create: `src/financial_analyst/wisdom/extractor.py`
- Test: `tests/test_wisdom_extractor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_extractor.py
import asyncio
import json

import financial_analyst.wisdom.extractor as extractor_mod
from financial_analyst.wisdom.extractor import extract_cards


class _FakeClient:
    """Stand-in for LLMClient: returns a canned OpenAI-compat response."""
    def __init__(self, payload: dict):
        self._payload = payload
        self.calls = 0

    async def chat(self, messages, tools=None, response_format=None, temperature=0.2):
        self.calls += 1
        return {"choices": [{"message": {"content": json.dumps(self._payload, ensure_ascii=False)}}]}


def _patch_client(monkeypatch, fake):
    monkeypatch.setattr(extractor_mod.LLMClient, "for_agent",
                        classmethod(lambda cls, name: fake))


def test_extracts_cards_with_draft_status(monkeypatch):
    payload = {"cards": [{
        "title": "证券组合判断兑现",
        "quality_score": 0.8,
        "confidence": "高",
        "tags": ["择时"],
        "body": "## 经验\n证券+科技高开看兑现.\n\n## 适用条件\n抱团时.\n\n"
                "## 操作建议\n减仓.\n\n## 反例 / 边界\n证券独立行情失真.",
        "corroborates": ["EV-002"],
        "conflicts": [],
    }]}
    fake = _FakeClient(payload)
    _patch_client(monkeypatch, fake)
    cards = asyncio.run(extract_cards("转写x", {"up": "来去由心"}, existing=None))
    assert len(cards) == 1
    c = cards[0]
    assert c.status == "draft"
    assert c.title == "证券组合判断兑现"
    assert c.quality_score == 0.8
    assert c.corroborates == ["EV-002"]
    assert c.source["up"] == "来去由心"
    assert c.id == ""   # id 由 store.next_id() 在落盘时分配, extractor 不分配


def test_quality_gate_drops_card_without_counterexample(monkeypatch):
    payload = {"cards": [{
        "title": "水文条",
        "quality_score": 0.9,
        "confidence": "高",
        "tags": [],
        "body": "## 经验\n保持好心态.\n\n## 适用条件\n随时.\n\n## 操作建议\n顺势.",
        # 缺 "## 反例 / 边界" 段 → 应被质量门丢弃
        "corroborates": [],
        "conflicts": [],
    }]}
    fake = _FakeClient(payload)
    _patch_client(monkeypatch, fake)
    cards = asyncio.run(extract_cards("x", {}, None))
    assert cards == []


def test_retries_once_on_bad_json_then_raises(monkeypatch):
    class _BadClient:
        def __init__(self):
            self.calls = 0
        async def chat(self, messages, tools=None, response_format=None, temperature=0.2):
            self.calls += 1
            return {"choices": [{"message": {"content": "NOT JSON"}}]}
    bad = _BadClient()
    _patch_client(monkeypatch, bad)
    import pytest
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(extract_cards("x", {}, None))
    assert bad.calls == 2   # 1 try + 1 retry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_extractor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.wisdom.extractor'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/wisdom/extractor.py
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from financial_analyst.llm.client import LLMClient
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.prompts import build_extraction_messages

logger = logging.getLogger(__name__)

_REQUIRED_BODY_MARKERS = ("## 经验", "## 反例")  # 质量门: 必须有经验 + 反例段


def _passes_quality_gate(item: dict[str, Any]) -> bool:
    body = item.get("body", "") or ""
    if not all(marker in body for marker in _REQUIRED_BODY_MARKERS):
        return False
    if not (item.get("title") or "").strip():
        return False
    return True


async def extract_cards(
    transcript: str,
    source: dict[str, Any],
    existing: Optional[list[WisdomCard]] = None,
) -> list[WisdomCard]:
    """转写文本 → 草稿经验卡. id 留空 (由 store 落盘时 next_id 分配).

    Raises:
        json.JSONDecodeError: LLM 两次都返回非法 JSON 时 (绝不返回半成品脏卡).
    """
    existing = existing or []
    client = LLMClient.for_agent("wisdom")
    messages = build_extraction_messages(transcript, source, existing)

    data: Optional[dict] = None
    last_err: Optional[Exception] = None
    for attempt in range(2):
        resp = await client.chat(
            messages,
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        content = resp["choices"][0]["message"]["content"]
        try:
            data = json.loads(content)
            break
        except json.JSONDecodeError as e:
            last_err = e
            logger.warning("wisdom extract: bad JSON on attempt %d", attempt + 1)
    if data is None:
        assert last_err is not None
        raise last_err

    cards: list[WisdomCard] = []
    for item in data.get("cards", []):
        if not _passes_quality_gate(item):
            logger.info("wisdom extract: dropped low-quality card %r", item.get("title"))
            continue
        cards.append(WisdomCard(
            id="",
            title=item.get("title", "").strip(),
            status="draft",
            quality_score=float(item.get("quality_score") or 0.0),
            confidence=item.get("confidence", "中"),
            tags=item.get("tags") or [],
            source=source,
            body=item.get("body", "").strip(),
            corroborates=item.get("corroborates") or [],
            conflicts=item.get("conflicts") or [],
            created="",
            reviewed_by=None,
        ))
    return cards
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_extractor.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/wisdom/extractor.py tests/test_wisdom_extractor.py
git commit -m "feat(wisdom): async extract_cards with quality gate + JSON retry"
```

---

## Task 5: cli (extract 子命令)

**Files:**
- Create: `src/financial_analyst/wisdom/cli.py`
- Test: `tests/test_wisdom_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_cli.py
import asyncio

import financial_analyst.wisdom.cli as cli_mod
from financial_analyst.wisdom.card import WisdomCard


def test_extract_assigns_ids_and_saves_drafts(tmp_path, monkeypatch):
    transcript = tmp_path / "t.txt"
    transcript.write_text("今天大盘跌4200家.", encoding="utf-8")

    # fake extract_cards: 返回两张无 id 草稿
    async def _fake_extract(text, source, existing=None):
        return [
            WisdomCard(id="", title="c1", status="draft", body="## 经验\nx\n## 反例\ny"),
            WisdomCard(id="", title="c2", status="draft", body="## 经验\nx\n## 反例\ny"),
        ]
    monkeypatch.setattr(cli_mod, "extract_cards", _fake_extract)

    rc = cli_mod.main([
        "extract", str(transcript),
        "--platform", "bilibili", "--up", "老徐",
        "--bvid", "BVxxx", "--date", "2026-05-26",
        "--root", str(tmp_path / "wisdom"),
    ])
    assert rc == 0
    from financial_analyst.wisdom.store import WisdomStore
    store = WisdomStore(root=tmp_path / "wisdom")
    drafts = store.list_by_status("draft")
    assert len(drafts) == 2
    assert {c.id for c in drafts} == {"EV-001", "EV-002"}
    assert all(c.source["bvid"] == "BVxxx" for c in drafts)
    assert all(c.created for c in drafts)   # created 被填充
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.wisdom.cli'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/wisdom/cli.py
from __future__ import annotations

import argparse
import asyncio
import datetime as _dt
import sys
from pathlib import Path
from typing import Optional

from financial_analyst.wisdom.extractor import extract_cards
from financial_analyst.wisdom.store import WisdomStore


def _run_extract(args: argparse.Namespace) -> int:
    transcript = Path(args.transcript).read_text(encoding="utf-8")
    source = {
        "platform": args.platform,
        "up": args.up,
        "bvid": args.bvid,
        "date": args.date,
    }
    store = WisdomStore(root=Path(args.root) if args.root else None)
    existing = store.list_by_status("approved")
    cards = asyncio.run(extract_cards(transcript, source, existing=existing))
    today = _dt.date.today().isoformat()
    for card in cards:
        card.id = store.next_id()
        card.created = today
        store.save(card)
    print(f"[wisdom] 抽取 {len(cards)} 张草稿卡, 待审总数 {len(store.list_by_status('draft'))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="financial_analyst.wisdom.cli")
    sub = p.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("extract", help="转写文本 → 草稿经验卡")
    ex.add_argument("transcript", help="转写文本 .txt 路径")
    ex.add_argument("--platform", default="bilibili")
    ex.add_argument("--up", default="")
    ex.add_argument("--bvid", default="")
    ex.add_argument("--date", default="")
    ex.add_argument("--root", default=None, help="wisdom 存储根 (默认 ~/.financial-analyst/wisdom)")
    ex.set_defaults(func=_run_extract)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_cli.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add src/financial_analyst/wisdom/cli.py tests/test_wisdom_cli.py
git commit -m "feat(wisdom): cli extract subcommand (assigns ids + saves drafts)"
```

---

## Task 6: buddy 工具 wisdom_review + wisdom_search

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (新增 2 个 `_tool_*` 函数 + 2 个 `Tool(...)` 注册到工具 list)
- Test: `tests/test_wisdom_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_tools.py
from financial_analyst.buddy.tools import _tool_wisdom_review, _tool_wisdom_search
from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore


def _seed(tmp_path):
    store = WisdomStore(root=tmp_path)
    store.save(WisdomCard(id="EV-001", title="低分草稿", status="draft",
                          quality_score=0.3, body="## 经验\nx\n## 反例\ny"))
    store.save(WisdomCard(id="EV-002", title="高分草稿", status="draft",
                          quality_score=0.9, body="## 经验\nx\n## 反例\ny"))
    store.save(WisdomCard(id="EV-003", title="已批准的证券组合经验", status="approved",
                          quality_score=0.8,
                          body="## 经验\n证券板块判断兑现.\n## 反例\n独立行情失真"))
    return store


def test_review_list_sorted_by_quality_desc(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_review(action="list")
    assert not res.is_error
    # 高分在前
    assert res.content.index("EV-002") < res.content.index("EV-001")
    # 只列 draft, 不含 approved
    assert "EV-003" not in res.content


def test_review_approve_moves_to_approved(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_review(action="approve", card_id="EV-002", reviewed_by="xuyi")
    assert not res.is_error
    assert (tmp_path / "approved" / "EV-002.md").exists()
    assert not (tmp_path / "draft" / "EV-002.md").exists()


def test_review_approve_missing_card_is_error(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_review(action="approve", card_id="EV-999")
    assert res.is_error


def test_search_only_hits_approved(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_search(query="证券")
    assert not res.is_error
    assert "证券" in res.content
    # draft 内容不应被检索到
    assert "低分草稿" not in res.content


def test_search_no_hit_returns_message(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setenv("FA_WISDOM_ROOT", str(tmp_path))
    res = _tool_wisdom_search(query="不存在的关键词zzz")
    assert not res.is_error
    assert "未找到" in res.content or "no" in res.content.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_tools.py -v`
Expected: FAIL — `ImportError: cannot import name '_tool_wisdom_review'`

- [ ] **Step 3: Write minimal implementation**

Add near the other `_tool_*` functions in `src/financial_analyst/buddy/tools.py`. First add a helper to resolve the wisdom root (env override for tests):

```python
def _wisdom_store():
    """Resolve WisdomStore, honouring FA_WISDOM_ROOT (tests inject tmp)."""
    import os
    from pathlib import Path
    from financial_analyst.wisdom.store import WisdomStore
    root = os.environ.get("FA_WISDOM_ROOT")
    return WisdomStore(root=Path(root) if root else None)


def _tool_wisdom_review(action: str, card_id: Optional[str] = None,
                        reviewed_by: Optional[str] = None) -> ToolResult:
    """审阅视频经验草稿卡. action: list | approve | reject.

    - list: 列出待审 draft 卡 (按 quality_score 降序), 给出 id/标题/自评分/置信/互证.
    - approve: 把指定卡移入正式知识库 (approved), 之后 wisdom_search 可命中.
    - reject: 把指定卡移入 rejected (留痕防重复抽取).
    """
    store = _wisdom_store()
    if action == "list":
        drafts = sorted(store.list_by_status("draft"),
                        key=lambda c: c.quality_score, reverse=True)
        if not drafts:
            return ToolResult("没有待审经验草稿.")
        lines = [
            f"- {c.id} | score={c.quality_score:.2f} | {c.confidence} | {c.title}"
            f"{' | 互证 ' + ','.join(c.corroborates) if c.corroborates else ''}"
            for c in drafts
        ]
        return ToolResult("待审经验草稿 (按自评分降序):\n" + "\n".join(lines))
    if action in ("approve", "reject"):
        if not card_id:
            return ToolResult("approve/reject 需要 card_id", is_error=True)
        new_status = "approved" if action == "approve" else "rejected"
        try:
            store.set_status(card_id, new_status, reviewed_by=reviewed_by)
        except KeyError:
            return ToolResult(f"找不到经验卡 {card_id}", is_error=True)
        return ToolResult(f"{card_id} → {new_status}")
    return ToolResult(f"未知 action: {action} (应为 list/approve/reject)", is_error=True)


def _tool_wisdom_search(query: str, tags: Optional[list] = None,
                        top_k: int = 5) -> ToolResult:
    """检索已沉淀的视频投资经验 (仅 approved). 返回相关经验卡正文供研判引用.

    query: 关键词 (子串匹配); tags: 可选标签过滤; top_k: 返回条数.
    """
    from financial_analyst.knowledge.local_markdown import LocalMarkdownKB
    store = _wisdom_store()
    kb = LocalMarkdownKB(store.root / "approved")
    hits = kb.query(query, top_k=top_k)
    if tags:
        hits = [h for h in hits if any(t in h["content"] for t in tags)]
    if not hits:
        return ToolResult(f"未找到匹配 {query!r} 的经验卡.")
    blocks = [f"### {h['path']}\n{h['content']}" for h in hits]
    return ToolResult("\n\n---\n\n".join(blocks))
```

Then register both into the tools list (find where other `Tool(...)` entries are assembled, near line 1459+, and append):

```python
    Tool(
        name="wisdom_review",
        description="审阅视频经验草稿卡 (list/approve/reject). approve 后进正式知识库.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "approve", "reject"]},
                "card_id": {"type": "string", "description": "approve/reject 时必填, 如 EV-013"},
                "reviewed_by": {"type": "string", "description": "审阅人, 可选"},
            },
            "required": ["action"],
        },
        run=_tool_wisdom_review,
        cost_hint="seconds",
        confirm_required=True,
    ),
    Tool(
        name="wisdom_search",
        description="检索已沉淀的视频投资经验 (仅已批准). 研判/研报引用 UP 主经验时用.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
        run=_tool_wisdom_search,
        cost_hint="seconds",
        confirm_required=False,
    ),
```

> Note: confirm the exact import block at top of `tools.py` already has `Optional` (it does — used by `_tool_update_data`). If the tools list is built as a module-level list literal, append these two entries inside it; if assembled via `.append()` / a registry function, match that pattern instead. Read lines ~1440-1940 to find the assembly site before editing.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_tools.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Verify the tools registered (no schema breakage)**

Run: `cd G:/financial-analyst && python -c "from financial_analyst.buddy.tools import _tool_wisdom_review, _tool_wisdom_search; print('ok')"`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add src/financial_analyst/buddy/tools.py tests/test_wisdom_tools.py
git commit -m "feat(buddy): wisdom_review + wisdom_search tools"
```

---

## Task 7: 迁移现有 12 条 (bilibili_notes.md → approved)

**Files:**
- Create: `src/financial_analyst/wisdom/migrate.py`
- Test: `tests/test_wisdom_migrate.py`

> 背景: PoC 12 条在 `G:/stocks/strategy/wisdom/bilibili_notes.md`, 已人工 review, 直接进 `approved/`。迁移脚本接受任意 `bilibili_notes.md` 路径 (不 import stocks 代码, 跨仓库零耦合)。它解析 `## EV-NNN 标题` 段 + `**置信**` + `**标签**` + `**来源**` + 4 段正文。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wisdom_migrate.py
from financial_analyst.wisdom.migrate import parse_notes_markdown
from financial_analyst.wisdom.store import WisdomStore
from financial_analyst.wisdom.migrate import migrate_file


_SAMPLE = """# B 站视频经验沉淀

## 索引
(表格略)

---

## EV-001 「安心持股」四要件信号

**经验**: 四个条件同时成立才持股.

**适用条件**: 仓位决策.

**操作建议**: 四要件齐 → 维持仓位.

**反例 / 边界**: 5 月底已不满足.

**置信**: 高

**标签**: #择时 #信号体系 #金叉

**来源**: BV1f2VA6yEXS / 舵主老徐 / 2026-05-26 / 段 96-109

---

## EV-002 市场结构极端集中警示

**经验**: 板块成交占比 >15% 警示.

**适用条件**: 大盘择时.

**操作建议**: 占比高谨慎追涨.

**反例 / 边界**: 题材爆发期初不算.

**置信**: 高

**标签**: #流动性 #板块虹吸

**来源**: 舵主老徐 / BV1f2VA6yEXS / 段 320-342

---

## 元信息
(略)
"""


def test_parse_extracts_two_cards():
    cards = parse_notes_markdown(_SAMPLE)
    assert len(cards) == 2
    ev1 = cards[0]
    assert ev1.id == "EV-001"
    assert ev1.title == "「安心持股」四要件信号"
    assert ev1.status == "approved"
    assert ev1.confidence == "高"
    assert "择时" in ev1.tags
    assert "## 经验" in ev1.body
    assert "## 反例 / 边界" in ev1.body
    assert ev1.source.get("platform") == "bilibili"


def test_migrate_file_writes_approved(tmp_path):
    notes = tmp_path / "bilibili_notes.md"
    notes.write_text(_SAMPLE, encoding="utf-8")
    store = WisdomStore(root=tmp_path / "wisdom")
    n = migrate_file(str(notes), store)
    assert n == 2
    approved = store.list_by_status("approved")
    assert {c.id for c in approved} == {"EV-001", "EV-002"}
    assert (tmp_path / "wisdom" / "approved" / "EV-001.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_migrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'financial_analyst.wisdom.migrate'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/financial_analyst/wisdom/migrate.py
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from financial_analyst.wisdom.card import WisdomCard
from financial_analyst.wisdom.store import WisdomStore

# 匹配 "## EV-001 标题"  (排除 "## 索引" / "## 元信息" 等非卡片段)
_HEADER_RE = re.compile(r"^##\s+(EV-\d+)\s+(.+?)\s*$", re.MULTILINE)
_FIELD_RE = {
    "confidence": re.compile(r"\*\*置信\*\*[:：]\s*(\S+)"),
    "tags": re.compile(r"\*\*标签\*\*[:：]\s*(.+)"),
    "source": re.compile(r"\*\*来源\*\*[:：]\s*(.+)"),
}
_BVID_RE = re.compile(r"(BV[0-9A-Za-z]+)")
_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
# 4 段正文标题
_SECTION_HEADS = ["经验", "适用条件", "操作建议", "反例 / 边界"]


def _slice_sections(block: str) -> str:
    """从一段卡片正文里抽出 4 段式 (经验/适用条件/操作建议/反例 边界), 拼成标准 body."""
    out = []
    for head in _SECTION_HEADS:
        m = re.search(rf"\*\*{re.escape(head)}\*\*[:：]\s*(.+?)(?=\n\*\*|\Z)", block, re.DOTALL)
        if m:
            out.append(f"## {head}\n{m.group(1).strip()}")
    return "\n\n".join(out)


def _parse_tags(raw: str) -> list[str]:
    return [t.lstrip("#").strip() for t in raw.split() if t.strip()]


def _parse_source(raw: str) -> dict:
    src: dict = {"platform": "bilibili"}
    bv = _BVID_RE.search(raw)
    if bv:
        src["bvid"] = bv.group(1)
    dt = _DATE_RE.search(raw)
    if dt:
        src["date"] = dt.group(1)
    return src


def parse_notes_markdown(text: str) -> list[WisdomCard]:
    """解析 bilibili_notes.md 全文 → approved 状态的 WisdomCard 列表."""
    headers = list(_HEADER_RE.finditer(text))
    cards: list[WisdomCard] = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        conf_m = _FIELD_RE["confidence"].search(block)
        tags_m = _FIELD_RE["tags"].search(block)
        src_m = _FIELD_RE["source"].search(block)
        cards.append(WisdomCard(
            id=h.group(1),
            title=h.group(2).strip(),
            status="approved",
            quality_score=1.0,           # 已人工 review
            confidence=conf_m.group(1) if conf_m else "中",
            tags=_parse_tags(tags_m.group(1)) if tags_m else [],
            source=_parse_source(src_m.group(1)) if src_m else {"platform": "bilibili"},
            body=_slice_sections(block),
            corroborates=[],
            conflicts=[],
            created="2026-05-28",
            reviewed_by="migrated",
        ))
    return cards


def migrate_file(notes_path: str, store: WisdomStore) -> int:
    text = Path(notes_path).read_text(encoding="utf-8")
    cards = parse_notes_markdown(text)
    for c in cards:
        store.save(c)
    return len(cards)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="financial_analyst.wisdom.migrate")
    p.add_argument("notes", help="bilibili_notes.md 路径")
    p.add_argument("--root", default=None, help="wisdom 存储根")
    args = p.parse_args(argv)
    store = WisdomStore(root=Path(args.root) if args.root else None)
    n = migrate_file(args.notes, store)
    print(f"[wisdom.migrate] 导入 {n} 张 approved 卡到 {store.root / 'approved'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_migrate.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the real migration (manual, one-off)**

Run: `cd G:/financial-analyst && python -m financial_analyst.wisdom.migrate G:/stocks/strategy/wisdom/bilibili_notes.md`
Expected: `[wisdom.migrate] 导入 12 张 approved 卡到 .../wisdom/approved`
Then spot-check: `python -m financial_analyst.wisdom.cli` is not needed; just `ls ~/.financial-analyst/wisdom/approved/` shows EV-001.md … EV-012.md.

- [ ] **Step 6: Commit**

```bash
git add src/financial_analyst/wisdom/migrate.py tests/test_wisdom_migrate.py
git commit -m "feat(wisdom): migrate bilibili_notes.md to approved cards"
```

---

## Task 8: 全套回归 + 文档

**Files:**
- Modify: `docs/extending.md` (加一节 video-wisdom 模块说明, 可选)

- [ ] **Step 1: Run full wisdom test suite**

Run: `cd G:/financial-analyst && python -m pytest tests/test_wisdom_*.py -v`
Expected: PASS (全部 ~23 tests)

- [ ] **Step 2: Run broader regression (确认没碰坏 buddy)**

Run: `cd G:/financial-analyst && python -m pytest tests/test_buddy.py tests/test_ask_tools.py -v`
Expected: PASS (buddy tools 加了 2 个不应破坏现有)

- [ ] **Step 3: Commit any doc updates**

```bash
git add docs/extending.md
git commit -m "docs(wisdom): document video-wisdom module"
```

---

## Self-Review Checklist (已核对)

- **Spec coverage**: extract (Task 4) / review (Task 6) / search (Task 6) / KB 存储 (Task 2) / 质量门 (Task 3,4) / 互证 (Task 3) / 状态机=目录 (Task 2) / 12 条迁移 (Task 7) — 全覆盖。`wisdom_collect` 是 Phase 2, 不在本计划 (符合 spec MVP 边界)。
- **Type consistency**: `WisdomCard(id,title,status,quality_score,confidence,tags,source,body,corroborates,conflicts,created,reviewed_by)` 在 card/store/extractor/cli/migrate/tools 全程一致。`WisdomStore(root).save/load/list_by_status/set_status/next_id` 签名一致。`build_extraction_messages(transcript,source,existing)` / `extract_cards(transcript,source,existing=None)` 一致。
- **Placeholder scan**: 无 TBD/TODO; 每个 code step 有完整代码; 每个 test step 有完整断言。
- **已知执行注意**: Task 1 的 `__init__.py` 引用 Task 2 的 `WisdomStore` — 若严格按序执行, Task 1 时先注释该行, Task 2 完成后恢复 (已在 Task 1 Note 标注)。Task 6 编辑 tools.py 前需 Read ~1440-1940 行确认工具 list 的组装方式 (literal vs append)。
