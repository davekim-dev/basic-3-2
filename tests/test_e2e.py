"""임시 Git 저장소 + 가짜 API 서버로 CLI 전체 흐름 검증."""

import contextlib
import io
import json
import os
import unittest

from gitgen import cli
from tests.helpers import FakeClaude, git, make_repo, write

COMMIT_OK = json.dumps({"title": "feat: 인사 함수에 이름 인자 추가", "body": ["app.py 의 hello 가 이름을 받도록 변경"]})
PR_OK = json.dumps({
    "title": "feat: 인사 기능 개선",
    "why": ["사용자 이름으로 인사하기 위해"],
    "what": ["app.py hello(name) 추가"],
    "how_to_test": ["python -c 'import app; print(app.hello(\"a\"))'"],
})


class E2ETest(unittest.TestCase):
    def setUp(self):
        self.repo = make_repo()
        self.old_cwd = os.getcwd()
        os.chdir(self.repo)
        self.env = dict(os.environ)
        os.environ["AI_API_KEY"] = "test-key"
        self.fake = None

    def tearDown(self):
        os.chdir(self.old_cwd)
        os.environ.clear()
        os.environ.update(self.env)
        if self.fake:
            self.fake.close()

    def run_cli(self, *argv, replies=None):
        if replies is not None:
            self.fake = FakeClaude(replies)
            os.environ["AI_API_BASE_URL"] = self.fake.url
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_no_changes(self):
        code, out, err = self.run_cli("commit")
        self.assertEqual(code, 0)
        self.assertIn("변경 사항이 없습니다", err)

    def test_missing_api_key(self):
        del os.environ["AI_API_KEY"]
        write(self.repo, "app.py", "def hello(name):\n    return name\n")
        code, _, err = self.run_cli("commit")
        self.assertEqual(code, 1)
        self.assertIn("AI_API_KEY 환경변수가 설정되지 않았습니다", err)

    def test_commit_flow(self):
        write(self.repo, "app.py", "def hello(name):\n    return f'hi {name}'\n")
        write(self.repo, "new_module.py", "X = 1\n")
        code, out, err = self.run_cli("commit", "--temperature", "0.2", "--max-tokens", "1234", replies=[{"text": COMMIT_OK}])
        self.assertEqual(code, 0, err)
        self.assertIn("--- Commit Message ---", out)
        self.assertIn("feat: 인사 함수에 이름 인자 추가", out)
        self.assertIn("AI API 호출 횟수: 1회", err)
        req = self.fake.requests[0]
        self.assertEqual(req["max_tokens"], 1234)
        self.assertEqual(req["temperature"], 0.2)
        self.assertIn("new_module.py", req["messages"][0]["content"])  # 신규 파일 포함
        self.assertEqual(self.fake.headers[0]["x-api-key"], "test-key")

    def test_retry_on_invalid_format(self):
        write(self.repo, "app.py", "x = 2\n")
        bad = json.dumps({"title": "그냥 고침", "body": []})
        code, out, err = self.run_cli("commit", replies=[{"text": bad}, {"text": COMMIT_OK}])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.fake.requests), 2)
        self.assertIn("이전 출력의 규칙 위반", self.fake.requests[1]["messages"][0]["content"])
        self.assertIn("AI API 호출 횟수: 2회", err)

    def test_pr_flow_on_branch(self):
        git(self.repo, "checkout", "-q", "-b", "feature/hello")
        write(self.repo, "app.py", "def hello(name):\n    return name\n")
        git(self.repo, "commit", "-qam", "change")
        code, out, err = self.run_cli("pr", replies=[{"text": PR_OK}])
        self.assertEqual(code, 0, err)
        for h in ("--- PR Title ---", "## Why", "## What", "## How to Test"):
            self.assertIn(h, out)
        self.assertIn("현재 브랜치: feature/hello", err)
        self.assertIn("git diff main...HEAD", err)

    def test_auth_error_message(self):
        write(self.repo, "app.py", "x = 3\n")
        code, _, err = self.run_cli("commit", replies=[{"status": 401, "message": "invalid x-api-key"}])
        self.assertEqual(code, 1)
        self.assertIn("인증 실패(401)", err)

    def test_network_error_message(self):
        write(self.repo, "app.py", "x = 3\n")
        os.environ["AI_API_BASE_URL"] = "http://127.0.0.1:1"
        code, _, err = self.run_cli("commit")
        self.assertEqual(code, 1)
        self.assertIn("네트워크 오류", err)

    def test_safe_mode_dry_run_masks(self):
        write(self.repo, "config.py", 'API_KEY = "sk-ant-api03-secretsecretsecret"\nOWNER = "kim@corp.com"\n')
        code, out, err = self.run_cli("commit", "--safe-mode", "--dry-run")
        self.assertEqual(code, 0)
        self.assertNotIn("secretsecret", out)
        self.assertNotIn("kim@corp.com", out)
        self.assertIn("AI API 호출 횟수: 0회", err)

    def test_not_repo_root(self):
        os.makedirs("sub")
        os.chdir("sub")
        code, _, err = self.run_cli("commit")
        self.assertEqual(code, 1)
        self.assertIn("프로젝트 루트", err)


if __name__ == "__main__":
    unittest.main()
