"""Git 변경 사항 수집 (git status / git diff 만 사용)."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field


class GitError(Exception):
    """Git 명령 실행 실패."""


@dataclass
class FileChange:
    """git status 한 줄."""

    path: str
    index: str  # 스테이징 영역 상태 (X)
    worktree: str  # 작업 트리 상태 (Y)
    orig_path: str | None = None  # rename 이전 경로

    @property
    def untracked(self) -> bool:
        return self.index == "?"

    @property
    def staged(self) -> bool:
        return self.index not in (" ", "?")

    @property
    def label(self) -> str:
        code = (self.index if self.staged else self.worktree).strip() or "M"
        return {
            "M": "modified",
            "A": "added",
            "D": "deleted",
            "R": "renamed",
            "C": "copied",
            "U": "conflict",
            "?": "untracked",
        }.get(code, code)


@dataclass
class FileDiff:
    """파일 하나의 diff 블록."""

    path: str
    text: str
    added: int = 0
    removed: int = 0
    note: str | None = None  # 내용 생략 사유 등

    @property
    def line_count(self) -> int:
        return self.text.count("\n") + (0 if self.text.endswith("\n") or not self.text else 1)


@dataclass
class GitChanges:
    branch: str
    files: list[FileChange]
    diffs: list[FileDiff] = field(default_factory=list)
    diff_scope: str = ""  # 어떤 기준으로 diff 를 수집했는지 (로그용)
    base: str | None = None  # PR 비교 기준 브랜치

    @property
    def total_lines(self) -> int:
        return sum(d.line_count for d in self.diffs)

    @property
    def added(self) -> int:
        return sum(d.added for d in self.diffs)

    @property
    def removed(self) -> int:
        return sum(d.removed for d in self.diffs)

    def is_empty(self) -> bool:
        return not self.files and not any(d.text.strip() for d in self.diffs)


def run_git(args: list[str], cwd: str | None = None, ok_codes: tuple[int, ...] = (0,)) -> str:
    cmd = ["git", "-c", "core.quotepath=false", *args]
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError as exc:
        raise GitError("git 명령을 찾을 수 없습니다. Git 이 설치되어 있는지 확인하세요.") from exc
    if result.returncode not in ok_codes:
        msg = result.stderr.strip() or f"git {' '.join(args)} 실패 (exit {result.returncode})"
        raise GitError(msg)
    return result.stdout


def ensure_repo_root(cwd: str) -> str:
    """현재 디렉토리가 Git 프로젝트 루트인지 확인하고 루트 경로를 돌려준다."""
    try:
        top = run_git(["rev-parse", "--show-toplevel"], cwd=cwd).strip()
    except GitError as exc:
        raise GitError(
            "Git 저장소가 아닙니다. `git init` 된 프로젝트 루트 디렉토리에서 실행하세요."
        ) from exc
    if os.path.realpath(top) != os.path.realpath(cwd):
        raise GitError(f"프로젝트 루트 디렉토리에서 실행하세요: cd {top}")
    return top


# ---------------------------------------------------------------------------
# git status
# ---------------------------------------------------------------------------

def parse_status(output: str) -> tuple[str, list[FileChange]]:
    """`git status --porcelain=v1 --branch` 출력 파싱."""
    branch = "HEAD"
    files: list[FileChange] = []
    for line in output.splitlines():
        if line.startswith("## "):
            head = line[3:]
            for prefix in ("No commits yet on ", "Initial commit on "):
                head = head.removeprefix(prefix)
            branch = head.split("...", 1)[0].split(" ", 1)[0]
            if "(no branch)" in line:
                branch = "HEAD (detached)"
            continue
        if len(line) < 4:
            continue
        x, y, rest = line[0], line[1], line[3:]
        orig = None
        if " -> " in rest and (x in "RC" or y in "RC"):
            orig, rest = rest.split(" -> ", 1)
        files.append(FileChange(path=_unquote(rest), index=x, worktree=y, orig_path=_unquote(orig) if orig else None))
    return branch, files


def _unquote(path: str) -> str:
    if len(path) >= 2 and path[0] == path[-1] == '"':
        return path[1:-1].encode("latin-1", "backslashreplace").decode("unicode_escape")
    return path


def collect_status(cwd: str) -> tuple[str, list[FileChange]]:
    return parse_status(run_git(["status", "--porcelain=v1", "--branch", "--untracked-files=all"], cwd=cwd))


# ---------------------------------------------------------------------------
# git diff
# ---------------------------------------------------------------------------

_DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")


def split_diff(text: str) -> list[FileDiff]:
    """하나의 diff 텍스트를 파일 단위 블록으로 나눈다."""
    diffs: list[FileDiff] = []
    current: list[str] = []
    path = ""

    def flush() -> None:
        if current:
            body = "".join(current)
            added = sum(1 for ln in current if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in current if ln.startswith("-") and not ln.startswith("---"))
            diffs.append(FileDiff(path=path, text=body, added=added, removed=removed))

    for line in text.splitlines(keepends=True):
        m = _DIFF_HEADER_RE.match(line.rstrip("\n"))
        if m:
            flush()
            current = [line]
            path = m.group("b")
        elif current:
            current.append(line)
    flush()
    return diffs


def _has_head(cwd: str) -> bool:
    try:
        run_git(["rev-parse", "--verify", "--quiet", "HEAD"], cwd=cwd)
        return True
    except GitError:
        return False


def _ref_exists(ref: str, cwd: str) -> bool:
    try:
        run_git(["rev-parse", "--verify", "--quiet", ref], cwd=cwd)
        return True
    except GitError:
        return False


_DIFF_OPTS = ["--no-color", "--no-ext-diff", "-M"]


def _untracked_diffs(files: list[FileChange], cwd: str) -> list[FileDiff]:
    """신규(untracked) 파일 내용을 `git diff --no-index` 로 가져온다."""
    out: list[FileDiff] = []
    for f in files:
        if not f.untracked:
            continue
        text = run_git(["diff", *_DIFF_OPTS, "--no-index", "--", os.devnull, f.path], cwd=cwd, ok_codes=(0, 1))
        out.extend(split_diff(text))
    return out


def _working_tree_diffs(files: list[FileChange], cwd: str) -> list[FileDiff]:
    """HEAD 대비 스테이징 + 작업 트리 변경 전체, 그리고 신규 파일."""
    if _has_head(cwd):
        text = run_git(["diff", "HEAD", *_DIFF_OPTS], cwd=cwd)
    else:  # 첫 커밋 전
        text = run_git(["diff", "--cached", *_DIFF_OPTS], cwd=cwd) + run_git(["diff", *_DIFF_OPTS], cwd=cwd)
    return split_diff(text) + _untracked_diffs(files, cwd)


def collect_commit_changes(cwd: str, staged_only: bool = False) -> GitChanges:
    """커밋 메시지용 변경 사항.

    - 스테이징된 변경이 있거나 --staged 이면: `git diff --cached` (실제로 커밋될 내용)
    - 그 외: 작업 트리 전체 (`git diff HEAD` + 신규 파일)
    """
    branch, files = collect_status(cwd)
    staged = [f for f in files if f.staged]

    if staged_only or staged:
        text = run_git(["diff", "--cached", *_DIFF_OPTS], cwd=cwd)
        return GitChanges(branch=branch, files=staged, diffs=split_diff(text), diff_scope="staged (git diff --cached)")

    diffs = _working_tree_diffs(files, cwd)
    return GitChanges(branch=branch, files=files, diffs=diffs, diff_scope="working tree (git diff HEAD + untracked)")


def default_base(cwd: str) -> str | None:
    for name in ("main", "master", "origin/main", "origin/master"):
        if _ref_exists(name, cwd):
            return name
    return None


def collect_pr_changes(cwd: str, base: str | None = None) -> GitChanges:
    """PR 초안용 변경 사항: base...HEAD 커밋 diff + 아직 커밋하지 않은 작업 트리 변경."""
    branch, files = collect_status(cwd)
    base = base or default_base(cwd)
    if base and not _ref_exists(base, cwd):
        raise GitError(f"비교 기준 브랜치 '{base}' 를 찾을 수 없습니다. --base 옵션을 확인하세요.")

    diffs: list[FileDiff] = []
    scope_parts: list[str] = []
    if base and _has_head(cwd) and branch != base:
        text = run_git(["diff", *_DIFF_OPTS, f"{base}...HEAD"], cwd=cwd)
        diffs.extend(split_diff(text))
        scope_parts.append(f"git diff {base}...HEAD")

    # 아직 커밋하지 않은 변경도 포함
    if files:
        wt_diffs = _working_tree_diffs(files, cwd)
        if wt_diffs:
            scope_parts.append("uncommitted changes (git diff HEAD)")
            diffs.extend(wt_diffs)

    return GitChanges(
        branch=branch,
        files=files,
        diffs=diffs,
        diff_scope=" + ".join(scope_parts) or "(none)",
        base=base,
    )
