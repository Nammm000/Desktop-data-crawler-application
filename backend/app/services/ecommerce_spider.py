"""E-commerce product spider: a two-phase autonomous crawl of a product
catalog. Seeds are category/listing pages; the spider discovers product
links on each listing page, follows the listing "next page" link until it
disappears (or a cap is hit), and extracts built-in product fields on each
detail page with optional per-field XPath overrides from the agent script.

The built-in selectors target books.toscrape.com (the reference sandbox —
static HTML, no login, no anti-bot) with og:/itemprop fallbacks that also
match many other static-HTML shops. JS-rendered shops need source_pages
(Playwright), not this spider.

Reality check: like mbasic.facebook.com, real shops change their markup.
When every product of a run reports `unexpected_html`, update the built-ins
(or add per-field overrides via the agent script)."""

from __future__ import annotations

import dataclasses
import re
from urllib.parse import urljoin, urlparse

from fastapi import HTTPException, status
from scrapy import Spider
from scrapy.http import Request, Response

from app.models.crawl import FailureReason
from app.services.source_pages_discovery import (
    CHROME_USER_AGENT,
    _as_xpath_list,
    normalize_discovered_href,
)

# Script keys that configure the crawl (links/product_link/next_page/caps)
# — never data fields, even though their values are strings/lists too.
RESERVED_KEYS = (
    "links",
    "product_link",
    "next_page",
    "max_next",
    "max_next_page",
    "max_products",
)

# Listing-page XPaths: product detail links and the pagination "next" link.
# books.toscrape.com: products are article.product_pod > h3 > a; the next
# link is li.next > a (the element is ABSENT on the last page — a missing
# match is the normal end of pagination, not a failure).
_BUILT_IN_PRODUCT_LINK = ["//article[contains(@class,'product_pod')]//h3/a/@href"]
_BUILT_IN_NEXT_PAGE = ["//li[@class='next']/a/@href"]

# Ordered XPath fallback lists per built-in product field. Multi-class
# elements (class="col-sm-6 product_main", class="star-rating Three") need
# contains() — an exact @class match would never fire.
_BUILT_IN_FIELDS: dict[str, list[str]] = {
    "title": [
        "//div[contains(@class,'product_main')]/h1/text()",
        "//meta[@property='og:title']/@content",
        "//h1/text()",
        "//*[@itemprop='name']/text()",
    ],
    # price/currency share the same sources: parse_price splits one match
    # into the numeric amount ("26.08") and the ISO currency ("GBP") —
    # data fields are flat strings, so this cannot be one nested object.
    "price": [
        "//div[contains(@class,'product_main')]//p[contains(@class,'price_color')]/text()",
        "//meta[@property='product:price:amount']/@content",
        "//*[@itemprop='price']/text()",
        "//*[@itemprop='price']/@content",
    ],
    "currency": [
        "//div[contains(@class,'product_main')]//p[contains(@class,'price_color')]/text()",
        "//meta[@property='product:price:currency']/@content",
    ],
    "availability": [
        "//div[contains(@class,'product_main')]//p[contains(@class,'availability')]/text()",
        "//*[@itemprop='availability']/@content",
        "//*[@itemprop='availability']/@href",
    ],
    # books.toscrape encodes the rating as a word class ("star-rating Four")
    # — rating_from_class maps it to "4".
    "rating": [
        "//div[contains(@class,'product_main')]//p[contains(@class,'star-rating')]/@class",
    ],
    # The last breadcrumb link is the leaf category; the final li (product
    # title) carries no <a> and is excluded by the li[a] filter.
    "category": [
        "//ul[contains(@class,'breadcrumb')]//li[a][last()]/a/text()",
    ],
    "imageUrl": [
        "//div[@id='product_gallery']//img/@src",
        "//meta[@property='og:image']/@content",
        "//*[@itemprop='image']/@src",
    ],
    "description": [
        "//div[@id='product_description']/following-sibling::p[1]/text()",
        "//meta[@property='og:description']/@content",
        "//*[@itemprop='description']/text()",
    ],
}

