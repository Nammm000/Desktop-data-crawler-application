"""Unit tests for the pure helpers behind the Facebook feature.

These four functions carry all the parsing/encryption logic with no I/O:
cookie-header parsing, proxy-list validation, facebook URL normalization,
and the Fernet round-trip. Run: .venv/bin/pytest tests/"""

import pytest

from app.core import encryption
from app.services.agent_secret_service import parse_cookie_header, parse_proxy_list
from app.services.facebook_spider import is_facebook_url, normalize_facebook_url


# -- parse_cookie_header ------------------------------------------------------


def test_cookie_header_semicolon_pairs():
    assert parse_cookie_header("c_user=123; xs=abc; fr=zzz") == {
        "c_user": "123",
        "xs": "abc",
        "fr": "zzz",
    }


def test_cookie_header_newline_pairs():
    assert parse_cookie_header("c_user=123\nxs=abc\n") == {
        "c_user": "123",
        "xs": "abc",
    }


def test_cookie_header_strips_whitespace_and_quotes():
    assert parse_cookie_header(' c_user = "123" ; xs=abc') == {
        "c_user": "123",
        "xs": "abc",
    }


def test_cookie_header_duplicate_last_wins():
    assert parse_cookie_header("a=1; a=2") == {"a": "2"}


def test_cookie_header_garbage_rejected():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        parse_cookie_header("no pairs here")
    assert exc_info.value.status_code == 400


# -- parse_proxy_list ---------------------------------------------------------


def test_proxy_list_valid():
    assert parse_proxy_list("http://p1:8080\n\nhttp://u:p@p2:3128\n") == [
        "http://p1:8080",
        "http://u:p@p2:3128",
    ]


def test_proxy_list_rejects_non_http_scheme():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        parse_proxy_list("socks5://p1:1080")
    assert exc_info.value.status_code == 400


def test_proxy_list_rejects_empty():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        parse_proxy_list("\n  \n")


def test_proxy_list_caps_at_20():
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        parse_proxy_list("\n".join(f"http://p{i}:8080" for i in range(21)))


# -- facebook URL handling ----------------------------------------------------


def test_is_facebook_url():
    assert is_facebook_url("https://www.facebook.com/posts/123")
    assert is_facebook_url("https://mbasic.facebook.com/story.php?story_fbid=1")
    assert not is_facebook_url("https://example.com")
    assert not is_facebook_url("http://www.facebook.com/posts/123")  # http, not https
    assert not is_facebook_url("https://evil-facebook.com/x")  # lookalike suffix


def test_normalize_rewrites_host_keeps_query():
    assert (
        normalize_facebook_url(
            "https://www.facebook.com/story.php?story_fbid=1&id=2",
            "mbasic.facebook.com",
        )
        == "https://mbasic.facebook.com/story.php?story_fbid=1&id=2"
    )


def test_normalize_rejects_non_facebook():
    with pytest.raises(ValueError):
        normalize_facebook_url("https://example.com/x", "mbasic.facebook.com")


# -- download-error classification -------------------------------------------


def test_classifier_404_is_broken_link():
    # The classifier matches on exception CLASS NAME, so the fake must be
    # named exactly like scrapy's HttpError.
    from app.models.crawl import FailureReason
    from app.services.crawler_service import _classify_download_error

    class HttpError(Exception):
        class response:  # mirrors scrapy's HttpError.response
            status = 404

    reason, _ = _classify_download_error(HttpError())
    assert reason == FailureReason.BROKEN_LINK


def test_classifier_strips_download_prefix():
    from app.models.crawl import FailureReason
    from app.services.crawler_service import _classify_download_error

    class DownloadConnectionRefusedError(Exception):
        pass

    reason, _ = _classify_download_error(DownloadConnectionRefusedError())
    assert reason == FailureReason.CONNECTION_ERROR


def test_classifier_connection_error_via_proxy_is_proxy_error():
    from app.models.crawl import FailureReason
    from app.services.crawler_service import _classify_download_error

    class ConnectionRefusedError(Exception):
        pass

    reason, _ = _classify_download_error(
        ConnectionRefusedError(), via_proxy=True
    )
    assert reason == FailureReason.PROXY_ERROR


