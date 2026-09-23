class AgentType:
    """The agent `type` field is a plain string (per the users convention)."""

    ONE_POST = "one_post"


class AgentSource:
    """Which spider runs the agent — a plain string on the agent doc."""

    GENERIC = "generic"  # XPath script against any allowed http(s) site
    FACEBOOK = "facebook"  # built-in post extraction, cookies/proxies


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
    STOPPED = "Stopped"  # run cancelled by a stop request; partial data kept
    FAILED = "Failed"  # crawl crashed / interrupted by a server restart


AGENTS_COLLECTION = "agents"

# One document per agent that HAS stored credentials, keyed by the agent's
# _id; holds only Fernet ciphertext + non-sensitive metadata (cookie names,
# counts) — plaintext exists only inside execute_crawl's stack frame.
AGENT_SECRETS_COLLECTION = "agent_secrets"
