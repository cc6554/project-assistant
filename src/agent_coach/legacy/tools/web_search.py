"""通用网页搜索：多搜索引擎 fallback，免费、无需 key，只抓搜索摘要。

用于「输入岗位名 → 搜索招聘 JD 文本」流程。返回每条结果的标题 + 摘要，
由 jd_parser 的文本版抽取为结构化岗位卡片。

源顺序（由上层岗位检索器按有界轮次尝试）：
  1. 搜狗（www.sogou.com/web，国内直连快、结果相关性好，摘要常含岗位要求全文）
  2. DuckDuckGo HTML（html.duckduckgo.com/html/，国际网络下质量好；本机有 Clash 代理时自动走代理）
  3. 必应国内版（cn.bing.com/search，无 Cookie 时结果质量差，仅作兜底）

每个源独立超时；结果做招聘语境过滤，过滤后为空视为该源无效，继续换源。
"""

from __future__ import annotations

import html as html_mod
import re
import threading
import urllib.parse

import httpx


# Search engines are untrusted inputs.  Do not let an error page, an HTML
# challenge, or a server that advertises an absurd Content-Length consume an
# unbounded amount of memory before the result parser gets a chance to reject
# it.  This limit is deliberately independent from the larger, user-facing
# ``fetch_url`` limit: search only needs titles and snippets.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 15.0
ROUTE_PROBE_TIMEOUT_SECONDS = 1.5

# The route probe is intentionally diagnostic-only.  A successful probe never
# bypasses the public-host checks in ``fetch_url`` and is never used as proof
# that an arbitrary target URL is safe to fetch.
_LAST_ROUTE_DIAGNOSTICS: dict[str, object] = {
    "selected_proxy": None,
    "probes": [],
}
_LAST_SEARCH_DIAGNOSTICS: dict[str, object] = {}
_ROUTE_LOCAL = threading.local()
_SEARCH_LOCAL = threading.local()


def _store_route_diagnostics(value: dict[str, object]) -> None:
    global _LAST_ROUTE_DIAGNOSTICS
    snapshot = dict(value)
    probes = snapshot.get("probes")
    if isinstance(probes, list):
        snapshot["probes"] = [dict(item) if isinstance(item, dict) else item for item in probes]
    _LAST_ROUTE_DIAGNOSTICS = snapshot
    _ROUTE_LOCAL.value = snapshot


def _store_search_diagnostics(value: dict[str, object]) -> None:
    global _LAST_SEARCH_DIAGNOSTICS
    _LAST_SEARCH_DIAGNOSTICS = value
    _SEARCH_LOCAL.value = value

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_HEADERS = {
    "User-Agent": _UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 搜狗结果块：<div class="vrwrap"> ... <h3 class="vr-title"><a>标题</a></h3> ... <div class="fz-mid ...">摘要</div>
_SOGOU_BLOCK_RE = re.compile(
    r'<div class="vrwrap"[^>]*>.*?<h3 class="vr-title">.*?<a[^>]*>(.*?)</a></h3>'
    r".*?(?:<div class=\"fz-mid[^\"]*\"[^>]*>(.*?)</div>)?",
    re.S,
)
_SOGOU_ANTISPIDER = ("antispider", "请输入验证码", "验证码")
_DDG_HTML_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
    re.S,
)
_BING_RE = re.compile(
    r'<li class="b_algo"[^>]*>.*?<h2[^>]*><a[^>]*href="([^"]+)"[^>]*>(.*?)</a></h2>'
    r'.*?(?:<p[^>]*>(.*?)</p>)?</li>',
    re.S,
)

# 招聘语境过滤
_RELEVANT_HINTS = ("招聘", "岗位", "职位", "要求", "职责", "薪资", "待遇", "校招", "社招", "JD", "工程师", "developer", "engineer")
_NOISE_HINTS = ("百科", "图片", "健康", "医院", "文库", "地图", "视频", "音乐", "论文", "词典")


def _clean(text: str) -> str:
    text = html_mod.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _relevant(line: str) -> bool:
    lower = line.lower()
    return any(h.lower() in lower for h in _RELEVANT_HINTS) and not any(h in line for h in _NOISE_HINTS)


def _safe_exception_text(exc: BaseException) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ").strip() or type(exc).__name__
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", text)
    text = re.sub(
        r"(?i)([?&](?:token|access_token|api_key|apikey|key|auth|authorization)=)[^&\s]+",
        r"\1[redacted]",
        text,
    )
    return text[:240]


