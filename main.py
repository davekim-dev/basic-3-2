"""AI Git 커밋/PR 초안 생성기 진입점.

사용법:
    python main.py commit [옵션]
    python main.py pr [옵션]
"""

import sys

if sys.version_info < (3, 10):
    sys.exit("[ERROR] Python 3.10 이상이 필요합니다. (현재 %d.%d)" % sys.version_info[:2])

import os  # noqa: E402

from gitgen.cli import main  # noqa: E402
from gitgen.envfile import load_env_file  # noqa: E402

if __name__ == "__main__":
    # main.py 와 같은 폴더의 .env 를 읽는다 (.gitignore 대상이라 커밋되지 않음)
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    loaded = load_env_file(env_path)
    if loaded:
        print(f"[INFO] .env 에서 환경변수 불러옴: {', '.join(loaded)}", file=sys.stderr)
    sys.exit(main())
