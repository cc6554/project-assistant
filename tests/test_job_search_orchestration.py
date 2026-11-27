from __future__ import annotations

import json
import socket
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import httpx

from agent_coach import integrations
from agent_coach.legacy.tools import job_sources
from agent_coach.legacy.tools import web_search


def _result_html(index: int, *, duplicate: bool = False) -> str:
    url_index = 0 if duplicate else index
    url = f"https://careers-{url_index // 5}.test/jobs/algorithm-{url_index}?utm_source=engine"
    return (
        '<div class="result"><a href="{url}">灵巧手算法工程师招聘 {index}</a>'
        '<span>岗位要求：Python、机器人算法与任职要求</span></div>'
    ).format(url=url, index=index)


def test_query_variants_are_deterministic_and_do_not_invent_scope() -> None:
    variants = job_sources.query_variants("灵巧手算法工程师")
    assert variants == job_sources.query_variants("灵巧手算法工程师")
    assert len(variants) == 4
    assert variants[1] == '"灵巧手算法工程师" 任职要求'
    assert all("深圳" not in item and "北京" not in item for item in variants)
    assert all("后端" not in item for item in variants)
    assert all("site:" not in item.casefold() for item in variants)
    assert any("招聘官网" in item for item in variants)


def test_candidate_quality_allows_related_titles_but_rejects_wrong_query_noise() -> None:
    related = job_sources._candidate_quality(
        "灵巧操作算法工程师",
        title="具身智能算法工程师（灵巧手方向）",
        text="工作内容包括灵巧手控制与操作算法",
        result_kind="job_detail",
    )
    unrelated = job_sources._candidate_quality(
        "财务分析师",
        title="Rust 平台工程师招聘",
        text="岗位要求 Rust 与分布式系统经验",
        result_kind="job_detail",
    )
    comparison = job_sources._candidate_quality(
        "Rust 平台工程师",
        title="Rust 平台工程师排行榜与对比",
        text="招聘岗位要求与任职资格",
        result_kind="search_index",
    )
    cplusplus = job_sources._candidate_quality(
        "C++ developer",
        title="C++ developer hiring",
        text="Role requirements include C++",
        result_kind="job_detail",
    )

    assert related[0] is True
    assert unrelated == (False, 0.0, "query_mismatch")
    assert comparison == (False, 0.0, "comparison_page")
    assert cplusplus[0] is True


def test_search_keeps_matching_official_jobs_collection_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_sources.web_search, "_proxy_alive", lambda **_kwargs: None)
    monkeypatch.setattr(job_sources.web_search, "route_diagnostics", lambda: {})
    page = (
        '<div class="result"><a href="https://careers.acme.test/jobs.html">'
        "招聘岗位列表</a><span>Rust 平台工程师招聘，岗位要求 Rust</span></div>"
    )
    monkeypatch.setattr(
        job_sources.web_search,
        "_fetch",
        lambda *_args, **_kwargs: SimpleNamespace(text=page),
    )

    result = job_sources.search("Rust 平台工程师", n=1)

    assert result
    assert result[0]["url"] == "https://careers.acme.test/jobs.html"
    assert result[0]["result_kind"] == "search_index"


def test_search_caps_one_registered_domain_and_continues_other_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(job_sources.web_search, "_proxy_alive", lambda **_kwargs: None)
    monkeypatch.setattr(job_sources.web_search, "route_diagnostics", lambda: {})
    calls: list[str] = []

    def result_page(domain: str, prefix: str, count: int) -> str:
        return "".join(
            f'<div class="result"><a href="https://{domain}/jobs/{prefix}-{index}">'
            f"Rust platform engineer hiring {index}</a>"
            "<span>Role requirements: Rust platform systems</span></div>"
            for index in range(count)
        )

    pages = {
        "www.so.com": result_page("www.liepin.com", "liepin", 12),
        "html.duckduckgo.com": result_page("careers.acme.test", "acme", 6),
        "cn.bing.com": result_page("jobs.startup.test", "startup", 6),
    }

    def fake_fetch(url: str, _timeout: float):
        host = urlsplit(url).hostname or ""
        calls.append(host)
        return SimpleNamespace(text=pages.get(host, "<html></html>"))

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)

    result = job_sources.search("Rust platform engineer", n=10)

    main_counts = result.diagnostics["domain_diversity"]["main_pool_counts"]
    returned_counts = result.diagnostics["domain_diversity"]["returned_counts"]
    assert calls[:3] == ["www.so.com", "html.duckduckgo.com", "cn.bing.com"]
    assert main_counts == {"liepin.com": 5, "acme.test": 5, "startup.test": 5}
    assert all(count <= job_sources.MAX_CANDIDATES_PER_DOMAIN for count in returned_counts.values())
    assert set(returned_counts) == {"acme.test", "startup.test"}


