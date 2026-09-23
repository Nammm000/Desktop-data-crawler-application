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
