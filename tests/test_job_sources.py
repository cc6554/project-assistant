from __future__ import annotations

import base64
from types import SimpleNamespace
from urllib.parse import quote

import httpx

from agent_coach.legacy.tools import job_sources


def _bing_wrapper(url: str) -> str:
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
    return "https://www.bing.com/ck/a?u=a1" + encoded


def test_search_parser_accepts_engine_wrappers_and_first_party_paths() -> None:
    boss_index = "https://m.zhipin.com/zhaopin/ef3b7aa5b7585a0b0nRz292_GQ~~/"
    boss_detail = "https://www.zhipin.com/job_detail/5332f41ba7a03619031839u8E1JT.html"
    source = f"""
      <div class="vrwrap"><h3 class="vr-title">
        <a href="//duckduckgo.com/l/?uddg={quote(boss_index, safe='')}&amp;rut=x">灵巧手算法工程师（深圳/北京）</a>
      </h3><div class="fz-mid">聚合页摘要</div></div>
      <li class="b_algo"><h2>
        <a data-mdurl="{boss_detail}" href="{_bing_wrapper(boss_detail)}">灵巧手算法工程师</a>
      </h2><p>岗位职责摘要</p></li>
      <a href="https://evil.zhipin.com/job_detail/wrong">错误 host</a>
      <a href="https://www.zhipin.com/">平台首页</a>
    """

    rows = job_sources._parse_links(source, "boss")

    assert [row["url"] for row in rows] == [boss_index, boss_detail]
    assert [row["result_kind"] for row in rows] == ["search_index", "job_detail"]
    assert rows[0]["text"]


def test_search_aggregates_public_engines_and_uses_data_mdurl(monkeypatch) -> None:
    detail_one = "https://www.zhipin.com/job_detail/one"
    detail_two = "https://www.zhipin.com/job_detail/two"
    index = "https://www.zhipin.com/zhaopin/index"
    responses = {
        "www.sogou.com": "<html><body>请输入验证码</body></html>",
        "www.so.com": (
            f'<div class="result"><h3><a href="https://www.so.com/link?m=opaque" '
            f'data-mdurl="{detail_one}">岗位一</a></h3></div>'
        ),
        "html.duckduckgo.com": (
            f'<a class="result__a" href="//duckduckgo.com/l/?uddg={quote(detail_two, safe="")}">岗位二</a>'
        ),
        "cn.bing.com": f'<li class="b_algo"><h2><a href="{index}">聚合页</a></h2></li>',
        "www.baidu.com": "<html></html>",
    }

    def fake_fetch(url: str, timeout: float):
        host = url.split("/", 3)[2]
        return SimpleNamespace(text=responses[host])

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)

    rows = job_sources.search("灵巧手算法工程师", platform="boss", n=3)

    assert [row["url"] for row in rows] == [detail_one, detail_two, index]
    assert [row["result_kind"] for row in rows] == ["job_detail", "job_detail", "search_index"]
    assert rows[0]["method"].endswith("so360）")


def test_platform_and_result_path_boundaries_are_explicit() -> None:
    assert job_sources.platform_for("HTTPS://WWW.ZHIPIN.COM/job_detail/one") == "boss"
    assert job_sources.platform_for("https://zhipin.com.evil.example/job_detail/one") is None
    assert job_sources.platform_for("https://evil.zhipin.com/job_detail/one") is None
    assert job_sources.result_kind("https://www.zhipin.com/", "boss") is None
    assert job_sources.result_kind("https://www.zhipin.com/companys/company", "boss") == "search_index"


def test_liepin_a_route_is_a_detail_surface_not_an_aggregate() -> None:
    url = "https://www.liepin.com/a/77763063.shtml"
    assert job_sources.platform_for(url) == "liepin"
    assert job_sources.result_kind(url, "liepin") == "job_detail"


def test_boss_query_only_job_pk_is_not_a_detail_candidate() -> None:
    assert job_sources.result_kind("https://www.zhipin.com/job_pk/123456", "boss") is None
    assert job_sources.result_kind(
        "https://www.zhipin.com/job_detail?job_pk=123456", "boss"
    ) is None
    assert job_sources.result_kind(
        "https://www.zhipin.com/job_detail/123456.html?job_pk=123456", "boss"
    ) == "job_detail"


def test_web_parser_accepts_enterprise_career_detail_and_labels_lists() -> None:
    detail = "https://careers.acme.test/careers/jobs/backend-123"
    listing = "https://jobs.board.test/search?query=backend"
    source = f"""
      <div class="result"><a href="{detail}">Python 后端工程师招聘</a><span>任职要求：Python、服务开发</span></div>
      <div class="result"><a href="{listing}">后端岗位列表</a><span>招聘职位搜索结果</span></div>
      <div class="result"><a href="https://news.example.test/story/1">后端工程师新闻</a><span>公司新闻</span></div>
    """

    rows = job_sources._parse_links(source, "web")

    assert [row["url"] for row in rows] == [detail, listing]
    assert [row["result_kind"] for row in rows] == ["job_detail", "search_index"]


