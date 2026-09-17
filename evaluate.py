"""두 지표(도구 호출 적절성 · 답변 적절성) + 분류 지표(정확도 · macro F1 · 혼동행렬) 측정.

사용:
    python evaluate.py validate-judge          # 모범 답안으로 채점기 자체를 검증
    python evaluate.py run <라벨> "<바꾼 것>"   # 점수용 문항 전체 실행 → runs/<라벨>.json 저장 → 채점
    python evaluate.py score <라벨>            # 저장된 실행 결과를 다시 채점(파이프라인 재호출 없음)
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import agent
import prompts
from context import CATEGORIES

ROOT = Path(__file__).parent
RUNS = ROOT / "runs"
MODEL_JUDGE = os.getenv("MODEL_JUDGE", "gpt-5.6-luna")
LABELS = list(CATEGORIES)  # eligibility, application, evaluation, support, out_of_scope
SHORT = {"eligibility": "자격", "application": "신청", "evaluation": "평가", "support": "지원", "out_of_scope": "범위밖"}

JUDGE_SYSTEM = """너는 답변 채점기다. 표현이 아니라 사실을 보고 판정한다.

[필수 사실] 각 항목의 사실이 답변에 담겨 있는지 판정한다.
- 표현이 달라도 같은 사실이면 담긴 것으로 본다. 같은 값의 단위 환산도 같은 사실이다(예: 20백만원 = 2천만원).
- 숫자·단위·조건이 다르거나, 사실에 붙은 조건이 빠졌으면 담기지 않은 것으로 본다.

[금지 사항] 답변이 각 항목을 어겼는지 판정한다.
- 답변이 그 내용을 사실로 주장한 경우에만 어긴 것으로 본다.
- 그 내용을 부정하거나 공고문에 없다고 밝힌 경우는 어긴 것이 아니다.

문장 품질·길이·말투는 채점하지 않는다.

