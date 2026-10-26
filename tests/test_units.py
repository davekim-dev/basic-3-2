import os
import tempfile
import unittest

from gitgen.ai_client import supports_effort
from gitgen.envfile import load_env_file, parse_env
from gitgen.git_collector import parse_status, split_diff
from gitgen.safe_mode import SafePolicy, mask_text, DEFAULT_MASK_RULES, prepare_diff
from gitgen.validator import (
    CommitDraft,
    PRDraft,
    extract_json,
    fix_commit,
    fix_pr,
    parse_commit,
    shorten,
    validate_commit,
    validate_pr,
    validate_pr_body,
)


class StatusTest(unittest.TestCase):
    def test_parse_status(self):
        out = "## feature/x...origin/feature/x [ahead 1]\nM  a.py\n M b.py\nR  old.py -> new.py\n?? new/c.py\n"
        branch, files = parse_status(out)
        self.assertEqual(branch, "feature/x")
        self.assertEqual([f.path for f in files], ["a.py", "b.py", "new.py", "new/c.py"])
        self.assertTrue(files[0].staged)
        self.assertFalse(files[1].staged)
        self.assertEqual(files[2].orig_path, "old.py")
        self.assertTrue(files[3].untracked)

    def test_no_commits_yet(self):
        branch, _ = parse_status("## No commits yet on main\n?? x\n")
        self.assertEqual(branch, "main")

    def test_split_diff(self):
        text = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1,2 @@\n-x\n+y\n+z\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-q\n+w\n"
        )
        diffs = split_diff(text)
        self.assertEqual([d.path for d in diffs], ["a.py", "b.py"])
        self.assertEqual((diffs[0].added, diffs[0].removed), (2, 1))


class SafeModeTest(unittest.TestCase):
    def test_masking(self):
        text = (
            '+API_KEY = "sk-ant-api03-abcdefghijklmnop"\n'
            "+contact = 'dev.kim@example.com'\n"
            "+aws = AKIAABCDEFGHIJKLMNOP\n"
            '+password: "hunter2hunter2"\n'
            "+phone = 010-1234-5678\n"
        )
        masked, counts = mask_text(text, DEFAULT_MASK_RULES)
        for secret in ("sk-ant-api03", "dev.kim@example.com", "AKIAABCDEFGHIJKLMNOP", "hunter2", "1234-5678"):
            self.assertNotIn(secret, masked)
        self.assertIn("[MASKED_EMAIL]", masked)
        self.assertGreaterEqual(sum(counts.values()), 5)

    def _diff(self, path, n):
        body = "".join(f"+line {i}\n" for i in range(n))
        return split_diff(f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -0,0 +1,{n} @@\n{body}")[0]

    def test_limits(self):
        diffs = [self._diff(f"f{i}.py", 50) for i in range(15)]
        off = prepare_diff(diffs, SafePolicy(enabled=False))
        on = prepare_diff(diffs, SafePolicy(enabled=True, max_files=10, max_lines=200))
        self.assertEqual(len(off.sent_files), 15)
        self.assertLessEqual(on.sent_lines, 201)
        self.assertLessEqual(len(on.sent_files), 10)
        self.assertTrue(on.omitted)

    def test_sensitive_files_always_excluded(self):
        diffs = [self._diff(".env", 3), self._diff("package-lock.json", 3), self._diff("app.py", 3)]
        prep = prepare_diff(diffs, SafePolicy(enabled=False))
        self.assertEqual(prep.sent_files, ["app.py"])
        self.assertEqual(len(prep.omitted), 2)


class ValidatorTest(unittest.TestCase):
    def test_extract_json_with_fence(self):
        self.assertEqual(extract_json('설명\n```json\n{"title": "x"}\n```'), {"title": "x"})

    def test_parse_commit_cleans(self):
        d = parse_commit('{"title": "feat: 기능 추가.", "body": ["- 하나", "2. 둘"]}')
        self.assertEqual(d.title, "feat: 기능 추가")
        self.assertEqual(d.body, ["하나", "둘"])
        self.assertEqual(d.render(), "feat: 기능 추가\n\n- 하나\n- 둘")

    def test_commit_rules(self):
        self.assertEqual(validate_commit(CommitDraft("feat: 짧은 제목")), [])
        long = CommitDraft("feat: " + "가" * 70)
        self.assertEqual(validate_commit(long)[0].level, "error")
        self.assertTrue(any("형식" in i.message for i in validate_commit(CommitDraft("그냥 제목"))))
        self.assertTrue(validate_commit(CommitDraft("feat: x"), scope="required"))
        fix_commit(long)
        self.assertLessEqual(len(long.title), 72)

    def test_pr_rules(self):
        d = PRDraft("feat: x", why=["a"], what=["b"], how_to_test=[])
        self.assertTrue(any("How to Test" in i.message for i in validate_pr(d)))
        fix_pr(d)
        self.assertEqual(validate_pr(d), [])
        self.assertTrue(validate_pr_body("## Why\n- a\n## What\n- b\n"))

    def test_shorten_word_boundary(self):
        s = shorten("feat: add a very long title that keeps going and going", 30)
        self.assertLessEqual(len(s), 30)
        self.assertFalse(s.endswith(" "))


class EffortTest(unittest.TestCase):
    def test_supports_effort(self):
        for model in ("claude-sonnet-4", "claude-opus-4", "claude-sonnet-4-20250514", "claude-haiku-4",
                      "claude-sonnet-4-5", "claude-3-5-haiku-latest"):
            self.assertFalse(supports_effort(model), model)
        for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-4-5"):
            self.assertTrue(supports_effort(model), model)


class EnvFileTest(unittest.TestCase):
    def test_parse_env(self):
        text = '# 주석\n\nAI_API_KEY=abc\nexport AI_MODEL="claude-sonnet-4"\nAI_API_BASE_URL = \'https://x.kr\'\n잘못된 줄\n'
        self.assertEqual(
            parse_env(text),
            {"AI_API_KEY": "abc", "AI_MODEL": "claude-sonnet-4", "AI_API_BASE_URL": "https://x.kr"},
        )

    def test_load_does_not_override(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w", encoding="utf-8-sig") as f:  # BOM 포함 파일도 읽혀야 함
                f.write("GITGEN_TEST_A=from-file\nGITGEN_TEST_B=from-file\n")
            os.environ["GITGEN_TEST_A"] = "from-shell"
            try:
                loaded = load_env_file(path)
                self.assertEqual(loaded, ["GITGEN_TEST_B"])
                self.assertEqual(os.environ["GITGEN_TEST_A"], "from-shell")
                self.assertEqual(os.environ["GITGEN_TEST_B"], "from-file")
            finally:
                os.environ.pop("GITGEN_TEST_A", None)
                os.environ.pop("GITGEN_TEST_B", None)

    def test_missing_file(self):
        self.assertEqual(load_env_file(os.path.join(tempfile.gettempdir(), "no-such-dir", ".env")), [])


if __name__ == "__main__":
    unittest.main()