def test_html_entities_are_not_unescaped_twice_in_result_links() -> None:
    source = (
        '<div class="result"><a href="https://careers.acme.test/jobs/one?src=search'
        '&amp;timestamp=1700000000">Python 后端工程师招聘</a>'
        "<span>岗位要求 Python</span></div>"
    )

    rows = job_sources._parse_links(source, "web")

    assert rows[0]["url"] == (
        "https://careers.acme.test/jobs/one?src=search&timestamp=1700000000"
    )


def test_baidu_http_wrapper_is_upgraded_to_https_before_resolution() -> None:
    assert job_sources._destination(
        "http://www.baidu.com/link?url=opaque&timestamp=1"
    ) == "https://www.baidu.com/link?url=opaque&timestamp=1"


def test_signature_and_timestamp_query_values_do_not_split_one_source() -> None:
    first = "https://mp.example.test/s/jobs/one?timestamp=1&signature=abc&scene=1"
    second = "https://mp.example.test/s/jobs/one?timestamp=2&signature=xyz&scene=1"

    assert job_sources._dedupe_key(first) == job_sources._dedupe_key(second)


def test_web_search_does_not_append_site_constraint(monkeypatch) -> None:
    seen_queries: list[str] = []
    detail = "https://careers.acme.test/jobs/backend-123"

    def fake_fetch(url: str, timeout: float):
        from types import SimpleNamespace

        query = url.split("?", 1)[1]
        seen_queries.append(query)
        if "so.com" in url:
            return SimpleNamespace(
                text=f'<div class="result"><a href="{detail}">Python 后端工程师招聘</a><span>岗位要求 Python</span></div>'
            )
        return SimpleNamespace(text="<html></html>")

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)
    rows = job_sources.search("Python 后端工程师", platform="web", n=1)

    assert rows[0]["platform"] == "全网公开网页"
    assert rows[0]["result_kind"] == "job_detail"
    assert seen_queries
    assert all("site:" not in value.lower() for value in seen_queries)


def test_public_source_url_allows_enterprise_site_and_rejects_credentials_private_hosts() -> None:
    assert job_sources.public_source_url("https://careers.acme.test/jobs/backend-123") == (
        "https://careers.acme.test/jobs/backend-123"
    )
    assert job_sources.public_source_url("https://careers.acme.test/#/job/backend-123") == (
        "https://careers.acme.test/#/job/backend-123"
    )
    for value in (
        "http://careers.acme.test/jobs/1",
        "https://user:pass@careers.acme.test/jobs/1",
        "https://127.0.0.1/jobs/1",
        "https://localhost/jobs/1",
        "https://careers.acme.test/jobs/1?token=secret",
        "https://example.com/job/1",
    ):
        try:
            job_sources.public_source_url(value)
        except ValueError:
            continue
        raise AssertionError(f"unsafe or placeholder URL accepted: {value}")


def test_search_does_not_swallow_memory_error(monkeypatch) -> None:
    def fail(_url: str, _timeout: float):
        raise MemoryError("simulated allocator failure")

    monkeypatch.setattr(job_sources.web_search, "_fetch", fail)
    try:
        job_sources.search("Python 后端工程师", platform="web", n=1)
    except MemoryError:
        pass
    else:
        raise AssertionError("MemoryError must not be converted to an empty search")


def test_bounded_fetch_does_not_double_decode_content_encoding(monkeypatch) -> None:
    class FakeStream:
        status_code = 200
        headers = httpx.Headers(
            {
                "content-encoding": "gzip",
                "content-length": "999999",
                "transfer-encoding": "chunked",
            }
        )
        request = httpx.Request("GET", "https://search.test")
        extensions = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self, *, chunk_size: int):
            del chunk_size
            # httpx has already decoded this entity before the collector sees
            # it. The reconstructed response must not advertise gzip again.
            yield "岗位详情：Python 后端工程师".encode("utf-8")

    monkeypatch.setattr(job_sources.web_search.httpx, "stream", lambda *args, **kwargs: FakeStream())
    response = job_sources.web_search._fetch("https://search.test", 3)

    assert response.text == "岗位详情：Python 后端工程师"
    assert "content-encoding" not in response.headers
    assert response.headers.get("content-length") == str(len("岗位详情：Python 后端工程师".encode("utf-8")))
