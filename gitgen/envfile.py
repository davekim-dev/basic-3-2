""".env 파일을 읽어 환경변수로 등록 (외부 라이브러리 없이).

- 형식: KEY=VALUE (한 줄에 하나), '#' 으로 시작하는 줄과 빈 줄은 무시
- `export KEY=VALUE`, 값의 앞뒤 따옴표('...' / "...") 허용
- 이미 설정된 환경변수는 덮어쓰지 않는다 (터미널에서 지정한 값이 우선)
"""

from __future__ import annotations

import os
import re

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: str) -> list[str]:
    """path 의 .env 를 읽어 아직 없는 환경변수만 등록하고, 등록한 이름 목록을 돌려준다."""
    if not os.path.isfile(path):
        return []
    # utf-8-sig: Windows 메모장 등이 붙이는 BOM 제거
    with open(path, encoding="utf-8-sig") as f:
        values = parse_env(f.read())
    loaded = []
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