def test_search_uses_small_domain_fallback_when_only_one_domain_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(job_sources.web_search, "_proxy_alive", lambda **_kwargs: None)
    monkeypatch.setattr(job_sources.web_search, "route_diagnostics", lambda: {})
    page = "".join(
        f'<div class="result"><a href="https://www.liepin.com/job/rust-{index}">'
        f"Rust platform engineer hiring {index}</a>"
        "<span>Role requirements: Rust platform systems</span></div>"
        for index in range(20)
    )
    monkeypatch.setattr(
        job_sources.web_search,
        "_fetch",
        lambda *_args, **_kwargs: SimpleNamespace(text=page),
    )

    result = job_sources.search("Rust platform engineer", n=10)

    assert len(result) == job_sources.MAX_CANDIDATES_PER_DOMAIN + job_sources.MAX_DOMAIN_FALLBACK_CANDIDATES
    assert result.diagnostics["domain_diversity"]["main_pool_counts"] == {"liepin.com": 5}
    assert result.diagnostics["domain_diversity"]["fallback_pool_counts"] == {"liepin.com": 3}
    assert result.diagnostics["domain_diversity"]["fallback_used_count"] == 3


def test_search_collects_deduped_leads_and_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, timeout: float):
        del timeout
        host = urlsplit(url).netloc
        calls.append(url)
        params = parse_qs(urlsplit(url).query)
        query = (params.get("q") or params.get("query") or params.get("wd") or [""])[0]
        variant_base = {
            "灵巧手算法工程师": 0,
            '"灵巧手算法工程师" 任职要求': 5,
            "灵巧手算法工程师 招聘 岗位要求": 10,
            "灵巧手算法工程师 招聘官网 职位": 15,
        }.get(query, 0)
        engine_offset = {"www.so.com": 0, "html.duckduckgo.com": 1, "cn.bing.com": 2, "www.sogou.com": 3, "www.baidu.com": 4}.get(host, 0)
        return SimpleNamespace(text=_result_html(variant_base + engine_offset))

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)
    result = job_sources.search("灵巧手算法工程师", platform="web", n=18)

    assert isinstance(result, job_sources.SearchResult)
    assert len(result) == 18
    assert len({item["url"] for item in result}) == 18
    assert all(item["source_kind"] == "search_lead" for item in result)
    assert all(item["evidence_status"] == "unverified" for item in result)
    assert all(item["readable"] is False for item in result)
    assert result.diagnostics["search_lead_count"] == 18
    assert result.diagnostics["source_evidence_count"] == 0
    assert all(count > 0 for count in result.diagnostics["variant_candidate_counts"])
    assert result.diagnostics["request_count"] >= 4
    assert result.diagnostics["status"] in {"ok", "partial"}
    assert all("site:" not in url.lower() for url in calls)


def test_search_records_engine_errors_without_hiding_partial_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, timeout: float):
        del timeout
        host = urlsplit(url).netloc
        if host == "www.so.com":
            raise RuntimeError("simulated route failure")
        return SimpleNamespace(text=_result_html({"html.duckduckgo.com": 1}.get(host, 2)))

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)
    result = job_sources.search("灵巧手算法工程师", n=2)

    assert len(result) == 2
    errors = result.diagnostics["errors"]
    assert isinstance(errors, list)
    assert errors[0]["engine"] == "so360"
    assert result.diagnostics["status"] == "ok"


