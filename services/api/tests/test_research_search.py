"""Generic SearXNG adapter tests; no external network is used."""

from __future__ import annotations

import httpx
import pytest
from app.integrations.research_search import (
    ResearchSearchUnavailable,
    SearXNGResearchSearchProvider,
)


def test_searxng_sends_only_the_supplied_query_and_filters_unsafe_results() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = request.url.params["q"]
        captured["language"] = request.url.params["language"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://example.com/company",
                        "title": "Example company",
                        "content": "A source snippet",
                        "engine": "general",
                        "publishedDate": "2026-11-20",
                    },
                    {
                        "url": "javascript:alert(1)",
                        "title": "Unsafe scheme",
                        "content": "must not be persisted",
                    },
                    {
                        "url": "https://example.com/company",
                        "title": "Duplicate URL",
                        "content": "duplicate",
                    },
                ]
            },
        )

    provider = SearXNGResearchSearchProvider(
        "https://search.example.test",
        transport=httpx.MockTransport(handler),
    )
    results = provider.search("user's exact query", language="en")

    assert captured == {"query": "user's exact query", "language": "en"}
    assert len(results) == 1
    assert results[0].url == "https://example.com/company"
    assert results[0].provider == "searxng"
    assert results[0].engine == "general"
    assert results[0].published_at == "2026-11-20"
    assert results[0].snippet == "A source snippet"


def test_searxng_rejects_invalid_base_url() -> None:
    with pytest.raises(ValueError):
        SearXNGResearchSearchProvider("file:///tmp/search")


def test_searxng_rejects_malformed_provider_response() -> None:
    provider = SearXNGResearchSearchProvider(
        "https://search.example.test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"unexpected": []})
        ),
    )
    with pytest.raises(ResearchSearchUnavailable):
        provider.search("an ordinary user query")
