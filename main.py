"""AI Git 커밋/PR 초안 생성기 진입점.

사용법:
    python main.py commit [옵션]
    python main.py pr [옵션]
"""

import sys

if sys.version_info < (3, 10):
    sys.exit("[ERROR] Python 3.10 이상이 필요합니다. (현재 %d.%d)" % sys.version_info[:2])

from gitgen.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
