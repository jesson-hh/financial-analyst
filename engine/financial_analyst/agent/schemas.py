from __future__ import annotations

import re
from enum import IntEnum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_TEXT_RE = re.compile(r"^[一-鿿\w\s\.,%$()/_:\-]+$")


class Severity(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    SEVERE = 3


class EventItem(BaseModel):
    model_config = {"extra": "forbid"}
    date: str = Field(max_length=10)
    category: str = Field(max_length=32)
    sentiment: str = Field(pattern="^(pos|neg|neu)$")
    summary: str = Field(max_length=256)
    severity: Severity = Severity.NONE

    @field_validator("date")
    @classmethod
    def _validate_date(cls, v: str) -> str:
        if not DATE_RE.match(v):
            raise ValueError(f"date must be YYYY-MM-DD, got {v}")
        return v

    @field_validator("summary")
    @classmethod
    def _safe_summary(cls, v: str) -> str:
        if not SAFE_TEXT_RE.match(v):
            raise ValueError("summary contains disallowed characters")
        return v


class LHBSeat(BaseModel):
    model_config = {"extra": "forbid"}
    seat_name: str = Field(max_length=64)
    side: str = Field(pattern="^(buy|sell)$")
    amount_yi: float
    trader_tag: Optional[str] = Field(default=None, max_length=64)
