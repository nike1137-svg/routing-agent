# 예비창업패키지 공고 안내 라우팅 에이전트

「2026 예비창업패키지 예비창업자 수정 모집공고(2차)」(중소벤처기업부 공고 제2026-207호) 한 건만 근거로 답하는 안내 에이전트입니다.

질문을 받으면 **카테고리를 판정**하고, 그 카테고리에 연결된 **공고문 부분만 근거로 조립**해 답한 뒤, 답변에 **근거에 없는 숫자·사업명이 섞였는지 기계적으로 검사**합니다. 공고문에 답이 없거나 판정이 불확실하면 지어내지 않고 넘깁니다.

설계 판단과 측정 결과는 [REPORT.md](REPORT.md)에 정리했습니다.

## 구조

```
routing-agent/
├── docs/
│   ├── notice_2026-207.pdf   # 근거 문서 (공고문 1건)
│   └── SOURCE.md             # 출처 · 이용 조건
├── data/
│   └── goldenset.json        # 평가셋 36건 (점수용 31 + 예시용 5)
├── prompts.py                # 분류 지침 · 답변 규칙
├── context.py                # 문서 쪼개기 · 카테고리별 근거 조립 (매핑표)
├── agent.py                  # LangGraph 파이프라인 · 기계적 검증 · 비용 상한
├── evaluate.py               # 도구 호출 적절성 · 답변 적절성 · 분류 지표 측정
├── app.py                    # Streamlit 데모
├── requirements.txt
├── .env.example
└── REPORT.md
```

## 설치

Python 3.12 기준입니다.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

`.env.example`을 `.env`로 복사하고 `OPENAI_API_KEY`를 입력합니다. `.env`는 `.gitignore`에 등록되어 있어 저장소에 올라가지 않습니다.

## 실행

```bash
# 질문 하나
python agent.py "접수 마감이 언제인가요?"

# 데모 화면 (이 컴퓨터에서만 접속되도록 localhost로 띄운다)
streamlit run app.py --server.address localhost

# 측정
python evaluate.py validate-judge            # 모범 답안으로 채점기 자체를 검증
python evaluate.py run <라벨> "<바꾼 것>"    # 점수용 31건 실행 → runs/<라벨>.json → 채점
python evaluate.py score <라벨>              # 저장된 결과만 다시 채점
```

## 비용 주의

- 모든 호출은 OpenAI API를 사용하며 유료입니다. 기본 모델은 `gpt-5.6-luna`이고, 점수용 31건 1회 측정에 약 0.03달러가 들었습니다(채점 제외).
- 호출마다 실제 토큰 사용량으로 비용을 계산해 `.cost_ledger.json`에 누적하고, `COST_LIMIT_USD`(기본 1.5달러)를 넘으면 다음 호출 전에 멈춥니다.
- 데모를 외부에 공개하면 다른 사람이 키로 비용을 쓸 수 있습니다. `--server.address localhost`로 실행하세요.

## 근거 문서 출처

중소벤처기업부 · 창업진흥원, K-Startup 창업지원포털 게시. 게시 페이지 표기에 따라 공공누리 제1유형(출처표시) 저작물입니다. 자세한 내용은 [docs/SOURCE.md](docs/SOURCE.md)를 참고하세요.
