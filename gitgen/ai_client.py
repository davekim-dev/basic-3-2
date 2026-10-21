"""Claude Messages API REST 연동 (표준 라이브러리 urllib 만 사용)."""

from __future__ import annotations

import json
import os
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass

API_KEY_ENV = "AI_API_KEY"
DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# 안전 분류기가 요청을 거절(refusal)하면 서버가 다른 모델로 자동 재시도하도록 하는 옵션을 지원하는 모델
_FALLBACK_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# effort 파라미터를 받지 않는 구형 모델
_NO_EFFORT = ("claude-3", "haiku-4", "sonnet-4-5", "sonnet-4-0", "opus-4-0", "opus-4-1")


class AIError(Exception):
    """사용자에게 그대로 보여줄 수 있는 오류 메시지."""


@dataclass
class AIRequest:
    system: str
    prompt: str
    model: str
    max_tokens: int
    temperature: float | None = None
    effort: str | None = None
    timeout: float = 120.0


@dataclass
class AIResponse:
    text: str
    model: str
    stop_reason: str | None
    input_tokens: int = 0
    output_tokens: int = 0


def get_api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise AIError(f'{API_KEY_ENV} 환경변수가 설정되지 않았습니다.\n  예) export {API_KEY_ENV}="YOUR_KEY"')
    return key


def build_payload(req: AIRequest) -> tuple[dict, dict]:
    """요청 본문과 추가 헤더를 만든다."""
    body: dict = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "system": req.system,
        "messages": [{"role": "user", "content": req.prompt}],
    }
    headers: dict[str, str] = {}
    if req.temperature is not None:
        body["temperature"] = req.temperature
    if req.effort and not any(tag in req.model for tag in _NO_EFFORT):
        body["output_config"] = {"effort": req.effort}
    if req.model in _FALLBACK_MODELS:
        body["fallbacks"] = "default"
        headers["anthropic-beta"] = _FALLBACK_BETA
    return body, headers


class ClaudeClient:
    def __init__(self, api_key: str, base_url: str | None = None):
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("AI_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.calls = 0  # 이번 실행에서 API 를 호출한 횟수

    def complete(self, req: AIRequest) -> AIResponse:
        body, extra_headers = build_payload(req)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            **extra_headers,
        }
        http_req = urllib.request.Request(
            f"{self.base_url}/v1/messages",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(http_req, timeout=req.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AIError(_describe_http_error(exc, req)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, ssl.SSLError):
                raise AIError(
                    f"SSL 인증서 오류: {reason}\n  macOS python.org 설치본이면 'Install Certificates.command' 를 실행하세요."
                ) from exc
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise AIError(f"요청 시간 초과({req.timeout:.0f}초). --timeout 값을 늘리거나 잠시 후 다시 시도하세요.") from exc
            raise AIError(f"네트워크 오류: API 서버에 연결할 수 없습니다 ({reason}). 인터넷 연결/프록시를 확인하세요.") from exc
        except (socket.timeout, TimeoutError) as exc:
            raise AIError(f"요청 시간 초과({req.timeout:.0f}초). --timeout 값을 늘리거나 잠시 후 다시 시도하세요.") from exc
        except json.JSONDecodeError as exc:
            raise AIError("API 응답을 JSON 으로 해석할 수 없습니다.") from exc

        return _parse_response(data, req)


def _parse_response(data: dict, req: AIRequest) -> AIResponse:
    stop = data.get("stop_reason")
    if stop == "refusal":
        details = data.get("stop_details") or {}
        raise AIError(f"모델이 요청을 거절했습니다 (category: {details.get('category')}). diff 내용을 확인하세요.")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
    if stop == "max_tokens" and not text:
        raise AIError(f"max_tokens({req.max_tokens}) 에 도달해 응답이 비었습니다. --max-tokens 값을 늘리세요.")
    if not text:
        raise AIError(f"API 응답에 텍스트가 없습니다 (stop_reason={stop}).")
    usage = data.get("usage") or {}
    return AIResponse(
        text=text,
        model=data.get("model", req.model),
        stop_reason=stop,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )


def _describe_http_error(exc: urllib.error.HTTPError, req: AIRequest) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        api_msg = payload.get("error", {}).get("message", "")
    except Exception:  # noqa: BLE001 - 본문이 JSON 이 아닐 수 있음
        api_msg = ""
    code = exc.code
    detail = f" - {api_msg}" if api_msg else ""
    if code == 401:
        return f"인증 실패(401): API Key 가 올바르지 않습니다. AI_API_KEY 값을 확인하세요.{detail}"
    if code == 403:
        return f"권한 없음(403): 이 키로 해당 모델/기능을 사용할 수 없습니다.{detail}"
    if code == 404:
        return f"찾을 수 없음(404): 모델 이름 '{req.model}' 을 확인하세요.{detail}"
    if code == 413:
        return f"요청이 너무 큽니다(413): --safe-mode 또는 --max-lines 로 diff 를 줄이세요.{detail}"
    if code == 429:
        return f"요청 한도 초과(429): 잠시 후 다시 시도하세요.{detail}"
    if code == 400:
        hint = ""
        if "temperature" in api_msg:
            hint = "\n  힌트: 최신 Claude 모델은 temperature 를 지원하지 않습니다. --temperature 를 빼거나 --model claude-haiku-4-5 등으로 바꾸세요."
        return f"잘못된 요청(400){detail}{hint}"
    if code in (500, 502, 503, 504, 529):
        return f"API 서버 오류({code}): 일시적인 문제일 수 있습니다. 잠시 후 다시 시도하세요.{detail}"
    return f"API 호출 실패(HTTP {code}){detail}"