_PRICE_RE = re.compile(r"^\s*([£$€])?\s*(\d[\d,]*(?:\.\d+)?)")
_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
_RATING_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5"}


def parse_price(raw: str | None) -> tuple[str | None, str | None]:
    """Split a raw price string ("£51.77", "$1,234.56", "26.08") into
    (numeric amount, ISO currency). Either side is None when absent."""
    if not isinstance(raw, str):
        return None, None
    match = _PRICE_RE.match(raw)
    if match is None:
        return None, None
    symbol, amount = match.groups()
    return amount.replace(",", ""), _CURRENCY_SYMBOLS.get(symbol)


def rating_from_class(raw: str | None) -> str | None:
    """Map a class attribute containing a rating word ("star-rating Five")
    to "5". None when no rating word is present."""
    if not isinstance(raw, str):
        return None
    for token in raw.lower().split():
        if token in _RATING_WORDS:
            return _RATING_WORDS[token]
    return None


@dataclasses.dataclass(frozen=True)
class EcommercePlan:
    """Normalized ecommerce script (see parse_ecommerce_script)."""

    seeds: list[str]
    product_link_xpaths: list[str] | None  # None -> spider built-in default
    next_page_xpaths: list[str] | None  # None -> spider built-in default
    max_next: int | None  # None = follow pagination until it runs out
    max_products: int | None  # None = filled from settings at run time
    field_xpaths: dict[str, list[str]]


def parse_ecommerce_script(parsed: dict) -> EcommercePlan:
    """Pure structural validation/normalization of an already-parsed
    ecommerce script. Raises 400 with the same message style as
    crawler_service._parse_run_script; safe to call from write-time
    validation (agent_service) and run-time validation alike."""
    links = parsed.get("links")
    if (
        not isinstance(links, list)
        or not links
        or not all(isinstance(u, str) and u.strip() for u in links)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script 'links' must be a non-empty list of URL strings",
        )
    product_link_xpaths = (
        _as_xpath_list("'product_link'", parsed["product_link"])
        if "product_link" in parsed
        else None
    )
    next_page_xpaths = (
        _as_xpath_list("'next_page'", parsed["next_page"])
        if "next_page" in parsed
        else None
    )

    has_max = "max_next" in parsed
    has_max_alias = "max_next_page" in parsed
    if has_max and has_max_alias:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script cannot contain both 'max_next' and 'max_next_page'",
        )
    max_next = None
    if has_max or has_max_alias:
        raw = parsed["max_next" if has_max else "max_next_page"]
        # bool is an int subclass — reject True/False explicitly.
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="script 'max_next' must be an integer of at least 1",
            )
        max_next = raw

    max_products = None
    if "max_products" in parsed:
        raw = parsed["max_products"]
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="script 'max_products' must be an integer of at least 1",
            )
        max_products = raw

    field_xpaths: dict[str, list[str]] = {}
    for key, value in parsed.items():
        if key in RESERVED_KEYS:
            continue
        field_xpaths[key] = _as_xpath_list(f"field '{key}'", value)
    return EcommercePlan(
        seeds=[u.strip() for u in links],
        product_link_xpaths=product_link_xpaths,
        next_page_xpaths=next_page_xpaths,
        max_next=max_next,
        max_products=max_products,
        field_xpaths=field_xpaths,
    )


