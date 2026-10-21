"""AI 로 보내기 전 diff 정리: 민감 파일 제외, 마스킹(safe-mode), 전송량 제한."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field

from .git_collector import FileDiff

# 항상 내용을 보내지 않는 파일 (safe-mode 여부와 무관)
SENSITIVE_FILE_PATTERNS = [
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "*credentials*", "*secret*", ".npmrc", ".pypirc", ".netrc",
]
# 내용이 길기만 하고 요약에 도움이 안 되는 파일
NOISE_FILE_PATTERNS = [
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock",
    "*.min.js", "*.min.css", "*.map",
]

# (이름, 정규식, 치환 문자열) — 위에서부터 순서대로 적용
DEFAULT_MASK_RULES: list[tuple[str, str, str]] = [
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "[MASKED_PRIVATE_KEY]"),
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{10,}", "[MASKED_API_KEY]"),
    ("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}", "[MASKED_API_KEY]"),
    ("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", "[MASKED_TOKEN]"),
    ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", "[MASKED_AWS_KEY]"),
    ("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}\b", "[MASKED_API_KEY]"),
    ("slack_token", r"\bxox[abposr]-[A-Za-z0-9\-]{10,}", "[MASKED_TOKEN]"),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}", "[MASKED_JWT]"),
    (
        "secret_assignment",
        r"(?i)\b([A-Za-z0-9_\-]*(?:api[_\-]?key|secret|token|passw(?:or)?d|pwd|access[_\-]?key|auth)[A-Za-z0-9_\-]*)"
        r"(\s*[:=]\s*)([\"']?)(?!\[MASKED)([^\s\"',;]{6,})\3",
        r"\1\2\3[MASKED_SECRET]\3",
    ),
    ("email", r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "[MASKED_EMAIL]"),
    ("kr_rrn", r"\b\d{6}-[1-4]\d{6}\b", "[MASKED_RRN]"),
    ("phone", r"\b01[016789]-?\d{3,4}-?\d{4}\b", "[MASKED_PHONE]"),
    ("card_number", r"\b(?:\d{4}[- ]){3}\d{4}\b", "[MASKED_CARD]"),
]


@dataclass
class SafePolicy:
    enabled: bool = False
    max_files: int = 10  # safe-mode: 내용까지 보내는 최대 파일 수
    max_lines: int = 200  # safe-mode: 전송하는 diff 최대 줄 수 (전체 합)
    max_chars: int = 60_000  # 항상 적용: 비용 보호용 전체 문자 수 상한
    extra_patterns: list[tuple[str, str, str]] = field(default_factory=list)
    exclude_files: list[str] = field(default_factory=list)


@dataclass
class PreparedDiff:
    text: str
    sent_files: list[str]
    omitted: list[tuple[str, str]]  # (경로, 사유)
    mask_counts: dict[str, int]
    sent_lines: int
    truncated: bool

    @property
    def masked_total(self) -> int:
        return sum(self.mask_counts.values())


def _matches(path: str, patterns: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, p) or fnmatch.fnmatch(path, p) for p in patterns)


def mask_text(text: str, rules: list[tuple[str, str, str]]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for name, pattern, repl in rules:
        text, n = re.subn(pattern, repl, text)
        if n:
            counts[name] = counts.get(name, 0) + n
    return text, counts


def prepare_diff(diffs: list[FileDiff], policy: SafePolicy) -> PreparedDiff:
    rules = DEFAULT_MASK_RULES + policy.extra_patterns
    sent: list[str] = []
    omitted: list[tuple[str, str]] = []
    counts: dict[str, int] = {}
    chunks: list[str] = []
    lines_used = 0
    chars_used = 0
    truncated = False

    for d in diffs:
        if _matches(d.path, SENSITIVE_FILE_PATTERNS + policy.exclude_files):
            omitted.append((d.path, "민감 파일 - 내용 제외"))
            continue
        if _matches(d.path, NOISE_FILE_PATTERNS):
            omitted.append((d.path, "lock/빌드 산출물 - 내용 생략"))
            continue
        if "\nBinary files " in d.text or d.text.startswith("Binary files "):
            omitted.append((d.path, "바이너리 파일"))
            continue
        if policy.enabled and len(sent) >= policy.max_files:
            omitted.append((d.path, f"safe-mode 파일 수 제한({policy.max_files}개) 초과"))
            continue

        text = d.text
        if policy.enabled:
            text, c = mask_text(text, rules)
            for k, v in c.items():
                counts[k] = counts.get(k, 0) + v

        lines = text.splitlines(keepends=True)
        kept = len(lines)
        if policy.enabled:
            remaining = policy.max_lines - lines_used
            if remaining <= 0:
                omitted.append((d.path, f"safe-mode 줄 수 제한({policy.max_lines}줄) 초과"))
                continue
            if len(lines) > remaining:
                kept = remaining
                lines = lines[:remaining] + [f"... (safe-mode: {len(lines) - remaining}줄 생략)\n"]
                truncated = True

        chunk = "".join(lines)
        if chars_used + len(chunk) > policy.max_chars:
            room = policy.max_chars - chars_used
            if room < 500:
                omitted.append((d.path, f"전송량 상한({policy.max_chars}자) 초과"))
                truncated = True
                continue
            chunk = chunk[:room].rsplit("\n", 1)[0] + "\n... (전송량 상한으로 이후 생략)\n"
            kept = min(kept, chunk.count("\n") - 1)
            truncated = True

        chunks.append(chunk)
        sent.append(d.path)
        lines_used += kept
        chars_used += len(chunk)

    return PreparedDiff(
        text="".join(chunks),
        sent_files=sent,
        omitted=omitted,
        mask_counts=counts,
        sent_lines=lines_used,
        truncated=truncated,
    )
