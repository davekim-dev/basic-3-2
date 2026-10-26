"""CLI: python main.py commit | pr [옵션]."""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .ai_client import AIError, AIRequest, ClaudeClient, get_api_key
from .config import ConfigError, load_config
from .git_collector import GitChanges, GitError, collect_commit_changes, collect_pr_changes, ensure_repo_root
from .prompts import SYSTEM_PROMPT, build_commit_prompt, build_pr_prompt, build_retry_prompt
from .safe_mode import SafePolicy, prepare_diff
from .validator import (
    CommitDraft,
    ParseError,
    PRDraft,
    fix_commit,
    fix_pr,
    parse_commit,
    parse_pr,
    validate_commit,
    validate_pr,
)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 8000
DEFAULT_EFFORT = "medium"
SEP = "-" * 60


# ---------------------------------------------------------------------------
# 출력 도우미 (로그는 stderr, 결과물은 stdout)
# ---------------------------------------------------------------------------

def log(tag: str, msg: str) -> None:
    print(f"[{tag}] {msg}", file=sys.stderr, flush=True)


def section(title: str, body: str) -> None:
    print(f"\n--- {title} ---")
    print(body.rstrip())


# ---------------------------------------------------------------------------
# 인자
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    g = common.add_argument_group("AI API 파라미터")
    g.add_argument("--model", default=None, help=f"모델 ID (기본: {DEFAULT_MODEL}, 환경변수 AI_MODEL 로도 지정)")
    g.add_argument("--temperature", type=float, default=None,
                   help="0.0~1.0 샘플링 온도 (기본: 보내지 않음=모델 기본값). 최신 Claude 모델은 미지원")
    g.add_argument("--max-tokens", type=int, default=None, help=f"응답 최대 토큰 (기본: {DEFAULT_MAX_TOKENS})")
    g.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                   help=f"추론 깊이/토큰 사용량 (기본: {DEFAULT_EFFORT})")
    g.add_argument("--timeout", type=float, default=120.0, help="요청 타임아웃(초, 기본 120)")
    g.add_argument("--max-calls", type=int, choices=[1, 2], default=2,
                   help="실행당 최대 API 호출 수. 2면 형식 위반 시 1회 재생성 (기본 2)")

    s = common.add_argument_group("민감정보 / 전송량")
    s.add_argument("--safe-mode", action="store_true", help="키/이메일 등 마스킹 + diff 전송량 제한")
    s.add_argument("--max-files", type=int, default=None, help="safe-mode: 내용을 보낼 최대 파일 수 (기본 10)")
    s.add_argument("--max-lines", type=int, default=None, help="safe-mode: 보낼 diff 최대 줄 수 (기본 200)")
    s.add_argument("--max-chars", type=int, default=None, help="항상 적용되는 diff 최대 문자 수 (기본 60000)")

    o = common.add_argument_group("기타")
    o.add_argument("-c", "--context", default=None, help='변경 이유/요구사항 메모 (예: -c "로그인 실패 버그 #12 대응")')
    o.add_argument("--lang", choices=["ko", "en"], default=None, help="출력 언어 (기본 ko)")
    o.add_argument("--config", default=None, help="컨벤션 설정 파일 경로 (기본: ./.ai-gitgen.json)")
    o.add_argument("--no-config", action="store_true", help="설정 파일 무시 (기본 컨벤션 사용)")
    o.add_argument("--dry-run", action="store_true", help="API 를 호출하지 않고 전송될 프롬프트만 출력")
    o.add_argument("--save", metavar="PATH", default=None, help="결과를 파일로 저장 (예: git commit -F PATH)")

    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="git status / git diff 를 읽어 AI 로 커밋 메시지와 PR 초안을 생성합니다.",
    )
    parser.add_argument("--version", action="version", version=f"ai-gitgen {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="{commit,pr}")

    pc = sub.add_parser("commit", parents=[common], help="커밋 메시지 생성")
    pc.add_argument("--staged", action="store_true", help="스테이징된 변경만 사용 (git diff --cached)")

    pp = sub.add_parser("pr", parents=[common], help="PR 제목/본문 초안 생성")
    pp.add_argument("--base", default=None, help="비교 기준 브랜치 (기본: main 또는 master)")
    return parser


# ---------------------------------------------------------------------------
# 실행 흐름
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except GitError as exc:
        log("ERROR", f"Git 오류: {exc}")
    except ConfigError as exc:
        log("ERROR", str(exc))
    except AIError as exc:
        log("ERROR", str(exc))
    except KeyboardInterrupt:
        log("ERROR", "사용자가 중단했습니다.")
        return 130
    return 1


def run(args: argparse.Namespace) -> int:
    cwd = os.getcwd()
    root = ensure_repo_root(cwd)
    cfg = load_config(root, args.config, args.no_config)
    conv = cfg.convention
    if args.lang:
        conv.language = args.lang
    defaults = cfg.defaults

    model = args.model or os.environ.get("AI_MODEL") or defaults.get("model") or DEFAULT_MODEL
    max_tokens = args.max_tokens or defaults.get("max_tokens") or DEFAULT_MAX_TOKENS
    effort = args.effort or defaults.get("effort") or DEFAULT_EFFORT
    temperature = args.temperature if args.temperature is not None else defaults.get("temperature")
    if temperature is not None and not 0.0 <= temperature <= 1.0:
        log("ERROR", "--temperature 는 0.0~1.0 사이여야 합니다.")
        return 2

    sm = cfg.safe_mode
    policy = SafePolicy(
        enabled=args.safe_mode or bool(sm.get("enabled", False)),
        max_files=args.max_files or sm.get("max_files", 10),
        max_lines=args.max_lines or sm.get("max_lines", 200),
        max_chars=args.max_chars or sm.get("max_chars", 60_000),
        extra_patterns=[(p["name"], p["pattern"], p.get("replace", f"[MASKED_{p['name'].upper()}]"))
                        for p in sm.get("extra_patterns", [])],
        exclude_files=list(sm.get("exclude_files", [])),
    )

    # 1) Git 변경 사항 수집 ----------------------------------------------------
    if args.command == "commit":
        changes = collect_commit_changes(cwd, staged_only=args.staged)
    else:
        changes = collect_pr_changes(cwd, base=args.base)
        log("INFO", f"현재 브랜치: {changes.branch}" + (f" (base: {changes.base})" if changes.base else ""))

    if changes.is_empty():
        what = "커밋 메시지" if args.command == "commit" else "PR 초안"
        log("INFO", f"변경 사항이 없습니다. {what}를 생성하지 않고 종료합니다.")
        return 0

    log("INFO", f"Git status 수집 완료: {len(changes.files)}개 파일 변경 감지")
    log("INFO", f"Git diff 수집 완료: {changes.total_lines}줄 (+{changes.added}/-{changes.removed}) [{changes.diff_scope}]")

    # 2) diff 정리: 민감 파일 제외, safe-mode 마스킹/제한 ------------------------
    prepared = prepare_diff(changes.diffs, policy)
    if policy.enabled:
        log("INFO", f"safe-mode ON: 파일 최대 {policy.max_files}개 / 최대 {policy.max_lines}줄, "
                    f"마스킹 {prepared.masked_total}건 {dict(prepared.mask_counts) or ''}".rstrip())
    for path, why in prepared.omitted:
        log("INFO", f"내용 제외: {path} ({why})")
    log("INFO", f"AI 전송 diff: 파일 {len(prepared.sent_files)}개, {prepared.sent_lines}줄, {len(prepared.text)}자")

    if args.command == "commit":
        prompt = build_commit_prompt(changes, prepared, conv, args.context)
    else:
        prompt = build_pr_prompt(changes, prepared, conv, args.context)

    if args.dry_run:
        log("INFO", "--dry-run: API 를 호출하지 않습니다. 전송될 프롬프트:")
        section("System Prompt", SYSTEM_PROMPT)
        section("User Prompt", prompt)
        print(SEP)
        log("INFO", "AI API 호출 횟수: 0회")
        return 0

    # 3) AI API 호출 (+ 형식 위반 시 1회 재생성) ----------------------------------
    client = ClaudeClient(get_api_key())
    temp_label = "model default" if temperature is None else temperature
    log("INFO", f"API 주소: {client.base_url}")
    log("INFO", f"모델: {model}, temperature={temp_label}, max_tokens={max_tokens}, effort={effort}")

    def ask(p: str):
        log("INFO", f"AI API 요청 중... ({client.calls + 1}/{args.max_calls})")
        r = client.complete(AIRequest(SYSTEM_PROMPT, p, model, max_tokens, temperature, effort, args.timeout))
        log("INFO", f"응답 수신: 입력 {r.input_tokens} / 출력 {r.output_tokens} 토큰, stop_reason={r.stop_reason}")
        return r

    is_commit = args.command == "commit"
    types = list(conv.commit_types)
    draft: CommitDraft | PRDraft | None = None
    current_prompt = prompt
    while True:
        resp = ask(current_prompt)
        try:
            draft = parse_commit(resp.text) if is_commit else parse_pr(resp.text)
            issues = validate_commit(draft, types, conv.scope) if is_commit else validate_pr(draft)
            problems = [i.message for i in issues if i.level == "error"]
        except ParseError as exc:
            draft, problems = None, [str(exc) + " 지정된 JSON 형식만 출력하세요."]
        if not problems or client.calls >= args.max_calls:
            break
        log("WARN", "형식 규칙 위반 → 1회 재생성: " + "; ".join(problems))
        current_prompt = build_retry_prompt(prompt, resp.text, problems)

    if draft is None:
        raise AIError("AI 응답을 해석하지 못했습니다. 응답 원문:\n" + resp.text[:1000])

    # 4) 후처리 + 최종 검증 -------------------------------------------------------
    notes = fix_commit(draft) if is_commit else fix_pr(draft)
    if not is_commit:
        draft.checklist = conv.pr_checklist
    for n in notes:
        log("WARN", f"후처리: {n}")
    final_issues = validate_commit(draft, types, conv.scope) if is_commit else validate_pr(draft)

    log("DONE", ("커밋 메시지" if is_commit else "PR 초안") + " 생성 완료")
    log("INFO", f"AI API 호출 횟수: {client.calls}회")

    # 5) 출력 ------------------------------------------------------------------
    if is_commit:
        text = draft.render()
        section("Commit Message", text)
        print(SEP)
    else:
        section("PR Title", draft.title)
        section("PR Body", draft.render_body())
        print(SEP)
        text = f"{draft.title}\n\n{draft.render_body()}"

    _print_checks(draft, final_issues, is_commit)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
        hint = f"git commit -F {args.save}" if is_commit else "PR 작성 화면에 붙여넣기"
        log("INFO", f"저장됨: {args.save} (적용: {hint})")
    log("INFO", "생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.")
    return 0


def _print_checks(draft, issues, is_commit: bool) -> None:
    if is_commit:
        n = len(draft.title)
        checks = [f"제목 길이 {n}자 (권장 ≤50, 최대 72)", f"본문 불릿 {len(draft.body)}개"]
    else:
        checks = [
            f"PR 제목 {len(draft.title)}자 (최대 80)",
            f"Why {len(draft.why)} / What {len(draft.what)} / How to Test {len(draft.how_to_test)} 불릿",
        ]
    status = "통과" if not [i for i in issues if i.level == "error"] else "확인 필요"
    print(f"[검증 {status}] " + " · ".join(checks), file=sys.stderr)
    for i in issues:
        print(f"  - ({i.level}) {i.message}", file=sys.stderr)
