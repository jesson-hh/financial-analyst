from __future__ import annotations
from enum import Enum


class PortfolioRating(str, Enum):
    BUY = "Buy"; OVERWEIGHT = "Overweight"; HOLD = "Hold"
    UNDERWEIGHT = "Underweight"; SELL = "Sell"


class ResearchAction(str, Enum):
    BUY = "buy"; ACCUMULATE = "accumulate"; HOLD = "hold"; AVOID = "avoid"; SELL = "sell"


class PositionAction(str, Enum):
    BUY = "buy"; ADD = "add"; HOLD = "hold"; REDUCE = "reduce"; SELL = "sell"


class SentimentBand(str, Enum):
    BULLISH = "Bullish"; MILDLY_BULLISH = "Mildly Bullish"; NEUTRAL = "Neutral"
    MIXED = "Mixed"; MILDLY_BEARISH = "Mildly Bearish"; BEARISH = "Bearish"


class Tier(str, Enum):
    READER = "reader"; CRITIC = "critic"; WRITER = "writer"


class Confidence(str, Enum):
    LOW = "low"; MEDIUM = "medium"; HIGH = "high"


class RotationStage(str, Enum):
    START = "启动"; SPREAD = "扩散"; DIVERGENCE = "分化"; EBB = "退潮"; UNKNOWN = "unknown"


class LegacyMarketCycleStage(str, Enum):
    FREEZE = "冰点"; DIVERGENCE = "分化"; SQUEEZE = "逼空"
    FERMENT = "发酵"; PULLBACK_START = "回踩/启动"


class MappingStatus(str, Enum):
    MAPPED = "mapped"; UNMAPPABLE = "unmappable"


class ExecutionKind(str, Enum):
    LLM = "llm"; DETERMINISTIC = "deterministic"


class ToolCallRequirement(str, Enum):
    FORBIDDEN = "forbidden"; OPTIONAL = "optional"; REQUIRED = "required"


class NodeStatus(str, Enum):
    PENDING = "pending"; READY = "ready"; RUNNING = "running"; COMPLETED = "completed"
    DEGRADED = "degraded"; INCOMPLETE = "incomplete"; FAILED = "failed"
    TIMED_OUT = "timed_out"; BLOCKED = "blocked"; SKIPPED = "skipped"; CANCELLED = "cancelled"


class ExperimentStatus(str, Enum):
    RUNNING = "running"; WAITING_FOR_MATURITY = "waiting_for_maturity"
    PASSED_VALIDATION = "passed_validation"; SEALED_EVALUATING = "sealed_evaluating"
    COMPLETED = "completed"; FAILED = "failed"


class DependencyPolicy(str, Enum):
    BLOCK = "block"; DEGRADE = "degrade"; SKIP = "skip"


class PlanSource(str, Enum):
    BOOTSTRAP = "bootstrap"; DYNAMIC = "dynamic"; PRESET = "preset"; PRESET_FALLBACK = "preset_fallback"


class ApprovalPolicy(str, Enum):
    REQUIRED = "required"; AUTO = "auto"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"; REJECTED = "rejected"


class DataStatus(str, Enum):
    OK = "ok"; NO_DATA = "no_data"; STALE = "stale"; UNAVAILABLE = "unavailable"; DEGRADED = "degraded"


class DataMode(str, Enum):
    ONLINE = "online"; PIT_REPLAY = "pit_replay"


class DataBackend(str, Enum):
    LIVE = "live"; PIT_STORE = "pit_store"; CACHE = "cache"
