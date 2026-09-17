# 예비창업패키지 공고 안내 라우팅 에이전트

「2026 예비창업패키지 예비창업자 수정 모집공고(2차)」(중소벤처기업부 공고 제2026-207호) 한 건만 근거로, **예비창업자의 질문에 답하는 안내 에이전트**입니다.

질문을 받으면 **카테고리를 판정**하고, 그 카테고리에 연결된 **공고문 부분만 근거로 조립**해 답한 뒤, 답변에 **근거에 없는 숫자·사업명이 섞였는지 기계적으로 검사**합니다. 공고문에 답이 없거나 판정이 불확실하면 지어내지 않고 넘깁니다.

설계 판단과 측정 결과는 [REPORT.md](REPORT.md)에 정리했습니다.

![데모 화면](docs/demo.png)

## 카테고리

| 카테고리 | 답하는 내용 |
|---|---|
| 신청 자격 | 누가 신청할 수 있나, 자격 기준일·예외, 신청분야, 제외 대상 |
| 신청 절차·서류 | 접수 기간·방법, 주관기관 선택, 제출서류, 문의처 |
| 선정 평가 | 평가 단계·일정, 가점·면제·우선선정, 평가지표, 이의신청 |
| 지원 내용·선정 후 의무 | 사업화 자금, 단계별 지원, 집행 비목, 선정 후 의무·제재 |
| 범위 밖 | 이 공고와 관계없는 질문 → 넘김 |

## 데모 화면에 보이는 것

답변과 함께 **① 카테고리 판정(확신도) · ② 호출한 도구 · ③ 근거로 쓴 공고문 섹션(펼치면 원문) · ④ 검증 결과**를 보여 줍니다. 넘긴 경우에는 넘긴 이유를 함께 표시합니다.

## 결과 요약

점수용 평가셋 31건, 제출 구성(개선 3)으로 2회 측정한 결과입니다. 측정 방법과 개선 과정(개선 1~8), 실패 원인은 [REPORT.md](REPORT.md) 4절에 있습니다.

| 지표 | 1회차 | 2회차 |
|---|---|---|
| 도구 호출 적절성 | 1.000 | 0.968 |
| 답변 적절성 | 0.968 | 0.968 (채점기 변동 보정 시 0.935) |
| 분류 정확도 / macro F1 | 1.000 / 1.000 | 0.968 / 0.966 |

## 기술 스택

Python 3.12 · LangGraph · OpenAI API (`gpt-5.6-luna`) · PyMuPDF · Streamlit · python-dotenv

## 구조

```
routing-agent/
├── docs/
│   ├── notice_2026-207.pdf    # 근거 문서 (공고문 1건)
│   ├── SOURCE.md              # 출처 · 이용 조건
│   ├── demo.png               # 데모 캡처 (예시 버튼 질문)
│   ├── demo_evaluation.png    # 데모 캡처 (평가지표 질문)
│   └── demo_chat_input.png    # 데모 캡처 (채팅 입력창 질문)
├── data/
│   └── goldenset.json         # 평가셋 36건 (점수용 31 + 예시용 5)
├── config.py                  # 모델 · 임계값 · 경로 설정
├── prompts.py                 # 분류 지침 · 답변 규칙
├── context.py                 # 문서 쪼개기 · 카테고리별 근거 조립 (매핑표)
├── agent.py                   # LangGraph 파이프라인 · 기계적 검증
├── evaluate.py                # 도구 호출 적절성 · 답변 적절성 · 분류 지표 측정
├── app.py                     # Streamlit 데모
├── requirements.txt
├── .env.example               # 환경변수 이름 예시 (값 없음)
├── .gitignore
├── README.md
└── REPORT.md
```

실행하면 로컬에만 생기고 저장소에는 올라가지 않는 파일: `.env`(키), `runs/`(측정 결과) 등

미채택 실험 코드는 브랜치에 있습니다: `exp4-model-tools`, `exp5-tools-answerable`, `exp6-router-intent`, `exp8-tools-terra-router` (내용은 REPORT 4절)

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

`.env.example`을 `.env`로 복사한 뒤 `OPENAI_API_KEY`에 키를 입력합니다.

## 환경변수

| 이름 | 역할 | 기본값 |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API 키 (필수) | 없음 |
| `MODEL_ROUTER` | 카테고리 판정 모델 | `gpt-5.6-luna` |
| `MODEL_ANSWER` | 답변 모델 | `gpt-5.6-luna` |
| `MODEL_JUDGE` | 답변 채점 모델 (측정용) | `gpt-5.6-luna` |

키가 없으면 실행 명령은 오류 화면 대신 안내 문구를 출력하고 멈춥니다.

## 실행

```bash
# 질문 하나
python agent.py "접수 마감이 언제인가요?"

# 예시용 질문 5건 실행
python agent.py --examples

# 데모 화면 (이 컴퓨터에서만 접속되도록 localhost로 띄운다)
streamlit run app.py --server.address localhost
```

### 측정

```bash
# 1) 채점기 자체를 검증 (모범 답안은 통과, 일부러 틀린 답은 실패해야 함)
python evaluate.py validate-judge

# 2) 점수용 31건 실행 → runs/<라벨>.json 저장 → 채점
python evaluate.py run <라벨> "<바꾼 것>"

# 3) 2)에서 저장한 결과만 다시 채점 (파이프라인은 다시 호출하지 않음)
python evaluate.py score <라벨>
```

`runs/`는 저장소에 없으므로, 새로 클론했다면 `score` 전에 `run`을 먼저 실행해야 합니다.

## 주의

- 데모를 외부에 공개하면 다른 사람이 내 API 키로 호출할 수 있습니다. `--server.address localhost`로 실행하세요.

## 근거 문서 출처

중소벤처기업부 · 창업진흥원, K-Startup 창업지원포털 게시. 게시 페이지 표기에 따라 공공누리 제1유형(출처표시) 저작물입니다. 자세한 내용은 [docs/SOURCE.md](docs/SOURCE.md)를 참고하세요.
