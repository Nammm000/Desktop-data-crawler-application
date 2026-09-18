class AgentType:
    """The agent `type` field is a plain string (per the users convention)."""

    ONE_POST = "one_post"


class AgentFormat:
    """The agent `format` field describes how `script` should be parsed."""

    JSON = "json"
    XML = "xml"
    MD = "md"


class AgentStatus:
    """The agent `status` field is a plain string (per the users convention)."""

    NEW = "New"
    RUNNING = "Running"
    COMPLETED = "Completed"
    FAILED = "Failed"  # crawl crashed / interrupted by a server restart


AGENTS_COLLECTION = "agents"
