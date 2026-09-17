"""문서 쪼개기 · 카테고리별 근거 조립.

공고문을 장·항목 제목(앵커) 기준으로 섹션으로 나누고,
매핑표(SECTIONS)에 따라 카테고리마다 해당 섹션만 모아 근거로 만든다.
LLM 호출 없음.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf

from config import DOC_PATH

CATEGORIES = {
    "eligibility": "신청 자격",
    "application": "신청 절차·서류",
    "evaluation": "선정 평가",
    "support": "지원 내용·선정 후 의무",
    "out_of_scope": "범위 밖",
}

# 도구 이름 -> 카테고리 (out_of_scope 는 조회 도구 없음)
TOOL_CATEGORY = {
    "search_eligibility": "eligibility",
    "search_application": "application",
    "search_evaluation": "evaluation",
    "search_support": "support",
}
CATEGORY_TOOL = {c: t for t, c in TOOL_CATEGORY.items()}

# 매핑표: (섹션 ID, 제목, 카테고리, 시작 앵커). 문서 순서대로 나열한다.
# 섹션은 앵커부터 다음 섹션의 앵커 직전까지다.
SECTIONS = [
    ("S01", "사업개요", "support", None),  # 문서 처음(표지 포함)부터
    ("S02", "세부 지원내용", "support", "세부 지원내용"),
    ("S03", "신청자격 및 자격 예외 요건", "eligibility", "신청자격 및 요건"),
    ("S04", "신청분야 · 신청 제외 대상", "eligibility", "□ 신청분야"),
    ("S05", "접수방법 · 제출서류", "application", "접수방법"),
    ("S06", "평가 및 선정절차 · 평가지표", "evaluation", "평가 및 선정절차"),
    ("S07", "사업 운영일정", "evaluation", "□ 사업 운영일정"),
    ("S08", "유의사항(부정수급 제재 등)", "support", "유의사항 ◈ 창업지원사업에 신청하는"),
    ("S09", "신청 시 유의사항", "application", "□ 신청 시 유의사항"),
    ("S10", "평가 중 유의사항", "evaluation", "□ 평가 중 유의사항"),
    ("S11", "선정 후 유의사항 · 선정자의 의무 및 책임", "support", "□ 선정 후 유의사항"),
    ("S12", "문의처(주관기관)", "application", "기타사항 □ 문의처"),
    ("S13", "붙임1 지원 제외 대상 업종", "eligibility", "붙임 1"),
    ("S14", "붙임2 지원 제외사업 목록", "eligibility", "붙임 2"),
    ("S15", "붙임3 2026년 동시수행 불가 사업 목록", "eligibility", "붙임 3"),
    ("S16", "붙임4 제3자 부당개입 주의 안내", "application", "붙임 4"),
]

PAGE_MARK = re.compile(r"^- (\d+) -$", re.M)
# 앵커 바로 앞에 붙은 쪽 표시("- 7 -")와 장 번호("5")는 다음 섹션에 속한다
LEAD_IN = re.compile(r"(?:- \d+ -\n)?(?:\d{1,2}\n)?\s*$")


@dataclass
class Section:
    id: str
    title: str
    category: str
    pages: list[int] = field(default_factory=list)
    text: str = ""


def _anchor_regex(anchor: str) -> re.Pattern:
    # 추출 텍스트는 공백·줄바꿈이 불규칙하므로 글자 사이 공백을 허용한다
    return re.compile(r"\s*".join(map(re.escape, anchor.replace(" ", ""))))


@lru_cache(maxsize=1)
def load_sections(doc_path: Path = DOC_PATH) -> tuple[Section, ...]:
    full = "".join(page.get_text() for page in pymupdf.open(doc_path))

    starts = []
    pos = 0
    for sid, _, _, anchor in SECTIONS:
        if anchor is None:
            starts.append(0)
            continue
        m = _anchor_regex(anchor).search(full, pos)
        if m is None:
            raise ValueError(f"{sid}: 앵커를 찾지 못함 - {anchor!r}")
        starts.append(LEAD_IN.search(full, pos, m.start()).start())
        pos = m.end()
    starts.append(len(full))

    marks = [(m.start(), int(m.group(1))) for m in PAGE_MARK.finditer(full)]

    def page_at(offset: int) -> int:
        return max((p for s, p in marks if s <= offset), default=1)

    sections = []
    for i, (sid, title, category, _) in enumerate(SECTIONS):
        begin, end = starts[i], starts[i + 1]
        first, last = page_at(begin), page_at(max(begin, end - 1))
        body = PAGE_MARK.sub("", full[begin:end])
        body = re.sub(r"\n\s*\n+", "\n", body).strip()
        sections.append(Section(sid, title, category, list(range(first, last + 1)), body))
    return tuple(sections)


def sections_for(category: str) -> list[Section]:
    return [s for s in load_sections() if s.category == category]


def build_context(category: str) -> tuple[str, list[Section]]:
    """카테고리의 근거 텍스트와 사용한 섹션 목록을 돌려준다. 범위 밖은 빈 근거."""
    used = sections_for(category)
    blocks = [f"[{s.id} {s.title} | 공고문 {_page_label(s.pages)}]\n{s.text}" for s in used]
    return "\n\n".join(blocks), used


def _page_label(pages: list[int]) -> str:
    return f"{pages[0]}쪽" if len(pages) == 1 else f"{pages[0]}~{pages[-1]}쪽"


if __name__ == "__main__":
    for cat, name in CATEGORIES.items():
        text, used = build_context(cat)
        print(f"{cat:13} {name:14} 섹션 {len(used):2}개  {len(text):6,}자  "
              + ", ".join(f"{s.id}({_page_label(s.pages)})" for s in used))
