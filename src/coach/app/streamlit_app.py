"""Streamlit 本地应用：截图确认 → 技能档案 → 访谈 → 学习计划 + GitHub 项目。

运行（在项目根目录）：
    streamlit run src/coach/app/streamlit_app.py
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from coach.core.llm.router import ModelRouter
from coach.domain.schemas import (
    JDCard,
    SkillItem,
    UserSkillProfile,
)
from coach.tools.interviewer import apply_interview_answer, next_interview_question
from coach.tools.jd_parser import parse_jd_screenshots
from coach.tools.pipeline import plan_and_recommend
from coach.tools.profile_builder import build_profile_from_documents, build_profile_from_text
from coach.tools.session import SessionState

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "config" / "providers.yaml"
DATA_DIR = PROJECT_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
SESSION_PATH = DATA_DIR / "session.json"

st.set_page_config(page_title="校招雷达·学习规划师", layout="wide")


# ── 资源初始化 ─────────────────────────────────────────────────

@st.cache_resource
def get_router() -> ModelRouter:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    return ModelRouter.from_yaml(CONFIG_PATH)


# 会话从 data/session.json 恢复：关闭网页/终端后，岗位、技能档案、
# 访谈记录、学习计划和项目都还在；下次打开自动加载（与命令行版共用同一文件）。
if "state" not in st.session_state:
    st.session_state.state = SessionState.load(SESSION_PATH)
if "pending_q" not in st.session_state:
    st.session_state.pending_q = None

state: SessionState = st.session_state.state


def save_state() -> None:
    """每次关键修改后落盘。"""
    state.save(SESSION_PATH)

try:
    router = get_router()
    router_ready = True
except Exception as exc:  # noqa: BLE001
    router_ready = False
    router_error = exc

st.title("校招雷达 · 学习规划师")

if not router_ready:
    st.error(
        "模型配置未就绪。请先复制 config/providers.example.yaml 为 "
        f"config/providers.yaml 并在 .env 填入 API key。\n\n详情：{router_error}"
    )
    st.stop()


def _save_uploads(uploaded) -> list[str]:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    for f in uploaded:
        dest = UPLOAD_DIR / f.name
        dest.write_bytes(f.getbuffer())
        paths.append(str(dest))
    return paths


# ── 侧边栏：导航与进度 ─────────────────────────────────────────

with st.sidebar:
    st.header("流程导航")
    page = st.radio(
        "步骤",
        ["1️⃣ 岗位截图", "2️⃣ 我的技能", "3️⃣ 学习计划 & 项目"],
    )
    st.divider()
    st.markdown("**当前进度**")
    st.markdown(f"- 岗位：{'✅ ' + (state.jd.job_title or '已识别') if state.jd else '⬜ 未识别'}")
    st.markdown(f"- 技能：{len(state.profile.skills) if state.profile else 0} 项")
    st.markdown(f"- 访谈：{len(state.interview_history)} 轮")
    st.markdown(f"- 计划：{'✅ 已生成' if state.plan else '⬜ 未生成'}")
    st.caption(f"记录自动保存于\n`{SESSION_PATH}`")
    if st.button("🗑️ 清空记录重新开始", use_container_width=True):
        SessionState().save(SESSION_PATH)
        st.session_state.state = SessionState.load(SESSION_PATH)
        st.session_state.pending_q = None
        st.rerun()

# ── 页面 1：岗位截图识别 + 确认页 ──────────────────────────────

if page.startswith("1"):
    st.subheader("上传岗位截图（支持多张 / 长截图分段）")
    shots = st.file_uploader(
        "BOSS 直聘等平台的岗位详情截图，可多选",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
    )
    col1, _ = st.columns([1, 3])
    if col1.button("🔍 识别并结构化", type="primary", disabled=not shots):
        with st.spinner("视觉模型读图中…"):
            paths = _save_uploads(shots)
            state.jd = parse_jd_screenshots(router, paths)
            save_state()
        st.success("识别完成，请在下方核对修改")

    if state.jd:
        jd: JDCard = state.jd
        if jd.uncertain_fields:
            st.warning("⚠️ 这些字段识别不确定，请重点核对：" + "、".join(jd.uncertain_fields))

        with st.form("jd_confirm"):
            c1, c2, c3, c4 = st.columns(4)
            job_title = c1.text_input("岗位名", jd.job_title or "")
            company = c2.text_input("公司", jd.company or "")
            salary = c3.text_input("薪资", jd.salary or "")
            location = c4.text_input("地点", jd.location or "")
            team = st.text_input("团队/部门", jd.team or "")

            def _list_editor(label: str, items: list[str]) -> list[str]:
                df = pd.DataFrame({"条目": items}) if items else pd.DataFrame({"条目": [""]})
                edited = st.data_editor(
                    df, num_rows="dynamic", use_container_width=True, key=f"jd_{label}"
                )
                return [x for x in edited["条目"].tolist() if str(x).strip()]

            st.markdown("**硬性要求**")
            hard = _list_editor("hard", jd.hard_requirements)
            st.markdown("**技能标签**（规范技术名词）")
            tags = _list_editor("tags", jd.skill_tags)
            st.markdown("**岗位职责**")
            resp_items = _list_editor("resp", jd.responsibilities)
            st.markdown("**加分项**")
            nice = _list_editor("nice", jd.nice_to_haves)
            raw_summary = st.text_area("JD 摘要", jd.raw_summary, height=120)

            if st.form_submit_button("💾 保存岗位信息"):
                state.jd = JDCard(
                    job_title=job_title or None,
                    company=company or None,
                    salary=salary or None,
                    location=location or None,
                    team=team or None,
                    hard_requirements=hard,
                    skill_tags=tags,
                    responsibilities=resp_items,
                    nice_to_haves=nice,
                    raw_summary=raw_summary,
                    uncertain_fields=[],
                )
                save_state()
                st.success("已保存，进入左侧「我的技能」继续")
    else:
        st.info("上传截图后点击「识别并结构化」，识别结果可直接在页面上修改。")

# ── 页面 2：技能档案（文档 / 自填 / 访谈）──────────────────────

elif page.startswith("2"):
    st.subheader("建立你的技能档案")

    tab_doc, tab_self, tab_chat, tab_review = st.tabs(
        ["📄 简历/日志", "✍️ 技能自填", "💬 访谈补全", "📋 核对档案"]
    )

    with tab_doc:
        kind = st.radio("材料类型", ["resume", "work_log"], format_func=lambda k: "简历" if k == "resume" else "工作日志")
        docs = st.file_uploader(
            "支持 txt / md / pdf，可多份",
            type=["txt", "md", "markdown", "log", "pdf"],
            accept_multiple_files=True,
            key="doc_uploader",
        )
        if st.button("解析材料并更新档案", disabled=not docs):
            with st.spinner("解析中…"):
                paths = _save_uploads(docs)
                state.profile = build_profile_from_documents(
                    router, paths, source=kind, existing=state.profile
                )
                save_state()
            st.success(f"档案更新完成，当前 {len(state.profile.skills)} 项技能")

    with tab_self:
        self_text = st.text_area(
            "用一段话描述你会的技术、做过的项目", height=160,
            placeholder="例如：熟悉 Python，用过 PyTorch 做过一个图像分类课设；Web 方面用 FastAPI 写过两个小项目…",
        )
        if st.button("提交自填", disabled=not self_text.strip()):
            state.profile = build_profile_from_text(
                router, self_text, source="self_report", existing=state.profile
            )
            save_state()
            st.success("已并入技能档案")

    with tab_chat:
        if state.profile is None:
            st.info("先通过简历或自填建立初步档案，访谈会更有针对性；也可以直接开始。")
        if st.button("🙋 生成下一个访谈问题"):
            if state.profile is None:
                state.profile = UserSkillProfile()
                save_state()
            with st.spinner("思考问题中…"):
                st.session_state.pending_q = next_interview_question(
                    router, state.profile, state.history_pairs()
                )

        pending = st.session_state.pending_q
        if pending:
            st.markdown(f"**教练问：** {pending}")
            answer = st.text_area("你的回答", height=120, key="interview_answer")
            if st.button("提交回答", disabled=not answer.strip()):
                with st.spinner("更新档案中…"):
                    state.profile = apply_interview_answer(
                        router, state.profile, pending, answer
                    )
                state.interview_history.append([pending, answer])
                save_state()
                st.session_state.pending_q = None
                st.rerun()
        elif state.interview_history:
            st.success("当前没有更多必问问题，可以继续生成或前往下一步。")

        if state.interview_history:
            with st.expander(f"已完成 {len(state.interview_history)} 轮访谈"):
                for q, a in state.interview_history:
                    st.markdown(f"**Q：** {q}\n\n**A：** {a}")

    with tab_review:
        profile = state.profile
        if profile is None or not profile.skills:
            st.info("档案还是空的，先去前三个标签补充吧。")
        else:
            st.markdown(f"**求职方向：** {profile.target_direction or '未识别'}")
            st.markdown(f"**画像摘要：** {profile.summary or '（无）'}")
            rows = pd.DataFrame(
                [
                    {
                        "技能": s.name,
                        "熟练度": s.proficiency,
                        "置信度": s.confidence,
                        "来源": ",".join(s.sources),
                        "证据": s.evidence,
                    }
                    for s in profile.skills
                ]
            )
            edited = st.data_editor(
                rows,
                num_rows="dynamic",
                use_container_width=True,
                column_config={
                    "熟练度": st.column_config.SelectboxColumn(
                        options=["aware", "working", "proficient", "expert"], required=True
                    ),
                    "置信度": st.column_config.NumberColumn(min_value=0.0, max_value=1.0, step=0.05),
                },
                key="profile_review",
            )
            if st.button("💾 保存档案修改"):
                skills = []
                for _, r in edited.iterrows():
                    if not str(r["技能"]).strip():
                        continue
                    skills.append(
                        SkillItem(
                            name=str(r["技能"]).strip(),
                            proficiency=r["熟练度"],
                            confidence=float(r["置信度"]),
                            sources=[s for s in str(r["来源"]).split(",") if s],
                            evidence=str(r["证据"]),
                        )
                    )
                profile.skills = skills
                save_state()
                st.success(f"已保存 {len(skills)} 项技能")

            if profile.open_questions:
                st.divider()
                st.caption("待确认问题：" + "；".join(profile.open_questions))

# ── 页面 3：计划 + GitHub ──────────────────────────────────────

else:
    st.subheader("差距分析、学习计划与复现项目")
    if not state.jd:
        st.warning("请先在「岗位截图」步骤识别并确认岗位信息。")
    elif not state.profile or not state.profile.skills:
        st.warning("请先在「我的技能」步骤补充技能。")
    else:
        c1, c2 = st.columns([1, 3])
        min_stars = c1.number_input("项目最低 stars", min_value=0, value=50, step=50)
        use_rerank = c2.checkbox("LLM 读 README 智能重排（更准，稍慢）", value=True)
        if st.button("🚀 生成学习计划并找项目", type="primary"):
            with st.spinner("差距分析中…"):
                plan, repos = plan_and_recommend(
                    router, state.jd, state.profile,
                    min_stars=int(min_stars), llm_rerank=use_rerank,
                )
            state.plan, state.repos = plan, repos
            save_state()
            st.success("完成")

        if state.plan:
            plan = state.plan
            tab_gap, tab_plan, tab_repo = st.tabs(["差距分析", "分阶段计划", "GitHub 项目"])

            with tab_gap:
                st.dataframe(
                    pd.DataFrame(
                        [
                            {"岗位要求": g.requirement, "你的技能": g.matched_skill or "—",
                             "状态": g.status, "说明": g.note}
                            for g in plan.gaps
                        ]
                    ),
                    use_container_width=True,
                )

            with tab_plan:
                st.markdown(f"**方向：{plan.direction}**")
                for p in plan.phases:
                    weeks = f"（约 {p.duration_weeks} 周）" if p.duration_weeks else ""
                    st.markdown(f"### {p.phase}{weeks}\n**目标：** {p.goal}")
                    st.markdown("\n".join(f"- {t}" for t in p.topics))
                    if p.suggested_practice:
                        st.markdown(f"**动手练习：** {p.suggested_practice}")
                    st.divider()

            with tab_repo:
                if not state.repos:
                    st.info("没有检索到满足条件的项目，试试降低最低 stars。")
                else:
                    st.dataframe(
                        pd.DataFrame(
                            [
                                {
                                    "项目": r.full_name,
                                    "链接": r.html_url,
                                    "语言": r.language or "",
                                    "Stars": r.stargazers_count,
                                    "匹配分": r.match_score,
                                    "推荐理由": r.reason,
                                }
                                for r in state.repos
                            ]
                        ),
                        column_config={"链接": st.column_config.LinkColumn()},
                        use_container_width=True,
                    )
