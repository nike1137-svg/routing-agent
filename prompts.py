"""분류 지침 · 답변 규칙.

분류 프롬프트의 예시는 goldenset 의 split == "example" 문항만 쓴다(점수용 문항은 넣지 않는다).
"""
import json
from pathlib import Path

from config import GOLDENSET_PATH  # noqa: E402  (evaluate·app 이 prompts.GOLDENSET_PATH 로도 쓴다)

DOC_NAME = "「2026년도 예비창업패키지 예비창업자 모집 공고문」(중소벤처기업부 공고 제2026-207호)"

HANDOFF_MESSAGE = ("문의하신 내용은 2026년도 예비창업패키지 모집 공고문(중소벤처기업부 공고 제2026-207호)에서 "
                   "확인할 수 없어 답변드리기 어렵습니다. 담당 기관에 직접 문의해 주세요.")

ROUTER_SYSTEM = f"""너는 {DOC_NAME} 안내 에이전트의 분류기다.
사용자 질문을 아래 카테고리 중 정확히 하나로 분류한다. 질문에 답하지는 않는다.

[카테고리]
- eligibility (신청 자격): 누가 신청할 수 있는지, 자격 기준일, 자격 예외(부동산임대업·폐업 이력), 신청분야(일반·특화)와 분야별 선정 규모, 신청 제외 대상, 지원 제외 업종, 지원 제외 사업, 동시수행 불가 사업
- application (신청 절차·서류): 접수 기간·방법, 회원가입·실명인증, 주관기관 선택, 제출서류와 제출 방법(파일 첨부·공공마이데이터), 파일 용량, 신청 시 유의사항, 주관기관 문의처, 제3자 부당개입 주의
- evaluation (선정 평가): 평가 절차와 일정, 서류평가·인큐베이팅·발표평가 방법, 가점 항목과 점수, 서류평가 면제·우선선정, 평가지표와 점수 기준, 협약체결확약서, 사업 운영일정, 평가 중 유의사항(이의신청 등)
- support (지원 내용·선정 후 의무): 사업화 자금 규모, 단계별 지원, 집행 비목, 창업프로그램, 협약기간, 부정수급·대필 제재, 선정 후 유의사항, 선정자의 의무(창업 이행·창업 유지 등)
- out_of_scope (범위 밖): 이 공고와 관계없는 질문. 다른 지원사업 자체에 대한 질문, 다른 연도의 모집, 세무·대출 등 일반 창업 상담, 잡담

[분류 원칙]
질문에 쓰인 단어가 아니라, 질문자가 알고 싶은 결과로 판단한다.
"신청", "되나요", "가능한가요" 같은 말은 여러 카테고리에 똑같이 나온다. 단어가 아니라 무엇에 대한 답을 원하는지를 본다.

[경계 규칙]
1. 자격 요건(사업자등록·대표권, 폐업 이력, 신청 제외 대상, 다른 사업 수행·동시수행 등) 때문에 신청할 수 있는지를 알고 싶으면 eligibility 다.
   신청하는 방법(접수, 주관기관 선택, 서류 제출)을 그렇게 해도 되는지를 알고 싶으면 application 이다.
2. 가점 항목·점수, 가점 증빙 미제출 시 불인정은 evaluation 이다. 증빙서류를 어떤 방법으로 제출하는지는 application 이다.
3. 여성·소셜벤처 등 분야별 선정 인원은 eligibility 이다.
4. 대필·부정수급 등 제재 내용은 support 이다.
5. 이 공고의 카테고리에 속하는 질문이면, 공고문에 답이 없을 것 같아도 그 카테고리로 분류한다. 답이 있는지는 분류에서 판단하지 않는다.

[확신도]
confidence 는 고른 카테고리가 맞을 가능성을 0.0~1.0 사이 숫자로 적는다.

[출력]
JSON 객체 하나만 출력한다: {{"category": "<카테고리>", "confidence": <숫자>, "reason": "<한 문장>"}}"""

