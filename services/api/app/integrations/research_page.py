"""Bounded, SSRF-resistant extraction of user-selected public research pages.

Only a user-selected source can be fetched. Every redirect is revalidated, DNS results
are pinned to a checked public address before any HTTP bytes are sent, and the complete
page is never persisted: only a bounded excerpt plus a digest of normalized article text.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from hashlib import sha256
from html.parser import HTMLParser
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit, urlunsplit


EXTRACTOR_VERSION = "article-text-v1"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_EXCERPT_CHARS = 12_000
MAX_REDIRECTS = 3
MAX_URL_LENGTH = 2048

_SKIP_TAGS = {
    "script", "style", "noscript", "svg", "canvas", "nav", "footer", "header",
    "aside", "form", "iframe", "object", "embed", "template", "button",
    "select", "textarea", "video", "audio",
}
_BLOCK_TAGS = {"h1", "h2", "h3", "p", "li", "blockquote", "td", "th", "pre"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_NOISE_CLASS = re.compile(
    r"(?:^|[\\s_-])(cookie|consent|advert|sidebar|navigation|navbar|menu|footer|header|"
    r"share|social|breadcrumb|modal|popup|subscribe|related-content)(?:$|[\\s_-])",
    re.IGNORECASE,
)
_WHITESPACE = re.compile(r"\\s+")


class ResearchPageFetchError(RuntimeError):
    """A page could not be safely and usefully extracted."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ResearchPage:
    requested_url: str
    final_url: str
    title: str | None
    description: str | None
    author: str | None
    published_at: datetime | None
    published_at_raw: str | None
    observed_canonical_url: str | None
    language: str | None
    excerpt: str
    content_hash: str
    content_chars: int
    content_type: str
    retrieved_at: datetime
    extractor_version: str = EXTRACTOR_VERSION


class ResearchPageProvider:
    """Closed-by-default page extraction provider."""

    name = "unavailable"

    @property
    def configured(self) -> bool:
        return False

    def fetch(self, url: str) -> ResearchPage:
        raise ResearchPageFetchError(
            "PROVIDER_UNAVAILABLE", "网页正文提取 Provider 未配置", retryable=False
        )


Resolver = Callable[[str, int], list[str]]
Requester = Callable[[str, str], tuple[int, Mapping[str, str], bytes]]


def _system_resolve(host: str, port: int) -> list[str]:
    rows = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(item[4][0] for item in rows))


def _normalise_target_url(value: str) -> tuple[str, str, int]:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_URL_LENGTH:
        raise ResearchPageFetchError("INVALID_URL", "来源 URL 为空或超过长度限制")
    try:
        parts = urlsplit(value.strip())
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("scheme or host")
        if parts.username is not None or parts.password is not None:
            raise ValueError("credentials")
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not host or "%" in host:
            raise ValueError("host")
        port = parts.port or (443 if scheme == "https" else 80)
    except (ValueError, UnicodeError) as exc:
        raise ResearchPageFetchError(
            "INVALID_URL", "来源 URL 必须是有效的 HTTP(S) 地址，且不得包含凭证"
        ) from exc

    expected_port = 443 if scheme == "https" else 80
    if port != expected_port:
        raise ResearchPageFetchError(
            "PORT_NOT_ALLOWED", "网页提取仅允许 HTTPS 443 或 HTTP 80 端口"
        )

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise ResearchPageFetchError("NON_PUBLIC_HOST", "来源地址不是公网地址")

    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port == expected_port else f"{netloc_host}:{port}"
    path = parts.path or "/"
    # Fragments are client-side navigation and are never sent to the origin.
    normalized = urlunsplit((scheme, netloc, path, parts.query, ""))
    return normalized, host, port


