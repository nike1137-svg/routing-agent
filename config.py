"""한곳에 모아 둔 설정. 여기 값만 바꿔도 동작이 달라진다. `.env` 에 같은 이름이 있으면 그 값을 쓴다.

- MODEL_ROUTER : 카테고리 판정용. 출력이 짧아 비용 최적화 등급이면 충분하다.
- MODEL_ANSWER : 답변 생성용. 공고문 근거가 붙어 입력이 길다.
- MODEL_JUDGE  : 답변 채점용(측정할 때만 쓴다).
- CONFIDENCE_THRESHOLD : 이 값 미만이면 넘긴다. 올리면 안전해지고 자동 응답이 줄어든다.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent
load_dotenv(ROOT / ".env")

MODEL_ROUTER = os.getenv("MODEL_ROUTER", "gpt-5.6-luna")
MODEL_ANSWER = os.getenv("MODEL_ANSWER", "gpt-5.6-luna")
MODEL_JUDGE = os.getenv("MODEL_JUDGE", "gpt-5.6-luna")

CONFIDENCE_THRESHOLD = 0.7   # 분류 확신도 임계값
MAX_TOOL_TURNS = 3           # 모델 ↔ 조회 도구 왕복 상한. 넘으면 넘긴다

# USD / 1M tokens (input, output). OpenAI 요금 페이지 Standard·Short context, 2026-09-17 확인.
# 캐시 입력 할인은 반영하지 않는다(비용을 많게 잡는 쪽).
PRICES = {
    "gpt-5.6-luna": (0.20, 1.20),
    "gpt-5.6-terra": (2.00, 12.00),
    "gpt-5.6-sol": (4.00, 20.00),
}


def cost_limit_usd() -> float:
    """누적 API 비용 상한. 호출할 때마다 다시 읽어 .env 변경을 바로 반영한다."""
    return float(os.getenv("COST_LIMIT_USD", "1.5"))


LEDGER_PATH = ROOT / ".cost_ledger.json"          # 누적 비용 기록 (저장소에 올리지 않음)
DOC_PATH = ROOT / "docs" / "notice_2026-207.pdf"   # 근거 문서
GOLDENSET_PATH = ROOT / "data" / "goldenset.json"  # 평가셋
RUNS_DIR = ROOT / "runs"                           # 측정 결과 (저장소에 올리지 않음)
