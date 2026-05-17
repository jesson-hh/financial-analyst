from financial_analyst.utils.events import EventBus


def test_subscribe_and_emit():
    bus = EventBus()
    received = []
    bus.subscribe("ping", lambda data: received.append(data))
    bus.emit("ping", {"x": 1})
    bus.emit("ping", {"x": 2})
    assert received == [{"x": 1}, {"x": 2}]


def test_unrelated_events_not_received():
    bus = EventBus()
    received = []
    bus.subscribe("ping", lambda data: received.append(data))
    bus.emit("pong", {"x": 1})
    assert received == []


def test_multiple_subscribers():
    bus = EventBus()
    a, b = [], []
    bus.subscribe("evt", a.append)
    bus.subscribe("evt", b.append)
    bus.emit("evt", "hi")
    assert a == ["hi"] and b == ["hi"]
