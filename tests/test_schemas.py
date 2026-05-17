from financial_analyst.agent.schemas import EventItem, Severity


def test_event_item_validates_date():
    e = EventItem(date="2026-05-17", category="earnings", sentiment="pos", summary="Q1 beat")
    assert e.date == "2026-05-17"


def test_severity_enum():
    assert Severity.SEVERE.value == 3
