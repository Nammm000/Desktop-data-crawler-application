from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.models.agent import AgentFormat, AgentStatus, AgentType


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# Wire values derived from the constants so the API contract and the stored
# values cannot drift (same idiom as UserStatusValue).
AgentTypeValue = Literal[AgentType.ONE_POST]
AgentFormatValue = Literal[AgentFormat.JSON, AgentFormat.XML, AgentFormat.MD]
AgentStatusValue = Literal[AgentStatus.NEW]

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
    """Public view of an agent."""

    id: str
    name: str
    type: AgentTypeValue
    status: AgentStatusValue
    format: AgentFormatValue
    script: str
    created_at: datetime
    updated_at: datetime
    updated_by: str

    @classmethod
    def from_doc(cls, doc: dict) -> "AgentOut":
        return cls(
            id=str(doc["_id"]),
            name=doc["name"],
            type=doc["type"],
            status=doc["status"],
            format=doc["format"],
            script=doc["script"],
            created_at=doc["createdAt"],
            updated_at=doc["updatedAt"],
            updated_by=doc["updatedBy"],
        )


class AgentCreate(_CamelModel):
    name: NameStr
    type: AgentTypeValue = AgentType.ONE_POST
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
