from __future__ import annotations

from pathlib import Path

import pytest

import agent_coach.job_radar as job_radar
from agent_coach.contracts import AgentSession, ToolContext, ToolRegistry
from agent_coach.legacy.tools import github_search


class _Response:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses: dict[str, _Response]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        return self.responses.get(url, _Response(404, {}))


def _context(workspace: Path) -> ToolContext:
    return ToolContext(
        workspace=workspace,
        session=AgentSession(workspace=str(workspace)),
        router=None,
    )


def test_tree_verification_requires_nonempty_source_blobs_and_ignores_docs_data_submodules():
    client = _Client(
        {
            f"{github_search.API_ROOT}/repos/owner/repo/git/trees/main": _Response(
                200,
                {
                    "tree": [
                        {"path": "README.md", "type": "blob", "size": 100},
                        {"path": "paper.pdf", "type": "blob", "size": 100},
                        {"path": "data.csv", "type": "blob", "size": 100},
                        {"path": "empty.py", "type": "blob", "size": 0},
                        {"path": "vendor/lib", "type": "commit", "size": 1},
                        {"path": "src/main.py", "type": "blob", "size": 17},
                    ]
                },
            )
        }
    )

    result = github_search._verify_repository_source(
        client,
        {"full_name": "owner/repo", "default_branch": "main"},
    )

    assert result["verified"] is True
    assert result["paths"] == ["src/main.py"]
    assert result["evidence"] == [
        "https://github.com/owner/repo/blob/main/src/main.py"
    ]


def test_contents_fallback_reconstructs_fixed_github_origin_for_nested_directories():
    root = f"{github_search.API_ROOT}/repos/owner/repo"
    tree_url = f"{root}/git/trees/main"
    contents_url = f"{root}/contents"
    nested_url = f"{root}/contents/src"
    client = _Client(
        {
            tree_url: _Response(200, {"tree": []}),
            contents_url: _Response(
                200,
                [
                    {
                        "name": "src",
                        "path": "src",
                        "type": "dir",
                        "url": "https://evil.example/steal-token",
                    }
                ],
            ),
            nested_url: _Response(
                200,
                [{"name": "main.py", "path": "src/main.py", "type": "file", "size": 3}],
            ),
        }
    )

    result = github_search._verify_repository_source(
        client,
        {"full_name": "owner/repo", "default_branch": "main"},
    )

    assert result["verified"] is True
    assert result["paths"] == ["src/main.py"]
    assert all(url.startswith(github_search.API_ROOT) for url in client.calls)
    assert all("evil.example" not in url for url in client.calls)


def test_search_boundary_drops_metadata_only_candidates(monkeypatch, tmp_path: Path):
    good = {
        "full_name": "owner/implementation",
        "html_url": "https://github.com/owner/implementation",
        "description": "implementation",
        "language": "Python",
        "stargazers_count": 100,
        "updated_at": "",
        "det_score": 1,
        "source_verified": True,
        "source_paths": ["src/main.py"],
        "source_evidence": [
            "https://github.com/owner/implementation/blob/main/src/main.py"
        ],
        "code_verified": True,
        "code_paths": ["src/main.py"],
        "code_evidence": [
            "https://github.com/owner/implementation/blob/main/src/main.py"
        ],
    }
    metadata_only = {
        "full_name": "owner/readme-only",
        "html_url": "https://github.com/owner/readme-only",
        "description": "paper and README",
        "language": None,
        "stargazers_count": 1000,
        "updated_at": "",
        "det_score": 2,
    }
    monkeypatch.setattr(
        github_search,
        "_collect_candidates",
        lambda *_args: [good, metadata_only],
    )

    repos = github_search.search_github_projects(
        None,
        ["implementation"],
        llm_rerank=False,
        min_stars=0,
    )

    assert [repo.full_name for repo in repos] == ["owner/implementation"]
    assert repos[0].code_paths == ["src/main.py"]


def test_local_registration_requires_an_actual_nonempty_code_file(tmp_path: Path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text("print('ok')", encoding="utf-8")
    (source / "README.md").write_text("implementation notes", encoding="utf-8")
    ctx = _context(tmp_path)
    register = job_radar.register_job_radar_tools(ToolRegistry()).get(
        "register_project", active_skills=["job-radar"]
    ).handler

    result = register(
        {
            "project": {
                "name": "local implementation",
                "source": {
                    "kind": "local_import",
                    "paths": ["src"],
                    "evidence": ["imported source directory"],
                },
            }
        },
        ctx,
    )
    assert result.data["project"]["source"]["code_verified"] is True
    assert result.data["project"]["source"]["code_paths"] == ["src/main.py"]

    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("paper only", encoding="utf-8")
    with pytest.raises(ValueError, match="源码"):
        register(
            {
                "project": {
                    "name": "README only",
                    "source": {
                        "kind": "local_import",
                        "paths": ["docs"],
                        "evidence": ["imported documentation"],
                    },
                }
            },
            ctx,
        )


def test_model_declared_code_provenance_does_not_bypass_verification(tmp_path: Path):
    ctx = _context(tmp_path)
    register = job_radar.register_job_radar_tools(ToolRegistry()).get(
        "register_project", active_skills=["job-radar"]
    ).handler

    with pytest.raises(ValueError, match="无法核验项目源码"):
        register(
            {
                "project": {
                    "name": "claimed implementation",
                    "source": {
                        "kind": "conversation",
                        "evidence": ["model claimed code exists"],
                        "code_verified": True,
                        "code_paths": ["src/main.py"],
                        "code_evidence": ["claimed source path"],
                    },
                }
            },
            ctx,
        )


def test_private_github_registration_persists_verified_provenance(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        github_search,
        "verify_github_repository_source",
        lambda full_name: {
            "verified": True,
            "private": True,
            "branch": "main",
            "paths": ["src/private.py"],
            "evidence": [
                f"https://github.com/{full_name}/blob/main/src/private.py"
            ],
        },
    )
    ctx = _context(tmp_path)
    register = job_radar.register_job_radar_tools(ToolRegistry()).get(
        "register_project", active_skills=["job-radar"]
    ).handler

    result = register(
        {
            "project": {
                "name": "private implementation",
                "full_name": "owner/private-repo",
                "source": {
                    "kind": "github",
                    "evidence": ["user supplied private repository"],
                },
            }
        },
        ctx,
    )

    project = result.data["project"]
    assert project["source"]["private"] is True
    assert project["source"]["code_verified"] is True
    assert project["source"]["code_paths"] == ["src/private.py"]
