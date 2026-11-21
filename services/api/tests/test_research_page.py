"""Security and parsing tests for public research page extraction."""

from __future__ import annotations

from hashlib import sha256

import pytest

from app.integrations.research_page import (
    ResearchPageFetchError,
    SafeHttpResearchPageProvider,
    _normalise_article_text,
)


PUBLIC_IP = "93.184.216.34"
URL = "https://example.com/research"


def resolver_public(host: str, port: int) -> list[str]:
    assert host == "example.com"
    assert port == 443
    return [PUBLIC_IP]


def html_page(paragraph: str | None = None) -> bytes:
    body = paragraph or (
        "This article explains a concrete development in the selected market. "
        "It includes dates, named organizations, operational details and supporting context. "
    ) * 4
    return (
        "<html lang='en'><head>"
        "<title>Fallback title</title>"
        "<meta property='og:title' content='Research Article'>"
        "<meta name='description' content='A source summary'>"
        "<meta name='author' content='Editorial desk'>"
        "<meta property='article:published_time' content='2026-11-20T10:30:00Z'>"
        "<link rel='canonical' href='https://example.com/canonical-article'>"
        "</head><body>"
        "<nav><p>Navigation must never appear in extracted text.</p></nav>"
        "<main><article><h1>Primary heading</h1>"
        f"<p>{body}</p>"
        "<div hidden><div><p>Hidden nested content must never appear.</p></div></div>"
        "<div class='sidebar'><p>Sidebar content must never appear.</p></div>"
        "</article></main>"
        "<script>window.secret='script content';</script>"
        "</body></html>"
    ).encode("utf-8")


def test_extracts_metadata_and_discards_navigation_hidden_and_script_content() -> None:
    requests: list[tuple[str, str]] = []

    def requester(url: str, address: str):
        requests.append((url, address))
        return 200, {"content-type": "text/html; charset=utf-8"}, html_page()

    provider = SafeHttpResearchPageProvider(
        resolver=resolver_public,
        requester=requester,
    )
    result = provider.fetch(URL)

    assert result.title == "Research Article"
    assert result.description == "A source summary"
    assert result.author == "Editorial desk"
    assert result.published_at is not None
    assert result.published_at.isoformat() == "2026-11-20T10:30:00+00:00"
    assert result.observed_canonical_url == "https://example.com/canonical-article"
    assert result.language == "en"
    assert "Primary heading" in result.excerpt
    assert "Navigation must never appear" not in result.excerpt
    assert "Hidden nested content" not in result.excerpt
    assert "Sidebar content" not in result.excerpt
    assert "script content" not in result.excerpt
    assert requests == [(URL, PUBLIC_IP)]


def test_full_text_hash_is_preserved_while_only_bounded_excerpt_is_returned() -> None:
    long_paragraph = "Evidence-bearing sentence with a distinct factual detail. " * 100
    requests = []

    def requester(url: str, address: str):
        requests.append(url)
        return 200, {"content-type": "text/html; charset=utf-8"}, html_page(long_paragraph)

    provider = SafeHttpResearchPageProvider(
        resolver=resolver_public,
        requester=requester,
        max_excerpt_chars=1000,
    )
    result = provider.fetch(URL)

    assert result.content_chars > len(result.excerpt)
    assert len(result.excerpt) == 1000
    assert len(result.content_hash) == 64
    assert result.content_hash != sha256(result.excerpt.encode("utf-8")).hexdigest()


def test_rejects_private_ip_literal_before_any_network_request() -> None:
    called = False

    def requester(url: str, address: str):
        nonlocal called
        called = True
        return 200, {"content-type": "text/html"}, html_page()

    provider = SafeHttpResearchPageProvider(requester=requester)
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch("http://127.0.0.1/internal")
    assert error.value.code == "NON_PUBLIC_HOST"
    assert called is False


def test_rejects_mixed_public_and_private_dns_answers() -> None:
    called = False

    def resolver(host: str, port: int) -> list[str]:
        return [PUBLIC_IP, "10.0.0.8"]

    def requester(url: str, address: str):
        nonlocal called
        called = True
        return 200, {"content-type": "text/html"}, html_page()

    provider = SafeHttpResearchPageProvider(resolver=resolver, requester=requester)
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(URL)
    assert error.value.code == "NON_PUBLIC_HOST"
    assert called is False


def test_redirect_to_private_network_is_rejected_before_second_request() -> None:
    requests: list[str] = []

    def requester(url: str, address: str):
        requests.append(url)
        return 302, {"location": "http://127.0.0.1:8080/admin"}, b""

    provider = SafeHttpResearchPageProvider(
        resolver=resolver_public,
        requester=requester,
    )
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(URL)
    assert error.value.code == "NON_PUBLIC_HOST"
    assert requests == [URL]


@pytest.mark.parametrize(
    "url, expected_code",
    [
        ("https://user:secret@example.com/article", "INVALID_URL"),
        ("https://example.com:8443/article", "PORT_NOT_ALLOWED"),
        ("file:///etc/passwd", "INVALID_URL"),
        ("https://", "INVALID_URL"),
    ],
)
def test_rejects_unsafe_url_shapes(url: str, expected_code: str) -> None:
    provider = SafeHttpResearchPageProvider(requester=lambda *_: (200, {}, html_page()))
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(url)
    assert error.value.code == expected_code


def test_http_429_is_retryable_and_404_is_terminal() -> None:
    def busy(url: str, address: str):
        return 429, {"content-type": "text/html"}, b""

    provider = SafeHttpResearchPageProvider(resolver=resolver_public, requester=busy)
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(URL)
    assert error.value.code == "UPSTREAM_TEMPORARY"
    assert error.value.retryable is True

    def missing(url: str, address: str):
        return 404, {"content-type": "text/html"}, b""

    provider = SafeHttpResearchPageProvider(resolver=resolver_public, requester=missing)
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(URL)
    assert error.value.code == "UPSTREAM_REJECTED"
    assert error.value.retryable is False


def test_rejects_unsupported_media_and_oversized_body() -> None:
    provider = SafeHttpResearchPageProvider(
        resolver=resolver_public,
        requester=lambda *_: (200, {"content-type": "application/pdf"}, b"%PDF"),
    )
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(URL)
    assert error.value.code == "MEDIA_TYPE_UNSUPPORTED"

    provider = SafeHttpResearchPageProvider(
        resolver=resolver_public,
        requester=lambda *_: (
            200,
            {"content-type": "text/plain"},
            b"x" * (64 * 1024 + 1),
        ),
        max_body_bytes=64 * 1024,
    )
    with pytest.raises(ResearchPageFetchError) as error:
        provider.fetch(URL)
    assert error.value.code == "BODY_TOO_LARGE"


def test_normalized_article_text_is_stable_for_hashing() -> None:
    text = _normalise_article_text(" First   sentence \n\nSecond sentence\nSecond sentence\n")
    assert text == "First sentence\n\nSecond sentence"