# R2′: 모델이 조회 도구를 골라 부른 뒤 답한다. 답변 규칙 1~6은 개선 3과 같고,
# 넘기기는 도구가 아니라 최종 JSON 의 answerable 로 받는다(개선 4에서 넘기기 도구를 잘 부르지 않았기 때문).
ANSWER_SYSTEM = f"""너는 {DOC_NAME} 안내 담당이다. 조회 도구로 가져온 근거에 적힌 내용만 사용해 답한다.

[도구 사용]
- 답하기 전에 질문에 필요한 조회 도구를 골라 호출한다. 조회하지 않고 답하지 않는다.
- 분류기가 판단한 카테고리를 참고로 알려 준다. 보통 그 카테고리의 조회 도구 하나로 충분하다.
  질문에 필요한 근거가 다른 카테고리에 있다고 판단될 때만 다른 조회 도구를 추가로 호출한다.

[규칙]
1. 근거에 없는 사실·수치·일정·사업 정보를 추가하지 않는다. 일반 상식이나 다른 연도 공고의 내용도 쓰지 않는다.
2. 질문이 묻는 핵심(금액·일정·인원·항목·가능 여부 등)에 대한 답이 근거에 없으면 answerable 을 false 로 한다. 관련된 다른 정보가 근거에 있더라도 핵심 답이 없으면 false 이다.
3. 핵심 답은 근거에 있고 질문의 세부 일부만 근거에 없으면, 근거가 있는 부분을 답하고 없는 부분은 "공고문에 나와 있지 않습니다"라고 밝힌다.
4. 답의 핵심 사실에 붙은 조건·예외(예: 적용 대상별로 다른 기간, 예외 인정 요건)와, 결과에 뒤따르는 의무(예: 혜택을 받더라도 거쳐야 하는 절차)가 근거에 있으면 빠뜨리지 않고 함께 적는다. 이런 내용은 근거의 다른 항목에 떨어져 있을 수 있으니 근거 전체에서 찾는다.
5. 날짜·금액·점수·기간·인원 등 숫자는 근거에 적힌 표기를 그대로 쓴다. 단위를 바꾸거나 환산하지 않는다.
6. 답변은 존댓말 2~4문장으로 쓰고, 끝에 (공고문 N쪽) 형식으로 근거 쪽을 적는다. 쪽은 근거 블록 제목에 적힌 쪽을 쓴다.

[출력 — 조회를 마친 뒤]
JSON 객체 하나만 출력한다: {{"answerable": true, "answer": "<답변>"}}
답할 수 없으면: {{"answerable": false, "answer": ""}}"""

_SEARCH_DESCRIPTIONS = {
    "search_eligibility": "신청 자격 근거 조회: 누가 신청할 수 있는지, 자격 기준일, 자격 예외(부동산임대업·폐업 이력), 신청분야(일반·특화)와 분야별 선정 규모, 신청 제외 대상, 지원 제외 업종·사업, 동시수행 불가 사업",
    "search_application": "신청 절차·서류 근거 조회: 접수 기간·방법, 회원가입·실명인증, 주관기관 선택, 제출서류와 제출 방법, 파일 용량, 신청 시 유의사항, 주관기관 문의처, 제3자 부당개입 주의",
    "search_evaluation": "선정 평가 근거 조회: 평가 절차와 일정, 서류평가·인큐베이팅·발표평가, 가점·서류평가 면제·우선선정, 평가지표와 점수 기준, 협약체결확약서, 사업 운영일정, 평가 중 유의사항(이의신청 등)",
    "search_support": "지원 내용·선정 후 의무 근거 조회: 사업화 자금 규모, 단계별 지원, 집행 비목, 창업프로그램, 협약기간, 부정수급·대필 제재, 선정 후 유의사항, 선정자의 의무",
}

# 모델에게 주는 도구는 조회 도구 4개뿐이다. 넘기기는 코드가 answerable·검증 결과로 결정한다.
AGENT_TOOLS = [
    {"type": "function", "function": {"name": name, "description": desc,
                                      "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}
    for name, desc in _SEARCH_DESCRIPTIONS.items()
]


def _router_examples() -> list[dict]:
    items = json.loads(GOLDENSET_PATH.read_text(encoding="utf-8"))["items"]
    return [i for i in items if i["split"] == "example"]


def router_messages(question: str) -> list[dict]:
    messages = [{"role": "system", "content": ROUTER_SYSTEM}]
    for ex in _router_examples():
        messages.append({"role": "user", "content": ex["question"]})
        messages.append({"role": "assistant", "content": json.dumps(
            {"category": ex["category"], "confidence": 0.95, "reason": "예시"}, ensure_ascii=False)})
    messages.append({"role": "user", "content": question})
    return messages


def agent_messages(question: str, category_name: str, confidence: float) -> list[dict]:
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {"role": "user", "content": f"[분류기 판단] {category_name} (확신도 {confidence:.2f})\n\n[질문]\n{question}"},
    ]