def _checked_public_address(host: str, port: int, resolver: Resolver) -> str:
    try:
        literal = ipaddress.ip_address(host)
        addresses = [str(literal)]
    except ValueError:
        try:
            addresses = resolver(host, port)
        except OSError as exc:
            raise ResearchPageFetchError(
                "DNS_FAILURE", "无法解析来源主机名", retryable=True
            ) from exc

    unique = list(dict.fromkeys(addresses))
    if not unique:
        raise ResearchPageFetchError("DNS_EMPTY", "来源主机名没有可用地址", retryable=True)

    parsed: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for address in unique:
        try:
            resolved = ipaddress.ip_address(address)
        except ValueError as exc:
            raise ResearchPageFetchError("DNS_INVALID", "DNS 返回了无效地址") from exc
        if not resolved.is_global or resolved.is_multicast or resolved.is_unspecified:
            raise ResearchPageFetchError(
                "NON_PUBLIC_HOST", "来源主机解析到非公网地址，已阻止请求"
            )
        parsed.append(resolved)

    # Do not fall back to another address if the selected public address fails.
    # The socket is connected to this exact address; Host and TLS SNI remain the DNS name.
    return str(parsed[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host=host, port=port, timeout=timeout)
        self._pinned_address = address

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host=host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        if self._tunnel_host:
            self._tunnel()


def _request_pinned(url: str, address: str, timeout: float, max_bytes: int) -> tuple[int, Mapping[str, str], bytes]:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    connection: http.client.HTTPConnection
    if parts.scheme == "https":
        connection = _PinnedHTTPSConnection(host, port, address, timeout)
    else:
        connection = _PinnedHTTPConnection(host, port, address, timeout)
    host_header = f"[{host}]" if ":" in host else host
    connection.request(
        "GET",
        target,
        headers={
            "Host": host_header,
            "User-Agent": "GlobalBusinessTravelResearchBot/1.0",
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
            "Accept-Encoding": "identity",
            "Connection": "close",
        },
    )
    response = connection.getresponse()
    headers = {key.lower(): value.strip() for key, value in response.getheaders()}
    raw = response.read(max_bytes + 1)
    status = response.status
    connection.close()
    return status, headers, raw


def _parse_published_at(value: str | None) -> datetime | None:
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _decode_body(body: bytes, content_type: str) -> str:
    message = Message()
    message["content-type"] = content_type
    charset = message.get_content_charset() or "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


class _ArticleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.h1_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.canonical_url: str | None = None
        self.language: str | None = None
        self.blocks: list[str] = []
        self._skip_stack: list[str] = []
        self._in_title = False
        self._active_tag: str | None = None
        self._active_parts: list[str] = []

    def _flush(self) -> None:
        text = _WHITESPACE.sub(" ", " ".join(self._active_parts)).strip()
        if text:
            self.blocks.append(text)
            if self._active_tag == "h1":
                self.h1_parts.append(text)
        self._active_tag = None
        self._active_parts = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if self._skip_stack:
            if tag not in _VOID_TAGS:
                self._skip_stack.append(tag)
            return

        class_id = f"{values.get('class', '')} {values.get('id', '')}"
        role = values.get("role", "").lower()
        if (
            tag in _SKIP_TAGS
            or values.get("aria-hidden", "").lower() == "true"
            or "hidden" in values
            or role in {"navigation", "complementary"}
            or _NOISE_CLASS.search(class_id)
        ):
            if tag not in _VOID_TAGS:
                self._skip_stack.append(tag)
            return

        if tag == "html":
            self.language = values.get("lang")[:35] or None
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or values.get("itemprop")).lower()
            value = values.get("content", "").strip()
            if key and value:
                self.meta.setdefault(key, value[:2000])
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            candidate = values.get("href", "").strip()
            if candidate:
                self.canonical_url = candidate[:2000]
        elif tag == "title":
            self._in_title = True

        if tag in _BLOCK_TAGS:
            self._flush()
            self._active_tag = tag
            self._active_parts = []

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            if tag in self._skip_stack:
                # Close the matching element and any malformed descendants above it.
                index = len(self._skip_stack) - 1 - self._skip_stack[::-1].index(tag)
                del self._skip_stack[index:]
            return
        if tag == "title":
            self._in_title = False
        if self._active_tag == tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if not cleaned:
            return
        if self._in_title:
            self.title_parts.append(cleaned)
        if self._active_tag is not None:
            self._active_parts.append(cleaned)

    def result(self) -> tuple[str | None, str | None, str | None, str | None, list[str]]:
        self._flush()
        title = (
            self.meta.get("og:title")
            or self.meta.get("twitter:title")
            or _WHITESPACE.sub(" ", " ".join(self.h1_parts)).strip()
            or _WHITESPACE.sub(" ", " ".join(self.title_parts)).strip()
            or None
        )
        description = self.meta.get("description") or self.meta.get("og:description")
        author = self.meta.get("author") or self.meta.get("article:author")
        published = (
            self.meta.get("article:published_time")
            or self.meta.get("datepublished")
            or self.meta.get("date")
            or self.meta.get("pubdate")
            or self.meta.get("publication_date")
        )
        return title, description, author, published, self.blocks


