"""팀 컨벤션 설정 파일(.ai-gitgen.json) 로드."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

CONFIG_FILENAME = ".ai-gitgen.json"

DEFAULT_COMMIT_TYPES = {
    "feat": "새 기능",
    "fix": "버그 수정",
    "docs": "문서 변경",
    "style": "포맷/세미콜론 등 동작 변화 없는 수정",
    "refactor": "동작 변화 없는 구조 개선",
    "perf": "성능 개선",
    "test": "테스트 추가/수정",
    "build": "빌드/의존성",
    "ci": "CI 설정",
    "chore": "기타 잡무",
}


@dataclass
class Convention:
    language: str = "ko"
    commit_types: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COMMIT_TYPES))
    scope: str = "optional"  # optional | required | none
    scopes: list[str] = field(default_factory=list)
    commit_body: bool = True
    commit_rules: list[str] = field(default_factory=list)
    pr_title_format: str = "<type>: <요약>"
    pr_rules: list[str] = field(default_factory=list)
    pr_checklist: list[str] = field(default_factory=list)
    source: str = "기본값 (Conventional Commits)"


@dataclass
class FileConfig:
    convention: Convention
    safe_mode: dict
    defaults: dict


class ConfigError(Exception):
    pass


def load_config(root: str, path: str | None = None, disabled: bool = False) -> FileConfig:
    conv = Convention()
    if disabled:
        return FileConfig(conv, {}, {})

    cfg_path = path or os.path.join(root, CONFIG_FILENAME)
    if not os.path.exists(cfg_path):
        if path:
            raise ConfigError(f"설정 파일을 찾을 수 없습니다: {path}")
        return FileConfig(conv, {}, {})

    try:
        with open(cfg_path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다 ({cfg_path}): {exc}") from exc

    commit = data.get("commit", {})
    pr = data.get("pr", {})
    conv.language = data.get("language", conv.language)
    types = commit.get("types")
    if isinstance(types, list):
        conv.commit_types = {t: DEFAULT_COMMIT_TYPES.get(t, "") for t in types}
    elif isinstance(types, dict):
        conv.commit_types = dict(types)
    conv.scope = commit.get("scope", conv.scope)
    if conv.scope not in ("optional", "required", "none"):
        raise ConfigError("commit.scope 는 optional | required | none 중 하나여야 합니다.")
    conv.scopes = list(commit.get("scopes", []))
    conv.commit_body = bool(commit.get("body", True))
    conv.commit_rules = list(commit.get("rules", []))
    conv.pr_title_format = pr.get("title_format", conv.pr_title_format)
    conv.pr_rules = list(pr.get("rules", []))
    conv.pr_checklist = list(pr.get("checklist", []))
    conv.source = os.path.relpath(cfg_path, root)

    return FileConfig(conv, data.get("safe_mode", {}), data.get("defaults", {}))