def _fetch(url: str, timeout: float, proxy: str | None = None) -> httpx.Response:
    """Fetch one public search page with a bounded response body.

    ``httpx.get`` buffers the complete response before returning.  Search
    pages are external, so use the streaming API and construct a small,
    in-memory response only after enforcing the byte cap.  ``MemoryError`` is
    intentionally not caught here (or by callers): a process-level allocation
    failure must remain visible instead of being reported as an honest
    zero-result search.
    """

    try:
        bounded_timeout = max(0.5, min(float(timeout), MAX_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        bounded_timeout = 8.0
    with httpx.stream(
        "GET",
        url,
        headers=_HEADERS,
        timeout=bounded_timeout,
        follow_redirects=True,
        proxy=proxy,
        # Preserve an explicitly configured enterprise route for search
        # engines.  The separate ``fetch_url`` tool still disables environment
        # proxies and performs DNS validation for every user target; this
        # search-only request must not silently override a user's route.
        trust_env=True,
    ) as response:
        response.raise_for_status()
        content_length = response.headers.get("Content-Length")
        try:
            advertised = int(content_length) if content_length is not None else None
        except (TypeError, ValueError):
            advertised = None
        if advertised is not None and advertised > MAX_RESPONSE_BYTES:
            raise RuntimeError("搜索响应超过大小上限")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes(chunk_size=64 * 1024):
            if not chunk:
                continue
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise RuntimeError("搜索响应超过大小上限")
            chunks.append(chunk)
        # ``iter_bytes`` returns the decoded entity body.  Do not carry
        # Content-Encoding/Transfer-Encoding into the reconstructed response,
        # otherwise httpx would try to decompress the bytes a second time
        # when callers access ``.text``.  Content-Length is likewise removed
        # because the buffered length can differ from the wire length.
        response_headers = response.headers.copy()
        for header in ("content-encoding", "transfer-encoding", "content-length"):
            response_headers.pop(header, None)
        return httpx.Response(
            response.status_code,
            headers=response_headers,
            content=b"".join(chunks),
            request=response.request,
            extensions=response.extensions,
        )


def _proxy_alive(timeout: float = 1.5) -> str | None:
    """本机 Clash 代理是否在跑；在则返回代理地址（用于走代理访问国际源）。"""
    global _LAST_ROUTE_DIAGNOSTICS
    try:
        bounded_timeout = max(0.5, min(float(timeout), ROUTE_PROBE_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        bounded_timeout = ROUTE_PROBE_TIMEOUT_SECONDS
    probes: list[dict[str, object]] = []
    for addr in ("http://127.0.0.1:7890", "http://127.0.0.1:7897"):
        try:
            response = httpx.get(
                "http://www.gstatic.com/generate_204",
                timeout=bounded_timeout,
                proxy=addr,
                follow_redirects=False,
                # Never let an environment proxy replace the route we are
                # diagnosing.  This is a local, fixed-address probe only.
                trust_env=False,
            )
            status = int(getattr(response, "status_code", 0) or 0)
            ok = 200 <= status < 400
            probes.append({"proxy": addr, "status": status, "ok": ok})
            if ok:
                _store_route_diagnostics({
                    "selected_proxy": addr,
                    "probes": probes,
                    "timeout_seconds": bounded_timeout,
                    "trust_env": False,
                })
                return addr
        except MemoryError:
            raise
        except Exception as exc:  # noqa: BLE001
            # Keep the diagnostic useful without exposing arbitrary exception
            # payloads (which may contain proxy credentials in some clients).
            probes.append({"proxy": addr, "ok": False, "error": type(exc).__name__})
            continue
    _store_route_diagnostics({
        "selected_proxy": None,
        "probes": probes,
        "timeout_seconds": bounded_timeout,
        "trust_env": False,
    })
    return None


def route_diagnostics() -> dict[str, object]:
    """Return the latest bounded proxy-route probe without secrets.

    The result is intentionally a snapshot of *transport diagnostics*, not a
    claim that any target page is public or readable.  Callers still need the
    normal URL/DNS validation before fetching a user-provided URL.
    """

    value = dict(getattr(_ROUTE_LOCAL, "value", _LAST_ROUTE_DIAGNOSTICS))
    probes = value.get("probes")
    if isinstance(probes, list):
        value["probes"] = [dict(item) if isinstance(item, dict) else item for item in probes]
    return value


def search_texts_diagnostics() -> dict[str, object]:
    """Return the latest multi-engine text-search attempt snapshot."""

    value = dict(getattr(_SEARCH_LOCAL, "value", _LAST_SEARCH_DIAGNOSTICS))
    attempts = value.get("attempts")
    if isinstance(attempts, list):
        value["attempts"] = [dict(item) if isinstance(item, dict) else item for item in attempts]
    return value


def _from_sogou(query: str, n: int, timeout: float) -> list[str]:
    url = "https://www.sogou.com/web?" + urllib.parse.urlencode({"query": query})
    html_text = _fetch(url, timeout).text
    if any(s in html_text for s in _SOGOU_ANTISPIDER):
        raise RuntimeError("搜狗触发验证码")
    out: list[str] = []
    for m in _SOGOU_BLOCK_RE.finditer(html_text):
        title = _clean(m.group(1))
        if not title:
            continue
        snippet = _clean(m.group(2) or "")
        out.append(f"{title}：{snippet}" if snippet else title)
        if len(out) >= n:
            break
    return out


def _from_ddg_html(query: str, n: int, timeout: float, proxy: str | None = None) -> list[str]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
        {"q": query, "kl": "cn-zh"}
    )
    html_text = _fetch(url, timeout, proxy).text
    out: list[str] = []
    for m in _DDG_HTML_RE.finditer(html_text):
        title = _clean(m.group(2))
        if not title:
            continue
        snippet = _clean(m.group(3))
        out.append(f"{title}：{snippet}" if snippet else title)
        if len(out) >= n:
            break
    return out


def _from_bing(query: str, n: int, timeout: float) -> list[str]:
    url = "https://cn.bing.com/search?" + urllib.parse.urlencode({"q": query, "mkt": "zh-CN"})
    html_text = _fetch(url, timeout).text
    out: list[str] = []
    for m in _BING_RE.finditer(html_text):
        title = _clean(m.group(2))
        if not title:
            continue
        snippet = _clean(m.group(3))
        out.append(f"{title}：{snippet}" if snippet else title)
        if len(out) >= n:
            break
    return out


def search_texts(query: str, n: int = 8, *, timeout: float = 6.0) -> list[str]:
    """依次尝试多个搜索源，返回「标题：摘要」列表（首个拿到招聘相关结果的源即止）。

    Args:
        query: 搜索词。
        n: 期望结果条数（可能少于 n，取决于各源实际返回）。
        timeout: 单源单次超时（秒）。

    Raises:
        RuntimeError: 所有源都失败或结果都不相关时。
    """
    global _LAST_SEARCH_DIAGNOSTICS
    proxy = _proxy_alive()
    diagnostics: dict[str, object] = {
        "query": str(query),
        "requested": int(n) if isinstance(n, int) else n,
        "attempts": [],
        "route": route_diagnostics(),
        "status": "pending",
    }
    _store_search_diagnostics(diagnostics)
    last_err: Exception | None = None
    sources: list[tuple[str, object]] = [
        ("sogou", lambda: _from_sogou(query, n, timeout)),
        ("ddg_html", lambda: _from_ddg_html(query, n, timeout, proxy)),
        ("cn_bing", lambda: _from_bing(query, n, timeout)),
    ]
    for name, fn in sources:
        attempt: dict[str, object] = {"source": name, "status": "started"}
        diagnostics["attempts"].append(attempt)  # type: ignore[union-attr]
        try:
            results = fn()  # type: ignore[misc]
            if not results:
                last_err = RuntimeError(f"{name} 无结果")
                attempt.update({"status": "empty", "result_count": 0})
                continue
            filtered = [r for r in results if _relevant(r)]
            if filtered:
                attempt.update({"status": "ok", "result_count": len(filtered)})
                diagnostics["status"] = "ok"
                diagnostics["returned_count"] = min(len(filtered), n)
                diagnostics["route"] = route_diagnostics()
                return filtered[:n]
            last_err = RuntimeError(f"{name} 结果与招聘无关")
            attempt.update({"status": "irrelevant", "result_count": len(results)})
        except MemoryError:
            # Never turn an allocator failure into a misleading "no jobs"
            # response.  The caller can decide whether to abort the operation.
            raise
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            attempt.update(
                {"status": "error", "error_type": type(exc).__name__, "message": _safe_exception_text(exc)}
            )
            continue
    diagnostics["status"] = "failed"
    diagnostics["route"] = route_diagnostics()
    diagnostics["error"] = _safe_exception_text(last_err) if last_err else "unknown"
    raise RuntimeError(f"搜索请求失败：{diagnostics['error']}")
