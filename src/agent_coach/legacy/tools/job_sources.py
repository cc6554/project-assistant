"""Public indexed recruitment sources and explicit user-provided JD imports.

The default ``web`` search spans public employer career pages and recruitment
sites.  The historical BOSS/猎聘/智联 adapters remain available when a caller
explicitly selects one of them.
"""

from __future__ import annotations

import base64
import ipaddress
import re
import socket
import time
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from . import web_search

PLATFORMS = {
    "boss": ("BOSS 直聘", "zhipin.com"),
    "liepin": ("猎聘", "liepin.com"),
    "zhilian": ("智联招聘", "zhaopin.com"),
}

# A search result is a lead until the separate bounded ``fetch_url`` step has
# read its public page.  Keep the candidate pool large enough for that step to
# find three to five independent readable JDs, while preventing a model or a
# flaky engine from turning one request into an unbounded crawl.
DEFAULT_SEARCH_CANDIDATES = 18
MAX_SEARCH_CANDIDATES = 20
MAX_SEARCH_VARIANTS = 4
MAX_SEARCH_REQUESTS = 20
MAX_SEARCH_REDIRECT_REQUESTS = 12
MAX_TOTAL_SEARCH_REQUESTS = MAX_SEARCH_REQUESTS + MAX_SEARCH_REDIRECT_REQUESTS
MAX_CANDIDATES_PER_DOMAIN = 5
MAX_DOMAIN_FALLBACK_CANDIDATES = 3
MAX_SEARCH_SECONDS = 60.0
SEARCH_ENGINE_TIMEOUT_SECONDS = 8.0
SEARCH_REDIRECT_TIMEOUT_SECONDS = 3.0
MAX_SEARCH_REDIRECT_HOPS = 3
MAX_REDIRECT_BODY_BYTES = 64 * 1024
MAX_SEARCH_TITLE_CHARS = 180
MAX_SEARCH_SNIPPET_CHARS = 360


class SearchResult(list):
    """List-compatible search results with bounded retrieval diagnostics."""

    def __init__(self, values: list[dict[str, object]] | None = None, *, diagnostics: dict[str, object] | None = None):
        super().__init__(values or [])
        self.diagnostics: dict[str, object] = diagnostics or {}


class SearchError(ValueError):
    """A public-search failure carrying the attempts that were made."""

    def __init__(self, message: str, *, diagnostics: dict[str, object] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}

# Search results sometimes use a first-party subdomain for the mobile or job
# detail surface. Keep this list explicit: accepting every subdomain of a
# recruitment domain would turn a search result into an arbitrary host trust
# boundary.
_ALLOWED_HOSTS = {
    "boss": frozenset({"zhipin.com", "www.zhipin.com", "m.zhipin.com", "jobs.zhipin.com"}),
    "liepin": frozenset({"liepin.com", "www.liepin.com", "m.liepin.com", "jobs.liepin.com"}),
    "zhilian": frozenset(
        {"zhaopin.com", "www.zhaopin.com", "m.zhaopin.com", "jobs.zhaopin.com", "sou.zhaopin.com"}
    ),
}

# ``web`` search is intentionally not tied to a vendor allow-list.  These
# lexical checks are the first boundary for links returned by an untrusted
# search engine.  The actual page fetch still goes through ``fetch_url``,
# whose DNS-aware public-address check is the authoritative SSRF boundary.
_LOCAL_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".lan")
_LOCAL_HOST_NAMES = frozenset(
    {"localhost", "localhost.localdomain", "metadata.google.internal", "metadata"}
)
_CREDENTIAL_QUERY_KEYS = frozenset(
    {"token", "access_token", "api_key", "apikey", "key", "auth", "authorization"}
)
# A raw result that still points at one of these hosts is an unresolved
# search-engine wrapper, not a public job source.  It is safe to keep the
# original engine's external redirect only after ``_destination`` decodes it
# or a first-party data-* URL exposes the real destination.
_SEARCH_ENGINE_HOSTS = frozenset(
    {
        "so.com",
        "www.so.com",
        "duckduckgo.com",
        "www.duckduckgo.com",
        "html.duckduckgo.com",
        "bing.com",
        "www.bing.com",
        "cn.bing.com",
        "sogou.com",
        "www.sogou.com",
        "baidu.com",
        "www.baidu.com",
    }
)
_RECRUITMENT_HINTS = (
    "招聘",
    "岗位",
    "职位",
    "任职",
    "职责",
    "薪资",
    "待遇",
    "校招",
    "社招",
    "jd",
    "工程师",
    "开发",
    "developer",
    "engineer",
    "software",
    "programmer",
)
_DETAIL_ROUTE_RE = re.compile(
    r"(?:^|/)(?:detail|job(?:s)?(?:[-_]?detail)?|position(?:s)?|opening(?:s)?|vacanc(?:y|ies)|requisition|opportunit(?:y|ies))(?:/|$)",
    re.I,
)
_INDEX_ROUTE_RE = re.compile(
    r"(?:^|/)(?:search|results?|list|listing|category|categories|zhaopin|recruit(?:ment)?|campus|career(?:s)?)(?:/|$)",
    re.I,
)
_INDEX_TEXT_RE = re.compile(r"(?:岗位|职位|招聘).{0,8}(?:列表|搜索|合集|大全|职位汇总)|(?:job|position).{0,8}(?:list|search|board)", re.I)
_DETAIL_TEXT_RE = re.compile(r"(?:招聘|岗位|职位|任职|职责|要求|薪资|待遇|jd|工程师|developer|engineer)", re.I)
_RECRUITMENT_CONTEXT_RE = re.compile(
    r"(?:招聘|岗位|职位|任职|职责|要求|薪资|待遇|校招|社招|\bjd\b|"
    r"recruit(?:ment)?|career(?:s)?|opening(?:s)?|vacanc(?:y|ies)|job(?:s)?|position(?:s)?)",
    re.I,
)
_CHALLENGE_MARKERS = (
    "captcha",
    "antispider",
    "verify you are human",
    "unusual traffic",
    "security check",
    "access denied",
    "请输入验证码",
    "验证码",
    "人机验证",
    "安全验证",
    "访问异常",
    "异常访问",
    "访问过于频繁",
    "请求过于频繁",
)
_COMPARISON_TITLE_RE = re.compile(
    r"(?:对比|测评|评测|排行榜|排名|哪个好|哪家好|如何选择|优缺点|价格比较|招聘网站大全|招聘平台推荐)",
    re.I,
)
_PROMOTION_RE = re.compile(r"(?:广告|赞助|付费推广|广告位|招商加盟|立即咨询|课程报名|免费领取)", re.I)
_QUERY_CONTEXT_STOPWORDS = (
    "任职要求",
    "职位要求",
    "岗位要求",
    "招聘信息",
    "职位信息",
    "岗位信息",
    "招聘",
    "职位",
    "岗位",
    "任职",
    "要求",
    "工程师",
    "高级",
    "资深",
    "初级",
    "中级",
    "应届",
    "校招",
    "社招",
    "实习",
    "senior",
    "junior",
    "developer",
    "engineer",
    "specialist",
    "professional",
    "position",
    "opening",
    "vacancy",
    "job",
)