def test_baidu_opaque_link_is_resolved_with_public_dns_and_shared_time_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(job_sources.web_search, "_proxy_alive", lambda **_kwargs: None)
    monkeypatch.setattr(job_sources.web_search, "route_diagnostics", lambda: {})
    monkeypatch.setattr(job_sources, "_dns_resolves_public", lambda _host: True)
    resolved_target = "https://careers.acme.test/jobs/rust-platform-1"
    wrapper = "https://www.baidu.com/link?url=opaque&timestamp=1700000000"
    redirect_calls: list[str] = []

    def redirect_fetch(url: str, timeout: float):
        assert timeout <= job_sources.SEARCH_REDIRECT_TIMEOUT_SECONDS
        redirect_calls.append(url)
        return SimpleNamespace(status_code=302, headers={"location": resolved_target}, text="")

    monkeypatch.setattr(job_sources, "_request_redirect", redirect_fetch)

    def search_fetch(url: str, _timeout: float):
        if urlsplit(url).hostname == "www.baidu.com":
            return SimpleNamespace(
                text=(
                    f'<div class="result"><a href="{wrapper.replace("&", "&amp;")}">Rust 平台工程师招聘</a>'
                    "<span>岗位要求 Rust 与分布式系统</span></div>"
                )
            )
        return SimpleNamespace(text="<html><body>no results</body></html>")

    monkeypatch.setattr(job_sources.web_search, "_fetch", search_fetch)

    result = job_sources.search("Rust 平台工程师", n=1)

    assert result[0]["url"] == resolved_target
    assert redirect_calls == [wrapper] * len(job_sources.query_variants("Rust 平台工程师"))
    assert result.diagnostics["engine_request_count"] == job_sources.MAX_SEARCH_REQUESTS
    assert result.diagnostics["redirect_request_count"] == len(redirect_calls)
    assert result.diagnostics["request_count"] == (
        result.diagnostics["engine_request_count"] + result.diagnostics["redirect_request_count"]
    )
    assert result.diagnostics["request_count"] <= job_sources.MAX_TOTAL_SEARCH_REQUESTS


def test_redirect_budget_does_not_starve_search_engine_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(job_sources.web_search, "_proxy_alive", lambda **_kwargs: None)
    monkeypatch.setattr(job_sources.web_search, "route_diagnostics", lambda: {})
    monkeypatch.setattr(job_sources, "_dns_resolves_public", lambda _host: True)
    page_parts = []
    for index in range(job_sources.MAX_SEARCH_REDIRECT_REQUESTS + 4):
        wrapper = f"https://www.baidu.com/link?url=opaque-{index}&amp;timestamp={index}"
        page_parts.append(
            f'<div class="result"><a href="{wrapper}">Rust platform engineer hiring {index}</a>'
            "<span>Role requirements Rust platform systems</span></div>"
        )
    baidu_page = "".join(page_parts)

    def redirect_fetch(url: str, _timeout: float):
        index = int(urlsplit(url).query.split("opaque-", 1)[1].split("&", 1)[0])
        target = f"https://careers.acme.test/jobs/platform-{index}"
        return SimpleNamespace(status_code=302, headers={"location": target}, text="")

    monkeypatch.setattr(job_sources, "_request_redirect", redirect_fetch)
    monkeypatch.setattr(
        job_sources.web_search,
        "_fetch",
        lambda url, _timeout: SimpleNamespace(
            text=baidu_page if urlsplit(url).hostname == "www.baidu.com" else "<html></html>"
        ),
    )

    result = job_sources.search("Rust platform engineer", n=1)

    assert result
    assert result.diagnostics["redirect_request_count"] == job_sources.MAX_SEARCH_REDIRECT_REQUESTS
    assert result.diagnostics["engine_request_count"] == job_sources.MAX_SEARCH_REQUESTS
    assert result.diagnostics["request_count"] == job_sources.MAX_TOTAL_SEARCH_REQUESTS
    assert result.diagnostics["redirect_resolution_count"] == job_sources.MAX_SEARCH_REDIRECT_REQUESTS