# -- Fernet encryption --------------------------------------------------------


def test_encrypt_decrypt_round_trip(monkeypatch):
    class _FakeSettings:
        credentials_encryption_key = _TEST_KEY

    monkeypatch.setattr("app.core.encryption.get_settings", lambda: _FakeSettings())
    encryption._cipher.cache_clear()
    assert encryption.decrypt(encryption.encrypt("c_user=123; xs=abc")) == (
        "c_user=123; xs=abc"
    )


def test_missing_key_disables_storage(monkeypatch):
    class _FakeSettings:
        credentials_encryption_key = "CHANGE_ME"

    monkeypatch.setattr("app.core.encryption.get_settings", lambda: _FakeSettings())
    encryption._cipher.cache_clear()
    with pytest.raises(encryption.CredentialsKeyError):
        encryption.encrypt("secret")
    # restore the real cached cipher for other tests
    monkeypatch.undo()
    encryption._cipher.cache_clear()


def test_decrypt_with_wrong_key_raises(monkeypatch):
    class _KeyA:
        credentials_encryption_key = _TEST_KEY

    class _KeyB:
        credentials_encryption_key = _TEST_KEY_2

    monkeypatch.setattr("app.core.encryption.get_settings", lambda: _KeyA())
    encryption._cipher.cache_clear()
    ciphertext = encryption.encrypt("secret")
    monkeypatch.setattr("app.core.encryption.get_settings", lambda: _KeyB())
    encryption._cipher.cache_clear()
    with pytest.raises(encryption.CredentialsKeyError):
        encryption.decrypt(ciphertext)
    monkeypatch.undo()
    encryption._cipher.cache_clear()


# Keys generated with: python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
_TEST_KEY = "l0QST9Eh-CnKs20pW2yQlaVZ6jfUta80Yb6X7pCy7n4="
_TEST_KEY_2 = "nvk3wFBLcenHCbBZgdofBvRi0zoS5Xw8j2PuHVz6POc="


# -- source_pages script parsing ------------------------------------------------


def _sp_script(**overrides):
    from app.services.source_pages_discovery import parse_source_pages_script

    base = {
        "source_pages": ["https://example.com/news"],
        "post_link": "//article//h2/a/@href",
        "title": "//title",
    }
    base.update(overrides)
    return parse_source_pages_script(base)


def test_source_pages_minimal_single_pass():
    plan = _sp_script()
    assert plan.mode == "single"
    assert plan.button_xpaths == []
    assert plan.max_next is None
    assert plan.post_link_xpaths == ["//article//h2/a/@href"]
    assert plan.field_xpaths == {"title": ["//title"]}


def test_source_pages_next_page_mode_and_max_next():
    plan = _sp_script(
        next_page=["//a[contains(@id,'next')]", "//a[@rel='next']"], max_next=3
    )
    assert plan.mode == "next_page"
    assert plan.button_xpaths == ["//a[contains(@id,'next')]", "//a[@rel='next']"]
    assert plan.max_next == 3


def test_source_pages_none_button_still_counts_as_specified():
    # JSON null is a PRESENT key: it selects the mode (and conflicts with the
    # other button), same as any other value.
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _sp_script(next_page="//a[@id='next']", load_more=None)


def test_source_pages_max_next_page_alias_canonicalized():
    plan = _sp_script(max_next_page=4)
    assert plan.max_next == 4


