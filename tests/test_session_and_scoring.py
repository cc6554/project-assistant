"""会话持久化与 GitHub 确定性打分（不访问网络）。"""

from datetime import datetime, timezone

from coach.domain.schemas import JDCard, SkillItem, UserSkillProfile
from coach.tools.github_search import _score_repo, _to_repo
from coach.tools.session import SessionState


def test_session_round_trip(tmp_path):
    path = tmp_path / "session.json"
    state = SessionState(
        jd=JDCard(job_title="后端开发", company="X"),
        profile=UserSkillProfile(
            skills=[SkillItem(name="Go", proficiency="working", confidence=0.6, sources=["chat"])]
        ),
    )
    state.interview_history.append(["会 Go 吗？", "写过两个服务"])
    state.save(path)

    loaded = SessionState.load(path)
    assert loaded.jd.job_title == "后端开发"
    assert loaded.profile.skills[0].name == "Go"
    assert loaded.history_pairs() == [("会 Go 吗？", "写过两个服务")]


def test_session_load_missing_returns_empty(tmp_path):
    assert SessionState.load(tmp_path / "nope.json").jd is None


def test_github_deterministic_score():
    now = datetime.now(timezone.utc).isoformat()
    item = {
        "full_name": "org/demo",
        "html_url": "https://github.com/org/demo",
        "description": "a rag demo",
        "language": "Python",
        "stargazers_count": 5000,
        "pushed_at": now,
        "archived": False,
        "disabled": False,
    }
    scored = _score_repo(item)
    assert scored["det_score"] == 1.0  # 5000 stars 且刚更新
    repo = _to_repo({**scored, "match_score": 0.88, "reason": "推荐"})
    assert repo.full_name == "org/demo"
    assert repo.match_score == 0.88
    assert repo.reason == "推荐"


def test_github_old_repo_lower_score():
    old = "2020-01-01T00:00:00Z"
    item = {
        "full_name": "org/old",
        "html_url": "https://github.com/org/old",
        "description": "",
        "language": None,
        "stargazers_count": 5000,
        "pushed_at": old,
        "archived": False,
        "disabled": False,
    }
    scored = _score_repo(item)
    assert scored["det_score"] < 0.8  # 活跃度为 0，只剩 star 分
