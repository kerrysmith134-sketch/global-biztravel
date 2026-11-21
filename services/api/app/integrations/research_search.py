"""Generic user-query-driven web search adapter.

The application never chooses an industry-specific source or query. It sends only the
reviewed queries from a ResearchPlan to an explicitly configured SearXNG instance.
Search snippets are stored as unverified search evidence, not as verified facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class ResearchSearchUnavailable(RuntimeError):
    """Search provider missing, unreachable, or returned an invalid response."""


@dataclass(frozen=True)
class ResearchSearchResult:
    url: str
    title: str
    snippet: str
    provider: str
    engine: str | None = None
    published_at: str | None = None


class ResearchSearchProvider:
    """Synchronous provider protocol used by the background Worker."""

    name = "unavailable"

    @property
    def configured(self) -> bool:
        return False

    def search(self, query: str, *, language: str | None = None) -> list[ResearchSearchResult]:
        raise ResearchSearchUnavailable("外部研究搜索源未配置")


class SearXNGResearchSearchProvider(ResearchSearchProvider):
    """SearXNG JSON endpoint; the base URL is trusted deployment configuration."""

    name = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 12.0,
        max_results: int = 8,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlparse(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("research_search_base_url 必须是 http(s) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("research_search_base_url 不允许 credentials/query/fragment")
        self.base_url = base_url.strip().rstrip("/")
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 30.0))
        self.max_results = max(1, min(int(max_results), 20))
        self.transport = transport

    @property
    def configured(self) -> bool:
        return True

    def search(self, query: str, *, language: str | None = None) -> list[ResearchSearchResult]:
        cleaned = query.strip()
        if not cleaned:
            return []
        params: dict[str, str] = {"q": cleaned, "format": "json"}
        if language and len(language) <= 35:
            language_code = language.replace("_", "-").split("-", maxsplit=1)[0].lower()
            params["language"] = language_code
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
                headers={"Accept": "application/json"},
            ) as client:
                response = client.get(f"{self.base_url}/search", params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ResearchSearchUnavailable(f"SearXNG 搜索请求失败：{exc}") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ResearchSearchUnavailable("SearXNG 响应缺少 results 数组")

        results: list[ResearchSearchResult] = []
        seen: set[str] = set()
        for raw in payload["results"]:
            if not isinstance(raw, dict):
                continue
            url = raw.get("url")
            title = raw.get("title")
            snippet = raw.get("content")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            parsed_url = urlparse(url.strip())
            if (
                parsed_url.scheme not in {"http", "https"}
                or not parsed_url.netloc
                or parsed_url.username
                or parsed_url.password
            ):
                continue
            normalized_url = url.strip()
            if normalized_url in seen:
                continue
            seen.add(normalized_url)
            engine = raw.get("engine")
            published = raw.get("publishedDate")
            results.append(
                ResearchSearchResult(
                    url=normalized_url[:2000],
                    title=title.strip()[:500],
                    snippet=snippet.strip()[:3000] if isinstance(snippet, str) else "",
                    provider=self.name,
                    engine=engine[:120] if isinstance(engine, str) else None,
                    published_at=published[:80] if isinstance(published, str) else None,
                )
            )
            if len(results) >= self.max_results:
                break
        return results


def get_research_search_provider() -> ResearchSearchProvider:
    """Default closed; only deployment configuration can enable outbound search."""
    from app.config import get_settings

    settings = get_settings()
    if (
        settings.research_search_provider == "searxng"
        and settings.research_search_base_url
    ):
        return SearXNGResearchSearchProvider(
            settings.research_search_base_url,
            timeout_seconds=settings.research_search_timeout_seconds,
            max_results=settings.research_search_max_results,
        )
    return ResearchSearchProvider()


__all__ = [
    "ResearchSearchProvider",
    "ResearchSearchResult",
    "ResearchSearchUnavailable",
    "SearXNGResearchSearchProvider",
    "get_research_search_provider",
]