def _normalise_article_text(text: str) -> str:
    blocks = [_WHITESPACE.sub(" ", item).strip() for item in text.splitlines()]
    cleaned: list[str] = []
    previous = None
    for block in blocks:
        if block and block != previous:
            cleaned.append(block)
            previous = block
    return "\n\n".join(cleaned)


def _extract(
    body: bytes,
    *,
    content_type: str,
    final_url: str,
    requested_url: str,
    max_excerpt_chars: int,
) -> ResearchPage:
    text = _decode_body(body, content_type)
    if content_type.split(";", 1)[0].strip().lower() == "text/plain":
        extracted = _normalise_article_text(text)
        title = description = author = published_raw = canonical = language = None
    else:
        parser = _ArticleParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception as exc:  # noqa: BLE001 - malformed publisher HTML is not a worker crash
            raise ResearchPageFetchError(
                "HTML_PARSE_FAILED", "来源网页结构无法解析", retryable=False
            ) from exc
        title, description, author, published_raw, blocks = parser.result()
        extracted = _normalise_article_text("\n".join(blocks))
        canonical = parser.canonical_url
        language = parser.language

    if len(extracted) < 80:
        raise ResearchPageFetchError(
            "NO_ARTICLE_TEXT", "来源网页没有足够的可提取正文，可能是登录页或脚本渲染页面"
        )
    digest = sha256(extracted.encode("utf-8")).hexdigest()
    return ResearchPage(
        requested_url=requested_url,
        final_url=final_url,
        title=(title or "")[:500] or None,
        description=(description or "")[:2000] or None,
        author=(author or "")[:300] or None,
        published_at=_parse_published_at(published_raw),
        published_at_raw=(published_raw or "")[:120] or None,
        observed_canonical_url=(canonical or "")[:2000] or None,
        language=language,
        excerpt=extracted[:max_excerpt_chars],
        content_hash=digest,
        content_chars=len(extracted),
        content_type=content_type.split(";", 1)[0].strip().lower(),
        retrieved_at=datetime.now(UTC),
    )


