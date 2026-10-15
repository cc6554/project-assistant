"""通用网页搜索：多搜索引擎 fallback，免费、无需 key，只抓搜索摘要。

用于「输入岗位名 → 搜索招聘 JD 文本」流程。返回每条结果的标题 + 摘要，
由 jd_parser 的文本版抽取为结构化岗位卡片。

源顺序（依次尝试，第一个拿到「招聘相关」结果的源即停止）：
  1. 搜狗（www.sogou.com/web，国内直连快、结果相关性好，摘要常含岗位要求全文）
  2. DuckDuckGo HTML（html.duckduckgo.com/html/，国际网络下质量好；本机有 Clash 代理时自动走代理）
  3. 必应国内版（cn.bing.com/search，无 Cookie 时结果质量差，仅作兜底）

每个源独立超时；结果做招聘语境过滤，过滤后为空视为该源无效，继续换源。
"""

from __future__ import annotations

import html as html_mod
import re
import urllib.parse

import httpx

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


def _fetch(url: str, timeout: float, proxy: str | None = None) -> httpx.Response:
    resp = httpx.get(
        url,
        headers=_HEADERS,
        timeout=timeout,
        follow_redirects=True,
        proxy=proxy,
    )
    resp.raise_for_status()
    return resp


def _proxy_alive(timeout: float = 1.5) -> str | None:
    """本机 Clash 代理是否在跑；在则返回代理地址（用于走代理访问国际源）。"""
    for addr in ("http://127.0.0.1:7890", "http://127.0.0.1:7897"):
        try:
            httpx.get("http://www.gstatic.com/generate_204", timeout=timeout, proxy=addr)
            return addr
        except Exception:  # noqa: BLE001
            continue
    return None


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
    proxy = _proxy_alive()
    last_err: Exception | None = None
    sources: list[tuple[str, object]] = [
        ("sogou", lambda: _from_sogou(query, n, timeout)),
        ("ddg_html", lambda: _from_ddg_html(query, n, timeout, proxy)),
        ("cn_bing", lambda: _from_bing(query, n, timeout)),
    ]
    for name, fn in sources:
        try:
            results = fn()  # type: ignore[misc]
            if not results:
                last_err = RuntimeError(f"{name} 无结果")
                continue
            filtered = [r for r in results if _relevant(r)]
            if filtered:
                return filtered[:n]
            last_err = RuntimeError(f"{name} 结果与招聘无关")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            continue
    raise RuntimeError(f"搜索请求失败：{last_err}")
