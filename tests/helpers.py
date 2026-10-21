"""테스트 도우미: 임시 Git 저장소 + 가짜 Claude API 서버."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def git(cwd: str, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_repo() -> str:
    d = os.path.realpath(tempfile.mkdtemp(prefix="gitgen-test-"))
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "test@example.com")
    git(d, "config", "user.name", "Test")
    write(d, "app.py", "def hello():\n    return 'hi'\n")
    git(d, "add", ".")
    git(d, "commit", "-q", "-m", "init")
    return d


def write(cwd: str, path: str, content: str) -> None:
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class FakeClaude:
    """미리 정한 응답(또는 HTTP 오류)을 순서대로 돌려주는 로컬 서버."""

    def __init__(self, replies: list):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length", 0))
                outer.requests.append(json.loads(self.rfile.read(length)))
                outer.headers.append({k.lower(): v for k, v in self.headers.items()})
                reply = outer.replies.pop(0) if outer.replies else {"text": "{}"}
                status = reply.get("status", 200)
                if status == 200:
                    body = {
                        "model": outer.requests[-1]["model"],
                        "stop_reason": reply.get("stop_reason", "end_turn"),
                        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": reply["text"]}],
                        "usage": {"input_tokens": 100, "output_tokens": 50},
                    }
                else:
                    body = {"type": "error", "error": {"type": "x", "message": reply.get("message", "boom")}}
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):  # 조용히
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