def test_redirect_fetch_checks_dns_and_does_not_forward_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_getaddrinfo(host: str, _port: int, *, type: int):
        assert type == socket.SOCK_STREAM
        address = "8.8.8.8" if host == "www.baidu.com" else "192.168.1.3"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    class Response:
        status_code = 302
        headers = {"location": "https://careers.acme.test/jobs/one"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, *, chunk_size: int):
            del chunk_size
            return iter(())

    def fake_stream(method: str, url: str, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        return Response()

    monkeypatch.setattr(job_sources.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(job_sources.httpx, "stream", fake_stream)

    assert job_sources._dns_resolves_public("www.baidu.com") is True
    assert job_sources._dns_resolves_public("careers.acme.test") is False
    job_sources._request_redirect("https://www.baidu.com/link?url=opaque", 1.5)

    assert len(calls) == 1
    assert calls[0]["method"] == "GET"
    assert calls[0]["url"].startswith("https://www.baidu.com/")
    assert calls[0]["follow_redirects"] is False
    assert calls[0]["trust_env"] is True
    assert "Authorization" not in calls[0]["headers"]
    assert "Cookie" not in calls[0]["headers"]

    followed: list[str] = []

    def private_redirect(url: str, _timeout: float):
        followed.append(url)
        return SimpleNamespace(
            status_code=302,
            headers={"location": "https://careers.acme.test/jobs/private"},
            text="",
        )

    assert job_sources._resolve_baidu_wrapper(
        "https://www.baidu.com/link?url=opaque", request=private_redirect
    ) == ""
    assert followed == ["https://www.baidu.com/link?url=opaque"]


def test_redirect_refresh_body_is_capped_at_64_kib(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(job_sources, "_dns_resolves_public", lambda _host: True)

    class Response:
        status_code = 200
        headers = {}
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, *, chunk_size: int):
            assert chunk_size == 16 * 1024
            yield b"<meta http-equiv='refresh' content='0;url=https://careers.acme.test/job/1'>"
            yield b"x" * (job_sources.MAX_REDIRECT_BODY_BYTES + 1)

    monkeypatch.setattr(job_sources.httpx, "stream", lambda *_args, **_kwargs: Response())

    response = job_sources._request_redirect("https://www.baidu.com/link?url=opaque", 1.0)

    assert response.status_code == 200
    assert response.text == ""


def test_http_failure_challenge_and_vacuum_are_separately_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(job_sources.web_search, "_proxy_alive", lambda **_kwargs: None)
    monkeypatch.setattr(job_sources.web_search, "route_diagnostics", lambda: {})

    def fake_fetch(url: str, _timeout: float):
        host = urlsplit(url).hostname
        if host == "www.so.com":
            return SimpleNamespace(text="<html>captcha: verify you are human</html>")
        if host == "html.duckduckgo.com":
            request = httpx.Request("GET", url)
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("HTTP 429", request=request, response=response)
        return SimpleNamespace(text="<html><body>unrelated results</body></html>")

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)

    with pytest.raises(job_sources.SearchError) as raised:
        job_sources.search("财务分析师", n=1)

    diagnostics = raised.value.diagnostics
    counts = diagnostics["failure_counts"]
    assert counts["challenge"] > 0
    assert counts["http_failure"] > 0
    assert counts["vacuum"] > 0
    assert {item["status"] for item in diagnostics["attempts"]} >= {
        "challenge",
        "http_failure",
        "vacuum",
    }


def test_unresolved_search_engine_wrappers_do_not_become_public_leads() -> None:
    source = (
        '<div class="result"><a href="https://www.so.com/link?m=opaque">'
        "灵巧手算法工程师招聘</a><span>岗位要求 Python</span></div>"
    )
    assert job_sources._parse_links(source, "web") == []
    assert job_sources.result_kind(
        "https://www.so.com/link?m=opaque", "web", title="灵巧手算法工程师招聘", text="岗位要求 Python"
    ) is None


def test_search_ranks_job_details_ahead_of_aggregate_leads(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, timeout: float):
        del timeout
        return SimpleNamespace(
            text=(
                '<div class="result"><a href="https://jobs.example.test/search?query=hand">'
                "灵巧手算法工程师岗位列表</a><span>职位搜索结果</span></div>"
                '<div class="result"><a href="https://careers.example.test/jobs/hand-1">'
                "灵巧手算法工程师招聘</a><span>任职要求 Python</span></div>"
            )
        )

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)
    result = job_sources.search("灵巧手算法工程师", n=1)

    assert len(result) == 1
    assert result[0]["result_kind"] == "job_detail"
    assert result.diagnostics["raw_candidate_count"] >= 2