class SafeHttpResearchPageProvider(ResearchPageProvider):
    """Direct HTTP fetcher that pins every request to a checked public DNS address."""

    name = "safe_http"

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_excerpt_chars: int = DEFAULT_MAX_EXCERPT_CHARS,
        max_redirects: int = MAX_REDIRECTS,
        resolver: Resolver | None = None,
        requester: Requester | None = None,
    ) -> None:
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 20.0))
        self.max_body_bytes = max(64 * 1024, min(int(max_body_bytes), 4 * 1024 * 1024))
        self.max_excerpt_chars = max(1000, min(int(max_excerpt_chars), 20_000))
        self.max_redirects = max(0, min(int(max_redirects), MAX_REDIRECTS))
        self.resolver = resolver or _system_resolve
        self.requester = requester

    @property
    def configured(self) -> bool:
        return True

    def _request(self, url: str, address: str) -> tuple[int, Mapping[str, str], bytes]:
        if self.requester is not None:
            return self.requester(url, address)
        try:
            return _request_pinned(url, address, self.timeout_seconds, self.max_body_bytes)
        except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException) as exc:
            raise ResearchPageFetchError(
                "NETWORK_ERROR", "连接来源网站失败", retryable=True
            ) from exc

    def fetch(self, url: str) -> ResearchPage:
        requested_url, _, _ = _normalise_target_url(url)
        current_url = requested_url
        visited: set[str] = set()

        for redirect_count in range(self.max_redirects + 1):
            current_url, host, port = _normalise_target_url(current_url)
            if current_url in visited:
                raise ResearchPageFetchError("REDIRECT_LOOP", "来源网站存在重定向循环")
            visited.add(current_url)
            address = _checked_public_address(host, port, self.resolver)

            status, headers, body = self._request(current_url, address)
            if status in {301, 302, 303, 307, 308}:
                location = headers.get("location")
                if not location:
                    raise ResearchPageFetchError("REDIRECT_INVALID", "来源网站返回了不完整的重定向")
                if redirect_count >= self.max_redirects:
                    raise ResearchPageFetchError("REDIRECT_LIMIT", "来源网站重定向次数超过限制")
                next_url = urljoin(current_url, location)
                normalized_next, _, _ = _normalise_target_url(next_url)
                if urlsplit(current_url).scheme == "https" and urlsplit(normalized_next).scheme != "https":
                    raise ResearchPageFetchError("HTTPS_DOWNGRADE", "已阻止 HTTPS 降级到 HTTP")
                current_url = normalized_next
                continue

            if status in {408, 425, 429} or status >= 500:
                raise ResearchPageFetchError(
                    "UPSTREAM_TEMPORARY", f"来源网站暂时不可用（HTTP {status}）", retryable=True
                )
            if status != 200:
                raise ResearchPageFetchError(
                    "UPSTREAM_REJECTED", f"来源网站未返回可提取页面（HTTP {status}）"
                )
            if len(body) > self.max_body_bytes:
                raise ResearchPageFetchError("BODY_TOO_LARGE", "来源网页超过最大读取体积限制")
            encoding = headers.get("content-encoding", "identity").lower()
            if encoding not in {"", "identity"}:
                raise ResearchPageFetchError(
                    "CONTENT_ENCODING_UNSUPPORTED", "来源网页使用了不支持的压缩编码"
                )
            content_type = headers.get("content-type", "")
            message = Message()
            message["content-type"] = content_type
            media_type = message.get_content_type().lower()
            if media_type not in {"text/html", "application/xhtml+xml", "text/plain"}:
                raise ResearchPageFetchError(
                    "MEDIA_TYPE_UNSUPPORTED", "目前只提取 HTML、XHTML 和纯文本页面"
                )
            final_url, _, _ = _normalise_target_url(current_url)
            return _extract(
                body,
                content_type=content_type,
                final_url=final_url,
                requested_url=requested_url,
                max_excerpt_chars=self.max_excerpt_chars,
            )

        raise ResearchPageFetchError("REDIRECT_LIMIT", "来源网站重定向次数超过限制")


def get_research_page_provider() -> ResearchPageProvider:
    """Default closed; direct public-page retrieval requires explicit deployment enablement."""
    from app.config import get_settings

    settings = get_settings()
    if settings.research_page_provider == "safe_http":
        return SafeHttpResearchPageProvider(
            timeout_seconds=settings.research_page_timeout_seconds,
            max_body_bytes=settings.research_page_max_body_bytes,
            max_excerpt_chars=settings.research_page_max_excerpt_chars,
        )
    return ResearchPageProvider()


__all__ = [
    "EXTRACTOR_VERSION",
    "ResearchPage",
    "ResearchPageFetchError",
    "ResearchPageProvider",
    "SafeHttpResearchPageProvider",
    "get_research_page_provider",
]
