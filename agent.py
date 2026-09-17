"""파이프라인: 판정 → 넘기기 판단 → (모델이 조회 도구를 골라) 근거 조립 → 답변 → 검증 (LangGraph).

사용:
    python agent.py "질문"
    python agent.py --examples      # goldenset 예시용 5건만 실행
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from openai import OpenAI

import context
import prompts

load_dotenv(Path(__file__).parent / ".env")

MODEL_ROUTER = os.getenv("MODEL_ROUTER", "gpt-5.6-luna")
MODEL_ANSWER = os.getenv("MODEL_ANSWER", "gpt-5.6-luna")
CONFIDENCE_THRESHOLD = 0.7
MAX_TOOL_ROUNDS = 3  # 모델 ↔ 도구 왕복 상한. 넘으면 넘긴다

# USD / 1M tokens (input, output). OpenAI 요금 페이지 Standard·Short context, 2026-09-17 확인.
# 캐시 입력 할인은 반영하지 않는다(비용을 많게 잡는 쪽).
PRICES = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (4.00, 20.00),
}
LEDGER_PATH = Path(__file__).parent / ".cost_ledger.json"


# ---------------------------------------------------------------- 비용 기록 · LLM 호출
class CostLimitExceeded(RuntimeError):
    pass


class MissingApiKey(RuntimeError):
    pass


def _read_ledger() -> dict:
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    return {"total_usd": 0.0, "calls": []}


def ledger_total() -> float:
    return _read_ledger()["total_usd"]


def _client() -> OpenAI:
    return OpenAI()  # OPENAI_API_KEY 는 .env 에서 읽는다


def _check_before_call(model: str) -> dict:
    if not os.getenv("OPENAI_API_KEY"):
        raise MissingApiKey("OPENAI_API_KEY 가 없습니다. .env.example 을 .env 로 복사하고 키를 입력하세요.")
    if model not in PRICES:
        raise ValueError(f"가격 정보가 없는 모델: {model}")
    limit = float(os.getenv("COST_LIMIT_USD", "1.5"))
    ledger = _read_ledger()
    if ledger["total_usd"] >= limit:
        raise CostLimitExceeded(f"누적 비용 {ledger['total_usd']:.4f}달러가 상한 {limit}달러에 도달해 호출을 멈춥니다.")
    return ledger


def _record_usage(ledger: dict, model: str, resp, purpose: str) -> dict:
    in_price, out_price = PRICES[model]
    u = resp.usage
    usage = {
        "purpose": purpose,
        "model": resp.model,
        "input_tokens": u.prompt_tokens,
        "output_tokens": u.completion_tokens,
        "cost_usd": round(u.prompt_tokens * in_price / 1e6 + u.completion_tokens * out_price / 1e6, 6),
    }
    ledger["total_usd"] = round(ledger["total_usd"] + usage["cost_usd"], 6)
    ledger["calls"].append({"at": datetime.now().isoformat(timespec="seconds"), **usage})
    LEDGER_PATH.write_text(json.dumps(ledger, ensure_ascii=False, indent=1), encoding="utf-8")
    return usage


def chat_json(model: str, messages: list[dict], max_tokens: int, purpose: str) -> tuple[dict | None, dict]:
    """JSON 응답을 받는다. 호출 전 누적 비용 상한을 확인하고, 호출 후 실제 사용량을 기록한다."""
    ledger = _check_before_call(model)
    resp = _client().chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        max_completion_tokens=max_tokens,
    )
    usage = _record_usage(ledger, model, resp, purpose)
    try:
        data = json.loads(resp.choices[0].message.content or "")
    except json.JSONDecodeError:
        data = None
    return data, usage


def chat_tools(model: str, messages: list[dict], tools: list[dict], max_tokens: int, purpose: str):
    """도구를 붙여 호출한다. 모델이 도구를 부를지, 답할지 정한다.

    reasoning_effort="none": gpt-5.6 계열은 chat completions 에서 추론 모드와 도구 호출을 함께 쓸 수 없다
    (API 오류 400). 그래서 도구 구조로 바꾸면 답변 단계의 추론 모드가 함께 꺼진다.
    """
    ledger = _check_before_call(model)
    resp = _client().chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        reasoning_effort="none",
        max_completion_tokens=max_tokens,
    )
    usage = _record_usage(ledger, model, resp, purpose)
    return resp.choices[0].message, usage


# ---------------------------------------------------------------- 기계적 검증
# "개"는 넣지 않는다: 모델이 근거의 항목 수를 세어 쓴 표현("4개 항목")을 지어낸 수치로 보지 않기 위해서다
_UNITS = ("백만원|억원|만원|원|개월|주|일|시|분|년|월|점|명|팀|회|배수|단계|자리|세|MB|GB|%")
NUMBER_EXPR = re.compile(rf"\d+(?:[.,]\d+)*\s*(?:{_UNITS})?")
PROGRAM_NAME = re.compile(r"[가-힣A-Za-z]+(?:패키지|사관학교)")
PAGE_CITATION = re.compile(r"\(공고문[^)]*\)")
PAGE_RANGE = re.compile(r"(\d+)(?:\s*~\s*(\d+))?\s*쪽")


def _nospace(s: str) -> str:
    return re.sub(r"\s+", "", s)


# 공고문 날짜 표기(’26.1.22. / 3.26. / ’25년)를 "2026년·1월·22일" 형태로도 인정하기 위한 목록
DATE_FULL = re.compile(r"[’‘']?(\d{2}|\d{4})\.(\d{1,2})\.(\d{1,2})\.")
DATE_MONTH_DAY = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.")
DATE_YEAR = re.compile(r"[’‘'](\d{2})(?=[.년])")


def _date_forms(ctx: str) -> set[str]:
    forms = set()
    year = lambda y: f"20{y}년" if len(y) == 2 else f"{y}년"
    for y, m, d in DATE_FULL.findall(ctx):
        forms |= {year(y), f"{int(m)}월", f"{int(d)}일"}
    for m, d in DATE_MONTH_DAY.findall(ctx):
        forms |= {f"{int(m)}월", f"{int(d)}일"}
    for y in DATE_YEAR.findall(ctx):
        forms.add(year(y))
    return forms


def verify_answer(answer: str, context_text: str, cited: list[str], provided_ids: list[str],
                  question: str = "") -> dict:
    """답변 속 숫자 표현·사업명이 근거(또는 사용자 질문)에 있는지 검사한다."""
    body = PAGE_CITATION.sub("", answer)
    ctx = _nospace(context_text) + "\n" + _nospace(question)
    allowed_dates = _date_forms(ctx)
    numbers = sorted({_nospace(m.group()) for m in NUMBER_EXPR.finditer(body)})
    programs = sorted(set(PROGRAM_NAME.findall(body)))
    result = {
        "checked_numbers": numbers,
        "unsupported_numbers": [n for n in numbers if n not in ctx and n not in allowed_dates],
        "checked_programs": programs,
        "unsupported_programs": [p for p in programs if _nospace(p) not in ctx],
        "invalid_citations": [c for c in cited if c not in provided_ids],
    }
    result["passed"] = not (result["unsupported_numbers"] or result["unsupported_programs"] or result["invalid_citations"])
    return result


def cited_sections(answer: str, sections: list[dict]) -> list[str]:
    """답변 끝의 (공고문 N쪽) 표기와 쪽이 겹치는 조회 섹션을 인용한 것으로 본다."""
    pages = set()
    for m in PAGE_CITATION.finditer(answer):
        for a, b in PAGE_RANGE.findall(m.group()):
            pages |= set(range(int(a), int(b or a) + 1))
    return [s["id"] for s in sections if pages & set(s["pages"])]


# ---------------------------------------------------------------- LangGraph
class AgentState(TypedDict, total=False):
    question: str
    category: str
    confidence: float
    route_reason: str
    route: str
    messages: list[dict]
    tool_rounds: int
    tools_called: list[str]
    sections: list[dict]
    context_text: str
    answer: str
    cited_sections: list[str]
    verification: dict
    handoff_reason: str
    final_answer: str
    usage: list[dict]


def classify(state: AgentState) -> AgentState:
    data, usage = chat_json(MODEL_ROUTER, prompts.router_messages(state["question"]), 300, "classify")
    category, confidence, reason = "out_of_scope", 0.0, "분류 응답을 읽지 못함"
    if isinstance(data, dict) and data.get("category") in context.CATEGORIES:
        category = data["category"]
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        reason = str(data.get("reason", ""))
    return {"category": category, "confidence": confidence, "route_reason": reason,
            "tools_called": [], "sections": [], "context_text": "", "tool_rounds": 0,
            "usage": state.get("usage", []) + [usage]}


def gate(state: AgentState) -> AgentState:
    # 카테고리 선택(classify)과 넘기기 판단(gate)을 분리한다
    if state["category"] == "out_of_scope":
        return {"route": "handoff", "handoff_reason": "범위 밖 질문"}
    if state["confidence"] < CONFIDENCE_THRESHOLD:
        return {"route": "handoff", "handoff_reason": f"분류 확신도 낮음({state['confidence']:.2f} < {CONFIDENCE_THRESHOLD})"}
    return {"route": "agent"}


def agent(state: AgentState) -> AgentState:
    """모델 차례: 조회 도구를 부를지, handoff 를 부를지, 답할지 모델이 정한다."""
    messages = state.get("messages") or prompts.agent_messages(
        state["question"], context.CATEGORIES[state["category"]], state["confidence"])
    msg, usage = chat_tools(MODEL_ANSWER, messages, prompts.AGENT_TOOLS, 800, "agent")
    assistant = {"role": "assistant", "content": msg.content}
    calls = msg.tool_calls or []
    if calls:
        assistant["tool_calls"] = [{"id": c.id, "type": "function",
                                    "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                   for c in calls]
    update: AgentState = {"messages": messages + [assistant], "usage": state["usage"] + [usage]}

    if calls:
        if state["tool_rounds"] >= MAX_TOOL_ROUNDS:
            update.update(route="handoff", handoff_reason=f"도구 호출 상한({MAX_TOOL_ROUNDS}회) 초과")
        else:
            update["route"] = "tools"
        return update

    text = (msg.content or "").strip()
    if not any(t in context.TOOL_CATEGORY for t in state["tools_called"]):
        update.update(route="handoff", handoff_reason="근거를 조회하지 않고 답하려 함")
    elif not text:
        update.update(route="handoff", handoff_reason="답변이 비어 있음")
    else:
        update.update(answer=text, route="verify")
    return update


def tools(state: AgentState) -> AgentState:
    """모델이 요청한 도구를 실행한다. 조회 도구는 자기 카테고리 섹션만 돌려준다."""
    messages = list(state["messages"])
    tools_called = list(state["tools_called"])
    sections = list(state["sections"])
    context_text = state["context_text"]
    route, reason = "agent", ""

    for call in messages[-1]["tool_calls"]:
        name = call["function"]["name"]
        already = name in tools_called
        tools_called.append(name)
        if name == "handoff":
            try:
                reason = str(json.loads(call["function"]["arguments"] or "{}").get("reason", ""))
            except json.JSONDecodeError:
                reason = ""
            content = json.dumps({"ok": True}, ensure_ascii=False)
            route = "handoff"
        elif name in context.TOOL_CATEGORY:
            text, used = context.build_context(context.TOOL_CATEGORY[name])
            if not already:
                sections += [{"id": s.id, "title": s.title, "pages": s.pages} for s in used]
                context_text = f"{context_text}\n\n{text}" if context_text else text
            content = text
        else:
            content = json.dumps({"error": f"없는 도구: {name}"}, ensure_ascii=False)
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})

    update: AgentState = {"messages": messages, "tools_called": tools_called, "sections": sections,
                          "context_text": context_text, "tool_rounds": state["tool_rounds"] + 1, "route": route}
    if route == "handoff":
        update["handoff_reason"] = f"모델이 넘기기 선택: {reason}" if reason else "모델이 넘기기 선택"
    return update


def verify(state: AgentState) -> AgentState:
    cited = cited_sections(state["answer"], state["sections"])
    result = verify_answer(state["answer"], state["context_text"], cited,
                           [s["id"] for s in state["sections"]], state["question"])
    update: AgentState = {"verification": result, "cited_sections": cited}
    if result["passed"]:
        update.update(route="end", final_answer=state["answer"])
    else:
        update.update(route="handoff", handoff_reason="검증 실패(근거에 없는 내용 포함)")
    return update


def handoff(state: AgentState) -> AgentState:
    tools_called = state.get("tools_called", [])
    if "handoff" not in tools_called:
        tools_called = tools_called + ["handoff"]
    return {"tools_called": tools_called, "final_answer": prompts.HANDOFF_MESSAGE}


def _next(state: AgentState) -> str:
    return state["route"]


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [("classify", classify), ("gate", gate), ("agent", agent), ("tools", tools),
                     ("verify", verify), ("handoff", handoff)]:
        g.add_node(name, fn)
    g.add_edge(START, "classify")
    g.add_edge("classify", "gate")
    g.add_conditional_edges("gate", _next, {"handoff": "handoff", "agent": "agent"})
    g.add_conditional_edges("agent", _next, {"tools": "tools", "verify": "verify", "handoff": "handoff"})
    g.add_conditional_edges("tools", _next, {"agent": "agent", "handoff": "handoff"})  # 결과를 들고 모델에게 돌아간다
    g.add_conditional_edges("verify", _next, {"handoff": "handoff", "end": END})
    g.add_edge("handoff", END)
    return g.compile()


GRAPH = build_graph()


def run(question: str) -> AgentState:
    state = GRAPH.invoke({"question": question})
    state.pop("context_text", None)
    state.pop("messages", None)
    state["cost_usd"] = round(sum(u["cost_usd"] for u in state.get("usage", [])), 6)
    return state


def _print(state: AgentState) -> None:
    print(f"Q: {state['question']}")
    print(f"  판정: {state['category']} (확신도 {state['confidence']:.2f}) - {state.get('route_reason', '')}")
    print(f"  도구: {state['tools_called']} (도구 왕복 {state.get('tool_rounds', 0)}회)")
    if state.get("sections"):
        print("  근거: " + ", ".join(f"{s['id']}({s['pages'][0]}~{s['pages'][-1]}쪽)" for s in state["sections"]))
    if state.get("verification"):
        v = state["verification"]
        print(f"  검증: {'통과' if v['passed'] else '실패'} 숫자{v['checked_numbers']} "
              f"근거없음{v['unsupported_numbers'] + v['unsupported_programs'] + v['invalid_citations']}")
    if state.get("handoff_reason"):
        print(f"  넘김 사유: {state['handoff_reason']}")
        if state.get("answer"):
            print(f"  (검증 전 답변: {state['answer']})")
    print(f"  최종: {state['final_answer']}")
    print(f"  비용: {state['cost_usd']}달러")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) > 1 and sys.argv[1] == "--examples":
        items = json.loads(prompts.GOLDENSET_PATH.read_text(encoding="utf-8"))["items"]
        questions = [i["question"] for i in items if i["split"] == "example"]
    else:
        questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else []
    try:
        for q in questions:
            _print(run(q))
    except (MissingApiKey, CostLimitExceeded) as e:
        print(f"실행 중단: {e}")
        sys.exit(1)
    print(f"누적 비용(기록 파일): {ledger_total():.6f}달러")
