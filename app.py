"""데모 화면: 질문 → 답변 + 호출한 도구 · 근거로 쓴 문서 부분 · 검증 결과.

실행: streamlit run app.py
"""
import json
import os

import streamlit as st

import agent
import context
import prompts

st.set_page_config(page_title="예비창업패키지 공고 안내", page_icon="📄", layout="wide")

CATEGORY_NAMES = context.CATEGORIES
TOOL_NAMES = {**{t: f"{CATEGORY_NAMES[c]} 근거 조회" for t, c in context.TOOL_CATEGORY.items()}, "handoff": "넘기기"}
SECTIONS = {s.id: s for s in context.load_sections()}

st.title("📄 2026 예비창업패키지 공고 안내 에이전트")
st.caption("근거 문서: 중소벤처기업부 공고 제2026-207호 「2026 예비창업패키지 예비창업자 수정 모집공고(2차)」 · "
           "공고문에 없는 내용은 답하지 않고 넘깁니다.")

if not os.getenv("OPENAI_API_KEY"):
    st.error("OPENAI_API_KEY 가 없습니다. .env.example 을 .env 로 복사하고 키를 입력한 뒤 다시 실행하세요.")
    st.stop()

with st.sidebar:
    st.subheader("설정")
    st.write(f"분류 모델: `{agent.MODEL_ROUTER}`")
    st.write(f"답변 모델: `{agent.MODEL_ANSWER}`")
    st.write(f"넘기기 확신도 기준: `{agent.CONFIDENCE_THRESHOLD}`")
    st.divider()
    st.subheader("비용")
    limit = float(os.getenv("COST_LIMIT_USD", "1.5"))
    total = agent.ledger_total()
    st.progress(min(total / limit, 1.0), text=f"누적 {total:.4f} / 상한 {limit}달러")
    st.divider()
    st.subheader("예시 질문")
    examples = [i["question"] for i in json.loads(prompts.GOLDENSET_PATH.read_text(encoding="utf-8"))["items"]
                if i["split"] == "example"]
    for q in examples:
        if st.button(q, use_container_width=True):
            st.session_state.pending = q

if "history" not in st.session_state:
    st.session_state.history = []


def show_result(state: dict) -> None:
    handed_off = "handoff" in state["tools_called"]
    if handed_off:
        st.warning(state["final_answer"])
    else:
        st.success(state["final_answer"])

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**① 카테고리 판정**")
        st.write(f"{CATEGORY_NAMES[state['category']]} (`{state['category']}`)")
        st.write(f"확신도 {state['confidence']:.2f}")
        st.caption(state.get("route_reason", ""))
    with col2:
        st.markdown("**② 호출한 도구**")
        for t in state["tools_called"]:
            st.write(f"- `{t}` · {TOOL_NAMES.get(t, t)}")
        if state.get("handoff_reason"):
            st.caption(f"넘긴 이유: {state['handoff_reason']}")
    with col3:
        st.markdown("**④ 검증 결과**")
        v = state.get("verification")
        if v is None:
            st.write("검증 전에 넘김" if handed_off else "-")
        else:
            st.write("✅ 통과" if v["passed"] else "❌ 실패 → 넘김")
            st.caption(f"확인한 숫자 표현: {', '.join(v['checked_numbers']) or '없음'}")
            problems = v["unsupported_numbers"] + v["unsupported_programs"] + v["invalid_citations"]
            if problems:
                st.caption(f"근거에 없는 항목: {', '.join(problems)}")

    if state.get("sections"):
        cited = set(state.get("cited_sections") or [])
        st.markdown("**③ 근거로 쓴 문서 부분** (이 카테고리에 연결된 섹션만 프롬프트에 들어감)")
        for s in state["sections"]:
            mark = "📌 답변이 인용 · " if s["id"] in cited else ""
            pages = f"{s['pages'][0]}쪽" if len(s["pages"]) == 1 else f"{s['pages'][0]}~{s['pages'][-1]}쪽"
            with st.expander(f"{mark}{s['id']} {s['title']} (공고문 {pages})"):
                st.text(SECTIONS[s["id"]].text)
    if handed_off and state.get("answer"):
        with st.expander("검증에서 막힌 답변 (사용자에게 보내지 않음)"):
            st.write(state["answer"])
    st.caption(f"이 질문의 API 비용: {state['cost_usd']}달러")


for past in st.session_state.history:
    with st.chat_message("user"):
        st.write(past["question"])
    with st.chat_message("assistant"):
        show_result(past)

question = st.chat_input("예비창업패키지 공고에 대해 물어보세요") or st.session_state.pop("pending", None)
if question:
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("판정 → 근거 조립 → 답변 → 검증 중..."):
                state = agent.run(question)
        except agent.CostLimitExceeded as e:
            st.error(str(e))
            st.stop()
        except Exception as e:  # 네트워크·API 오류 등: 오류 화면 대신 안내 후 멈춤
            st.error(f"답변을 만드는 중 오류가 발생했습니다. 잠시 후 다시 시도해 주세요. ({type(e).__name__}: {e})")
            st.stop()
        show_result(state)
    st.session_state.history.append(state)
