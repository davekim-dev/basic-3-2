# AI Git 커밋/PR 초안 생성기 (ai-gitgen)

`git status`, `git diff` 결과를 읽어 **AI API(Claude Messages API, REST)** 로 커밋 메시지와 Pull Request 제목/본문 초안을 만들어 주는 Python CLI 입니다.

```
python main.py commit   # 커밋 메시지 초안
python main.py pr       # PR 제목 + 본문(Why / What / How to Test) 초안
```

- 외부 패키지 없이 **Python 표준 라이브러리만** 사용합니다 (`urllib` 로 REST 호출).
- 명령 1번에 AI API **1회 호출**이 기본이고, 결과가 형식 규칙을 어기면 **최대 1회만** 다시 생성합니다 (실행당 최대 2회).
- `--safe-mode` 로 API Key·이메일 등을 마스킹하고 전송할 diff 양을 제한할 수 있습니다.
- 결과는 **초안**입니다. 검토한 뒤 직접 커밋/PR 에 적용하세요. (`git push`, PR 생성 같은 원격 반영은 하지 않습니다.)

---

## 목차

1. [동작 흐름](#1-동작-흐름)
2. [설치](#2-설치)
3. [API Key 환경변수 설정](#3-api-key-환경변수-설정)
4. [사용법](#4-사용법)
5. [출력 예시](#5-출력-예시)
6. [API 파라미터와 결과 품질](#6-api-파라미터와-결과-품질)
7. [프롬프트 설계](#7-프롬프트-설계)
8. [출력 검증과 다듬기](#8-출력-검증과-다듬기)
9. [민감정보와 safe-mode](#9-민감정보와-safe-mode)
10. [비용과 요청 횟수](#10-비용과-요청-횟수)
11. [팀 컨벤션 커스터마이징](#11-팀-컨벤션-커스터마이징-ai-gitgenjson)
12. [프로젝트 구조와 테스트](#12-프로젝트-구조와-테스트)
13. [주의사항 / 문제 해결](#13-주의사항--문제-해결)

---

## 1. 동작 흐름

```
 python main.py commit|pr
        │
        ▼
 ① Git 수집 ── git status --porcelain --branch   → 브랜치, 변경 파일 목록
        │      git diff HEAD / --cached / base...HEAD → 변경 내용(diff)
        │      (변경 없음 → "변경 사항이 없습니다" 출력 후 종료)
        ▼
 ② diff 정리 ── 민감 파일(.env, *.pem ...)·lock 파일 내용 제외
        │       --safe-mode: 키/이메일/전화번호 마스킹 + 파일 10개·200줄 제한
        ▼
 ③ 프롬프트 구성 ── 역할(system) + 출력 규칙 + 팀 컨벤션 + 변경 맥락 + diff
        ▼
 ④ AI API 호출 ── POST https://api.anthropic.com/v1/messages  (1회)
        │          실패 시: 인증/네트워크/한도 등 원인별 오류 메시지
        ▼
 ⑤ 파싱·검증 ── JSON 파싱 → 길이/형식 검사
        │       위반 시 문제점을 알려주고 1회 재생성 (총 2회 이내)
        │       그래도 남으면 후처리(제목 자르기, 빈 섹션 TODO)
        ▼
 ⑥ 출력 ── 구분선으로 나뉜 커밋 메시지 / PR 제목 / PR 본문 + 검증 결과
```

| 단계 | 파일 |
|---|---|
| ① Git 수집 | [gitgen/git_collector.py](gitgen/git_collector.py) |
| ② diff 정리, 마스킹 | [gitgen/safe_mode.py](gitgen/safe_mode.py) |
| ③ 프롬프트 | [gitgen/prompts.py](gitgen/prompts.py) |
| ④ REST 호출, 예외 처리 | [gitgen/ai_client.py](gitgen/ai_client.py) |
| ⑤ 파싱, 검증, 후처리 | [gitgen/validator.py](gitgen/validator.py) |
| ⑥ CLI, 전체 흐름 | [gitgen/cli.py](gitgen/cli.py) |

---

## 2. 설치

요구 사항: **Python 3.10 이상**, Git, 인터넷 연결, Anthropic API Key

```bash
git clone https://github.com/davekim-dev/ai-git-commit-pr-generator.git
cd ai-git-commit-pr-generator
python3 --version        # 3.10 이상인지 확인
```

설치할 외부 패키지는 없습니다 (`requirements.txt` 도 비어 있습니다).

다른 프로젝트에서 쓰려면, **그 프로젝트의 루트 디렉토리에서** 이 도구의 `main.py` 를 실행합니다.

```bash
cd ~/work/my-project
python3 ~/ai-git-commit-pr-generator/main.py commit

# 자주 쓴다면 alias 를 등록해 두면 편합니다
alias gitgen="python3 ~/ai-git-commit-pr-generator/main.py"
gitgen commit
```

---

## 3. API Key 환경변수 설정

API Key 는 **환경변수 `AI_API_KEY` 로만** 읽습니다. 코드나 설정 파일에 키를 적지 마세요.
키는 [Anthropic Console](https://console.anthropic.com/) 에서 발급합니다.

```bash
# macOS / Linux (현재 터미널에서만 유효)
export AI_API_KEY="sk-ant-..."

# 계속 쓰려면 셸 설정 파일에 추가
echo 'export AI_API_KEY="sk-ant-..."' >> ~/.zshrc && source ~/.zshrc
```

```powershell
# Windows PowerShell
$env:AI_API_KEY = "sk-ant-..."
```

| 환경변수 | 필수 | 설명 |
|---|---|---|
| `AI_API_KEY` | ✅ | Anthropic API Key |
| `AI_MODEL` | | 기본 모델 변경 (예: `claude-sonnet-5-5`) |
| `AI_API_BASE_URL` | | API 주소 변경 (프록시, 테스트용 가짜 서버). 기본 `https://api.anthropic.com` |

---

## 4. 사용법

### 커밋 메시지

```bash
python main.py commit                       # 기본
python main.py commit --staged              # 스테이징된 변경만 사용
python main.py commit -c "결제 실패 시 중복 결제되던 버그 #42 수정"   # 변경 이유 전달
python main.py commit --save .git/AI_MSG && git commit -F .git/AI_MSG   # 바로 적용
```

- 스테이징된 변경이 있으면 **스테이징된 내용(`git diff --cached`)** 으로, 없으면 작업 트리 전체(`git diff HEAD` + 새 파일)로 메시지를 만듭니다. 실제로 커밋될 내용과 메시지가 맞도록 하기 위해서입니다.

### PR 초안

```bash
python main.py pr                     # main(또는 master) 대비
python main.py pr --base develop      # 비교 기준 브랜치 지정
python main.py pr --save PR.md        # 파일로 저장 → PR 작성 화면에 붙여넣기
```

- `git diff <base>...HEAD` (브랜치에서 커밋한 변경) + 아직 커밋하지 않은 변경을 함께 사용합니다.

### 옵션

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--model` | `claude-opus-5-5` | 사용할 모델 ID |
| `--temperature` | (보내지 않음) | 0.0~1.0. 값을 줄 때만 요청에 포함 → [6장](#6-api-파라미터와-결과-품질) 참고 |
| `--max-tokens` | `8000` | 응답 최대 토큰 (내부 추론 토큰 포함) |
| `--effort` | `medium` | `low`/`medium`/`high`/`xhigh`/`max` 추론 깊이 |
| `--timeout` | `120` | 요청 타임아웃(초) |
| `--max-calls` | `2` | 실행당 최대 호출 수. `1` 이면 재생성 없이 후처리만 |
| `--safe-mode` | off | 마스킹 + 전송량 제한 |
| `--max-files` / `--max-lines` | `10` / `200` | safe-mode 전송 제한 |
| `--max-chars` | `60000` | 항상 적용되는 diff 최대 문자 수 (비용 보호) |
| `-c`, `--context` | | 변경 이유·요구사항 메모 (프롬프트에 함께 전달) |
| `--lang` | `ko` | 출력 언어 `ko`/`en` |
| `--config` / `--no-config` | `./.ai-gitgen.json` | 팀 컨벤션 파일 지정 / 무시 |
| `--dry-run` | | **API 를 호출하지 않고** 보낼 프롬프트만 출력 (비용 0) |
| `--save PATH` | | 결과를 파일로 저장 |
| `--staged` (commit) | | 스테이징된 변경만 사용 |
| `--base` (pr) | `main`/`master` | PR 비교 기준 브랜치 |

로그(`[INFO]` 등)는 stderr, 결과물은 stdout 으로 나옵니다. 그래서 `python main.py commit 2>/dev/null > msg.txt` 처럼 결과만 따로 받을 수도 있습니다.

---

## 5. 출력 예시

아래는 `feature/login-retry` 브랜치에서 `auth.py` 에 로그인 재시도 로직을 넣고 `test_auth.py`·`settings.py` 를 새로 만든 상태의 출력 예시입니다. 로그 형식은 실제 출력과 같고, AI 가 쓴 문구와 토큰 수는 모델·실행마다 달라집니다.

### 커밋 메시지

```
$ python main.py commit --safe-mode
[INFO] Git status 수집 완료: 3개 파일 변경 감지
[INFO] Git diff 수집 완료: 39줄 (+20/-2) [working tree (git diff HEAD + untracked)]
[INFO] safe-mode ON: 파일 최대 10개 / 최대 200줄, 마스킹 2건 {'anthropic_key': 1, 'email': 1}
[INFO] AI 전송 diff: 파일 3개, 39줄, 986자
[INFO] 모델: claude-opus-5-5, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/2)
[INFO] 응답 수신: 입력 1840 / 출력 412 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat(auth): 로그인 실패 시 지수 백오프 재시도 추가

- auth.py 의 login 이 최대 MAX_RETRY(3)회까지 2^n 초 간격으로 재시도하도록 변경
- test_auth.py 에 재시도 후 실패를 반환하는지 확인하는 테스트 추가
------------------------------------------------------------
[검증 통과] 제목 길이 30자 (권장 ≤50, 최대 72) · 본문 불릿 2개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

### PR 제목/본문 (형식 위반 → 1회 재생성된 경우)

```
$ python main.py pr --safe-mode
[INFO] 현재 브랜치: feature/login-retry (base: main)
[INFO] Git status 수집 완료: 3개 파일 변경 감지
[INFO] Git diff 수집 완료: 39줄 (+20/-2) [git diff main...HEAD + uncommitted changes (git diff HEAD)]
[INFO] safe-mode ON: 파일 최대 10개 / 최대 200줄, 마스킹 2건 {'anthropic_key': 1, 'email': 1}
[INFO] AI 전송 diff: 파일 3개, 39줄, 986자
[INFO] 모델: claude-opus-5-5, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/2)
[WARN] 형식 규칙 위반 → 1회 재생성: PR 본문 'How to Test' 섹션에 불릿이 없습니다.
[INFO] AI API 요청 중... (2/2)
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 2회

--- PR Title ---
feat(auth): 로그인 재시도(지수 백오프) 로직 추가

--- PR Body ---
## Why
- 일시적인 인증 서버 오류에도 로그인이 즉시 실패해 사용자가 직접 다시 시도해야 했습니다.

## What
- auth.py: login 에 retries 인자(기본 MAX_RETRY=3)와 2^n 초 지수 백오프 재시도 추가
- test_auth.py: 모든 시도가 실패하면 False 를 반환하는지 검증하는 테스트 추가
- settings.py: 설정 파일 추가 (값은 safe-mode 로 마스킹되어 검토 필요)

## How to Test
- pytest test_auth.py 실행 후 통과 확인
- check 가 계속 False 를 반환하도록 두고 login 호출 시 약 1+2+4초 후 False 반환 확인
------------------------------------------------------------
[검증 통과] PR 제목 33자 (최대 80) · Why 1 / What 3 / How to Test 2 불릿
```

### 변경 사항이 없을 때

```
$ python main.py commit
[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.
```

### 오류 상황

```
$ python main.py commit          # API Key 미설정
[ERROR] AI_API_KEY 환경변수가 설정되지 않았습니다.
  예) export AI_API_KEY="YOUR_KEY"

$ python main.py commit          # 잘못된 키
[ERROR] 인증 실패(401): API Key 가 올바르지 않습니다. AI_API_KEY 값을 확인하세요. - invalid x-api-key

$ python main.py commit          # 인터넷 끊김
[ERROR] 네트워크 오류: API 서버에 연결할 수 없습니다 ([Errno 8] nodename nor servname provided, or not known). 인터넷 연결/프록시를 확인하세요.

$ cd src && python ../main.py commit   # 루트가 아닌 곳에서 실행
[ERROR] Git 오류: 프로젝트 루트 디렉토리에서 실행하세요: cd /Users/me/my-project
```

| 상황 | 메시지 |
|---|---|
| 키 미설정 | `AI_API_KEY 환경변수가 설정되지 않았습니다` |
| 401 / 403 | 인증 실패 / 권한 없음 |
| 404 | 모델 이름 확인 |
| 400 | 잘못된 요청 + API 가 알려준 원인 (temperature 미지원 시 힌트) |
| 413 | 요청이 너무 큼 → `--safe-mode`, `--max-lines` 안내 |
| 429 | 요청 한도 초과 |
| 5xx / 529 | API 서버 일시 오류 |
| 연결 실패 / 시간 초과 / SSL | 네트워크 오류 / `--timeout` 안내 / 인증서 설치 안내 |
| 응답 거절(refusal), 빈 응답, `max_tokens` 도달 | 원인과 조치 안내 |

---

## 6. API 파라미터와 결과 품질

| 파라미터 | 역할 | 결과에 주는 영향 |
|---|---|---|
| `model` | 어떤 모델을 쓸지 | 큰 모델일수록 diff 의 **의도**를 더 잘 읽어 "왜 바꿨는지"를 잘 씁니다. 작은 모델은 빠르고 싸지만 파일 나열식 요약이 되기 쉽습니다. |
| `max_tokens` | 응답 길이 상한 | 너무 작으면 응답이 중간에 잘리거나(JSON 깨짐) 비어 버립니다. 최신 모델은 답하기 전 내부 추론에도 토큰을 쓰므로 넉넉히(기본 8000) 둡니다. 실제 사용량은 출력 길이만큼만 과금됩니다. |
| `temperature` | 무작위성 | 낮을수록(0~0.3) 같은 diff 에 비슷한 메시지 → **커밋 메시지처럼 일관성이 중요한 작업에 적합**. 높을수록(0.7~1) 표현이 다양해지지만 규칙을 어길 가능성도 커집니다. |
| `effort` | 추론 깊이 | `low` 는 빠르고 싸며 작은 변경에 충분합니다. 큰 리팩터링 PR 은 `high` 로 올리면 변경 간 관계를 더 잘 정리합니다. |

**temperature 에 대해:** Claude Opus 5.5 같은 최신 모델은 `temperature` 를 받지 않습니다 (값을 보내면 400 오류). 그래서 이 도구는 `--temperature` 를 **지정했을 때만** 요청에 넣습니다. temperature 효과를 직접 비교해 보려면 이를 지원하는 모델을 함께 지정하세요.

```bash
python main.py commit --model claude-haiku-4-5 --temperature 0.0   # 매번 거의 같은 결과
python main.py commit --model claude-haiku-4-5 --temperature 1.0   # 실행마다 표현이 달라짐
python main.py commit --effort low --max-tokens 4000                # 기본 모델, 비용 절약
```

---

## 7. 프롬프트 설계

[gitgen/prompts.py](gitgen/prompts.py) 에서 프롬프트를 다음 순서로 조립합니다.

1. **System (역할·원칙)**: 시니어 개발자 역할. *diff 에 있는 것만 근거로 쓸 것*, *파일 나열 말고 의도 중심으로 쓸 것*, *마스킹된 값·비밀을 옮기지 말 것*, *JSON 만 출력할 것*.
2. **출력 규칙**: 제목 길이(50자 권장/72자 최대, PR 80자), `type(scope): 요약` 형식, 언어, 불릿 개수(커밋 본문 1~3개, PR 섹션별 개수).
3. **팀 컨벤션**: 허용 type 목록과 의미, scope 규칙, 팀이 추가한 규칙 ([11장](#11-팀-컨벤션-커스터마이징-ai-gitgenjson)).
4. **출력 JSON 형식**: `{"title", "body"}` / `{"title", "why", "what", "how_to_test"}`.
5. **변경 맥락**: `-c` 로 받은 변경 이유 → 브랜치 → `git status` 파일 목록 → 변경 통계 → 내용을 보내지 않은 파일 목록 → diff.

설계 포인트:

- **AI 에게 마크다운 대신 JSON 을 받습니다.** `## Why / ## What / ## How to Test` 헤더와 `- ` 불릿은 프로그램이 직접 붙이므로 템플릿이 깨질 일이 없고, 검증도 필드 단위로 정확하게 할 수 있습니다.
- **변경 이유는 diff 에 없습니다.** 그래서 `-c "변경 이유"` 로 사람이 아는 맥락을 넣을 수 있게 했습니다. 넣지 않으면 diff 에서 읽히는 목적만 쓰도록 지시해 지어내지 않게 합니다.
- **파일 목록을 diff 와 따로 줍니다.** safe-mode 나 전송량 제한으로 내용이 빠진 파일도 이름은 전달되어 요약에서 빠지지 않습니다.
- 실제로 무엇이 전송되는지는 `--dry-run` 으로 언제든 확인할 수 있습니다.

---

## 8. 출력 검증과 다듬기

AI 결과는 규칙을 어길 수 있으므로 **검증 → 재생성(1회) → 후처리** 순서로 다듬습니다 ([gitgen/validator.py](gitgen/validator.py)).

| 대상 | 규칙 | 위반 시 |
|---|---|---|
| 커밋 제목 | 1줄, `type: 요약` / `type(scope): 요약`, 허용된 type | 재생성 |
| 커밋 제목 길이 | 50자 이내 권장 / **최대 72자** | 50 초과: 경고만, 72 초과: 재생성 → 그래도 넘으면 단어 경계에서 자름 |
| 커밋 본문 | 불릿 1~3개 권장 (파일/모듈 언급) | 5개 초과 시 3개로 줄임 |
| PR 제목 | 1줄, **최대 80자** | 재생성 → 단어 경계에서 자름 |
| PR 본문 | `## Why`, `## What`, `## How to Test` 헤더 + 각 섹션 불릿 1개 이상 | 재생성 → 그래도 비면 `(TODO: 작성 필요)` 불릿을 넣고 경고 |
| 공통 | 제목 끝 마침표, 따옴표, 마크다운 기호, 불릿 앞 `-`/`1.` 중복 | 자동 정리 |

이렇게 하는 이유:

- 팀 규칙(길이·템플릿)을 지키지 않은 메시지는 `git log --oneline`, GitHub PR 목록에서 잘리거나 리뷰 품질을 떨어뜨립니다.
- 재생성은 **문제점을 구체적으로 알려주는 피드백 프롬프트**로 1회만 합니다. 횟수를 제한해 비용이 예측 가능하고, 그래도 남는 문제는 기계적으로 고칠 수 있는 것만 고칩니다.
- 최종 출력 아래 `[검증 통과]` / `[검증 확인 필요]` 줄로 길이와 불릿 개수를 보여 줘서, 사용자가 검토할 부분을 바로 알 수 있습니다.

---

## 9. 민감정보와 safe-mode

`git diff` 에는 API Key, 비밀번호, 이메일 같은 값이 섞일 수 있고, 이 도구는 diff 를 외부 API 로 보냅니다. 그래서 두 단계로 막습니다.

### 항상 적용

| 대상 | 처리 |
|---|---|
| `.env`, `.env.*`, `*.pem`, `*.key`, `*.p12`, `id_rsa*`, `*credentials*`, `*secret*`, `.npmrc`, `.pypirc`, `.netrc` | **내용을 보내지 않고** 파일명만 전달 |
| `*.lock`, `package-lock.json`, `*.min.js`, `*.map` 등 | 내용 생략 (요약에 도움이 안 되고 비용만 큼) |
| 바이너리 파일 | 내용 생략 |
| diff 전체 | 최대 **60,000자** (`--max-chars`) 넘으면 이후 생략 |

### `--safe-mode` 정책

| 항목 | 기준 | 변경 옵션 |
|---|---|---|
| (A) 마스킹 | 아래 정규식에 맞는 값을 `[MASKED_...]` 로 바꿔서 전송 | 설정 파일 `safe_mode.extra_patterns` 로 규칙 추가 |
| (B) 파일 수 제한 | 내용을 보내는 파일 **최대 10개**, 나머지는 파일명만 | `--max-files N` |
| (B) 줄 수 제한 | 보내는 diff **최대 200줄** (전체 합계), 넘는 부분은 `... (N줄 생략)` | `--max-lines N` |

기본 마스킹 규칙 ([gitgen/safe_mode.py](gitgen/safe_mode.py)):

| 규칙 | 예 | 치환 |
|---|---|---|
| 개인키 블록 | `-----BEGIN RSA PRIVATE KEY----- ...` | `[MASKED_PRIVATE_KEY]` |
| Anthropic / OpenAI 키 | `sk-ant-...`, `sk-proj-...` | `[MASKED_API_KEY]` |
| GitHub 토큰 | `ghp_...`, `github_pat_...` | `[MASKED_TOKEN]` |
| AWS Access Key | `AKIA...` | `[MASKED_AWS_KEY]` |
| Google API Key | `AIza...` | `[MASKED_API_KEY]` |
| Slack 토큰 | `xoxb-...` | `[MASKED_TOKEN]` |
| JWT | `eyJ...eyJ...sig` | `[MASKED_JWT]` |
| 비밀값 대입 | `password = "..."`, `api_key: ...`, `SECRET_TOKEN=...` | 값만 `[MASKED_SECRET]` |
| 이메일 | `kim@example.com` | `[MASKED_EMAIL]` |
| 주민등록번호 | `900101-1234567` | `[MASKED_RRN]` |
| 휴대전화 | `010-1234-5678` | `[MASKED_PHONE]` |
| 카드번호 | `1234-5678-9012-3456` | `[MASKED_CARD]` |

### safe-mode ON/OFF 비교

같은 변경에서 `--dry-run` 으로 전송될 내용을 비교한 결과입니다 (`settings.py` 부분).

```diff
# OFF: python main.py commit --dry-run
+API_KEY = "sk-ant-api03-REALLOOKINGSECRET1234567890"
+ADMIN_EMAIL = "admin@mycompany.com"
[INFO] AI 전송 diff: 파일 3개, 39줄, 1015자

# ON:  python main.py commit --dry-run --safe-mode
+API_KEY = "[MASKED_API_KEY]"
+ADMIN_EMAIL = "[MASKED_EMAIL]"
[INFO] safe-mode ON: 파일 최대 10개 / 최대 200줄, 마스킹 2건 {'anthropic_key': 1, 'email': 1}
[INFO] AI 전송 diff: 파일 3개, 39줄, 986자
```

제한을 더 강하게 걸면:

```
$ python main.py commit --dry-run --safe-mode --max-files 1 --max-lines 10
[INFO] safe-mode ON: 파일 최대 1개 / 최대 10줄, 마스킹 0건
[INFO] 내용 제외: settings.py (safe-mode 파일 수 제한(1개) 초과)
[INFO] 내용 제외: test_auth.py (safe-mode 파일 수 제한(1개) 초과)
[INFO] AI 전송 diff: 파일 1개, 10줄, 221자
```

> 권장: 회사 코드나 설정 파일이 섞인 저장소에서는 `--safe-mode` 를 기본으로 쓰고(설정 파일에서 `"enabled": true`), 처음 쓰는 저장소는 `--dry-run` 으로 전송 내용을 먼저 확인하세요. 마스킹은 정규식 기반이라 모든 비밀을 잡지는 못합니다. 비밀값은 애초에 커밋하지 않는 것이 가장 안전합니다.

---

## 10. 비용과 요청 횟수

- **1회 실행 = API 1회 호출**이 기본이고, 형식 위반 시에만 1회 더 호출합니다 (최대 2회). `--max-calls 1` 이면 항상 1회입니다.
- 매 실행마다 `[INFO] AI API 호출 횟수: N회` 와 입력/출력 토큰 수를 출력합니다.
- 비용은 대부분 **입력(diff) 크기**에 비례합니다. diff 는 기본 60,000자로 제한되며, 큰 변경은 `--safe-mode --max-lines` 로 더 줄일 수 있습니다.
- lock 파일, 빌드 산출물은 자동으로 제외됩니다.
- `--dry-run` 은 API 를 호출하지 않으므로 프롬프트를 다듬을 때 비용이 들지 않습니다.
- 비용을 더 줄이려면 `--effort low` 나 더 작은 모델(`--model claude-haiku-5-5`)을 쓰세요. 단, 요약 품질이 떨어질 수 있습니다.
- 자동화 스크립트나 git hook 에서 매 커밋마다 부르기보다는 **커밋 직전에 사람이 필요할 때 실행**하는 용도를 권장합니다. 429(요청 한도 초과)가 나면 잠시 후 다시 시도하세요.

---

## 11. 팀 컨벤션 커스터마이징 (`.ai-gitgen.json`)

사용할 프로젝트 루트에 `.ai-gitgen.json` 을 두면 자동으로 읽습니다 (`--config 경로` 로 지정, `--no-config` 로 무시).
예시: [.ai-gitgen.example.json](.ai-gitgen.example.json)

```json
{
  "language": "ko",
  "commit": {
    "types": ["feat", "fix", "docs", "refactor", "test", "chore"],
    "scope": "required",
    "scopes": ["auth", "api", "ui", "infra", "docs"],
    "body": true,
    "rules": ["제목 요약은 '~ 추가', '~ 수정', '~ 개선' 처럼 명사형으로 끝낸다"]
  },
  "pr": {
    "title_format": "[<scope>] <type>: <요약>",
    "rules": ["리뷰어에게 특히 봐야 할 부분이 있으면 What 마지막 불릿에 '리뷰 포인트:' 로 적는다"],
    "checklist": ["셀프 리뷰 완료", "테스트 통과 확인", "민감정보(키/개인정보) 미포함 확인"]
  },
  "safe_mode": {
    "enabled": false, "max_files": 10, "max_lines": 200,
    "exclude_files": ["*.sql", "fixtures/*"],
    "extra_patterns": [{"name": "internal_host", "pattern": "\\b[a-z0-9-]+\\.internal\\.mycompany\\.com\\b", "replace": "[MASKED_HOST]"}]
  },
  "defaults": {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 8000}
}
```

| 키 | 효과 |
|---|---|
| `commit.types` | 프롬프트에 허용 type 목록으로 전달 + 목록 밖 type 이면 재생성 |
| `commit.scope` | `optional` / `required` / `none`. 위반 시 재생성 |
| `commit.scopes` | 쓸 수 있는 scope 목록 |
| `commit.body` | `false` 면 제목만 생성 |
| `commit.rules`, `pr.rules` | 팀 규칙 문장을 프롬프트에 그대로 추가 |
| `pr.title_format` | PR 제목 형식 |
| `pr.checklist` | PR 본문 끝에 `## Checklist` + `- [ ]` 항목을 고정으로 붙임 |
| `safe_mode.*` | safe-mode 기본값, 제외 파일, 추가 마스킹 규칙 |
| `defaults.*` | 모델/effort/max_tokens 기본값 (CLI 옵션이 우선) |

### 컨벤션 적용 전/후 비교

같은 변경(로그인 재시도)에 대한 비교 예시입니다. 프롬프트 차이는 `--dry-run` 으로 직접 확인할 수 있습니다.

| | 기본 컨벤션 (`--no-config`) | 팀 컨벤션 적용 |
|---|---|---|
| 프롬프트 | `scope 는 선택`, type 10종 | `scope 필수`, type 6종, scope 목록, 명사형 규칙 |
| 커밋 제목 | `feat: 로그인 실패 시 재시도 로직 추가` | `feat(auth): 로그인 실패 시 지수 백오프 재시도 추가` |
| PR 제목 | `feat: 로그인 재시도 로직 추가` | `[auth] feat: 로그인 재시도(지수 백오프) 추가` |
| PR 본문 | Why / What / How to Test | Why / What / How to Test + `## Checklist` (3개 항목) |
| 검증 | scope 없어도 통과 | scope 가 없으면 재생성 |

---

## 12. 프로젝트 구조와 테스트

```
ai-git-commit-pr-generator/
├── main.py                    # 진입점 (Python 3.10+ 확인)
├── gitgen/
│   ├── cli.py                 # 인자 처리, 전체 흐름, 출력
│   ├── git_collector.py       # git status / git diff 수집·파싱
│   ├── safe_mode.py           # 민감 파일 제외, 마스킹, 전송량 제한
│   ├── prompts.py             # system / user 프롬프트 조립
│   ├── ai_client.py           # Claude Messages API REST 호출, 오류 처리
│   ├── validator.py           # JSON 파싱, 길이/형식 검증, 후처리
│   └── config.py              # .ai-gitgen.json 로드
├── tests/
│   ├── test_units.py          # 파싱, 마스킹, 검증 단위 테스트
│   ├── test_e2e.py            # 임시 Git 저장소 + 가짜 API 서버로 전체 흐름 테스트
│   └── helpers.py
├── .ai-gitgen.example.json    # 팀 컨벤션 설정 예시
└── requirements.txt           # 외부 의존성 없음
```

```bash
python3 -m unittest discover -s tests -t .
```

테스트는 실제 API 를 호출하지 않습니다. 로컬에 가짜 Claude API 서버를 띄우고 `AI_API_BASE_URL` 로 연결해서 다음을 확인합니다: 변경 없음 종료, API Key 미설정, 커밋/PR 생성, 형식 위반 시 1회 재생성, 401/네트워크 오류 메시지, safe-mode 마스킹, 루트가 아닌 위치에서 실행.

---

## 13. 주의사항 / 문제 해결

- 생성된 커밋/PR 문구는 **정답이 아니라 초안**입니다. diff 에 없는 맥락(왜 바꿨는지)은 AI 가 정확히 알 수 없으니 `-c` 로 알려주거나 직접 고치세요.
- **Git 이 초기화된 프로젝트 루트**에서 실행해야 합니다.
- 변경 사항 수집은 `git status`, `git diff` 만 사용하며, `git push` 나 GitHub PR 생성은 하지 않습니다.
- 첫 커밋 전 저장소, 새 파일(untracked), 이름 변경도 처리합니다. 바이너리 파일은 이름만 전달합니다.
- macOS 에서 python.org 설치본을 쓰는데 `SSL 인증서 오류` 가 나면 `/Applications/Python 3.x/Install Certificates.command` 를 실행하세요.
- 사내 프록시를 쓰면 `HTTPS_PROXY` 환경변수를 설정하세요 (`urllib` 가 자동으로 사용).
- 기본 모델(`claude-opus-5-5`)로 보낸 요청이 안전 분류기에 거절되면 서버가 자동으로 다른 모델로 다시 시도하도록 `fallbacks` 옵션을 함께 보냅니다.