[출력] JSON 객체 하나만 출력한다:
{"facts": [{"index": 1, "included": true, "evidence": "<답변 속 해당 구절, 없으면 빈 문자열>"}],
 "violations": [{"index": 1, "violated": false, "evidence": "<답변 속 해당 구절, 없으면 빈 문자열>"}]}"""


def load_items(split: str | None = "eval") -> list[dict]:
    items = json.loads(prompts.GOLDENSET_PATH.read_text(encoding="utf-8"))["items"]
    return [i for i in items if split is None or i["split"] == split]


def expects_handoff(item: dict) -> bool:
    return "handoff" in item["expected_tools"]


# ---------------------------------------------------------------- 답변 적절성
def judge_answer(item: dict, answer_text: str) -> dict:
    """1점 조건: 필수 사실을 전부 담고, 금지 사항을 하나도 어기지 않음."""
    handed_off = answer_text == prompts.HANDOFF_MESSAGE
    if expects_handoff(item):
        # 넘기기가 정답인 문항: 고정 안내 문구로 넘겼는지를 코드로 판정한다(고정 문구는 금지 사항을 어길 수 없다)
        return {"passed": handed_off, "method": "rule", "detail": "넘김" if handed_off else "넘기지 않고 답함"}
    if handed_off:
        return {"passed": False, "method": "rule", "detail": "답해야 하는 문항을 넘김"}

    facts = [f["fact"] for f in item["must_include"]]
    rules = item["must_not"]
    user = (f"[질문]\n{item['question']}\n\n[필수 사실]\n"
            + "\n".join(f"{n}. {f}" for n, f in enumerate(facts, 1))
            + "\n\n[금지 사항]\n" + ("\n".join(f"{n}. {r}" for n, r in enumerate(rules, 1)) or "(없음)")
            + f"\n\n[답변]\n{answer_text}")
    data, usage = agent.chat_json(MODEL_JUDGE, [{"role": "system", "content": JUDGE_SYSTEM},
                                                {"role": "user", "content": user}], 800, "judge")
    try:
        included = {int(f["index"]): bool(f["included"]) for f in data["facts"]}
        violated = {int(v["index"]): bool(v["violated"]) for v in data.get("violations", [])}
        missing = [facts[n - 1] for n in range(1, len(facts) + 1) if not included.get(n, False)]
        broken = [rules[n - 1] for n in range(1, len(rules) + 1) if violated.get(n, False)]
    except (TypeError, KeyError, ValueError, IndexError):
        return {"passed": False, "method": "judge", "detail": "채점 응답 형식 오류", "raw": data, "usage": usage}
    return {"passed": not missing and not broken, "method": "judge", "missing_facts": missing,
            "violated_rules": broken, "raw": data, "usage": usage}


# ---------------------------------------------------------------- 분류 지표
def classification_metrics(pairs: list[tuple[str, str]]) -> dict:
    matrix = {t: {p: 0 for p in LABELS} for t in LABELS}
    for true, pred in pairs:
        matrix[true][pred] += 1
    per_class = {}
    for c in LABELS:
        tp = matrix[c][c]
        fp = sum(matrix[t][c] for t in LABELS if t != c)
        fn = sum(matrix[c][p] for p in LABELS if p != c)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[c] = {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3), "support": tp + fn}
    return {
        "accuracy": round(sum(matrix[c][c] for c in LABELS) / len(pairs), 3),
        "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(LABELS), 3),
        "per_class": per_class,
        "confusion_matrix": matrix,
    }


# ---------------------------------------------------------------- 실행 · 채점
def run_pipeline(label: str, change: str) -> Path:
    RUNS.mkdir(exist_ok=True)
    records, stopped = [], None
    for item in load_items():
        rec = {"id": item["id"], "question": item["question"], "expected_category": item["category"],
               "expected_tools": item["expected_tools"], "difficulty": item["difficulty"]}
        try:
            state = agent.run(item["question"])
            rec.update({k: state.get(k) for k in ("category", "confidence", "route_reason", "tools_called", "sections",
                                                   "answer", "cited_sections", "verification", "handoff_reason",
                                                   "final_answer", "cost_usd")})
        except agent.CostLimitExceeded as e:
            stopped = str(e)
            break
        except Exception as e:  # 한 문항의 오류로 전체 측정을 멈추지 않는다(해당 문항은 오답 처리)
            rec["error"] = f"{type(e).__name__}: {e}"
        records.append(rec)
        print(f"  {item['id']:5} -> {rec.get('category')} {rec.get('tools_called')}")
    path = RUNS / f"{label}.json"
    path.write_text(json.dumps({
        "label": label, "change": change, "at": datetime.now().isoformat(timespec="seconds"),
        "config": {"router": agent.MODEL_ROUTER, "answer": agent.MODEL_ANSWER, "judge": MODEL_JUDGE,
                   "confidence_threshold": agent.CONFIDENCE_THRESHOLD},
        "stopped": stopped, "records": records}, ensure_ascii=False, indent=1), encoding="utf-8")
    if stopped:
        print(f"비용 상한으로 중단: {stopped}")
    return path


def score(label: str) -> dict:
    path = RUNS / f"{label}.json"
    run = json.loads(path.read_text(encoding="utf-8"))
    items = {i["id"]: i for i in load_items()}
    pairs, tool_ok, answer_ok = [], 0, 0
    for rec in run["records"]:
        item = items[rec["id"]]
        pred = rec.get("category") or "out_of_scope"
        pairs.append((item["category"], pred))
        rec["tool_pass"] = set(rec.get("tools_called") or []) == set(item["expected_tools"])
        rec["answer_judge"] = judge_answer(item, rec.get("final_answer") or "")
        rec["answer_pass"] = rec["answer_judge"]["passed"]
        tool_ok += rec["tool_pass"]
        answer_ok += rec["answer_pass"]
    n = len(run["records"])
    metrics = {
        "n": n,
        "tool_call_accuracy": round(tool_ok / n, 3),
        "answer_accuracy": round(answer_ok / n, 3),
        "classification": classification_metrics(pairs),
        "pipeline_cost_usd": round(sum(r.get("cost_usd") or 0 for r in run["records"]), 6),
    }
    run["metrics"] = metrics
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    _report(run)
    return metrics


def _report(run: dict) -> None:
    m, c = run["metrics"], run["metrics"]["classification"]
    print(f"\n[{run['label']}] {run['change']}  ({run['config']})")
    print(f"문항 {m['n']}  |  도구 호출 적절성 {m['tool_call_accuracy']}  |  답변 적절성 {m['answer_accuracy']}"
          f"  |  분류 정확도 {c['accuracy']}  macro F1 {c['macro_f1']}  |  파이프라인 비용 {m['pipeline_cost_usd']}달러")
    print("\n혼동행렬 (행=정답, 열=예측)")
    print("        " + "".join(f"{SHORT[p]:>7}" for p in LABELS))
    for t in LABELS:
        print(f"{SHORT[t]:>6}  " + "".join(f"{c['confusion_matrix'][t][p]:>7}" for p in LABELS))
    print("\n틀린 문항")
    for r in run["records"]:
        if r["tool_pass"] and r["answer_pass"] and r["expected_category"] == r.get("category"):
            continue
        j = r["answer_judge"]
        print(f"- {r['id']:5} 분류 {SHORT[r['expected_category']]}→{SHORT.get(r.get('category'), r.get('category'))}"
              f" | 도구 {'O' if r['tool_pass'] else 'X'} {r.get('tools_called')} (기대 {r['expected_tools']})"
              f" | 답변 {'O' if r['answer_pass'] else 'X'} {j.get('detail', '')}"
              f"{' 누락:' + str(j['missing_facts']) if j.get('missing_facts') else ''}"
              f"{' 위반:' + str(j['violated_rules']) if j.get('violated_rules') else ''}"
              f"{' 넘김사유:' + r['handoff_reason'] if r.get('handoff_reason') else ''}"
              f"{' 오류:' + r['error'] if r.get('error') else ''}")
    print(f"\n누적 비용(기록 파일): {agent.ledger_total():.6f}달러")


def validate_judge() -> bool:
    """채점기 자체 검증.
    양성: 모범 답안은 전부 1점이어야 한다.
    음성: 일부러 틀리게 만든 답은 전부 0점이어야 한다.
      - 누락형: 답해야 하는 문항에 다른 문항의 모범 답안을 넣어 필수 사실을 빠뜨림
      - 위반형: 모범 답안 끝에 금지 사항에 해당하는 틀린 주장(negative_claim)을 덧붙임
      - 넘기기형: 넘겨야 하는 문항에 다른 문항의 모범 답안(실제 답변)을 넣음
    하나라도 기대와 다르면 채점기를 믿지 않는다.
    """
    items = load_items(split=None)
    answerable = [i for i in items if not expects_handoff(i)]
    handoff_items = [i for i in items if expects_handoff(i)]
    cases = [("양성(모범 답안)", i, i["gold_answer"], True) for i in items]
    cases += [("음성-누락형", i, answerable[(n + 1) % len(answerable)]["gold_answer"], False)
              for n, i in enumerate(answerable)]
    cases += [("음성-위반형", i, f"{i['gold_answer']} {i['negative_claim']}", False)
              for i in items if i.get("negative_claim")]
    cases += [("음성-넘기기형", i, answerable[n % len(answerable)]["gold_answer"], False)
              for n, i in enumerate(handoff_items)]

    summary: dict[str, list[int]] = {}
    for kind, item, text, expected in cases:
        res = judge_answer(item, text)
        ok = res["passed"] == expected
        tally = summary.setdefault(kind, [0, 0])
        tally[0] += ok
        tally[1] += 1
        if not ok:
            detail = res.get("detail") or f"누락 {res.get('missing_facts')} 위반 {res.get('violated_rules')}"
            print(f"  ✗ {kind} {item['id']:5} [{res['method']}] 기대 {'통과' if expected else '실패'}, 결과 반대: {detail}")

    print("\n채점기 검증")
    for kind, (ok, total) in summary.items():
        print(f"  {kind:14} {ok}/{total} 기대대로")
    print(f"누적 비용 {agent.ledger_total():.6f}달러")
    return all(ok == total for ok, total in summary.values())


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if cmd == "validate-judge":
            sys.exit(0 if validate_judge() else 1)
        elif cmd == "run" and len(sys.argv) >= 4:
            run_pipeline(sys.argv[2], sys.argv[3])
            score(sys.argv[2])
        elif cmd == "score" and len(sys.argv) >= 3:
            score(sys.argv[2])
        else:
            print(__doc__)
    except (agent.MissingApiKey, agent.CostLimitExceeded) as e:
        print(f"실행 중단: {e}")
        sys.exit(1)
