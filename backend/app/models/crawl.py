"""Per-link crawl failure reasons and their human-readable labels.

Reasons are stored on the agent's `lastRun.failures` entries and broadcast in
WS agentStatus frames; the frontend maps them via its own formatter."""

from app.models.agent import AgentStatus


class FailureReason:
    """Plain-string constants (per the agents/users convention)."""

    BROKEN_LINK = "broken_link"  # HTTP 404 / gone
    RATE_LIMITED = "rate_limited"  # HTTP 429
    HTTP_ERROR = "http_error"  # any other non-2xx status
    LOGIN_REDIRECT = "login_redirect"  # redirected to a login wall
    CHECKPOINT = "checkpoint"  # bot check / CAPTCHA / security checkpoint
    COOKIE_MISSING = "cookie_missing"  # page needs auth and none was stored
    TIMEOUT = "timeout"
    DNS_ERROR = "dns_error"
    CONNECTION_ERROR = "connection_error"
    PROXY_ERROR = "proxy_error"
    UNEXPECTED_HTML = "unexpected_html"  # 200 OK but no selector matched
    CANCELLED = "cancelled"
    REQUEST_ERROR = "request_error"  # anything unrecognized


# How many per-link failure entries a run keeps on the agent doc / broadcasts.
MAX_RECORDED_FAILURES = 100


class LastRunOutcome:
    """Terminal outcomes for a run; Completed/Stopped reuse AgentStatus."""

    COMPLETED = AgentStatus.COMPLETED
    FAILED = AgentStatus.FAILED
    STOPPED = AgentStatus.STOPPED
