from __future__ import annotations
from collections import defaultdict
from typing import Any, Callable, Dict, List


class EventBus:
    def __init__(self):
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, event: str, handler: Callable[[Any], None]) -> None:
        self._subscribers[event].append(handler)

    def emit(self, event: str, data: Any) -> None:
        for h in self._subscribers.get(event, []):
            h(data)
