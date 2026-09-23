"""Facebook post spider: fetches post URLs through mbasic.facebook.com with
optional user cookies (login) and rotating proxies, classifies failure
responses (login walls, bot checkpoints, rate limits), and extracts built-in
post fields with optional per-field XPath overrides from the agent script.

Reality check (see README): mbasic.facebook.com is server-rendered HTML but
Facebook changes it and may region-redirect to m.facebook.com. The built-in
selectors below are a starting point, deliberately overridable per field via
the agent script — when every run reports `unexpected_html`, update them (or
add overrides)."""

from __future__ import annotations

import itertools
from urllib.parse import urlparse, urlunparse

from scrapy import Spider
from scrapy.http import Request, Response

from app.models.crawl import FailureReason

# Ordered XPath fallback lists per built-in field. Joined with "\n" when a
# single XPath matches several nodes (post paragraphs, image links).
_BUILT_IN_FIELDS: dict[str, list[str]] = {
    "author": [
        "//header//strong/a/text()",
        "//div[@id='m_story_permalink_view']//h3/a/text()",
        "//strong/a/text()",
    ],
    "text": [
        "//div[@data-ft]//p//text()",
        "//div[@id='m_story_permalink_view']//p//text()",
        "//div[@data-ft]//div/span/text()",
    ],
    "timestamp": [
        "//abbr/text()",
        "//div[@id='m_story_permalink_view']//abbr/text()",
    ],
    "reactions": [
        "//a[contains(., 'reaction')]/text()",
    ],
    "comments": [
        "//a[contains(., 'comment')]/text()",
    ],
    "mediaUrls": [
        "//a[contains(@href, 'scontent')]/@href",
        "//img/@src",
    ],
}

_LOGIN_MARKERS = ("login.php", "/login", "device_based_login")
_CHECKPOINT_MARKERS = ("checkpoint", "captcha", "recover")


def is_facebook_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "facebook.com" or host.endswith(".facebook.com")
    )


def normalize_facebook_url(url: str, fb_host: str) -> str:
    """Rewrite any *.facebook.com URL onto the configured host (mbasic),
    keeping path and query. Facebook's own redirects are followed normally
    (e.g. mbasic -> m for some regions)."""
    if not is_facebook_url(url):
        raise ValueError(f"not a facebook.com https URL: {url}")
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    return urlunparse(parsed._replace(netloc=f"{fb_host}{port}"))


class FacebookPostSpider(Spider):
    """One pass over the agent's facebook links; results/failures land in the
    caller-owned shared lists (same contract as AgentScriptSpider)."""

    name = "facebook_posts"

    def __init__(
        self,
        links: list[str] | None = None,
        field_xpaths: dict[str, list[str]] | None = None,
        results: list[dict] | None = None,
        failures: list[dict] | None = None,
        cookies: dict[str, str] | None = None,
        proxies: list[str] | None = None,
        fb_host: str = "mbasic.facebook.com",
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.start_urls = links or []
        # Script keys override the built-in field XPaths key-by-key.
        self._field_xpaths = {**_BUILT_IN_FIELDS, **(field_xpaths or {})}
        self._results = results if results is not None else []
        self._failures = failures if failures is not None else []
        self._cookies = cookies
        self._proxy_cycle = (
            itertools.cycle(proxies) if proxies else None  # round-robin
        )
        self._fb_host = fb_host

    async def start(self):
        for url in self.start_urls:
            yield Request(
                normalize_facebook_url(url, self._fb_host),
                cookies=self._cookies,
                meta={"proxy": next(self._proxy_cycle)} if self._proxy_cycle else {},
                errback=self._on_error,
            )

    def parse(self, response: Response):
        reason, detail = self._classify(response)
        if reason is not None:
            self._failures.append(
                {"url": response.url, "reason": reason, "detail": detail}
            )
            return  # no data doc for a failed link
        fields: dict[str, str | None] = {}
        for field, xpaths in self._field_xpaths.items():
            fields[field] = self._extract(response, xpaths)
        if "permalink" not in self._field_xpaths or fields.get("permalink") in (None, ""):
            # The absolute final URL is the permalink (overridable via script).
            fields["permalink"] = response.url
        if all(value is None for value in fields.values()):
            self._failures.append(
                {
                    "url": response.url,
                    "reason": FailureReason.UNEXPECTED_HTML,
                    "detail": "page fetched but no field matched — selectors "
                    "may need updating (or an XPath override)",
                }
            )
            return
        self._results.append({"url": response.url, "fields": fields})

    @staticmethod
    def _extract(response: Response, xpaths: list[str]) -> str | None:
        """First XPath with matches wins; within it, all node values join
        with "\\n" (post paragraphs, image URLs). Bad XPaths are skipped."""
        for xp in xpaths:
            try:
                nodes = response.xpath(xp)
            except Exception:
                continue
                # parsel raises on malformed XPath — skip, don't kill the run
            values = []
            for node in nodes:
                if isinstance(node.root, str):  # attribute / text node
                    value = node.get()
                else:
                    value = node.xpath("string(.)").get()
                if value is not None and value.strip():
                    values.append(value.strip())
            if values:
                return "\n".join(values)
        return None

    def _classify(self, response: Response) -> tuple[str | None, str | None]:
        """Detect Facebook's soft failures (a 200 can still be a wall)."""
        url = response.url.lower()
        html_lower = response.text.lower() if response.text else ""
        if any(marker in url for marker in _LOGIN_MARKERS) or (
            'name="pass"' in html_lower and "login" in html_lower
        ):
            if self._cookies:
                return (
                    FailureReason.LOGIN_REDIRECT,
                    "redirected to the login page — the stored cookies may "
                    "have expired; re-enter them on the agent",
                )
            return (
                FailureReason.COOKIE_MISSING,
                "this content needs a logged-in session — add cookies to "
                "the agent",
            )
        if any(marker in url for marker in _CHECKPOINT_MARKERS) or (
            "security check" in html_lower
        ):
            return (
                FailureReason.CHECKPOINT,
                "Facebook showed a bot check / security checkpoint — slow "
                "the crawl down (FACEBOOK_DOWNLOAD_DELAY) or rotate proxies",
            )
        return None, None

    def _on_error(self, failure):
        """Mirror AgentScriptSpider's errback: record WHY the link is
        missing (HttpError/DNS/timeout classification lives there and is
        source-independent — import lazily to avoid a cycle)."""
        from app.services.crawler_service import _classify_download_error

        request = getattr(failure, "request", None)
        url = getattr(request, "url", None)
        via_proxy = bool((getattr(request, "meta", None) or {}).get("proxy"))
        exc = getattr(failure, "value", failure)
        reason, detail = _classify_download_error(exc, via_proxy=via_proxy)
        self._failures.append({"url": url, "reason": reason, "detail": detail})


def facebook_settings(base: dict, *, download_delay: float) -> dict:
    """Overlay for facebook runs: a realistic UA and deliberately gentle
    pacing keep the session away from checkpoints."""
    overlay = dict(base)
    overlay.update(
        {
            # A browser UA: Facebook serves bot-hostile markup to unknown agents.
            "USER_AGENT": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
            "DOWNLOAD_DELAY": download_delay,
            "RANDOMIZE_DOWNLOAD_DELAY": True,  # 0.5–1.5x the delay
            "AUTOTHROTTLE_ENABLED": True,
            "RETRY_TIMES": 2,
            "COOKIES_ENABLED": True,  # the session cookies must persist
        }
    )
    return overlay
