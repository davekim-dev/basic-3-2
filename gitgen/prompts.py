"""프롬프트 설계: 역할 + 출력 규칙 + 팀 컨벤션 + 변경 맥락(git status/diff)."""

from __future__ import annotations

from .config import Convention
from .git_collector import GitChanges
from .safe_mode import PreparedDiff
from .validator import COMMIT_TITLE_MAX, COMMIT_TITLE_RECOMMENDED, PR_TITLE_MAX

LANG_NAME = {"ko": "한국어", "en": "English"}

SYSTEM_PROMPT = """\
당신은 코드 리뷰 경험이 별로 없는 주니어 개발자입니다. 주어진 Git 변경 사항(git status, git diff)을 읽고
팀원이 그대로 쓸 수 있는 커밋 메시지 또는 Pull Request 초안을 작성합니다.

원칙:
- diff 에 실제로 드러난 변경만 근거로 씁니다. 추측한 기능, 존재하지 않는 테스트, 이슈 번호를 지어내지 않습니다.
- "무엇을 바꿨는지"를 파일 나열이 아니라 의도와 효과 중심으로 요약합니다. 변경 이유를 알 수 있으면 함께 적습니다.
- [MASKED_...] 로 가려진 값이나 비밀번호·키·개인정보는 결과에 절대 옮기지 않습니다.
- 반드시 지정된 JSON 형식 하나만 출력합니다. JSON 앞뒤에 설명이나 코드 블록 표시를 붙이지 않습니다."""


def _convention_block(conv: Convention) -> str:
    lines = ["[팀 컨벤션]"]
    types = ", ".join(f"{t}({d})" if d else t for t, d in conv.commit_types.items())
    lines.append(f"- 허용 type: {types}")
    if conv.scope == "required":
        scope_rule = "scope 필수: `type(scope): 요약`"
    elif conv.scope == "none":
        scope_rule = "scope 를 쓰지 않음: `type: 요약`"
    else:
        scope_rule = "scope 는 선택: 변경이 한 모듈에 집중될 때만 `type(scope): 요약`, 아니면 `type: 요약`"
    lines.append(f"- {scope_rule}")
    if conv.scopes:
        lines.append(f"- 사용 가능한 scope: {', '.join(conv.scopes)}")
    lines.extend(f"- {r}" for r in conv.commit_rules)
    return "\n".join(lines)


def _changes_block(changes: GitChanges, prepared: PreparedDiff, context: str | None) -> str:
    parts: list[str] = []
    if context:
        parts.append(f"[작성자가 알려준 변경 이유/요구사항]\n{context.strip()}")
    parts.append(f"[브랜치] {changes.branch}" + (f" (base: {changes.base})" if changes.base else ""))

    status_lines = [f"- {f.label}: {f.path}" + (f" (from {f.orig_path})" if f.orig_path else "") for f in changes.files]
    diff_paths = {d.path for d in changes.diffs}
    listed = {f.path for f in changes.files}
    status_lines += [f"- committed on branch: {p}" for p in sorted(diff_paths - listed)]
    parts.append("[변경 파일 목록 (git status)]\n" + ("\n".join(status_lines) or "- (없음)"))
    parts.append(f"[변경 통계] +{changes.added} / -{changes.removed} 줄, 파일 {len(diff_paths)}개")

    if prepared.omitted:
        parts.append(
            "[내용을 보내지 않은 파일 - 파일명만 참고]\n"
            + "\n".join(f"- {p} ({why})" for p, why in prepared.omitted)
        )
    parts.append(f"[git diff]\n```diff\n{prepared.text.rstrip()}\n```" if prepared.text.strip() else "[git diff]\n(내용 없음)")
    return "\n\n".join(parts)


def build_commit_prompt(changes: GitChanges, prepared: PreparedDiff, conv: Convention, context: str | None = None) -> str:
    lang = LANG_NAME.get(conv.language, conv.language)
    body_rule = (
        '- body: 1~3개 불릿 문자열 배열. 각 불릿은 핵심 변경 1개를 한 문장으로, 가능하면 관련 파일/모듈명(1~3개)을 함께 언급. "- " 기호는 붙이지 않음.'
        if conv.commit_body
        else "- body: 빈 배열 [] (본문을 쓰지 않음)"
    )
    return f"""다음 변경 사항으로 커밋 메시지를 작성하세요.

[출력 규칙]
- title: `type(scope): 요약` 또는 `type: 요약` 형식 한 줄. {COMMIT_TITLE_RECOMMENDED}자 이내 권장, 절대 {COMMIT_TITLE_MAX}자를 넘지 않음.
- title 요약은 {lang}로, 마침표 없이, 명령형/명사형으로 끝냄 (예: "로그인 실패 시 재시도 로직 추가").
- 변경이 여러 성격이면 가장 중요한 변경을 기준으로 type 을 고름.
{body_rule}
- 언어: {lang}. 코드 식별자·파일명은 원문 유지.

{_convention_block(conv)}

[출력 JSON 형식]
{{"title": "feat: ...", "body": ["...", "..."]}}

{_changes_block(changes, prepared, context)}"""


def build_pr_prompt(changes: GitChanges, prepared: PreparedDiff, conv: Convention, context: str | None = None) -> str:
    lang = LANG_NAME.get(conv.language, conv.language)
    extra = "\n".join(f"- {r}" for r in conv.pr_rules)
    return f"""다음 브랜치의 변경 사항으로 Pull Request 제목과 본문 초안을 작성하세요.

[출력 규칙]
- title: 한 줄, 최대 {PR_TITLE_MAX}자. 형식: {conv.pr_title_format}
- why: 변경 배경/필요성 불릿 1~3개. diff 나 작성자 설명에서 근거를 찾을 수 없으면 diff 로부터 합리적으로 읽히는 목적만 씀.
- what: 핵심 변경 사항 불릿 2~5개. 리뷰어가 어디를 봐야 하는지 알 수 있게 파일/모듈명을 포함.
- how_to_test: 리뷰어가 따라 할 수 있는 확인 절차 불릿 1~4개 (실행 명령, 확인할 결과). diff 에 테스트 코드가 있으면 그 실행 방법을 우선.
- 불릿 문자열에는 "- " 기호를 붙이지 않음. 언어: {lang}. 코드 식별자·명령어는 원문 유지.
{extra}

{_convention_block(conv)}

[출력 JSON 형식]
{{"title": "...", "why": ["..."], "what": ["..."], "how_to_test": ["..."]}}

{_changes_block(changes, prepared, context)}"""


def build_retry_prompt(original_prompt: str, previous_output: str, problems: list[str]) -> str:
    """검증 실패 시 1회 재생성용 프롬프트 (이전 출력과 문제점을 함께 전달)."""
    plist = "\n".join(f"- {p}" for p in problems)
    return f"""{original_prompt}

[이전 출력]
{previous_output.strip()[:2000]}

[이전 출력의 규칙 위반 - 반드시 고쳐서 JSON 만 다시 출력]
{plist}"""