# These are the public recruitment surfaces that can be returned by a search
# index. Aggregated/list pages are useful leads, but are explicitly labelled
# below and must not be counted as a single readable JD.
_DETAIL_PATHS = {
    "boss": ("/job_detail",),
    # 猎聘's public ``/a/<id>.shtml`` route is an individual posting, even
    # though the short path is easy to mistake for an aggregate endpoint.
    "liepin": ("/job", "/a"),
    "zhilian": ("/job", "/jobs"),
}
_INDEX_PATHS = {
    "boss": ("/zhaopin", "/companys"),
    "liepin": ("/zhaopin",),
    "zhilian": ("/zhaopin", "/sou"),
}

_WRAPPER_QUERY_KEYS = (
    "uddg",
    "target",
    "dest",
    "destination",
    "redirect",
    "redirect_url",
    "url",
    "q",
)
_BASE64_QUERY_KEYS = ("u", "url")


def _path_matches(path: str, prefix: str) -> bool:
    normalized = "/" + (path or "/").lstrip("/")
    candidate = prefix.rstrip("/") or "/"
    return normalized.casefold() == candidate.casefold() or normalized.casefold().startswith(
        candidate.casefold() + "/"
    )


def _is_public_host(host: str) -> bool:
    """Reject obvious local/private/metadata hosts without doing DNS here."""

    folded = str(host or "").rstrip(".").casefold()
    if not folded or folded in _LOCAL_HOST_NAMES or any(folded.endswith(suffix) for suffix in _LOCAL_HOST_SUFFIXES):
        return False
    try:
        return ipaddress.ip_address(folded).is_global
    except ValueError:
        # DNS answers are checked again by the bounded generic fetch tool.  A
        # normal hostname is retained here so public enterprise domains do not
        # need to be registered in this module.
        return True


