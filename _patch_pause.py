import io

# ── schemas.py：paused_questions ──
p = r"F:\Doubao\job-radar-coach\src\coach\domain\schemas.py"
t = io.open(p, encoding="utf-8").read()
old = '''    open_questions: list[str] = Field(
        default_factory=list, description="证据不足、下一轮访谈需要追问的问题"
    )
    updated_at: datetime = Field(default_factory=datetime.now)'''
new = '''    open_questions: list[str] = Field(
        default_factory=list, description="证据不足、下一轮访谈需要追问的问题"
    )
    paused_questions: list[str] = Field(
        default_factory=list, description="核对收尾时暂缓的问题（可随时恢复继续）"
    )
    updated_at: datetime = Field(default_factory=datetime.now)'''
assert old in t, "schemas anchor not found"
t = t.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(t)
print("schemas.py OK")

# ── session.py：clarify_rounds ──
p = r"F:\Doubao\job-radar-coach\src\coach\tools\session.py"
t = io.open(p, encoding="utf-8").read()
old = '''    interview_history: list[list[str]] = []  # [[question, answer], ...]'''
new = '''    interview_history: list[list[str]] = []  # [[question, answer], ...]
    clarify_rounds: int = 0  # 本轮待澄清核对已对话轮数（恢复继续时重置）'''
assert old in t, "session anchor not found"
t = t.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="\n").write(t)
print("session.py OK")

# ── main.py：start 恢复暂缓 + 移除 start 轮次拦截；chat 前置兜底暂缓；新增 pause 接口；删除重复兜底 ──
p = r"F:\Doubao\job-radar-coach\src\coach\sidecar\main.py"
t = io.open(p, encoding="utf-8").read()

old = '''    questions = state.profile.open_questions
    if not questions:
        return {"outline": [], "opening": "没有待澄清问题了。", "remaining": 0, "done": True}
    if len(state.interview_history) >= clarify_max_rounds(len(questions)):
        return {
            "outline": questions,
            "opening": "咱们已经核对得比较充分了。剩下的问题我保留在档案里，你之后想继续随时可以再打开。",
            "remaining": len(questions),
            "done": False,
        }
    # 打开时自动清理提纲里的同主题重复变体（保留首次出现），存量污染也能自愈'''
new = '''    questions = state.profile.open_questions
    opening_hint = ""
    # 上次核对暂缓的问题：用户重新打开 → 恢复继续（本轮对话计数重置）
    if not questions and state.profile.paused_questions:
        state.profile.open_questions = state.profile.paused_questions
        state.profile.paused_questions = []
        state.clarify_rounds = 0
        store.save(state)
        questions = state.profile.open_questions
        opening_hint = f"上次核对暂缓了 {len(questions)} 条问题，咱们接着来。"
    if not questions:
        return {"outline": [], "opening": "没有待澄清问题了。", "remaining": 0, "done": True}
    # 打开时自动清理提纲里的同主题重复变体（保留首次出现），存量污染也能自愈'''
assert old in t, "start resume anchor not found"
t = t.replace(old, new, 1)

old2 = '''    try:
        opening = interviewer.clarify_opening(router, questions)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"澄清开场失败：{exc}") from exc
    return {"outline": questions, "opening": opening, "remaining": len(questions), "done": False}'''
new2 = '''    try:
        opening = interviewer.clarify_opening(router, questions)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"澄清开场失败：{exc}") from exc
    if opening_hint:
        opening = f"{opening_hint}\\n\\n{opening}"
    return {"outline": questions, "opening": opening, "remaining": len(questions), "done": False}


@app.post("/api/sessions/{session_id}/profile/clarify/pause")
def clarify_pause(session_id: str) -> dict:
    """手动完成核对：剩余问题暂缓（移入 paused_questions），主页面不再显示「未回答」。"""
    state = _session(session_id)
    if state.profile is None:
        raise HTTPException(status_code=400, detail="请先建立技能档案")
    if state.profile.open_questions:
        state.profile.paused_questions = [
            *state.profile.paused_questions,
            *state.profile.open_questions,
        ]
        state.profile.open_questions = []
    state.clarify_rounds = 0
    store.save(state)
    return {"profile": state.profile.model_dump(exclude_none=True)}'''
assert old2 in t, "start opening anchor not found"
t = t.replace(old2, new2, 1)

old3 = '''    # 机器兜底：核对已超过 12 轮，任何消息都不再追问（与 start 的「已核对充分」提示一致）
    if questions and len(state.interview_history) + 1 >= clarify_max_rounds(len(questions)):
        return {
            "kind": "done",
            "reply": "咱们已经核对得比较充分了。剩余问题我保留在档案里，你之后想继续随时可以再打开。",
            "remaining": len(questions),
            "done": True,
            "profile": profile.model_dump(exclude_none=True),
        }'''
new3 = '''    # 机器兜底：本轮核对超过自适应上限，任何消息都统一收尾：剩余问题暂缓，不再追问
    state.clarify_rounds += 1
    if questions and state.clarify_rounds >= clarify_max_rounds(len(questions)):
        state.profile.open_questions = []
        state.profile.paused_questions = questions
        state.clarify_rounds = 0
        store.save(state)
        return {
            "kind": "done",
            "reply": "咱们已经核对得比较充分了，剩余问题我先暂存在档案里，你之后想继续随时再打开。",
            "remaining": 0,
            "done": True,
            "profile": state.profile.model_dump(exclude_none=True),
        }'''
assert old3 in t, "chat pre-guard anchor not found"
t = t.replace(old3, new3, 1)

old4 = '''        state.profile = profile
        state.interview_history.append([target, body.message])
        store.save(state)
        remaining = profile.open_questions
        rounds = len(state.interview_history)
        if remaining and rounds >= CLARIFY_MAX_ROUNDS:
            # 机器兜底：核对已充分，剩余问题保留在档案里，让对话自然收尾
            return {
                "kind": decision.intent,
                "reply": f"{decision.reply}\\n\\n咱们已经核对得比较充分了，剩余 {len(remaining)} 条问题我保留在档案里，之后想继续随时回来。",
                "remaining": len(remaining),
                "done": True,
                "profile": profile.model_dump(exclude_none=True),
            }
        return {'''
new4 = '''        state.profile = profile
        state.interview_history.append([target, body.message])
        store.save(state)
        remaining = profile.open_questions
        return {'''
assert old4 in t, "answer rounds dup anchor not found"
t = t.replace(old4, new4, 1)

io.open(p, "w", encoding="utf-8", newline="\n").write(t)
print("main.py OK")
