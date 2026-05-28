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


_SAMPLE_NESTED = """## EV-099 测试嵌套粗体子结构

**经验**: 总述句导语.

**维度 A — 成交占比**: 单板块 >15-20% 触线.

**维度 B — 涨跌比**: 涨跌家数 <1/10 触线.

**适用条件**: 大盘择时.

**操作建议**: 占比高谨慎追涨.

**反例 / 边界**: 题材爆发期初不算.

**置信**: 高

**标签**: #流动性

**来源**: BVxxx / 2026-05-27
"""


def test_body_preserves_bold_substructure():
    # 回归: 正文段落内行首 **粗体** 子结构 (维度 A/B) 不应把"经验"段提前截断,
    # 否则 >15-20% / <1/10 等关键数字会丢失 (controller verify 发现的 migrate bug).
    cards = parse_notes_markdown(_SAMPLE_NESTED)
    assert len(cards) == 1
    body = cards[0].body
    assert "维度 A" in body
    assert ">15-20%" in body
    assert "维度 B" in body
    assert "<1/10" in body
    assert "## 适用条件" in body
    assert "## 反例 / 边界" in body