def test_source_pages_rejects_both_pagination_buttons():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _sp_script(
            next_page="//a[@id='next']",
            load_more="//button[text()='Load more']",
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == (
        "script cannot contain both 'next_page' and 'load_more' "
        "- choose one pagination mode"
    )


def test_source_pages_rejects_both_max_next_spellings():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _sp_script(max_next=2, max_next_page=2)
    assert exc_info.value.detail == (
        "script cannot contain both 'max_next' and 'max_next_page'"
    )


def test_source_pages_missing_source_pages():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _sp_script(source_pages=None)
    assert exc_info.value.detail == (
        "script 'source_pages' must be a non-empty list of URL strings"
    )


def test_source_pages_missing_post_link():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        _sp_script(post_link=None)


def test_source_pages_rejects_bad_shapes():
    import pytest
    from fastapi import HTTPException

    for bad in ([], "https://example.com", [123], [""]):
        with pytest.raises(HTTPException):
            _sp_script(source_pages=bad)
    with pytest.raises(HTTPException):
        _sp_script(post_link=42)
    with pytest.raises(HTTPException):
        _sp_script(post_link=["ok", ""])


def test_source_pages_bad_max_next_values():
    import pytest
    from fastapi import HTTPException

    for bad in (0, -1, 2.5, True, "3"):
        with pytest.raises(HTTPException) as exc_info:
            _sp_script(max_next=bad)
        assert exc_info.value.detail == (
            "script 'max_next' must be an integer of at least 1"
        )


def test_source_pages_bad_field_xpath_type():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _sp_script(published_time=12)
    assert exc_info.value.detail == (
        "script field 'published_time' must be a non-empty XPath string "
        "or a non-empty list of XPath strings"
    )


def test_source_pages_reserved_keys_not_fields():
    plan = _sp_script(max_next=2)
    for key in (
        "source_pages",
        "post_link",
        "next_page",
        "load_more",
        "max_next",
        "max_next_page",
    ):
        assert key not in plan.field_xpaths


def test_source_pages_load_more_mode_lists_preserved():
    plan = _sp_script(
        post_link=["//h2/a/@href", "//a[@class='card']/@href"],
        load_more="//button[contains(., 'Load more')]",
    )
    assert plan.mode == "load_more"
    assert plan.post_link_xpaths == ["//h2/a/@href", "//a[@class='card']/@href"]
    assert plan.button_xpaths == ["//button[contains(., 'Load more')]"]


# -- normalize_discovered_href --------------------------------------------------


def test_normalize_href_relative_joined():
    from app.services.source_pages_discovery import normalize_discovered_href

    assert (
        normalize_discovered_href("https://example.com/news/", "/post/1")
        == "https://example.com/post/1"
    )
    assert (
        normalize_discovered_href("https://example.com/news/", "post/1")
        == "https://example.com/news/post/1"
    )


def test_normalize_href_absolute_kept_fragment_stripped():
    from app.services.source_pages_discovery import normalize_discovered_href

    assert (
        normalize_discovered_href("https://example.com/", "https://other.org/a?x=1")
        == "https://other.org/a?x=1"
    )
    assert (
        normalize_discovered_href("https://example.com/", "https://other.org/a#comments")
        == "https://other.org/a"
    )


def test_normalize_href_discards_non_links():
    from app.services.source_pages_discovery import normalize_discovered_href

    assert normalize_discovered_href("https://example.com/", "javascript:void(0)") is None
    assert normalize_discovered_href("https://example.com/", "mailto:a@b.c") is None
    assert normalize_discovered_href("https://example.com/", "#comments") is None
    assert normalize_discovered_href("https://example.com/", "   ") is None
    assert normalize_discovered_href("https://example.com/", None) is None


# -- navigation-error classification ---------------------------------------------


def test_navigation_error_http_status_mapping():
    from app.models.crawl import FailureReason
    from app.services.source_pages_discovery import _classify_navigation_error

    assert _classify_navigation_error(Exception(), 404)[0] == FailureReason.BROKEN_LINK
    assert _classify_navigation_error(Exception(), 429)[0] == FailureReason.RATE_LIMITED
    reason, detail = _classify_navigation_error(Exception(), 500)
    assert reason == FailureReason.HTTP_ERROR
    assert detail == "HTTP 500"


def test_navigation_error_exception_mapping():
    from app.models.crawl import FailureReason
    from app.services.source_pages_discovery import _classify_navigation_error

    assert _classify_navigation_error(TimeoutError())[0] == FailureReason.TIMEOUT
    assert (
        _classify_navigation_error(Exception("net::ERR_NAME_NOT_RESOLVED"))[0]
        == FailureReason.DNS_ERROR
    )
    assert (
        _classify_navigation_error(Exception("net::ERR_CONNECTION_REFUSED"))[0]
        == FailureReason.CONNECTION_ERROR
    )
    reason, _ = _classify_navigation_error(ValueError("weird"))
    assert reason == FailureReason.REQUEST_ERROR


# -- source_pages run-time wrapper ----------------------------------------------


class _FakeCrawlSettings:
    crawl_max_pages = 2
    ecommerce_max_products = 2


def _sp_doc(script: dict) -> dict:
    import json

    return {"format": "json", "script": json.dumps(script)}


def test_source_pages_run_script_rejects_non_http_source():
    import pytest
    from fastapi import HTTPException
    from app.services.crawler_service import _parse_source_pages_run_script

    doc = _sp_doc(
        {"source_pages": ["file:///etc/passwd"], "post_link": "//a/@href"}
    )
    with pytest.raises(HTTPException) as exc_info:
        _parse_source_pages_run_script(doc, _FakeCrawlSettings())
    assert "not a supported http/https URL" in exc_info.value.detail


def test_source_pages_run_script_caps_source_pages():
    import pytest
    from fastapi import HTTPException
    from app.services.crawler_service import _parse_source_pages_run_script

    doc = _sp_doc(
        {
            "source_pages": [
                "https://example.com/1",
                "https://example.com/2",
                "https://example.com/3",
            ],
            "post_link": "//a/@href",
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        _parse_source_pages_run_script(doc, _FakeCrawlSettings())
    assert "CRAWL_MAX_PAGES" in exc_info.value.detail


def test_source_pages_run_script_happy_path():
    from app.services.crawler_service import _parse_source_pages_run_script

    doc = _sp_doc(
        {
            "source_pages": ["https://example.com/1", "https://example.com/2"],
            "post_link": "//a/@href",
            "title": "//title",
        }
    )
    plan = _parse_source_pages_run_script(doc, _FakeCrawlSettings())
    assert plan.mode == "single"
    assert len(plan.source_pages) == 2


# -- agent_service._validate_script source_pages branch --------------------------


def test_validate_script_rejects_both_buttons_for_source_pages():
    import json
    import pytest
    from fastapi import HTTPException
    from app.models.agent import AgentSource
    from app.services.agent_service import _validate_script

    script = json.dumps(
        {
            "source_pages": ["https://example.com"],
            "post_link": "//a/@href",
            "next_page": "//a[@id='next']",
            "load_more": "//button",
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        _validate_script("json", script, AgentSource.SOURCE_PAGES)
    assert "cannot contain both" in exc_info.value.detail


def test_validate_script_source_pages_non_json_skips():
    from app.models.agent import AgentSource
    from app.services.agent_service import _validate_script

    # xml/md scripts are stored as-is for every source
    _validate_script("xml", "not json at all", AgentSource.SOURCE_PAGES)


def test_validate_script_generic_still_parse_only():
    import json
    from app.models.agent import AgentSource
    from app.services.agent_service import _validate_script

    # A links-style script is structurally invalid for source_pages but fine
    # for generic (structure is a run-time check there).
    _validate_script("json", json.dumps({"links": []}), AgentSource.GENERIC)


# -- ecommerce script parsing ---------------------------------------------------


_TRAVEL_LISTING = "https://books.toscrape.com/catalogue/category/books/travel_2/index.html"


def _ec_script(**overrides):
    from app.services.ecommerce_spider import parse_ecommerce_script

    base = {"links": [_TRAVEL_LISTING]}
    base.update(overrides)
    return parse_ecommerce_script(base)


def test_ecommerce_minimal_links_only():
    plan = _ec_script()
    assert plan.seeds == [_TRAVEL_LISTING]
    assert plan.product_link_xpaths is None  # spider built-in default
    assert plan.next_page_xpaths is None
    assert plan.max_next is None
    assert plan.max_products is None  # filled from settings at run time
    assert plan.field_xpaths == {}


def test_ecommerce_overrides_preserved_in_order():
    plan = _ec_script(
        product_link=["//a[@class='product']/@href", "//h3/a/@href"],
        next_page="//a[@rel='next']/@href",
        max_next=3,
        max_products=10,
        title=["//h1/text()", "//title"],
    )
    assert plan.product_link_xpaths == [
        "//a[@class='product']/@href",
        "//h3/a/@href",
    ]
    assert plan.next_page_xpaths == ["//a[@rel='next']/@href"]
    assert plan.max_next == 3
    assert plan.max_products == 10
    assert plan.field_xpaths == {"title": ["//h1/text()", "//title"]}


def test_ecommerce_max_next_page_alias_canonicalized():
    plan = _ec_script(max_next_page=4)
    assert plan.max_next == 4


def test_ecommerce_rejects_both_max_next_spellings():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _ec_script(max_next=2, max_next_page=2)
    assert exc_info.value.detail == (
        "script cannot contain both 'max_next' and 'max_next_page'"
    )


def test_ecommerce_bad_max_next_values():
    import pytest
    from fastapi import HTTPException

    for bad in (0, -1, 2.5, True, "3"):
        with pytest.raises(HTTPException) as exc_info:
            _ec_script(max_next=bad)
        assert exc_info.value.detail == (
            "script 'max_next' must be an integer of at least 1"
        )


def test_ecommerce_bad_max_products_values():
    import pytest
    from fastapi import HTTPException

    for bad in (True, 0, -3, 2.5, "5"):
        with pytest.raises(HTTPException) as exc_info:
            _ec_script(max_products=bad)
        assert exc_info.value.detail == (
            "script 'max_products' must be an integer of at least 1"
        )


def test_ecommerce_missing_or_bad_links():
    import pytest
    from fastapi import HTTPException

    for bad in (None, [], "https://example.com", [123], [""]):
        with pytest.raises(HTTPException) as exc_info:
            _ec_script(links=bad)
        assert exc_info.value.detail == (
            "script 'links' must be a non-empty list of URL strings"
        )


def test_ecommerce_bad_field_xpath_type():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        _ec_script(title=12)
    assert exc_info.value.detail == (
        "script field 'title' must be a non-empty XPath string "
        "or a non-empty list of XPath strings"
    )


def test_ecommerce_reserved_keys_not_fields():
    plan = _ec_script(max_next=2, max_products=5, next_page="//a", product_link="//b")
    for key in (
        "links",
        "product_link",
        "next_page",
        "max_next",
        "max_next_page",
        "max_products",
    ):
        assert key not in plan.field_xpaths


# -- ecommerce price / rating parsing -------------------------------------------


def test_parse_price_symbols_and_amounts():
    from app.services.ecommerce_spider import parse_price

    assert parse_price("£51.77") == ("51.77", "GBP")
    assert parse_price("$1,234.56") == ("1234.56", "USD")
    assert parse_price("€9.99") == ("9.99", "EUR")
    assert parse_price("26.08") == ("26.08", None)  # no symbol -> no currency
    assert parse_price("In stock") == (None, None)
    assert parse_price(None) == (None, None)


def test_rating_from_class_word_to_digit():
    from app.services.ecommerce_spider import rating_from_class

    assert rating_from_class("star-rating Five") == "5"
    assert rating_from_class("star-rating one") == "1"
    assert rating_from_class("star-rating") is None
    assert rating_from_class(None) is None


def test_books_relative_links_join_against_listing_url():
    from app.services.source_pages_discovery import normalize_discovered_href

    # Product hrefs climb out of the category directory...
    assert (
        normalize_discovered_href(
            "https://books.toscrape.com/catalogue/category/books/travel_2/index.html",
            "../../../a-summer-in-europe_458/index.html",
        )
        == "https://books.toscrape.com/catalogue/a-summer-in-europe_458/index.html"
    )
    # ...while pagination hrefs are relative to the category directory.
    assert (
        normalize_discovered_href(
            "https://books.toscrape.com/catalogue/category/books/nonfiction_13/index.html",
            "page-2.html",
        )
        == "https://books.toscrape.com/catalogue/category/books/nonfiction_13/page-2.html"
    )


# -- ecommerce run-time wrapper ---------------------------------------------------


def _ec_doc(script: dict) -> dict:
    import json

    return {"format": "json", "script": json.dumps(script)}


def test_ecommerce_run_script_rejects_non_http_seed():
    import pytest
    from fastapi import HTTPException
    from app.services.crawler_service import _parse_ecommerce_run_script

    doc = _ec_doc({"links": ["file:///etc/passwd"]})
    with pytest.raises(HTTPException) as exc_info:
        _parse_ecommerce_run_script(doc, _FakeCrawlSettings())
    assert "not a supported http/https URL" in exc_info.value.detail


def test_ecommerce_run_script_caps_seeds():
    import pytest
    from fastapi import HTTPException
    from app.services.crawler_service import _parse_ecommerce_run_script

    doc = _ec_doc({"links": ["https://example.com/1", "https://example.com/2", "https://example.com/3"]})
    with pytest.raises(HTTPException) as exc_info:
        _parse_ecommerce_run_script(doc, _FakeCrawlSettings())
    assert "CRAWL_MAX_PAGES" in exc_info.value.detail


def test_ecommerce_run_script_caps_max_products():
    import pytest
    from fastapi import HTTPException
    from app.services.crawler_service import _parse_ecommerce_run_script

    doc = _ec_doc({"links": ["https://example.com/1"], "max_products": 5})
    with pytest.raises(HTTPException) as exc_info:
        _parse_ecommerce_run_script(doc, _FakeCrawlSettings())
    assert "ECOMMERCE_MAX_PRODUCTS" in exc_info.value.detail


def test_ecommerce_run_script_happy_path_fills_default_cap():
    from app.services.crawler_service import _parse_ecommerce_run_script

    plan = _parse_ecommerce_run_script(
        _ec_doc({"links": ["https://example.com/1"]}), _FakeCrawlSettings()
    )
    assert plan.max_products == 2  # settings ceiling filled in
    assert plan.seeds == ["https://example.com/1"]
    # a lower script cap wins over the ceiling
    plan = _parse_ecommerce_run_script(
        _ec_doc({"links": ["https://example.com/1"], "max_products": 1}),
        _FakeCrawlSettings(),
    )
    assert plan.max_products == 1


# -- agent_service._validate_script ecommerce branch ------------------------------


def test_validate_script_rejects_both_cap_spellings_for_ecommerce():
    import json
    import pytest
    from fastapi import HTTPException
    from app.models.agent import AgentSource
    from app.services.agent_service import _validate_script

    script = json.dumps(
        {"links": ["https://example.com"], "max_next": 2, "max_next_page": 2}
    )
    with pytest.raises(HTTPException) as exc_info:
        _validate_script("json", script, AgentSource.ECOMMERCE)
    assert "cannot contain both" in exc_info.value.detail


def test_validate_script_ecommerce_non_json_skips():
    from app.models.agent import AgentSource
    from app.services.agent_service import _validate_script

    # xml/md scripts are stored as-is for every source
    _validate_script("xml", "not json at all", AgentSource.ECOMMERCE)


# -- ecommerce settings overlay ---------------------------------------------------


def test_ecommerce_settings_overlay():
    from app.services.ecommerce_spider import ecommerce_settings
    from app.services.source_pages_discovery import CHROME_USER_AGENT

    base = {
        "USER_AGENT": "data-crawler/0.1.0",
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,
        "RETRY_TIMES": 1,
    }
    overlay = ecommerce_settings(base, download_delay=1.5, concurrent_requests=2)
    assert overlay["USER_AGENT"] == CHROME_USER_AGENT
    assert overlay["CONCURRENT_REQUESTS_PER_DOMAIN"] == 2
    assert overlay["DOWNLOAD_DELAY"] == 1.5
    assert overlay["RANDOMIZE_DOWNLOAD_DELAY"] is True
    assert overlay["AUTOTHROTTLE_ENABLED"] is True
    # the base dict is copied, not mutated
    assert base["USER_AGENT"] == "data-crawler/0.1.0"
