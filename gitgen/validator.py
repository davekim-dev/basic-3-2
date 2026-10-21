"""AI 응답 파싱, 형식/길이 검증, 후처리."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

COMMIT_TITLE_RECOMMENDED = 50
COMMIT_TITLE_MAX = 72
PR_TITLE_MAX = 80
PR_SECTIONS = ("Why", "What", "How to Test")
_SECTION_KEYS = {"Why": "why", "What": "what", "How to Test": "how_to_test"}


class ParseError(Exception):
    pass


@dataclass
class CommitDraft:
    title: str
    body: list[str] = field(default_factory=list)

    def render(self) -> str:
        if not self.body:
            return self.title
        return self.title + "\n\n" + "\n".join(f"- {b}" for b in self.body)


@dataclass
class PRDraft:
    title: str
    why: list[str] = field(default_factory=list)
    what: list[str] = field(default_factory=list)
    how_to_test: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)

    def render_body(self) -> str:
        out: list[str] = []
        for header in PR_SECTIONS:
            out.append(f"## {header}")
            out.extend(f"- {b}" for b in getattr(self, _SECTION_KEYS[header]))
            out.append("")
        if self.checklist:
            out.append("## Checklist")
            out.extend(f"- [ ] {c}" for c in self.checklist)
            out.append("")
        return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------

def extract_json(text: str) -> dict:
    """응답에서 JSON 객체를 꺼낸다 (코드 블록이나 앞뒤 설명이 섞여 있어도 처리)."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = [fence.group(1)] if fence else []
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            data = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ParseError("AI 응답에서 JSON 을 찾지 못했습니다.")


def _str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    items = []
    for v in value:
        s = clean_bullet(str(v))
        if s:
            items.append(s)
    return items


def clean_bullet(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+|\[[ x]\]\s+)", "", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(s: str) -> str:
    s = str(s).strip().splitlines()[0] if str(s).strip() else ""
    s = re.sub(r"^#+\s*", "", s)  # markdown 헤더 기호
    s = s.strip().strip("`\"'").strip()
    s = re.sub(r"\s+", " ", s)
    return s.rstrip(".。 ")


def parse_commit(text: str) -> CommitDraft:
    data = extract_json(text)
    return CommitDraft(title=clean_title(data.get("title", "")), body=_str_list(data.get("body")))


def parse_pr(text: str) -> PRDraft:
    data = extract_json(text)
    return PRDraft(
        title=clean_title(data.get("title", "")),
        why=_str_list(data.get("why")),
        what=_str_list(data.get("what")),
        how_to_test=_str_list(data.get("how_to_test") or data.get("how")),
    )


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------

_TYPE_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?!?: \S")


@dataclass
class Issue:
    level: str  # "error" -> 재생성/후처리 대상, "warn" -> 안내만
    message: str


def validate_commit(d: CommitDraft, types: list[str] | None = None, scope: str = "optional") -> list[Issue]:
    issues: list[Issue] = []
    n = len(d.title)
    if not d.title:
        issues.append(Issue("error", "커밋 제목이 비어 있습니다."))
    elif n > COMMIT_TITLE_MAX:
        issues.append(Issue("error", f"커밋 제목이 {n}자로 최대 {COMMIT_TITLE_MAX}자를 넘습니다."))
    elif n > COMMIT_TITLE_RECOMMENDED:
        issues.append(Issue("warn", f"커밋 제목이 {n}자로 권장 {COMMIT_TITLE_RECOMMENDED}자를 넘습니다."))
    m = _TYPE_RE.match(d.title)
    if d.title and not m:
        issues.append(Issue("error", "커밋 제목이 `type: 요약` 또는 `type(scope): 요약` 형식이 아닙니다."))
    elif m:
        if types and m.group("type") not in types:
            issues.append(Issue("error", f"허용되지 않은 type '{m.group('type')}' (허용: {', '.join(types)})"))
        if scope == "required" and not m.group("scope"):
            issues.append(Issue("error", "팀 컨벤션상 scope 가 필요합니다: type(scope): 요약"))
        if scope == "none" and m.group("scope"):
            issues.append(Issue("error", "팀 컨벤션상 scope 를 쓰지 않습니다: type: 요약"))
    if len(d.body) > 5:
        issues.append(Issue("warn", f"커밋 본문 불릿이 {len(d.body)}개로 많습니다 (1~3개 권장)."))
    return issues


def validate_pr(d: PRDraft) -> list[Issue]:
    issues: list[Issue] = []
    if not d.title:
        issues.append(Issue("error", "PR 제목이 비어 있습니다."))
    elif len(d.title) > PR_TITLE_MAX:
        issues.append(Issue("error", f"PR 제목이 {len(d.title)}자로 최대 {PR_TITLE_MAX}자를 넘습니다."))
    issues.extend(validate_pr_body(d.render_body()))
    return issues


def validate_pr_body(body: str) -> list[Issue]:
    """렌더링된 PR 본문 텍스트 검증: 섹션 헤더 존재 + 섹션마다 불릿 1개 이상."""
    issues: list[Issue] = []
    sections: dict[str, list[str]] = {}
    current = None
    for line in body.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            current = h.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    for name in PR_SECTIONS:
        if name not in sections:
            issues.append(Issue("error", f"PR 본문에 '## {name}' 섹션이 없습니다."))
        elif not any(re.match(r"^\s*[-*]\s+\S", ln) for ln in sections[name]):
            issues.append(Issue("error", f"PR 본문 '{name}' 섹션에 불릿이 없습니다."))
    return issues


# ---------------------------------------------------------------------------
# 후처리 (재생성 후에도 남은 위반을 규칙에 맞게 다듬기)
# ---------------------------------------------------------------------------

def shorten(text: str, limit: int) -> str:
    """단어 경계에서 limit 글자 이내로 자른다."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.:;-—·")


def fix_commit(d: CommitDraft) -> list[str]:
    notes = []
    if len(d.title) > COMMIT_TITLE_MAX:
        d.title = shorten(d.title, COMMIT_TITLE_MAX)
        notes.append(f"커밋 제목을 {COMMIT_TITLE_MAX}자 이내로 줄였습니다.")
    if len(d.body) > 5:
        d.body = d.body[:3]
        notes.append("커밋 본문 불릿을 3개로 줄였습니다.")
    return notes


def fix_pr(d: PRDraft) -> list[str]:
    notes = []
    if len(d.title) > PR_TITLE_MAX:
        d.title = shorten(d.title, PR_TITLE_MAX)
        notes.append(f"PR 제목을 {PR_TITLE_MAX}자 이내로 줄였습니다.")
    for header in PR_SECTIONS:
        key = _SECTION_KEYS[header]
        if not getattr(d, key):
            setattr(d, key, ["(TODO: 작성 필요 - AI 가 근거를 찾지 못했습니다)"])
            notes.append(f"비어 있던 '{header}' 섹션에 TODO 불릿을 넣었습니다.")
    return notes