class EcommerceProductSpider(Spider):
    """Two-phase crawl: listing pages (product-link discovery + pagination)
    -> product detail pages (field extraction). Results/failures land in the
    caller-owned shared lists (same contract as AgentScriptSpider).

    Discovered product URLs are deduped across listing pages; links are only
    followed on the seeds' exact hostnames (an off-site href is skipped, not
    an error). `stats` counts discovered/attempted products so the run
    summary stays exact even when the crawl dies early."""

    name = "ecommerce_products"

    def __init__(
        self,
        links: list[str] | None = None,
        field_xpaths: dict[str, list[str]] | None = None,
        results: list[dict] | None = None,
        failures: list[dict] | None = None,
        product_link_xpaths: list[str] | None = None,
        next_page_xpaths: list[str] | None = None,
        max_next: int | None = None,
        max_products: int | None = None,
        stats: dict[str, int] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.start_urls = links or []
        # Script keys override the built-in XPaths wholesale (facebook merge).
        self._product_xpaths = (
            list(product_link_xpaths)
            if product_link_xpaths
            else list(_BUILT_IN_PRODUCT_LINK)
        )
        self._next_xpaths = (
            list(next_page_xpaths) if next_page_xpaths else list(_BUILT_IN_NEXT_PAGE)
        )
        self._field_xpaths = {**_BUILT_IN_FIELDS, **(field_xpaths or {})}
        self._results = results if results is not None else []
        self._failures = failures if failures is not None else []
        self._max_next = max_next
        self._max_products = max_products
        self._capped = False  # set once max_products is reached
        self._seen_products: set[str] = set()
        self._seen_listings: set[str] = set()
        self._allowed_hosts = {
            host
            for host in (urlparse(u).hostname for u in self.start_urls)
            if host
        }
        # Caller-injected so lastRun accounting survives an early crash.
        self._stats = stats if stats is not None else {}
        self._stats.setdefault("discovered_products", 0)
        self._stats.setdefault("attempted_products", 0)

    async def start(self):
        for url in self.start_urls:
            self._seen_listings.add(url)
            yield Request(
                url,
                callback=self.parse,
                errback=self._on_error,
                meta={"phase": "listing", "nav": 0},
            )

    def parse(self, response: Response):
        """Listing callback: queue product pages, then follow the next-page
        link (a missing link is the normal end of pagination)."""
        if self._capped:
            # The cap fired on an earlier listing — stop discovering; the
            # already-scheduled product requests drain to completion.
            return
        hrefs: list[str] = []
        for xp in self._product_xpaths:
            hrefs.extend(self._extract_all(response, xp))
        if not hrefs:
            # 200 OK but no product links: usually a markup change or a seed
            # that is not a listing page — the product_link drift canary.
            self._failures.append(
                {
                    "url": response.url,
                    "reason": FailureReason.UNEXPECTED_HTML,
                    "detail": "no product links matched on the listing page "
                    "— the product_link XPath may need an override",
                }
            )
            return  # a page this broken must not drive pagination either

        for href in hrefs:
            url = normalize_discovered_href(response.url, href)
            if url is None or urlparse(url).hostname not in self._allowed_hosts:
                continue  # off-site / non-link href — skipped, not an error
            if url in self._seen_products:
                continue  # a product shown on two listing pages is fetched once
            if (
                self._max_products is not None
                and len(self._seen_products) >= self._max_products
            ):
                # Cap reached: record it once, stop discovering, and let the
                # queued requests finish — an EXACT cap, unlike CloseSpider,
                # which would silently discard the still-queued requests.
                self._capped = True
                self._failures.append(
                    {
                        "url": None,
                        "reason": FailureReason.CANCELLED,
                        "detail": f"stopped after {self._max_products} unique "
                        "products (max_products)",
                    }
                )
                break
            self._seen_products.add(url)
            self._stats["discovered_products"] += 1
            yield Request(
                url,
                callback=self.parse_product,
                errback=self._on_error,
                meta={"phase": "product"},
            )
        if self._capped:
            return

        next_href = self._extract(response, self._next_xpaths)
        if next_href:
            next_url = normalize_discovered_href(response.url, next_href)
            nav = response.meta.get("nav", 0)
            if (
                next_url is not None
                and urlparse(next_url).hostname in self._allowed_hosts
                and next_url not in self._seen_listings  # circular-pagination guard
                and (self._max_next is None or nav < self._max_next)
            ):
                self._seen_listings.add(next_url)
                yield Request(
                    next_url,
                    callback=self.parse,
                    errback=self._on_error,
                    meta={"phase": "listing", "nav": nav + 1},
                )

    def parse_product(self, response: Response):
        self._stats["attempted_products"] += 1
        fields: dict[str, str | None] = {}
        for field, xpaths in self._field_xpaths.items():
            fields[field] = self._transform(field, response, self._extract(response, xpaths))
        if all(value is None for value in fields.values()):
            # 200 OK but nothing matched — the field-selector drift canary.
            self._failures.append(
                {
                    "url": response.url,
                    "reason": FailureReason.UNEXPECTED_HTML,
                    "detail": "page fetched but no field matched — selectors "
                    "may need updating (or an XPath override)",
                }
            )
            return
        if "productUrl" not in self._field_xpaths or fields.get("productUrl") in (None, ""):
            # The absolute final URL is the product URL (overridable via
            # script) — facebook's permalink idiom.
            fields["productUrl"] = response.url
        self._results.append({"url": response.url, "fields": fields})

    def _transform(
        self, field: str, response: Response, raw: str | None
    ) -> str | None:
        """Post-extraction per field: values are flat strings, so a price
        splits into amount/currency and a rating word maps to a digit."""
        if raw is None:
            return None
        if field == "price":
            return parse_price(raw)[0]
        if field == "currency":
            return parse_price(raw)[1]
        if field == "rating":
            return rating_from_class(raw)
        if field == "imageUrl":
            return urljoin(response.url, raw)
        return raw  # already stripped by _extract

    @staticmethod
    def _extract_all(response: Response, xp: str) -> list[str]:
        """All node values for ONE XPath (product/link lists); element nodes
        yield their XPath string-value. Bad XPaths are skipped, not fatal."""
        try:
            nodes = response.xpath(xp)
        except Exception:
            return []  # parsel raises on malformed XPath — skip it
        values = []
        for node in nodes:
            if isinstance(node.root, str):  # attribute / text node
                value = node.get()
            else:
                value = node.xpath("string(.)").get()
            if value is not None and value.strip():
                values.append(value.strip())
        return values

    @staticmethod
    def _extract(response: Response, xpaths: list[str]) -> str | None:
        """First non-empty value across an XPath list (product fields are
        scalars — no multi-node join, unlike the facebook text fields)."""
        for xp in xpaths:
            values = EcommerceProductSpider._extract_all(response, xp)
            if values:
                return values[0]
        return None

    def _on_error(self, failure):
        """Mirror AgentScriptSpider's errback: record WHY the page is
        missing (classification lives in crawler_service and is
        source-independent — import lazily to avoid the cycle)."""
        from app.services.crawler_service import _classify_download_error

        request = getattr(failure, "request", None)
        url = getattr(request, "url", None)
        meta = getattr(request, "meta", None) or {}
        if meta.get("phase") == "product":
            # A failed download was still attempted — keeps the stopped-run
            # accounting (discovered - attempted) exact.
            self._stats["attempted_products"] += 1
        exc = getattr(failure, "value", failure)
        reason, detail = _classify_download_error(exc)
        self._failures.append({"url": url, "reason": reason, "detail": detail})


def ecommerce_settings(
    base: dict, *, download_delay: float, concurrent_requests: int
) -> dict:
    """Overlay for ecommerce runs: static-HTML shops tolerate a browser UA
    and light concurrency; randomized delay + AutoThrottle keep it polite."""
    overlay = dict(base)
    overlay.update(
        {
            "USER_AGENT": CHROME_USER_AGENT,
            "CONCURRENT_REQUESTS_PER_DOMAIN": concurrent_requests,
            "DOWNLOAD_DELAY": download_delay,
            "RANDOMIZE_DOWNLOAD_DELAY": True,  # 0.5–1.5x the delay
            "AUTOTHROTTLE_ENABLED": True,
        }
    )
    return overlay
