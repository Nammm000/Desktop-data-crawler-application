from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DataOut(_CamelModel):
    """One crawled page produced by an agent run."""

    id: str
    agent_id: str
    agent_name: str
    url: str
    # XPath field -> extracted value; None means no XPath in the script matched.
    fields: dict[str, str | None]
    crawled_at: datetime

    @classmethod
    def from_doc(cls, doc: dict) -> "DataOut":
        return cls(
            id=str(doc["_id"]),
            agent_id=doc["agentId"],
            agent_name=doc["agentName"],
            url=doc["url"],
            fields=doc["fields"],
            crawled_at=doc["crawledAt"],
        )


class DataList(_CamelModel):
    """Paginated listing of crawled-data records."""

    data: list[DataOut]
    total: int


class DataDeleteRequest(_CamelModel):
    ids: list[str] = Field(min_length=1)


class DataDeleteResult(_CamelModel):
    deleted: int
