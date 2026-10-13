"""Public indexed recruitment sources and explicit user-provided JD imports."""
import base64
import html
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import httpx

from . import web_search

PLATFORMS = {"boss": ("BOSS 直聘", "zhipin.com"), "liepin": ("猎聘", "liepin.com"), "zhilian": ("智联招聘", "zhaopin.com")}


def platform_for(url: str) -> str | None:
    try:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password:
            return None
        host = parts.hostname or ""
        return next((key for key, (_, domain) in PLATFORMS.items() if host == domain or host.endswith("." + domain)), None)
    except ValueError:
        return None


def _destination(url: str) -> str:
    url = html.unescape(url)
    query = parse_qs(urlsplit(url).query)
    if "uddg" in query:
        return query["uddg"][0]
    encoded = query.get("u", [""])[0]
    if encoded.startswith("a1"):
        try:
            return base64.urlsafe_b64decode(encoded[2:] + "=" * (-len(encoded[2:]) % 4)).decode()
        except (ValueError, UnicodeError):
            return ""
    return url


def search(query: str, platform: str = "boss", n: int = 6) -> list[dict[str, str]]:
    if platform not in PLATFORMS:
        raise ValueError("请选择支持的招聘平台")
    domain = PLATFORMS[platform][1]
    # Remove caller/model site operators so the selected source remains authoritative.
    query = re.sub(r"\bsite:\S+", "", query)
    targeted = f"{query} 招聘 岗位要求 site:{domain}"
    engines = [
        ("https://html.duckduckgo.com/html/?" + urlencode({"q": targeted}), web_search._DDG_HTML_RE),
        ("https://cn.bing.com/search?" + urlencode({"q": targeted}), web_search._BING_RE),
    ]
    for url, pattern in engines:
        try:
            response = web_search._fetch(url, 8.0)
            results = []
            seen = set()
            for match in pattern.finditer(response.text):
                destination = _destination(match.group(1))
                if platform_for(destination) != platform or destination in seen:
                    continue
                seen.add(destination)
                results.append({"platform": PLATFORMS[platform][0], "url": destination,
                                "title": web_search._clean(match.group(2)),
                                "text": web_search._clean(match.group(3) or ""), "method": "公开搜索索引"})
                if len(results) >= n:
                    break
            if results:
                return results
        except (httpx.HTTPError, ValueError):
            continue
    raise ValueError("公开索引未返回可核实的平台岗位链接。可粘贴该平台 JD 文本和来源链接，或上传截图。")


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
    return {"platform": PLATFORMS[platform][0], "url": url, "title": "导入岗位",
            "text": text.strip(), "method": "用户提供正文" if supplied else "公开页面读取"}
