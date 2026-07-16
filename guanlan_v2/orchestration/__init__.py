"""guanlan_v2.orchestration — 通用编排内核的契约层(Phase 1)。

纯离线、零运行时行为:严格/带版本/不可变的 Pydantic v2 契约模型 + 确定性
语义/审计摘要。所有公共语义模型继承 Task 1 建立的 ``ContractModel`` /
``DigestModel``,并调用 ``canonical_json`` / ``content_digest`` /
``audit_digest``。

Task 13 冻结本包的**审阅过的公共导出面**:契约基座与数字摘要工具、逻辑
ref 原语、以及已封存的 schema 注册中心 API(``default_registry`` /
``PHASE1_PUBLIC_MODELS`` / ``INTERNAL_MODELS``)与其解析到的载荷/事实模型。

导入保持纯净:仅急切导入零依赖循环的基座(``digest`` / ``refs`` /
``schema_registry``,后者顶层只依赖前两者)。载荷/事实模型类与注册中心 population
经包级 ``__getattr__`` 惰性加载——``import guanlan_v2.orchestration`` 既不拉起整个
模型图,也不构建或封存任何注册中心。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# -- eager: cheap, cycle-free foundation ------------------------------------ #
from guanlan_v2.orchestration.digest import (
    CJSON_VERSION,
    ContractModel,
    DigestHex,
    DigestModel,
    FiniteFloat,
    NonEmptyStr,
    NonNegativeInt,
    PositiveInt,
    UtcDateTime,
    audit_digest,
    canonical_json,
    content_digest,
    verify_digest,
)
from guanlan_v2.orchestration.refs import (
    CapabilityRef,
    ContentRef,
    PayloadRef,
    SchemaManifestEntry,
    SchemaRef,
    TypedPayloadRef,
)
from guanlan_v2.orchestration.schema_registry import (
    RegistrySealedError,
    SchemaConflictError,
    SchemaRegistry,
    SchemaRegistryError,
    SchemaVersionMismatchError,
    UnknownSchemaError,
    default_registry,
)

#: names re-exported *lazily* — accessing them imports the model graph on demand,
#: keeping ``import guanlan_v2.orchestration`` itself light and pure.
_LAZY_CONTEXT = frozenset(
    {
        "ContextSnapshot",
        "ContextRuntimeRequirements",
        "InputArtifactBinding",
        "InputSnapshot",
        "MemoryRecordRef",
        "EmptyMemorySnapshot",
        "EmptyMemorySelection",
        "build_empty_memory_binding",
    }
)
_LAZY_SCHEMAS = frozenset({"ResearchPlan", "PortfolioDecision", "SentimentReport"})
_LAZY_REGISTRY = frozenset({"PHASE1_PUBLIC_MODELS", "INTERNAL_MODELS"})

if TYPE_CHECKING:  # give static tools the real symbols without eager runtime import
    from guanlan_v2.orchestration.context import (
        ContextRuntimeRequirements,
        ContextSnapshot,
        EmptyMemorySelection,
        EmptyMemorySnapshot,
        InputArtifactBinding,
        InputSnapshot,
        MemoryRecordRef,
        build_empty_memory_binding,
    )
    from guanlan_v2.orchestration.schemas import (
        PortfolioDecision,
        ResearchPlan,
        SentimentReport,
    )

__all__ = [
    # contract base + digests
    "CJSON_VERSION",
    "ContractModel",
    "DigestModel",
    "DigestHex",
    "UtcDateTime",
    "FiniteFloat",
    "NonEmptyStr",
    "NonNegativeInt",
    "PositiveInt",
    "canonical_json",
    "content_digest",
    "audit_digest",
    "verify_digest",
    # refs
    "SchemaRef",
    "ContentRef",
    "CapabilityRef",
    "PayloadRef",
    "TypedPayloadRef",
    "SchemaManifestEntry",
    # registry API
    "SchemaRegistry",
    "SchemaRegistryError",
    "UnknownSchemaError",
    "SchemaConflictError",
    "RegistrySealedError",
    "SchemaVersionMismatchError",
    "default_registry",
    "PHASE1_PUBLIC_MODELS",  # noqa: F822 — lazy via __getattr__
    "INTERNAL_MODELS",  # noqa: F822 — lazy via __getattr__
    # registered payload / fact schemas (lazy)
    "ResearchPlan",  # noqa: F822 — lazy via __getattr__
    "PortfolioDecision",  # noqa: F822 — lazy via __getattr__
    "SentimentReport",  # noqa: F822 — lazy via __getattr__
    "MemoryRecordRef",  # noqa: F822 — lazy via __getattr__
    "EmptyMemorySnapshot",  # noqa: F822 — lazy via __getattr__
    "EmptyMemorySelection",  # noqa: F822 — lazy via __getattr__
    "ContextSnapshot",  # noqa: F822 — lazy via __getattr__
    "ContextRuntimeRequirements",  # noqa: F822 — lazy via __getattr__
    "InputArtifactBinding",  # noqa: F822 — lazy via __getattr__
    "InputSnapshot",  # noqa: F822 — lazy via __getattr__
    "build_empty_memory_binding",  # noqa: F822 — lazy via __getattr__
]


def __getattr__(name: str) -> Any:
    """Lazily resolve the model graph + registry population (keeps import pure)."""
    if name in _LAZY_REGISTRY:
        from guanlan_v2.orchestration import schema_registry

        return getattr(schema_registry, name)
    if name in _LAZY_CONTEXT:
        from guanlan_v2.orchestration import context

        return getattr(context, name)
    if name in _LAZY_SCHEMAS:
        from guanlan_v2.orchestration import schemas

        return getattr(schemas, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
