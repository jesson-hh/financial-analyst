# guanlan_v2.cards.ingest · 把 bilibili_notes(wisdom)解析灌进"未验证"(draft)桶
# 复用引擎 financial_analyst.wisdom.migrate.parse_notes_markdown(引擎已 vendored 进仓库);
# 测试用内联样例,不依赖 G:/stocks 的真文件。需 engine 在 PYTHONPATH。
from financial_analyst.wisdom.migrate import parse_notes_markdown

from guanlan_v2.cards.card import Card
from guanlan_v2.cards.store import CardStore
from guanlan_v2.cards.ingest import card_from_wisdom, ingest_notes_text

_SAMPLE = """# B 站视频经验沉淀

## 索引
| ID | 一句话 |
|---|---|
| EV-001 | 甲 |

---

## EV-001 安心持股四要件
**经验**: 四要件同时成立才安心持股。
**适用条件**: 仓位决策。
**操作建议**: 齐则持有,缺则减仓。
**反例 / 边界**: 节前节后不同。
**置信**: 高
**标签**: #择时 #信号体系
**来源**: BV1f2VA6yEXS / 舵主老徐 / 2026-05-26 / 段 96-109

---

## EV-002 市场集中度警示
**经验**: 单板块成交占比过高警示。
**适用条件**: 大盘择时。
**操作建议**: 谨慎追涨。
**反例 / 边界**: 题材初期不算病。
**置信**: 中
**标签**: #流动性
**来源**: BV1F3Gy6oEMx / 2026-05-27
"""


def test_card_from_wisdom_maps_to_draft():
    wc = parse_notes_markdown(_SAMPLE)[0]
    card = card_from_wisdom(wc)
    assert isinstance(card, Card)
    assert card.id == "EV-001"
    assert card.title == "安心持股四要件"
    assert card.status == "draft"               # 落"未验证"桶
    assert "经验" in card.insight                # 4 段式 body 进 insight
    assert "适用条件" in card.insight
    assert card.conf == 80                       # 高 → 80
    assert "择时" in card.tags
    assert card.ic == "" and card.expr == ""     # 验未做


def test_conf_maps_from_confidence():
    cards = {c.id: card_from_wisdom(c) for c in parse_notes_markdown(_SAMPLE)}
    assert cards["EV-001"].conf == 80   # 高
    assert cards["EV-002"].conf == 60   # 中


def test_ingest_writes_to_draft_bucket(tmp_path):
    store = CardStore(root=tmp_path)
    res = ingest_notes_text(store, _SAMPLE)
    assert res["ingested"] == 2
    drafts = store.list_by_status("draft")
    assert {c.id for c in drafts} == {"EV-001", "EV-002"}
    assert store.list_by_status("approved") == []


def test_ingest_is_idempotent(tmp_path):
    store = CardStore(root=tmp_path)
    ingest_notes_text(store, _SAMPLE)
    res2 = ingest_notes_text(store, _SAMPLE)
    assert res2["ingested"] == 0
    assert res2["skipped"] == 2
    assert len(store.list_by_status("draft")) == 2


def test_ingest_skips_ids_already_moved_out(tmp_path):
    # 用户把 EV-001 验证后移到 approved → 再灌不应在 draft 复活它
    store = CardStore(root=tmp_path)
    ingest_notes_text(store, _SAMPLE)
    store.set_status("EV-001", "approved")
    res = ingest_notes_text(store, _SAMPLE)
    assert res["skipped"] == 2
    assert {c.id for c in store.list_by_status("draft")} == {"EV-002"}
    assert {c.id for c in store.list_by_status("approved")} == {"EV-001"}
