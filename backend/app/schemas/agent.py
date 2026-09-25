from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.models.agent import AgentFormat, AgentSource, AgentStatus, AgentType


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# Wire values derived from the constants so the API contract and the stored
# values cannot drift (same idiom as UserStatusValue).
AgentTypeValue = Literal[AgentType.ONE_POST]
AgentSourceValue = Literal[
    AgentSource.GENERIC,
    AgentSource.FACEBOOK,
    AgentSource.SOURCE_PAGES,
    AgentSource.ECOMMERCE,
]
AgentFormatValue = Literal[AgentFormat.JSON, AgentFormat.XML, AgentFormat.MD]
AgentStatusValue = Literal[
    AgentStatus.NEW,
    AgentStatus.RUNNING,
    AgentStatus.COMPLETED,
    AgentStatus.STOPPED,
    AgentStatus.FAILED,
]
LastRunOutcomeValue = Literal[
    AgentStatus.COMPLETED, AgentStatus.STOPPED, AgentStatus.FAILED
]


class LastRunFailure(_CamelModel):
    """One link that yielded no data document, and why."""

    url: str | None = None  # None for run-level entries (e.g. "not started")
    reason: str
    detail: str | None = None


class LastRun(_CamelModel):
    """Summary of the agent's most recent run, written atomically with the
    terminal status flip. `failures` is capped (MAX_RECORDED_FAILURES)."""

    started_at: datetime
    finished_at: datetime
    outcome: LastRunOutcomeValue
    total_links: int
    success_count: int
    failure_count: int
    failures: list[LastRunFailure] = []

    @classmethod
    def from_doc(cls, doc: dict | None) -> "LastRun | None":
        if not isinstance(doc, dict):
            return None
        try:
            return cls(
                started_at=doc["startedAt"],
                finished_at=doc["finishedAt"],
                outcome=doc["outcome"],
                total_links=doc["totalLinks"],
                success_count=doc["successCount"],
                failure_count=doc["failureCount"],
                failures=[
                    LastRunFailure(
                        url=f.get("url"),
                        reason=f["reason"],
                        detail=f.get("detail"),
                    )
                    for f in doc.get("failures", [])
                ],
            )
        except (KeyError, TypeError, ValueError):
            return None

# Agent names are display labels (not login identifiers): generous length,
# no character pattern. Scripts are capped at 1M chars — safely under the
# 16MB BSON document limit.
NameStr = Annotated[str, Field(min_length=1, max_length=100)]
ScriptStr = Annotated[str, Field(min_length=1, max_length=1_000_000)]


def _strip(value):
    if isinstance(value, str):
        return value.strip()
    return value


class AgentOut(_CamelModel):
    """Public view of an agent. Credential VALUES never appear here — only
    the hasCookies/hasProxies flags (and, via GET /credentials-metadata,
    cookie names + counts for the edit dialog)."""

    id: str
    name: str
    type: AgentTypeValue
    source_type: AgentSourceValue = AgentSource.GENERIC
    status: AgentStatusValue
    format: AgentFormatValue
    script: str
    created_at: datetime
    updated_at: datetime
    updated_by: str
    has_cookies: bool = False
    has_proxies: bool = False
    last_run: LastRun | None = None  # absent on agents that never ran

    @classmethod
    def from_doc(cls, doc: dict) -> "AgentOut":
        return cls(
            id=str(doc["_id"]),
            name=doc["name"],
            type=doc["type"],
            source_type=doc.get("sourceType", AgentSource.GENERIC),
            status=doc["status"],
            format=doc["format"],
            script=doc["script"],
            created_at=doc["createdAt"],
            updated_at=doc["updatedAt"],
            updated_by=doc["updatedBy"],
            has_cookies=doc.get("hasCookies", False),
            has_proxies=doc.get("hasProxies", False),
            last_run=LastRun.from_doc(doc.get("lastRun")),
        )


class AgentCredentials(_CamelModel):
    """Write-only payload: raw paste of a Cookie header and/or proxy URLs
    (one per line). Stored encrypted; never returned."""

    cookie_header: str | None = None
    proxy_text: str | None = None


class AgentCredentialsMeta(_CamelModel):
    """Non-secret summary of what is stored, for the edit dialog."""

    cookie_names: list[str] = []
    proxy_count: int = 0
    updated_at: datetime | None = None


class AgentCreate(_CamelModel):
    name: NameStr
    type: AgentTypeValue = AgentType.ONE_POST
    source_type: AgentSourceValue = AgentSource.GENERIC
    status: AgentStatusValue = AgentStatus.NEW
    format: AgentFormatValue  # required: JSON validation hinges on it
    script: ScriptStr

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return _strip(v)


class AgentUpdate(_CamelModel):
    """Partial update; omitted fields stay unchanged."""

    name: NameStr | None = None
    type: AgentTypeValue | None = None
    source_type: AgentSourceValue | None = None
    status: AgentStatusValue | None = None
    format: AgentFormatValue | None = None
    script: ScriptStr | None = None

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, v):
        return _strip(v)


class AgentList(_CamelModel):
    """Paginated listing of agents."""

    agents: list[AgentOut]
    total: int