def public_source_url(value: str) -> str:
    """Validate a public HTTPS recruitment-source URL lexically.

    This function deliberately does not fetch or resolve the host.  It rejects
    credentials, suspicious query keys, local-address literals and private
    naming conventions up front; ``fetch_url`` performs the DNS-aware check
    immediately before every network request and on every redirect.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError("岗位来源 URL 必须是非空字符串")
    raw = value.strip()
    if any(ord(char) < 0x20 for char in raw):
        raise ValueError("岗位来源 URL 包含非法控制字符")
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError("岗位来源 URL 格式无效") from exc
    host = parts.hostname
    if (
        parts.scheme.casefold() != "https"
        or not host
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
    ):
        raise ValueError("岗位来源 URL 必须是无凭据的 HTTPS 公共链接")
    if not _is_public_host(host):
        raise ValueError("岗位来源 URL 不能指向本机、内网或保留地址")
    query_keys = {
        str(key).strip().casefold().replace("-", "_")
        for key, _ in parse_qsl(parts.query, keep_blank_values=True)
    }
    if query_keys.intersection(_CREDENTIAL_QUERY_KEYS):
        raise ValueError("岗位来源 URL 查询参数不能携带凭据")
    # Reserved example hosts are fixtures/documentation, not independently
    # readable public job sources.  Rejecting them catches accidental model
    # placeholders while allowing real enterprise career domains everywhere.
    if host.casefold().rstrip(".") in {"example.com", "example.org", "example.net"}:
        raise ValueError("岗位来源 URL 不能使用示例域名")
    path = parts.path or "/"
    # Fragments are not sent in an HTTP request, but they are valid provenance
    # for client-rendered career SPAs (for example ``#/job/123``).  Preserve
    # them for the user's source record; fetch_url will reject/describe an
    # unreadable client-only route instead of silently rewriting provenance.
    return urlunsplit(("https", host.rstrip(".").casefold(), path, parts.query, parts.fragment))


def _host_for(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        if parts.scheme.casefold() != "https" or parts.port not in (None, 443):
            return None
        if parts.username is not None or parts.password is not None or parts.fragment or not parts.hostname:
            return None
        query_keys = {
            str(key).strip().casefold().replace("-", "_")
            for key, _ in parse_qsl(parts.query, keep_blank_values=True)
        }
        if query_keys.intersection(_CREDENTIAL_QUERY_KEYS):
            return None
        host = parts.hostname.rstrip(".").casefold()
        return host if _is_public_host(host) else None
    except ValueError:
        return None


def _is_search_engine_host(host: str | None) -> bool:
    folded = str(host or "").rstrip(".").casefold()
    return folded in _SEARCH_ENGINE_HOSTS or any(
        folded.endswith("." + root)
        for root in ("so.com", "duckduckgo.com", "bing.com", "sogou.com", "baidu.com")
    )


def platform_for(url: str) -> str | None:
    """Return the supported recruitment platform for an HTTPS first-party host."""

    host = _host_for(url)
    if host is None or _is_search_engine_host(host):
        return None
    return next((key for key, hosts in _ALLOWED_HOSTS.items() if host in hosts), None)


def _generic_result_kind(url: str, title: str = "", text: str = "") -> str | None:
    """Classify an arbitrary public-web search result.

    ``search_index`` is a useful lead but never an independent JD.  A result
    is marked ``job_detail`` only when its route or result text points to one
    concrete opening; unrelated pages are dropped by returning ``None``.
    """

    host = _host_for(url)
    if host is None or _is_search_engine_host(host):
        return None
    # Known first-party detail routes carry stronger provenance than a sparse
    # search snippet.  In particular, 猎聘's ``/a/<id>.shtml`` is a concrete
    # posting route, not a list page; classify it before lexical snippet
    # heuristics can demote it to ``search_index``.
    known_platform = platform_for(url)
    if known_platform:
        known_kind = result_kind(url, known_platform)
        if known_kind is not None:
            return known_kind
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    path = "/" + (parts.path or "/").lstrip("/")
    title = web_search._clean(title)
    text = web_search._clean(text)
    combined = f"{title} {text}".strip()
    route = f"{path}?{parts.query}".casefold()
    if not _DETAIL_TEXT_RE.search(combined) or not _RECRUITMENT_CONTEXT_RE.search(f"{combined} {route}"):
        return None
    has_detail_route = bool(_DETAIL_ROUTE_RE.search(path))
    has_index_route = bool(_INDEX_ROUTE_RE.search(path)) or bool(_INDEX_TEXT_RE.search(combined))
    has_career_detail = bool(re.search(r"/(?:career|careers)/[^/?#]{2,}", path, re.I))
    # A concrete id/slug after a detail route is stronger than a generic
    # ``/jobs`` route.  Keep detail pages that happen to use a plural route,
    # but never promote an obvious list/search page merely because its title
    # contains the word "招聘".
    if has_detail_route and not has_index_route:
        return "job_detail"
    if has_career_detail and not _INDEX_TEXT_RE.search(combined):
        return "job_detail"
    if has_detail_route and re.search(r"/(?:job|jobs|position|opening|vacanc(?:y|ies)|requisition)/[^/?#]{2,}", path, re.I):
        if not _INDEX_TEXT_RE.search(combined):
            return "job_detail"
    if has_index_route or any(key in route for key in ("q=", "query=", "keyword=", "search=")):
        return "search_index"
    # Some official career sites use opaque routes without a conventional
    # /job/ segment.  Treat a role-specific title plus requirement language as
    # a detail candidate, while keeping a bare careers home page out.
    if _DETAIL_TEXT_RE.search(title) and re.search(r"(?:要求|职责|任职|薪资|待遇|responsibilit|requirement)", combined, re.I):
        return "job_detail"
    if _DETAIL_TEXT_RE.search(combined):
        return "search_index"
    return None


def result_kind(
    url: str,
    platform: str | None = None,
    *,
    title: str = "",
    text: str = "",
) -> str | None:
    """Classify a first-party public result without treating a list as a JD.

    ``platform_for`` remains the host-level validator used for user-provided
    source URLs. Search results need the additional path boundary so a search
    engine home page or an unrelated route is never presented as a candidate.
    """

    if platform in (None, "", "web", "all"):
        return _generic_result_kind(url, title, text)
    detected = platform_for(url)
    if detected is None or (platform is not None and detected != platform):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").rstrip(".").casefold()
    # Zhaopin's jobs subdomain uses an opaque path without the /job prefix.
    if detected == "zhilian" and host == "jobs.zhaopin.com" and parts.path.strip("/"):
        return "job_detail"
    for prefix in _DETAIL_PATHS[detected]:
        if not _path_matches(parts.path, prefix):
            continue
        # A route root (or a query-only ``job_pk`` placeholder) is not an
        # individual posting. Real posting paths retain their opaque public ID
        # in the path, while aggregate ``jobs.html`` routes remain searchable
        # below through the generic index checks.
        suffix = (parts.path or "")[len(prefix) :].strip("/")
        if suffix:
            return "job_detail"
        query_keys = {
            key.casefold()
            for key, _ in parse_qsl(parts.query, keep_blank_values=True)
        }
        if detected == "boss" and "job_pk" in query_keys:
            return None
    if any(_path_matches(parts.path, prefix) for prefix in _INDEX_PATHS[detected]):
        return "search_index"
    return None


def _canonical_url(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").rstrip(".").casefold()
    path = parts.path or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def _decode_base64_url(value: str) -> str:
    encoded = str(value or "").strip()
    if encoded.startswith("a1"):
        encoded = encoded[2:]
    # Avoid treating an ordinary query value as a URL unless it decodes to a
    # complete HTTP(S) URL. This covers Bing's a1 wrapper and harmlessly skips
    # opaque redirect tokens used by engines that cannot be decoded publicly.
    if len(encoded) < 12 or not re.fullmatch(r"[A-Za-z0-9_\-]+=*", encoded):
        return ""
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
    except (ValueError, UnicodeError):
        return ""
    return decoded if decoded.startswith(("https://", "http://", "//")) else ""


def _destination(url: str) -> str:
    """Decode public search-engine wrappers without following a redirect."""

    # HTMLParser has already decoded character references in href/data-* attrs.
    # Unescaping a second time corrupts ordinary parameter names such as
    # ``timestamp`` (``&times`` is a legacy HTML entity without a semicolon).
    current = str(url or "").strip()
    if not current:
        return ""
    # Search engines can wrap one redirect in another. Decode only a small,
    # bounded number of layers; host validation still happens on the final URL.
    for _ in range(3):
        if current.startswith("//"):
            current = "https:" + current
        try:
            parsed = urlsplit(current)
            if (
                parsed.scheme.casefold() == "http"
                and (parsed.hostname or "").rstrip(".").casefold() in {"baidu.com", "www.baidu.com"}
            ):
                # Baidu has historically emitted both http and https wrapper
                # links. Resolve its known public wrapper over TLS only.
                current = urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
                parsed = urlsplit(current)
            query: dict[str, str] = {}
            for key, value in parse_qsl(parsed.query, keep_blank_values=True):
                query.setdefault(str(key).casefold(), str(value))
        except ValueError:
            return ""
        candidate = ""
        for key in _WRAPPER_QUERY_KEYS:
            value = query.get(key, "")
            if value.startswith(("https://", "http://", "//")):
                candidate = value
                break
            if key in _BASE64_QUERY_KEYS and value:
                candidate = _decode_base64_url(value)
                if candidate:
                    break
        if not candidate:
            for key in _BASE64_QUERY_KEYS:
                value = query.get(key, "")
                candidate = _decode_base64_url(value)
                if candidate:
                    break
        if not candidate:
            return current
        # parse_qsl and the base64 decoder each decode exactly one transport
        # layer. Do not recursively unquote or HTML-unescape an untrusted URL.
        candidate = candidate.strip()
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        if candidate == current:
            return current
        current = candidate
    return current


def _dns_resolves_public(host: str) -> bool:
    """Require every DNS answer to be globally routable before a redirect fetch."""

    folded = str(host or "").rstrip(".").casefold()
    if not _is_public_host(folded):
        return False
    try:
        addresses = socket.getaddrinfo(folded, 443, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError, ValueError):
        return False
    resolved: list[str] = []
    for answer in addresses:
        try:
            address = str(answer[4][0])
            resolved.append(address)
            if not ipaddress.ip_address(address).is_global:
                return False
        except (IndexError, TypeError, ValueError):
            return False
    return bool(resolved)


def _safe_redirect_target(current: str, location: str) -> str:
    """Resolve one redirect URL and reject credentials or non-public hosts."""

    target = urljoin(current, location)
    try:
        parts = urlsplit(target)
        port = parts.port
    except ValueError:
        return ""
    if (
        parts.scheme.casefold() != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.fragment
        or not _host_for(target)
    ):
        return ""
    return target


def _request_redirect(url: str, timeout: float):
    """Fetch one redirect hop, buffering at most 64 KiB for refresh markup."""

    parts = urlsplit(url)
    host = parts.hostname or ""
    if not _dns_resolves_public(host):
        raise ValueError("重定向目标 DNS 未验证为公共地址")
    bounded_timeout = max(0.5, min(float(timeout), SEARCH_REDIRECT_TIMEOUT_SECONDS))
    with httpx.stream(
        "GET",
        url,
        headers=web_search._HEADERS,
        timeout=bounded_timeout,
        follow_redirects=False,
        trust_env=True,
    ) as response:
        status = int(getattr(response, "status_code", 0) or 0)
        headers = dict(getattr(response, "headers", {}) or {})
        body = b""
        if not (300 <= status < 400) and status < 400:
            advertised = headers.get("content-length") or headers.get("Content-Length")
            try:
                too_large = advertised is not None and int(advertised) > MAX_REDIRECT_BODY_BYTES
            except (TypeError, ValueError):
                too_large = False
            if not too_large:
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes(chunk_size=16 * 1024):
                    if not chunk:
                        continue
                    if size + len(chunk) > MAX_REDIRECT_BODY_BYTES:
                        chunks.clear()
                        size = MAX_REDIRECT_BODY_BYTES + 1
                        break
                    chunks.append(bytes(chunk))
                    size += len(chunk)
                if size <= MAX_REDIRECT_BODY_BYTES:
                    body = b"".join(chunks)
        encoding = getattr(response, "encoding", None) or "utf-8"
    try:
        text = body.decode(encoding, errors="replace") if body else ""
    except (LookupError, UnicodeError):
        text = body.decode("utf-8", errors="replace") if body else ""
    return SimpleNamespace(status_code=status, headers=headers, text=text)


def _body_redirect_target(body: str, current: str) -> str:
    """Read conservative HTML refresh/script redirects without executing JS."""

    # Search-engine jump pages occasionally return HTTP 200 with a refresh.
    # Accept only an explicit URL value and still subject it to the same
    # public-host and credential checks as an HTTP Location header.
    meta = re.search(
        r"<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh['\"]?[^>]*content\s*=\s*['\"][^'\"]*?url\s*=\s*([^'\";>]+)",
        body or "",
        re.I,
    )
    if meta:
        return _safe_redirect_target(current, meta.group(1).strip())
    script = re.search(
        r"(?:window\s*\.\s*)?location(?:\s*\.\s*(?:href|replace))?\s*(?:=|\()\s*['\"](https?://[^'\"]+)['\"]",
        body or "",
        re.I,
    )
    if script:
        return _safe_redirect_target(current, script.group(1).strip())
    return ""


def _resolve_baidu_wrapper(
    url: str,
    *,
    timeout: float = SEARCH_REDIRECT_TIMEOUT_SECONDS,
    request: Callable[[str, float], object] | None = None,
) -> str:
    """Resolve a Baidu ``/link?url=opaque`` lead under a small public-only budget.

    Only search-engine hosts are fetched. Once an external public destination
    is reached, its DNS is checked and the URL is returned without requesting
    the destination page or forwarding cookies, credentials, or query data.
    """

    fetch = request or _request_redirect
    current = str(url or "").strip()
    try:
        initial = urlsplit(current)
        query = {key.casefold(): value for key, value in parse_qsl(initial.query, keep_blank_values=True)}
    except ValueError:
        return ""
    if (
        initial.scheme.casefold() != "https"
        or (initial.hostname or "").rstrip(".").casefold() not in {"baidu.com", "www.baidu.com"}
        or not _path_matches(initial.path, "/link")
        or not query.get("url")
        or not _host_for(current)
    ):
        return ""

    for _ in range(MAX_SEARCH_REDIRECT_HOPS):
        try:
            current_host = urlsplit(current).hostname or ""
            if not _dns_resolves_public(current_host):
                return ""
            response = fetch(current, timeout)
            status = int(getattr(response, "status_code", 0) or 0)
            headers = getattr(response, "headers", {}) or {}
            target = ""
            if 300 <= status < 400:
                target = _safe_redirect_target(current, str(headers.get("location", "")))
            elif status < 400:
                target = _body_redirect_target(str(getattr(response, "text", "") or ""), current)
            if not target:
                return ""
            target_parts = urlsplit(target)
            target_host = target_parts.hostname or ""
            # Validate public DNS before accepting the final public target too.
            if not _dns_resolves_public(target_host):
                return ""
            if _is_search_engine_host(target_host):
                current = target
                continue
            return _canonical_url(target)
        except MemoryError:
            raise
        except Exception:
            return ""
    return ""


def _query_match_terms(query: str) -> list[str]:
    """Build generic role-specific terms without requiring a literal title match."""

    normalized = re.sub(r"[\s\-_/|,，;；:：()（）\[\]【】]+", " ", query.casefold()).strip()
    for stopword in sorted(_QUERY_CONTEXT_STOPWORDS, key=len, reverse=True):
        normalized = normalized.replace(stopword.casefold(), " ")
    tokens: list[str] = []
    # Preserve Latin/tool names as whole tokens and use overlapping CJK
    # bigrams so related titles can match shared concepts without matching the
    # entire user phrase verbatim.
    for match in re.finditer(r"[a-z0-9][a-z0-9+#.]*|[\u3400-\u9fff]+", normalized, re.I):
        value = match.group(0).strip(" .")
        if not value:
            continue
        if re.fullmatch(r"[a-z0-9+#.]+", value, re.I):
            tokens.append(value.casefold())
        elif len(value) == 1:
            tokens.append(value)
        else:
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
    return list(dict.fromkeys(token for token in tokens if token))


def _candidate_quality(
    query: str, *, title: str, text: str, result_kind: str
) -> tuple[bool, float, str | None]:
    """Reject obvious noise while retaining related titles and official lists."""

    title_text = web_search._clean(title)
    snippet = web_search._clean(text)
    if _COMPARISON_TITLE_RE.search(title_text):
        return False, 0.0, "comparison_page"
    if _PROMOTION_RE.search(f"{title_text} {snippet}"):
        return False, 0.0, "promotion"
    terms = _query_match_terms(query)
    if not terms:
        return True, 0.5, None
    combined = f"{title_text} {snippet}".casefold()
    title_folded = title_text.casefold()

    def term_matches(term: str, haystack: str) -> bool:
        if re.fullmatch(r"[a-z0-9][a-z0-9+#.]*", term, re.I):
            return term.casefold() in re.findall(r"[a-z0-9][a-z0-9+#.]*", haystack, re.I)
        return term in haystack

    matched = sum(1 for term in terms if term_matches(term, combined))
    score = matched / len(terms)
    # A partial concept match is enough to keep related job titles (and a
    # career-list lead whose snippet names the requested role). A complete
    # phrase match is deliberately unnecessary.
    threshold = 0.34 if len(terms) >= 3 else 0.5
    if matched == 0 or score < threshold:
        return False, score, "query_mismatch"
    if result_kind == "job_detail" and title_text:
        title_matches = sum(1 for term in terms if term_matches(term, title_folded))
        # Snippets can supply missing concepts, but a detail page title should
        # still carry at least one role-specific signal where one exists.
        if title_matches == 0 and score < 0.6:
            return False, score, "query_mismatch"
    return True, score, None


def _page_challenge(text: str) -> bool:
    sample = str(text or "")[:300_000].casefold()
    return any(marker.casefold() in sample for marker in _CHALLENGE_MARKERS)


class _SearchLinkParser(HTMLParser):
    """Collect links and nearby result-block text from varied engine HTML."""

    _CONTAINER_HINTS = {
        "result",
        "b_algo",
        "vrwrap",
        "c-container",
        "result__body",
        "result-item",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, object]] = []
        self._anchor: dict[str, object] | None = None
        self._containers: list[tuple[str, dict[str, object]]] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(key).casefold(): str(value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = self._attrs(attrs)
        classes = set(values.get("class", "").split())
        folded = tag.casefold()
        if folded in {"li", "div", "article"} and classes.intersection(self._CONTAINER_HINTS):
            container: dict[str, object] = {"text": []}
            self._containers.append((folded, container))
        if folded == "a":
            anchor: dict[str, object] = {
                "attrs": values,
                "text": [],
                "container": self._containers[-1][1] if self._containers else None,
            }
            self.anchors.append(anchor)
            self._anchor = anchor

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        if self._anchor is not None:
            self._anchor["text"].append(data)  # type: ignore[union-attr]
        for _, container in self._containers:
            container["text"].append(data)  # type: ignore[union-attr]

    def handle_endtag(self, tag: str) -> None:
        folded = tag.casefold()
        if folded == "a":
            self._anchor = None
        for index in range(len(self._containers) - 1, -1, -1):
            if self._containers[index][0] == folded:
                del self._containers[index:]
                break


def _is_opaque_baidu_wrapper(url: str) -> bool:
    try:
        parts = urlsplit(url)
        query = {
            key.casefold(): value
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        }
    except ValueError:
        return False
    value = query.get("url", "").strip()
    return (
        parts.scheme.casefold() == "https"
        and (parts.hostname or "").rstrip(".").casefold() in {"baidu.com", "www.baidu.com"}
        and _path_matches(parts.path, "/link")
        and bool(value)
        and not value.startswith(("https://", "http://", "//"))
    )


def _parse_links(
    source: str,
    platform: str | None = "web",
    *,
    wrapper_resolver: Callable[[str], str] | None = None,
) -> list[dict[str, str]]:
    parser = _SearchLinkParser()
    try:
        parser.feed(source or "")
        parser.close()
    except (ValueError, TypeError):
        # A malformed result page should not prevent the next public engine
        # from being tried.
        return []
    results: list[dict[str, str]] = []
    for anchor in parser.anchors:
        attrs = anchor.get("attrs") if isinstance(anchor.get("attrs"), dict) else {}
        raw_candidates = [attrs.get("href", "")]
        for key in ("data-landurl", "data-mdurl", "data-url", "data-href", "mu"):
            if attrs.get(key):
                raw_candidates.append(attrs[key])
        destination = ""
        for raw in raw_candidates:
            candidate = _destination(str(raw or ""))
            if wrapper_resolver and _is_opaque_baidu_wrapper(candidate):
                candidate = wrapper_resolver(candidate)
            # For a known legacy platform, path classification is enough to
            # select a candidate.  Generic web results are classified after
            # title/snippet extraction; at this stage only require a public
            # HTTPS host so an aggregate page can still be labelled honestly.
            candidate_ok = (
                _host_for(candidate) is not None
                and not _is_search_engine_host(urlsplit(candidate).hostname)
                if platform in (None, "", "web", "all")
                else result_kind(candidate, platform) is not None
            )
            if candidate_ok:
                destination = _canonical_url(candidate)
                break
        if not destination:
            continue
        title = web_search._clean(" ".join(str(item) for item in anchor.get("text", [])))
        container = anchor.get("container")
        container_text = ""
        if isinstance(container, dict):
            container_text = web_search._clean(" ".join(str(item) for item in container.get("text", [])))
        if title and container_text.startswith(title):
            container_text = container_text[len(title) :].strip(" ：:|-—")
        kind = result_kind(destination, platform, title=title, text=container_text)
        if kind is None:
            continue
        results.append(
            {
                "url": destination,
                "title": title[:MAX_SEARCH_TITLE_CHARS],
                "text": container_text[:MAX_SEARCH_SNIPPET_CHARS],
                "result_kind": kind,
            }
        )
    return results


def _normalise_query(query: str) -> str:
    """Clean one user role query without inventing a role, city, or employer."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError("岗位搜索 query 不能为空")
    if any(line.strip() for line in query.splitlines()[1:]):
        raise ValueError("岗位搜索一次只接受一个目标岗位名称；请勿输入多个岗位方向")
    # A model/user supplied site operator must not narrow the default public
    # search to one vendor.  The selected legacy platform adds its own domain
    # constraint below, after this normalization step.
    cleaned = re.sub(r"\bsite:\S+", " ", query, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n,，;；|")
    if not cleaned:
        raise ValueError("岗位搜索 query 不能为空")
    return cleaned


def query_variants(query: str, *, limit: int = MAX_SEARCH_VARIANTS) -> list[str]:
    """Return deterministic role-preserving query variants.

    The suffixes are retrieval hints only.  They do not add a city, seniority,
    employer, or alternate profession, so a one-role request remains a
    one-role request across every engine attempt.
    """

    base = _normalise_query(query)
    try:
        requested = max(1, min(int(limit), MAX_SEARCH_VARIANTS))
    except (TypeError, ValueError):
        requested = MAX_SEARCH_VARIANTS
    # Keep the literal role query first so the engine sees exactly what the
    # user asked for before we add generic recruitment-context hints.
    quoted_role = '"' + base.replace('"', " ").strip() + '"'
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9+#.]*|[\u3400-\u9fff]+", base)
    tokenized_role = " ".join(tokens) if tokens else base
    suffixes = (
        "",
        f"{quoted_role} 任职要求",
        f"{tokenized_role} 招聘 岗位要求",
        f"{tokenized_role} 招聘官网 职位",
    )
    variants: list[str] = []
    seen: set[str] = set()
    for suffix in suffixes:
        if not suffix:
            candidate = base
        elif suffix.startswith('"'):
            candidate = suffix
        else:
            candidate = suffix
        candidate = re.sub(r"\s+", " ", candidate).strip()
        key = candidate.casefold()
        if key in seen:
            continue
        variants.append(candidate)
        seen.add(key)
        if len(variants) >= requested:
            break
    return variants


# Private compatibility spelling used by older callers/tests.
_query_variants = query_variants


def _dedupe_key(url: str) -> str:
    """Stable URL identity for cross-engine/variant deduplication."""

    try:
        parts = urlsplit(url)
        pairs = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            folded = str(key).casefold()
            if folded.startswith(("utm_", "wx_")) or folded in {
                "from",
                "ref",
                "src",
                "spm",
                "trk",
                "timestamp",
                "signature",
                "sign",
                "nonce",
                "echostr",
                "chksm",
                "scene",
                "isappinstalled",
            }:
                continue
            pairs.append((str(key), str(value)))
        query = urlencode(sorted(pairs))
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        path = path.rstrip("/") or "/"
        return urlunsplit(("https", (parts.hostname or "").rstrip(".").casefold(), path, query, ""))
    except (TypeError, ValueError):
        return str(url).strip().casefold()


_COMMON_MULTI_LABEL_SUFFIXES = frozenset(
    {"ac", "co", "com", "edu", "gov", "net", "org"}
)


def _registered_domain(host: str) -> str:
    """Approximate the registrable domain without a new PSL dependency."""

    folded = str(host or "").rstrip(".").casefold()
    try:
        return str(ipaddress.ip_address(folded))
    except ValueError:
        pass
    labels = [label for label in folded.split(".") if label]
    if len(labels) <= 2:
        return folded
    # Handle the common country-code suffix forms (example.com.cn,
    # example.co.uk) while keeping unrelated Chinese company domains distinct.
    if len(labels[-1]) == 2 and labels[-2] in _COMMON_MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _domain_for_url(url: str) -> str:
    try:
        return _registered_domain(urlsplit(url).hostname or "")
    except ValueError:
        return ""


def _error_text(exc: BaseException) -> str:
    """Bound external error text for diagnostics without leaking credentials."""

    text = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if not text:
        text = type(exc).__name__
    # httpx/requests exceptions may echo a proxy URL or a request URL.  Keep
    # useful route/status context while masking userinfo and credential-like
    # query values before diagnostics are returned to the model/UI.
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(
        r"(?i)([?&](?:token|access_token|api_key|apikey|key|auth|authorization)=)[^&\s]+",
        r"\1[redacted]",
        text,
    )
    return text[:240]


def _search_diagnostics(
    *, query: str, platform: str, requested: int, variants: list[str]
) -> dict[str, object]:
    return {
        "query": query,
        "platform": platform,
        "requested": requested,
        "variants": list(variants),
        "attempts": [],
        "engines_attempted": [],
        "engines_succeeded": [],
        "errors": [],
        "redirect_attempts": [],
        "failure_counts": {
            "http_failure": 0,
            "transport_failure": 0,
            "challenge": 0,
            "vacuum": 0,
            "usable_response": 0,
        },
        "rejected_candidates": {},
        "candidate_count": 0,
        "deduplicated_count": 0,
        "returned_count": 0,
        "search_lead_count": 0,
        "source_evidence_count": 0,
        "source_evidence_target": {"minimum": 3, "maximum": 5},
        "budget": {
            "max_candidates": MAX_SEARCH_CANDIDATES,
            "max_variants": MAX_SEARCH_VARIANTS,
            "max_engine_requests": MAX_SEARCH_REQUESTS,
            "max_redirect_requests": MAX_SEARCH_REDIRECT_REQUESTS,
            "max_requests": MAX_TOTAL_SEARCH_REQUESTS,
            "max_candidates_per_registered_domain": MAX_CANDIDATES_PER_DOMAIN,
            "domain_fallback_candidates": MAX_DOMAIN_FALLBACK_CANDIDATES,
            "max_seconds": MAX_SEARCH_SECONDS,
            "engine_timeout_seconds": SEARCH_ENGINE_TIMEOUT_SECONDS,
            "variant_soft_cap": None,
        },
        "route": web_search.route_diagnostics(),
        "status": "pending",
    }


def search(query: str, platform: str = "web", n: int = DEFAULT_SEARCH_CANDIDATES) -> SearchResult:
    """Collect bounded, deduplicated public-web search leads.

    The returned rows are deliberately *not* source evidence: callers must
    read selected URLs with ``fetch_url`` before counting independent JD
    samples.  ``SearchResult.diagnostics`` records every bounded attempt so a
    route/engine failure is visible to the caller instead of becoming a silent
    empty result.
    """

    if platform not in PLATFORMS and platform not in {"web", "all", None}:
        raise ValueError("请选择 web 全网搜索或兼容的招聘平台")
    result_platform = "web" if platform in {"web", "all", None} else platform
    clean_query = _normalise_query(query)
    try:
        requested = int(n)
    except (TypeError, ValueError) as exc:
        raise ValueError("岗位搜索 limit 必须是整数") from exc
    if requested <= 0:
        variants = query_variants(clean_query)
        diagnostics = _search_diagnostics(
            query=clean_query, platform=result_platform, requested=0, variants=variants
        )
        diagnostics["status"] = "empty"
        return SearchResult([], diagnostics=diagnostics)
    requested = min(requested, MAX_SEARCH_CANDIDATES)
    variants = query_variants(clean_query)
    diagnostics = _search_diagnostics(
        query=clean_query, platform=result_platform, requested=requested, variants=variants
    )
    engine_names = ("so360", "ddg_html", "cn_bing", "sogou", "baidu")
    # Collect a small reserve so concrete detail pages found after an
    # aggregate/list result can displace low-value leads when the final pool
    # is ranked.  The public result remains capped at the caller's request.
    collection_target = min(MAX_SEARCH_CANDIDATES, requested + 5)
    # Web-wide search also enforces a per-registered-domain cap below. Let it
    # continue across the remaining engines when one host fills its quota. The
    # explicit legacy platform adapters keep their smaller per-variant share.
    variant_soft_cap = (
        MAX_SEARCH_CANDIDATES
        if result_platform == "web"
        else max(1, (collection_target + len(variants) - 1) // len(variants))
    )
    budget = diagnostics.get("budget")
    if isinstance(budget, dict):
        budget["variant_soft_cap"] = variant_soft_cap
    diagnostics["variant_candidate_counts"] = [0 for _ in variants]
    started = time.monotonic()
    # Probe the two known local proxy routes once for diagnostics.  The
    # result is not treated as proof that a target URL is safe, and this
    # collector keeps using the normal bounded search request policy below.
    try:
        web_search._proxy_alive(timeout=1.0)
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        diagnostics.setdefault("errors", []).append(
            {"stage": "route_probe", "error_type": type(exc).__name__, "message": _error_text(exc)}
        )
    diagnostics["route"] = {
        **web_search.route_diagnostics(),
        "used_for_search": False,
        "note": "仅记录本机代理探测；搜索候选仍按每个引擎的独立请求结果判定。",
    }
    results: list[dict[str, object]] = []
    fallback_results: list[dict[str, object]] = []
    domain_counts: dict[str, int] = {}
    domain_fallback_counts: dict[str, int] = {}
    domain_overflow_count = 0
    seen: set[str] = set()
    attempts: list[dict[str, object]] = diagnostics["attempts"]  # type: ignore[assignment]
    attempted_engines: list[str] = diagnostics["engines_attempted"]  # type: ignore[assignment]
    succeeded_engines: list[str] = diagnostics["engines_succeeded"]  # type: ignore[assignment]
    errors: list[dict[str, object]] = diagnostics["errors"]  # type: ignore[assignment]
    variant_counts: list[int] = diagnostics["variant_candidate_counts"]  # type: ignore[assignment]
    request_count = 0
    engine_request_count = 0
    redirect_request_count = 0
    redirect_resolution_count = 0
    failure_counts: dict[str, int] = diagnostics["failure_counts"]  # type: ignore[assignment]
    rejected_candidates: dict[str, int] = diagnostics["rejected_candidates"]  # type: ignore[assignment]
    redirect_attempts: list[dict[str, object]] = diagnostics["redirect_attempts"]  # type: ignore[assignment]

    def resolve_baidu(candidate: str) -> str:
        """Spend from the separate, small redirect budget for Baidu links."""

        nonlocal redirect_resolution_count
        if (
            redirect_resolution_count >= MAX_SEARCH_REDIRECT_REQUESTS
            or redirect_request_count >= MAX_SEARCH_REDIRECT_REQUESTS
            or request_count >= MAX_TOTAL_SEARCH_REQUESTS
            or time.monotonic() - started >= MAX_SEARCH_SECONDS - 0.25
        ):
            if not redirect_attempts or redirect_attempts[-1].get("status") != "budget_exhausted":
                redirect_attempts.append({"status": "budget_exhausted"})
            return ""
        redirect_resolution_count += 1

        def bounded_redirect_fetch(url: str, timeout: float):
            nonlocal request_count, redirect_request_count
            elapsed = time.monotonic() - started
            if (
                redirect_request_count >= MAX_SEARCH_REDIRECT_REQUESTS
                or request_count >= MAX_TOTAL_SEARCH_REQUESTS
                or elapsed >= MAX_SEARCH_SECONDS - 0.25
            ):
                redirect_attempts.append(
                    {"host": urlsplit(url).hostname or "", "status": "budget_exhausted"}
                )
                raise RuntimeError("重定向解析预算已用完")
            remaining = MAX_SEARCH_SECONDS - elapsed
            bounded_timeout = max(0.5, min(timeout, SEARCH_REDIRECT_TIMEOUT_SECONDS, remaining - 0.1))
            redirect_request_count += 1
            request_count += 1
            entry: dict[str, object] = {
                "host": (urlsplit(url).hostname or "").rstrip(".").casefold(),
                "status": "started",
            }
            try:
                response = _request_redirect(url, bounded_timeout)
                status_code = int(getattr(response, "status_code", 0) or 0)
                entry.update({"status": "redirect" if 300 <= status_code < 400 else "response", "http_status": status_code})
                redirect_attempts.append(entry)
                return response
            except MemoryError:
                raise
            except Exception as exc:  # noqa: BLE001
                entry.update({"status": "error", "error_type": type(exc).__name__})
                redirect_attempts.append(entry)
                errors.append(
                    {
                        "stage": "redirect_resolve",
                        "host": entry["host"],
                        "error_type": type(exc).__name__,
                        "message": _error_text(exc),
                    }
                )
                raise

        return _resolve_baidu_wrapper(
            candidate,
            timeout=SEARCH_REDIRECT_TIMEOUT_SECONDS,
            request=bounded_redirect_fetch,
        )

    for variant_index, variant in enumerate(variants):
        if engine_request_count >= MAX_SEARCH_REQUESTS or request_count >= MAX_TOTAL_SEARCH_REQUESTS or time.monotonic() - started >= MAX_SEARCH_SECONDS:
            break
        if result_platform == "web":
            targeted = variant
        else:
            targeted = f"{variant} site:{PLATFORMS[result_platform][1]}"
        engine_urls = {
            "so360": "https://www.so.com/s?" + urlencode({"q": targeted}),
            "ddg_html": "https://html.duckduckgo.com/html/?" + urlencode({"q": targeted}),
            "cn_bing": "https://cn.bing.com/search?" + urlencode({"q": targeted, "mkt": "zh-CN"}),
            "sogou": "https://www.sogou.com/web?" + urlencode({"query": targeted}),
            "baidu": "https://www.baidu.com/s?" + urlencode({"wd": targeted}),
        }
        for engine in engine_names:
            if variant_counts[variant_index] >= variant_soft_cap:
                break
            elapsed = time.monotonic() - started
            if engine_request_count >= MAX_SEARCH_REQUESTS or request_count >= MAX_TOTAL_SEARCH_REQUESTS or elapsed >= MAX_SEARCH_SECONDS:
                break
            remaining = MAX_SEARCH_SECONDS - elapsed
            # Leave a small hand-off margin for socket cleanup and response
            # decoding; the wall-clock budget should not be exceeded merely
            # because a final bounded request returned at its deadline.
            if remaining <= 1.0:
                break
            request_timeout = max(0.5, min(SEARCH_ENGINE_TIMEOUT_SECONDS, remaining - 0.25))
            request_count += 1
            engine_request_count += 1
            url = engine_urls[engine]
            if engine not in attempted_engines:
                attempted_engines.append(engine)
            attempt: dict[str, object] = {
                "variant": variant,
                "variant_index": variant_index,
                "engine": engine,
                "status": "started",
                "new_candidates": 0,
            }
            try:
                response = web_search._fetch(url, request_timeout)
                page_text = str(getattr(response, "text", "") or "")
                if _page_challenge(page_text):
                    failure_counts["challenge"] += 1
                    attempt.update({"status": "challenge", "result_count": 0})
                    attempts.append(attempt)
                    continue
                parsed_rows = _parse_links(
                    page_text,
                    result_platform,
                    wrapper_resolver=resolve_baidu,
                )
                added = 0
                quality_rows = 0
                for row in parsed_rows:
                    if variant_counts[variant_index] >= variant_soft_cap:
                        break
                    destination = str(row.get("url") or "").strip()
                    title = str(row.get("title") or "")[:MAX_SEARCH_TITLE_CHARS]
                    text = str(row.get("text") or "")[:MAX_SEARCH_SNIPPET_CHARS]
                    result_kind = str(row.get("result_kind") or "search_index")
                    accepted, relevance_score, rejection = _candidate_quality(
                        clean_query,
                        title=title,
                        text=text,
                        result_kind=result_kind,
                    )
                    if result_platform != "web" and rejection == "query_mismatch":
                        # The explicit legacy platform adapter already sends
                        # a role-scoped query to its site index. Keep its
                        # historical behavior for sparse platform snippets;
                        # the unrestricted web path applies the stricter
                        # cross-query relevance gate.
                        accepted, relevance_score, rejection = True, 0.5, None
                    if not accepted:
                        reason = rejection or "low_quality"
                        rejected_candidates[reason] = rejected_candidates.get(reason, 0) + 1
                        continue
                    quality_rows += 1
                    key = _dedupe_key(destination)
                    if not destination or key in seen:
                        continue
                    seen.add(key)
                    lead = {
                        "platform": "全网公开网页" if result_platform == "web" else PLATFORMS[result_platform][0],
                        "url": destination,
                        "title": title,
                        "text": text,
                        "method": f"公开搜索索引（{engine}）",
                        "result_kind": result_kind,
                        # Search output is a lead only.  ``fetch_url`` and
                        # the JD workflow provide source evidence later.
                        "source_kind": "search_lead",
                        "evidence_status": "unverified",
                        "readable": False,
                        "lead_only": True,
                        "query_variant": variant,
                        "engine": engine,
                        "_relevance_score": relevance_score,
                    }
                    domain = _domain_for_url(destination)
                    if result_platform == "web" and domain_counts.get(domain, 0) >= MAX_CANDIDATES_PER_DOMAIN:
                        domain_overflow_count += 1
                        if domain_fallback_counts.get(domain, 0) >= MAX_DOMAIN_FALLBACK_CANDIDATES:
                            continue
                        fallback_results.append(lead)
                        domain_fallback_counts[domain] = domain_fallback_counts.get(domain, 0) + 1
                        added += 1
                        continue
                    results.append(lead)
                    if result_platform == "web":
                        domain_counts[domain] = domain_counts.get(domain, 0) + 1
                    added += 1
                    variant_counts[variant_index] += 1
                    if len(results) >= collection_target:
                        break
                attempt.update(
                    {
                        "status": "ok" if quality_rows else "vacuum",
                        "result_count": len(parsed_rows),
                        "quality_result_count": quality_rows,
                        "new_candidates": added,
                    }
                )
                if quality_rows:
                    failure_counts["usable_response"] += 1
                else:
                    failure_counts["vacuum"] += 1
                if quality_rows and engine not in succeeded_engines:
                    succeeded_engines.append(engine)
            except MemoryError:
                raise
            except Exception as exc:  # noqa: BLE001
                response = getattr(exc, "response", None)
                status_code = getattr(response, "status_code", None)
                is_http_failure = isinstance(exc, httpx.HTTPStatusError) or (
                    isinstance(status_code, int) and status_code >= 400
                )
                failure_kind = "http_failure" if is_http_failure else "transport_failure"
                failure_counts[failure_kind] += 1
                attempt.update({"status": failure_kind, "error": _error_text(exc)})
                if isinstance(status_code, int):
                    attempt["http_status"] = status_code
                errors.append(
                    {
                        "variant": variant,
                        "variant_index": variant_index,
                        "engine": engine,
                        "failure_kind": failure_kind,
                        "http_status": status_code,
                        "error_type": type(exc).__name__,
                        "message": _error_text(exc),
                    }
                )
            if attempt not in attempts:
                attempts.append(attempt)
            if len(results) >= collection_target:
                break
        if len(results) >= collection_target:
            break

    # Detail pages are the useful next step for source evidence.  Keep
    # aggregate/search-index pages in the pool, but never let them consume the
    # requested slots ahead of concrete job-detail leads.
    def _lead_priority(item: dict[str, object]) -> tuple[int, int, float]:
        kind = str(item.get("result_kind") or "search_index")
        if kind != "job_detail":
            return (2, 0, -float(item.get("_relevance_score") or 0.0))
        # Preserve unrestricted public-web coverage, but prefer employer
        # career pages over known platform detail pages when both are present;
        # those pages are more likely to expose readable canonical JD text.
        return (
            1 if platform_for(str(item.get("url") or "")) else 0,
            0,
            -float(item.get("_relevance_score") or 0.0),
        )

    def _diversify_domains(items: list[dict[str, object]]) -> list[dict[str, object]]:
        ordered = sorted(items, key=_lead_priority)
        groups: dict[tuple[int, int], dict[str, list[dict[str, object]]]] = {}
        for item in ordered:
            priority = _lead_priority(item)
            group = (priority[0], priority[1])
            domain = _domain_for_url(str(item.get("url") or ""))
            groups.setdefault(group, {}).setdefault(domain, []).append(item)
        diversified: list[dict[str, object]] = []
        for group in sorted(groups):
            domains = groups[group]
            while any(domains.values()):
                for domain_items in domains.values():
                    if domain_items:
                        diversified.append(domain_items.pop(0))
        return diversified

    fallback_results.sort(key=_lead_priority)
    candidate_pool = list(results)
    fallback_needed = max(0, requested - len(candidate_pool))
    fallback_used = fallback_results[:fallback_needed]
    candidate_pool.extend(fallback_used)
    ranked_pool = _diversify_domains(candidate_pool)
    returned_results = ranked_pool[:requested]
    for item in returned_results:
        item.pop("_relevance_score", None)
    diagnostics["raw_candidate_count"] = len(results) + len(fallback_results)
    diagnostics["candidate_count"] = len(returned_results)
    diagnostics["deduplicated_count"] = len(seen)
    diagnostics["returned_count"] = len(returned_results)
    diagnostics["search_lead_count"] = len(returned_results)
    returned_domain_counts: dict[str, int] = {}
    for item in returned_results:
        domain = _domain_for_url(str(item.get("url") or ""))
        returned_domain_counts[domain] = returned_domain_counts.get(domain, 0) + 1
    diagnostics["domain_diversity"] = {
        "max_per_registered_domain": MAX_CANDIDATES_PER_DOMAIN if result_platform == "web" else None,
        "fallback_per_domain": MAX_DOMAIN_FALLBACK_CANDIDATES if result_platform == "web" else 0,
        "main_pool_counts": dict(domain_counts),
        "fallback_pool_counts": dict(domain_fallback_counts),
        "fallback_used_count": len(fallback_used),
        "overflow_count": domain_overflow_count,
        "returned_counts": returned_domain_counts,
    }
    diagnostics["request_count"] = request_count
    diagnostics["engine_request_count"] = engine_request_count
    diagnostics["redirect_request_count"] = redirect_request_count
    diagnostics["redirect_resolution_count"] = redirect_resolution_count
    diagnostics["elapsed_seconds"] = round(max(0.0, time.monotonic() - started), 3)
    diagnostics["route"] = {
        **web_search.route_diagnostics(),
        "used_for_search": False,
        "note": "仅记录本机代理探测；搜索候选仍按每个引擎的独立请求结果判定。",
    }
    if len(returned_results) >= requested:
        diagnostics["status"] = "ok"
        diagnostics["outcome"] = "results"
    elif returned_results:
        diagnostics["status"] = "partial"
        diagnostics["outcome"] = "partial_results"
    else:
        diagnostics["status"] = "failed"
        if failure_counts["challenge"] and not any(
            failure_counts[name] for name in ("usable_response", "vacuum", "http_failure", "transport_failure")
        ):
            diagnostics["outcome"] = "challenge"
        elif failure_counts["http_failure"] and not any(
            failure_counts[name] for name in ("usable_response", "vacuum", "challenge")
        ):
            diagnostics["outcome"] = "http_failure"
        elif failure_counts["vacuum"] and not any(
            failure_counts[name] for name in ("usable_response", "challenge")
        ):
            diagnostics["outcome"] = "vacuum"
        elif failure_counts["transport_failure"] and not any(
            failure_counts[name] for name in ("usable_response", "vacuum", "challenge", "http_failure")
        ):
            diagnostics["outcome"] = "transport_failure"
        else:
            diagnostics["outcome"] = "mixed_failure"
    if not returned_results:
        if result_platform == "web":
            outcome = diagnostics.get("outcome")
            if outcome == "challenge":
                message = "搜索引擎返回了验证码或安全校验页；请稍后重试，或粘贴企业招聘官网/招聘网站岗位链接或正文。"
            elif outcome == "http_failure":
                message = "搜索引擎请求被拒绝或返回 HTTP 错误；请稍后重试，或粘贴企业招聘官网/招聘网站岗位链接或正文。"
            elif outcome == "vacuum":
                message = "搜索引擎访问成功，但没有找到符合岗位主题的招聘详情或招聘集合页；请粘贴企业招聘官网/招聘网站岗位链接或正文。"
            else:
                message = "公开网页搜索未返回可核实的岗位详情或招聘索引页；请粘贴企业招聘官网/招聘网站岗位链接或正文。"
        else:
            message = "公开索引未返回可核实的平台岗位链接。可粘贴该平台 JD 文本和来源链接，或上传截图。"
        raise SearchError(message, diagnostics=diagnostics)
    return SearchResult(returned_results, diagnostics=diagnostics)


def import_text(url: str, text: str = "") -> dict[str, str]:
    platform = platform_for(url)
    if not platform:
        raise ValueError("请提供 BOSS 直聘、猎聘或智联招聘的 HTTPS 来源链接")
    supplied = bool(text.strip())
    if not supplied:
        current = url
        for _ in range(6):
            if platform_for(current) != platform:
                raise ValueError("来源跳转出了选定招聘平台")
            response = httpx.get(current, headers=web_search._HEADERS, timeout=10, follow_redirects=False)
            if response.is_redirect:
                current = urljoin(current, response.headers.get("location", ""))
                continue
            response.raise_for_status()
            body = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", response.text, flags=re.S | re.I)
            text = web_search._clean(body)
            break
        else:
            raise ValueError("来源跳转过多，请粘贴岗位正文")
    if len(text.strip()) < 30:
        raise ValueError("岗位正文过短或平台限制访问，请粘贴完整 JD 文本")
    return {
        "platform": PLATFORMS[platform][0],
        "url": url,
        "title": "导入岗位",
        "text": text.strip(),
        "method": "用户提供正文" if supplied else "公开页面读取",
    }
