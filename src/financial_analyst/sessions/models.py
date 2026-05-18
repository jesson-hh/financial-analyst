"""Session data models."""
from __future__ import annotations
from datetime import datetime
from typing import List
from pydantic import BaseModel, Field


class SessionEvent(BaseModel):
    """One user interaction in a session."""
    ts: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    kind: str = "chat"     # ask | report | intraday | brief | mainline | dream | slash | chat
    input: str = ""
    output_summary: str = ""
    duration_s: float = 0.0
    refs: List[str] = []   # file paths referenced/generated


class SessionMeta(BaseModel):
    """Session metadata."""
    name: str
    created_at: str
    last_active_at: str
    n_messages: int = 0
    description: str = ""