def test_integrations_exposes_lead_boundary_and_search_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = job_sources.SearchResult(
        [
            {
                "platform": "全网公开网页",
                "url": "https://careers.example.test/jobs/one",
                "title": "灵巧手算法工程师招聘",
                "text": "岗位要求 Python",
                "result_kind": "job_detail",
                "source_kind": "search_lead",
            }
        ],
        diagnostics={"status": "partial", "variants": ["灵巧手算法工程师 招聘 岗位要求"]},
    )
    monkeypatch.setattr(job_sources, "search", lambda *args, **kwargs: rows)

    payload = integrations.search_jobs("灵巧手算法工程师", limit=2)

    assert payload["ok"] is True
    assert payload["partial"] is True
    assert payload["diagnostics"]["search_lead_count"] == 1
    assert payload["diagnostics"]["source_evidence_count"] == 0
    assert payload["results"][0]["source_kind"] == "search_lead"
    assert payload["results"][0]["readable"] is False
    assert "fetch_url" in payload["diagnostics"]["next_step"]


def test_search_jobs_twenty_leads_stay_below_tool_result_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        {
            "platform": "全网公开网页",
            "url": f"https://careers.example.test/jobs/hand-{index}?utm_source=engine",
            "title": "灵巧手算法工程师招聘 " * 10,
            "text": "岗位要求 Python、机器人算法与任职要求 " * 20,
            "result_kind": "job_detail",
            "source_kind": "search_lead",
        }
        for index in range(20)
    ]
    search_diagnostics = {
        "query": "灵巧手算法工程师",
        "platform": "web",
        "requested": 20,
        "variants": [
            "灵巧手算法工程师",
            '"灵巧手算法工程师" 任职要求',
            "灵巧手算法工程师 招聘 岗位要求",
            "灵巧手算法工程师 招聘简章",
        ],
        "attempts": [
            {
                "engine": "ddg_html",
                "variant": "灵巧手算法工程师",
                "status": "ok",
                "elapsed_ms": 100,
                "result_count": 20,
            }
            for _ in range(20)
        ],
        "engines_attempted": ["so360", "ddg_html", "cn_bing", "sogou", "baidu"],
        "engines_succeeded": ["so360", "ddg_html"],
        "errors": [],
        "candidate_count": 20,
        "deduplicated_count": 20,
        "returned_count": 20,
        "search_lead_count": 20,
        "source_evidence_count": 0,
        "source_evidence_target": {"minimum": 3, "maximum": 5},
        "budget": {
            "max_candidates": 20,
            "max_variants": 4,
            "max_requests": 20,
            "max_seconds": 60.0,
            "engine_timeout_seconds": 8.0,
            "variant_soft_cap": 7,
        },
        "variant_candidate_counts": [5, 5, 5, 5],
        "route": {"probes": []},
        "status": "ok",
    }
    monkeypatch.setattr(
        job_sources,
        "search",
        lambda *args, **kwargs: job_sources.SearchResult(rows, diagnostics=search_diagnostics),
    )

    payload = integrations.search_jobs("灵巧手算法工程师", limit=20)
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # The engine truncates tool content at 20,000 characters.  Keep the
    # complete JSON payload below that ceiling so late candidates and
    # diagnostics cannot disappear behind an invalid partial object.
    assert len(rendered) < 19_000


def test_proxy_probe_does_not_use_environment_route(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Response:
        status_code = 204

    def fake_get(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(web_search.httpx, "get", fake_get)
    assert web_search._proxy_alive() == "http://127.0.0.1:7890"
    assert calls
    assert calls[0]["trust_env"] is False
    assert calls[0]["follow_redirects"] is False
    assert web_search.route_diagnostics()["selected_proxy"] == "http://127.0.0.1:7890"


def test_search_error_diagnostics_redact_proxy_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(url: str, timeout: float):
        del url, timeout
        raise RuntimeError("connect https://alice:secret@proxy.example.test/?api_key=top-secret")

    monkeypatch.setattr(job_sources.web_search, "_fetch", fake_fetch)
    with pytest.raises(job_sources.SearchError) as raised:
        job_sources.search("灵巧手算法工程师", n=1)

    diagnostics = raised.value.diagnostics
    rendered = json.dumps(diagnostics, ensure_ascii=False)
    assert "secret" not in rendered
    assert "top-secret" not in rendered
    assert "[redacted]" in rendered
