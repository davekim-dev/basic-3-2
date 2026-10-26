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
```
                    ┌──────────────────┐
  git_collector ──▶ │                  │
  (GitChanges)      │                  │ ──▶ safe_mode.prepare_diff(changes.diffs)
                    │                  │ ◀── prepared (마스킹·제한된 diff)
                    │     cli.py       │
                    │     run()        │ ──▶ prompts.build_commit_prompt(changes, prepared)
                    │                  │ ◀── prompt (문자열)
                    │                  │
                    │                  │ ──▶ ai_client.complete(prompt)
                    │                  │ ◀── AI 응답
                    │                  │
                    │                  │ ──▶ validator.parse / validate / fix
                    └──────────────────┘ ◀── 최종 초안 → 화면 출력
```


| 단계 | 파일 |
|---|---|
| ① Git 수집 | [gitgen/git_collector.py](gitgen/git_collector.py) |
```
cli.py run()
 │
 ├─ ① ensure_repo_root(cwd)            ← cli.py:114, 검증 (실패하면 GitError로 종료)
 │     └─ run_git(rev-parse ...)
 │
 │   (설정 로드 등)
 │
② collect_commit_changes()
     ├─ collect_status()
     │     └─ run_git(status) → parse_status() → FileChange들 (+ 브랜치명)
     ├─ run_git(diff ...)   → split_diff()   → FileDiff들
     ├─ (새 파일) run_git(diff --no-index) → split_diff() → FileDiff 추가
     └─ GitChanges(branch, files, diffs, ...) 반환
 │
 └─ ③ changes.is_empty() 확인 → 이후 AI 프롬프트 생성 단계로
 ```
| ② diff 정리, 마스킹 | [gitgen/safe_mode.py](gitgen/safe_mode.py) |
```
for d in diffs:   (FileDiff 하나씩)
  │
  ├─ 민감 파일인가?            → 제외 "민감 파일"
  ├─ lock/빌드 파일인가?        → 제외 "lock/빌드 산출물"
  ├─ 바이너리 파일인가?         → 제외 "바이너리 파일"
  ├─ [safe] 파일 수 초과?       → 제외
  │
  ├─ [safe] mask_text()로 비밀 정보 가리기
  ├─ [safe] 줄 수 제한 넘으면 → 남은 줄까지만 자르고 "... N줄 생략" 표시
  ├─ 전체 글자 수 상한 넘으면 → 자르기 (남은 공간이 500자 미만이면 통째로 제외)
  │
  └─ 통과 → chunks에 추가, sent에 경로 기록
  
최종: PreparedDiff(text=chunks 합친 것, sent_files, omitted, ...) 반환
```
| ③ 프롬프트 | [gitgen/prompts.py](gitgen/prompts.py) |
```
build_commit_prompt / build_pr_prompt     ← cli.py가 부르는 메인 함수
  │
  ├─ [출력 규칙]        제목 길이, 형식, 언어 등 (커밋용 / PR용이 다름)
  ├─ _convention_block()  → [팀 컨벤션]   ← Convention에서
  ├─ [출력 JSON 형식]    {"title": ..., "body": [...]}
  └─ _changes_block()     → [브랜치] [변경 파일 목록] [변경 통계]
                            [내용을 보내지 않은 파일] [git diff]   ← GitChanges + PreparedDiff에서

build_retry_prompt   ← AI 응답이 규칙을 어기면 원래 프롬프트 + 이전 출력 + 문제점을 붙여 다시 보냄
```
| ④ REST 호출, 예외 처리 | [gitgen/ai_client.py](gitgen/ai_client.py) |
```
complete()
 ├─ build_payload()로 요청문 + 헤더 만들기
 ├─ 서버로 POST 전송
 │   ├─ 연결 자체 실패   → complete 안에서 바로 AIError
 │   ├─ HTTP 오류 코드   → _describe_http_error()가 메시지 생성 → AIError
 │   └─ 정상 응답(200)   → _parse_response()
 │                          ├─ 거절/빈 응답 → AIError
 │                          └─ 정상 → AIResponse 반환
```

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

API Key 는 **환경변수 `AI_API_KEY` 로만** 읽습니다(아래 `.env` 파일도 환경변수로 등록됨). 코드나 커밋되는 파일(`.ai-gitgen.json` 등)에는 키를 적지 마세요.
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

### `.env` 파일로 설정하기 (선택)

매번 터미널에 입력하기 번거로우면 `main.py` 와 같은 폴더에 `.env` 파일을 만들어 두세요. 실행할 때 자동으로 읽습니다.

```bash
cp .env.example .env              # Windows: Copy-Item .env.example .env
```

```dotenv
# .env
AI_API_KEY=sk-ant-...
# AI_API_BASE_URL=https://...     # Anthropic 호환 프록시를 쓸 때만
# AI_MODEL=claude-sonnet-5-5
```

- `.env` 는 `.gitignore` 에 포함되어 **커밋되지 않습니다.** (`git check-ignore -v .env` 로 확인)
- 터미널에서 이미 설정한 환경변수가 있으면 그 값이 **우선**합니다. `.env` 는 비어 있는 항목만 채웁니다.
- 불러온 항목은 이름만 표시하고 값은 출력하지 않습니다: `[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_MODEL`

---

## 4. 사용법
```
[System Prompt]              ← SYSTEM_PROMPT (고정)
당신은 시니어 개발자입니다... 원칙: diff에 있는 것만, 의도 중심, 비밀값 금지, JSON만

[User Prompt]                ← build_commit_prompt()
다음 변경 사항으로 커밋 메시지를 작성하세요.
[출력 규칙]        제목 50자 권장/72자 최대, type(scope): 요약, 불릿 1~3개
[팀 컨벤션]        허용 type, scope 규칙, 팀 규칙      ← _convention_block(conv)
[출력 JSON 형식]   {"title": "...", "body": [...]}
[작성자가 알려준 변경 이유]  -c 옵션 값             ← _changes_block(...)
[브랜치] [변경 파일 목록] [변경 통계] [내용 제외 파일] [git diff]
```

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


## 14. 추가 공부
- urllib: 파이썬에 기본으로 들어 있는 인터넷 통신용 표준 라이브러리

### 먼저 알아둘 것: 토큰(token)

AI 는 글을 **토큰** 단위로 읽고 씁니다. 토큰은 단어보다 조금 작은 조각입니다 (예: `"refactor"` → `"re"` + `"factor"`).
AI 가 답을 쓰는 과정은 "다음에 올 토큰 하나 고르기"를 계속 반복하는 것입니다.
아래 두 파라미터는 이 반복 과정에서 각각 다음을 정합니다.

- `temperature`: 매번 **어떤 토큰을 고를지**
- `max_tokens`: **몇 번까지** 고를지

### temperature: 단어(토큰) 고르기의 무작위성

**정의:** AI 가 다음 토큰을 고를 때 얼마나 무작위로 고를지 정하는 값입니다. 범위는 `0.0 ~ 1.0` 입니다.

AI 는 다음 토큰 후보마다 확률을 매깁니다. 예를 들어 커밋 메시지가 `"fix: "` 로 시작했다면:

| 다음 토큰 후보 | 확률 |
| --- | --- |
| `correct` | 60% |
| `resolve` | 30% |
| `handle` | 10% |

- **낮은 temperature (0 에 가까움):** 거의 항상 확률이 가장 높은 `correct` 를 고릅니다. 같은 입력이면 매번 거의 같은 결과가 나옵니다.
- **높은 temperature (1 에 가까움):** `resolve`, `handle` 도 자주 고릅니다. 실행할 때마다 표현이 달라집니다.

**예시:** 같은 diff 로 커밋 메시지를 두 번 생성하면

```text
temperature 0.0
  1회: fix: correct timeout error message
  2회: fix: correct timeout error message      ← 같음

temperature 1.0
  1회: fix: correct timeout error message
  2회: fix: handle request timeout gracefully  ← 달라짐
```

**언제 어떤 값을 쓰나**

| 값 | 특징 | 어울리는 작업 |
| --- | --- | --- |
| 낮음 (0 ~ 0.3) | 일관적, 예측 가능 | 커밋 메시지, 코드 생성, 정해진 형식(JSON) 출력 |
| 높음 (0.7 ~ 1.0) | 다양함, 창의적 | 아이디어 브레인스토밍, 글쓰기 |

> 이 프로젝트에서는 Claude Opus 5.5 같은 최신 모델이 `temperature` 를 받지 않습니다 (보내면 400 오류). 그래서 `--temperature` 를 지정했을 때만 요청에 넣습니다 → [6장](#6-api-파라미터와-결과-품질) 참고.

### max_tokens: 응답 길이 상한

**정의:** AI 가 한 번의 응답에서 만들 수 있는 **토큰 개수의 최대값**입니다. 이 개수에 닿으면 문장 중간이라도 바로 멈춥니다.
이때 응답의 `stop_reason` 은 `"max_tokens"` 가 됩니다. 끝까지 다 쓰고 멈추면 `"end_turn"` 입니다.

**예시:** 커밋 메시지를 JSON 으로 받아야 하는 상황

```text
max_tokens 넉넉함 (8000)
  {"subject": "fix: correct timeout error message", "body": "..."}   ← 정상

max_tokens 너무 작음 (10)
  {"subject": "fix: correct tim                                       ← 중간에 잘림, JSON 깨짐
```

**주의할 점**

- **상한일 뿐, 목표 길이가 아닙니다.** 8000 으로 줘도 답이 짧으면 100 토큰만 쓰고 끝납니다. 과금도 실제로 쓴 만큼만 됩니다.
- **너무 작으면** 응답이 잘리거나, 아예 비어 버립니다. 최신 모델은 답을 쓰기 전에 내부 추론에도 토큰을 쓰는데, 이것도 `max_tokens` 에 포함되기 때문입니다.
  - 이 경우 `ai_client.py` 의 `_parse_response` 가 `max_tokens(...) 에 도달해 응답이 비었습니다` 오류를 냅니다.
- 그래서 이 프로젝트는 기본값을 넉넉하게 `8000` 으로 둡니다.

### 한눈에 비교

| | temperature | max_tokens |
| --- | --- | --- |
| 정하는 것 | 답의 **내용**이 얼마나 달라지는지 | 답의 **길이**가 최대 얼마인지 |
| 값 범위 | 0.0 ~ 1.0 | 1 이상의 정수 |
| 잘못 정하면 | 너무 높음 → 형식 규칙을 어길 수 있음 | 너무 작음 → 응답이 잘리거나 비어 버림 |
| 이 프로젝트 기본값 | 보내지 않음 (모델 기본값 사용) | 8000 |


## 15. 확인해보기
- py -3.13 -m unittest tests.test_e2e.E2ETest.test_no_changes tests.test_e2e.E2ETest.test_not_repo_root -v

### 실행 경우의 수

#### 공통 (commit / pr 모두)

| # | 경우 | 상황 만드는 법 |
|---|---|---|
| 0-1 | Git 저장소가 아닌 곳에서 실행 | `git init` 안 된 폴더로 이동해서 실행 |
| 0-2 | 저장소의 하위 폴더에서 실행 | `cd tests` 후 `python ../main.py commit` |
| 0-3 | API Key 없음 | `.env` 의 `AI_API_KEY` 를 비우고 실행 |
| 0-4 | API Key 틀림 | `AI_API_KEY` 에 아무 값이나 넣고 실행 |
| 0-5 | API 주소가 웹페이지 | `AI_API_BASE_URL` 에 콘솔/웹사이트 주소를 넣고 실행 |
| 0-6 | API 서버에 연결 불가 | `AI_API_BASE_URL=http://127.0.0.1:1` 로 실행 |
| 0-7 | 지원하지 않는 모델 | `--model 없는-모델-이름` |
| 0-8 | API 호출 없이 확인 | `--dry-run` |

#### commit

| # | 경우 | 상황 만드는 법 |
|---|---|---|
| C-1 | 변경 없음 | 모든 변경을 커밋한 깨끗한 상태 |
| C-2 | 기존 파일 수정, `git add` 안 함 | 추적 중인 파일 수정 |
| C-3 | 새 파일만 있음, `git add` 안 함 | 새 파일 생성 |
| C-4 | `git add` 한 파일만 있음 | 파일 수정/생성 후 `git add` |
| C-5 | `git add` 한 파일 + 안 한 파일 섞임 | 파일 2개 수정 후 1개만 `git add` |
| C-6 | `--staged`, `git add` 한 파일 없음 | 파일만 수정하고 `commit --staged` |
| C-7 | 파일 삭제 | 추적 중인 파일 삭제 (`git rm` 또는 그냥 삭제) |
| C-8 | 파일 이름 변경 | `git mv a.py b.py` |
| C-9 | 민감 파일 포함 | `secret.env`, `id_rsa` 같은 이름의 파일 추가 |
| C-10 | 민감 값이 들어간 코드 + safe-mode | 코드에 `API_KEY = "sk-..."` 작성 후 `--safe-mode` |
| C-11 | 변경 이유 함께 전달 | `-c "로그인 버그 #12 수정"` |
| C-12 | 형식 위반 시 재생성 vs 후처리 | `--max-calls 2` (기본) / `--max-calls 1` |
| C-13 | 결과를 파일로 저장 | `--save AI_MSG` → `git commit -F AI_MSG` |

#### pr

| # | 경우 | 상황 만드는 법 |
|---|---|---|
| P-1 | `main` 에서 실행, 변경 없음 | `main` 의 깨끗한 상태 |
| P-2 | `main` 에서 실행, 커밋 안 한 변경 있음 | `main` 에서 파일 수정 |
| P-3 | 브랜치 생성 직후, 커밋·변경 없음 | `git switch -c feature/x` 직후 |
| P-4 | 브랜치에서 커밋 있음, 커밋 안 한 변경 없음 | 브랜치에서 수정 → 커밋 |
| P-5 | 브랜치에서 커밋 있음 + 커밋 안 한 변경 있음 | P-4 후 파일 하나 더 수정 |
| P-6 | 비교 기준 브랜치 지정 | `--base develop` (존재하는 브랜치) |
| P-7 | 없는 비교 기준 브랜치 지정 | `--base no-such-branch` |
| P-8 | 변경 이유 함께 전달 | `-c "회원가입 요구사항 반영"` |
| P-9 | 결과를 파일로 저장 | `--save PR.md` |

### 경우별 실습 명령 (Windows PowerShell / macOS)

각 경우는 **① Windows PowerShell → ② macOS (zsh/bash) → ③ CLI 출력 결과** 순서로 정리했습니다.

#### 시작 전 확인 (Windows / macOS 공통)

```bash
cd "<basic-3-2 폴더 경로>"
git status --short          # 아무것도 나오지 않아야 함 (작업 중인 변경은 먼저 커밋)
git branch --show-current   # main
```

- 작업 중인 변경이 남아 있으면 그 파일도 함께 수집되어 결과가 섞입니다. C-1 / P-1 은 깨끗한 상태에서만 재현됩니다.
- 연습 파일은 `practice.py`, `practice2.py` 등 **새 파일만** 사용하고, 각 블록의 `# 정리` 까지 실행하면 시작 전 상태로 돌아옵니다.
- 커밋이 필요한 경우는 임시 브랜치 `practice` 에서만 커밋하고, 정리할 때 브랜치째 삭제합니다. **`practice` 브랜치는 push 하지 않습니다.**
- 비용 없이 확인만 하려면 실행 명령 끝에 `--dry-run` 을 붙입니다.
- Windows 에서 파일 생성에 `-Encoding ascii` 를 쓰는 이유: PowerShell 5.1 의 `utf8` 은 파일 앞에 BOM 을 붙여 diff 에 보이지 않는 문자가 섞입니다.
- Windows 에서 `git add` 시 나오는 `LF will be replaced by CRLF` 경고는 줄바꿈 변환 안내이므로 무시해도 됩니다.

**③ CLI 출력 결과 읽는 법**

- `.env` 에 `AI_API_KEY`, `AI_API_BASE_URL`(https://copa.codyssey.kr), `AI_MODEL`(claude-sonnet-4) 을 설정하고 실행한 실제 결과입니다.
- AI 가 만든 부분(커밋 메시지, PR 제목/본문)과 토큰 수는 **실행할 때마다 달라질 수 있습니다.** 확인할 부분은 수집 범위(`[staged ...]`, `[working tree ...]` 등)와 형식입니다.
- 결과는 `--max-calls 1` 로 실행해서 `AI API 요청 중... (1/1)` 로 표시됩니다. 기본값으로 실행하면 `(1/2)` 로 표시됩니다.
- `[INFO] .env 에서 환경변수 불러옴` 줄은 `.env` 를 사용할 때만 나옵니다. 터미널에 직접 설정했다면 나오지 않습니다.

---

#### commit

##### C-1 변경 없음

① Windows PowerShell
```powershell
py -3.13 main.py commit
```

② macOS
```bash
python3 main.py commit
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.
```

##### C-2 기존(추적 중) 파일 수정, git add 안 함

준비: 임시 브랜치에서 `practice.py` 를 커밋해 "추적 중인 파일" 로 만든 뒤 수정합니다.

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
Add-Content practice.py "def sub(a, b):`n    return a - b" -Encoding ascii
# 실행
py -3.13 main.py commit
# 정리
git restore practice.py
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
printf 'def sub(a, b):\n    return a - b\n' >> practice.py
# 실행
python3 main.py commit
# 정리
git restore practice.py
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 9줄 (+2/-0) [working tree (git diff HEAD + untracked)]
[INFO] AI 전송 diff: 파일 1개, 9줄, 189자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1040 / 출력 56 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat(practice): 뺄셈 함수 추가

- practice.py에 두 수를 빼는 sub(a, b) 함수 추가
------------------------------------------------------------
[검증 통과] 제목 길이 24자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-3 새 파일만 있음, git add 안 함

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
py -3.13 main.py commit
# 정리
Remove-Item practice.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
python3 main.py commit
# 정리
rm practice.py
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [working tree (git diff HEAD + untracked)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1029 / 출력 53 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: add 함수 추가

- practice.py에 두 수를 더하는 add(a, b) 함수를 새로 작성
------------------------------------------------------------
[검증 통과] 제목 길이 15자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-4 git add 한 파일만 있음

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
py -3.13 main.py commit
# 정리
git restore --staged practice.py
Remove-Item practice.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
python3 main.py commit
# 정리
git restore --staged practice.py
rm practice.py
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1027 / 출력 52 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: add 함수 추가

- practice.py에 두 수를 더하는 add(a, b) 함수 신규 작성
------------------------------------------------------------
[검증 통과] 제목 길이 15자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-5 git add 한 파일 + 안 한 파일 섞임

`git add` 한 `practice.py` 만 수집되고, `practice2.py` 는 메시지에 포함되지 않습니다.

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
Set-Content practice2.py "def mul(a, b):`n    return a * b" -Encoding ascii
git add practice.py
py -3.13 main.py commit
# 정리
git restore --staged practice.py
Remove-Item practice.py, practice2.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
printf 'def mul(a, b):\n    return a * b\n' > practice2.py
git add practice.py
python3 main.py commit
# 정리
git restore --staged practice.py
rm practice.py practice2.py
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1027 / 출력 64 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: 두 수를 더하는 add 함수 추가

- practice.py에 두 인자를 받아 합산값을 반환하는 add 함수 구현
------------------------------------------------------------
[검증 통과] 제목 길이 24자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-6 --staged 인데 git add 한 파일 없음

파일이 바뀌어 있어도 `--staged` 는 add 한 파일만 보므로 "변경 없음" 으로 끝납니다.

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
py -3.13 main.py commit --staged
# 정리
Remove-Item practice.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
python3 main.py commit --staged
# 정리
rm practice.py
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 변경 사항이 없습니다. 커밋 메시지를 생성하지 않고 종료합니다.
```

##### C-7 파일 삭제

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
Remove-Item practice.py
# 실행
py -3.13 main.py commit
# 정리
git restore practice.py
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
rm practice.py
# 실행
python3 main.py commit
# 정리
git restore practice.py
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+0/-2) [working tree (git diff HEAD + untracked)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 169자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1027 / 출력 64 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
chore: practice.py 파일 삭제

- 연습용으로 작성된 practice.py의 add 함수 정의를 포함한 파일 전체를 제거
------------------------------------------------------------
[검증 통과] 제목 길이 24자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-8 파일 이름 변경

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
git mv practice.py practice_math.py
# 실행
py -3.13 main.py commit
# 정리
git mv practice_math.py practice.py
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
git mv practice.py practice_math.py
# 실행
python3 main.py commit
# 정리
git mv practice_math.py practice.py
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 4줄 (+0/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 4줄, 117자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 994 / 출력 79 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
refactor: practice.py 파일명을 practice_math.py로 변경

- 파일 내용 변경 없이 practice.py를 practice_math.py로 이름 변경하여 파일 목적을 명확히 함
------------------------------------------------------------
[검증 통과] 제목 길이 47자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-9 민감 파일 포함

`secret.env` 는 파일 목록에만 들어가고 **내용은 AI 에 보내지 않습니다** (`AI 전송 diff: 파일 1개`).

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
Set-Content secret.env "PASSWORD=practice1234" -Encoding ascii
py -3.13 main.py commit
# 정리
Remove-Item practice.py, secret.env
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
printf 'PASSWORD=practice1234\n' > secret.env
python3 main.py commit
# 정리
rm practice.py secret.env
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 2개 파일 변경 감지
[INFO] Git diff 수집 완료: 15줄 (+3/-0) [working tree (git diff HEAD + untracked)]
[INFO] 내용 제외: secret.env (민감 파일 - 내용 제외)
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1084 / 출력 103 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: 덧셈 함수 추가 및 환경 파일 생성

- practice.py에 두 수를 더하는 add(a, b) 함수 추가
- secret.env 환경 설정 파일 신규 생성 (민감 정보 포함으로 내용 비공개)
------------------------------------------------------------
[검증 통과] 제목 길이 25자 (권장 ≤50, 최대 72) · 본문 불릿 2개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-10 민감 값이 들어간 코드 + safe-mode (가짜 키/이메일 사용)

① Windows PowerShell
```powershell
Set-Content practice_config.py 'API_KEY = "sk-ant-api03-practicepracticepractice"', 'OWNER = "kim@example.com"' -Encoding ascii
py -3.13 main.py commit --dry-run               # safe-mode OFF: 전송될 프롬프트 확인
py -3.13 main.py commit --dry-run --safe-mode   # safe-mode ON : 전송될 프롬프트 비교
py -3.13 main.py commit --safe-mode             # 실제 실행
# 정리
Remove-Item practice_config.py
```

② macOS
```bash
printf '%s\n' 'API_KEY = "sk-ant-api03-practicepracticepractice"' 'OWNER = "kim@example.com"' > practice_config.py
python3 main.py commit --dry-run               # safe-mode OFF: 전송될 프롬프트 확인
python3 main.py commit --dry-run --safe-mode   # safe-mode ON : 전송될 프롬프트 비교
python3 main.py commit --safe-mode             # 실제 실행
# 정리
rm practice_config.py
```

③ CLI 출력 결과

safe-mode OFF (`--dry-run`, 프롬프트 중 해당 부분만 발췌)
```text
[INFO] AI 전송 diff: 파일 1개, 8줄, 230자
...
+API_KEY = "sk-ant-api03-practicepracticepractice"
+OWNER = "kim@example.com"
```

safe-mode ON (`--dry-run --safe-mode`, 프롬프트 중 해당 부분만 발췌)
```text
[INFO] safe-mode ON: 파일 최대 10개 / 최대 200줄, 마스킹 2건 {'anthropic_key': 1, 'email': 1}
[INFO] AI 전송 diff: 파일 1개, 8줄, 208자
...
+API_KEY = "[MASKED_API_KEY]"
+OWNER = "[MASKED_EMAIL]"
```

실제 실행 (`--safe-mode`)
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [working tree (git diff HEAD + untracked)]
[INFO] safe-mode ON: 파일 최대 10개 / 최대 200줄, 마스킹 2건 {'anthropic_key': 1, 'email': 1}
[INFO] AI 전송 diff: 파일 1개, 8줄, 208자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1048 / 출력 104 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
chore: practice_config 설정 파일 추가

- API_KEY와 OWNER 값을 보관하는 practice_config.py 파일 신규 생성
- 민감 정보(API 키, 이메일)가 포함되어 있으므로 .gitignore 등록 여부 확인 필요
------------------------------------------------------------
[검증 통과] 제목 길이 31자 (권장 ≤50, 최대 72) · 본문 불릿 2개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-11 변경 이유 함께 전달

`-c` 로 준 내용("계산 기능 연습용")이 제목에 반영됩니다.

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
py -3.13 main.py commit -c "계산 기능 연습용 함수 추가"
# 정리
git restore --staged practice.py
Remove-Item practice.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
python3 main.py commit -c "계산 기능 연습용 함수 추가"
# 정리
git restore --staged practice.py
rm practice.py
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1065 / 출력 72 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: 계산 기능 연습용 덧셈 함수 추가

- practice.py에 두 값을 더해 반환하는 add(a, b) 함수를 새로 추가함
------------------------------------------------------------
[검증 통과] 제목 길이 24자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-12 형식 위반 시 재생성(2) vs 후처리(1)

AI 응답이 규칙을 지키면 둘 다 1회로 끝납니다. 규칙을 어기면 `--max-calls 2` 는 `[WARN] 형식 규칙 위반 → 1회 재생성` 후 다시 요청하고, `--max-calls 1` 은 `[WARN] 후처리: ...` 로 직접 다듬습니다.

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
py -3.13 main.py commit --max-calls 2
py -3.13 main.py commit --max-calls 1
# 정리
git restore --staged practice.py
Remove-Item practice.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
python3 main.py commit --max-calls 2
python3 main.py commit --max-calls 1
# 정리
git restore --staged practice.py
rm practice.py
```

③ CLI 출력 결과 (이번 실행에서는 둘 다 형식을 지켜 1회로 끝남)

`--max-calls 2`
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/2)
[INFO] 응답 수신: 입력 1027 / 출력 70 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: 두 수를 더하는 add 함수 추가

- practice.py에 두 인자 a, b를 받아 합산 결과를 반환하는 add 함수 추가
------------------------------------------------------------
[검증 통과] 제목 길이 24자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

`--max-calls 1`
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1027 / 출력 60 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: 덧셈 함수 추가

- practice.py에 두 수를 더해 반환하는 add(a, b) 함수 신규 작성
------------------------------------------------------------
[검증 통과] 제목 길이 14자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### C-13 결과를 파일로 저장

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
py -3.13 main.py commit --save AI_MSG
Get-Content AI_MSG -Encoding utf8     # 실제 적용은 git commit -F AI_MSG (실습에서는 실행하지 않음)
# 정리
git restore --staged practice.py
Remove-Item practice.py, AI_MSG
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
python3 main.py commit --save AI_MSG
cat AI_MSG                            # 실제 적용은 git commit -F AI_MSG (실습에서는 실행하지 않음)
# 정리
git restore --staged practice.py
rm practice.py AI_MSG
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [staged (git diff --cached)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1027 / 출력 52 토큰, stop_reason=end_turn
[DONE] 커밋 메시지 생성 완료
[INFO] AI API 호출 횟수: 1회

--- Commit Message ---
feat: add 함수 추가

- practice.py에 두 수를 더하는 add(a, b) 함수 신규 작성
------------------------------------------------------------
[검증 통과] 제목 길이 15자 (권장 ≤50, 최대 72) · 본문 불릿 1개
[INFO] 저장됨: AI_MSG (적용: git commit -F AI_MSG)
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

`AI_MSG` 파일 내용
```text
feat: add 함수 추가

- practice.py에 두 수를 더하는 add(a, b) 함수 신규 작성
```

---

#### pr

##### P-1 main 에서 실행, 변경 없음

① Windows PowerShell
```powershell
py -3.13 main.py pr
```

② macOS
```bash
python3 main.py pr
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: main (base: main)
[INFO] 변경 사항이 없습니다. PR 초안를 생성하지 않고 종료합니다.
```

##### P-2 main 에서 실행, 커밋 안 한 변경 있음

① Windows PowerShell
```powershell
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
py -3.13 main.py pr
# 정리
Remove-Item practice.py
```

② macOS
```bash
printf 'def add(a, b):\n    return a + b\n' > practice.py
python3 main.py pr
# 정리
rm practice.py
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: main (base: main)
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [uncommitted changes (git diff HEAD)]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1085 / 출력 204 토큰, stop_reason=end_turn
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 1회

--- PR Title ---
feat: add 함수 추가

--- PR Body ---
## Why
- 두 수를 더하는 기본 연산 기능이 필요하여 practice.py에 add 함수를 작성함

## What
- practice.py 신규 파일 생성
- 두 인자 a, b를 받아 합산 결과를 반환하는 add(a, b) 함수 구현

## How to Test
- python3 -c "from practice import add; print(add(2, 3))" 실행 후 출력값이 5인지 확인
- python3 -c "from practice import add; print(add(-1, 1))" 실행 후 출력값이 0인지 확인
------------------------------------------------------------
[검증 통과] PR 제목 15자 (최대 80) · Why 1 / What 2 / How to Test 2 불릿
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### P-3 브랜치 생성 직후, 커밋·변경 없음

① Windows PowerShell
```powershell
git switch -c practice
py -3.13 main.py pr
# 정리
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
python3 main.py pr
# 정리
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: practice (base: main)
[INFO] 변경 사항이 없습니다. PR 초안를 생성하지 않고 종료합니다.
```

##### P-4 브랜치에서 커밋 있음, 커밋 안 한 변경 없음

커밋 안 한 파일이 없어서 `0개 파일 변경 감지` 로 나오지만, `main...HEAD` 커밋 차이를 수집합니다.

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
py -3.13 main.py pr
# 정리
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
python3 main.py pr
# 정리
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: practice (base: main)
[INFO] Git status 수집 완료: 0개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [git diff main...HEAD]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1085 / 출력 238 토큰, stop_reason=end_turn
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 1회

--- PR Title ---
feat: 두 수를 더하는 add 함수 추가

--- PR Body ---
## Why
- 기본적인 덧셈 연산을 재사용 가능한 함수로 제공하기 위해 practice.py 파일을 새로 생성

## What
- practice.py 신규 생성: 두 인자 a, b를 받아 합산 결과를 반환하는 add(a, b) 함수 추가

## How to Test
- Python 인터프리터 또는 스크립트에서 `from practice import add` 로 함수를 임포트
- `add(2, 3)` 호출 시 `5` 가 반환되는지 확인
- `add(-1, 1)` 호출 시 `0` 이 반환되는지 확인
------------------------------------------------------------
[검증 통과] PR 제목 24자 (최대 80) · Why 1 / What 1 / How to Test 3 불릿
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### P-5 브랜치에서 커밋 있음 + 커밋 안 한 변경 있음

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
Set-Content practice2.py "def mul(a, b):`n    return a * b" -Encoding ascii
py -3.13 main.py pr
# 정리
Remove-Item practice2.py
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
printf 'def mul(a, b):\n    return a * b\n' > practice2.py
python3 main.py pr
# 정리
rm practice2.py
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: practice (base: main)
[INFO] Git status 수집 완료: 1개 파일 변경 감지
[INFO] Git diff 수집 완료: 16줄 (+4/-0) [git diff main...HEAD + uncommitted changes (git diff HEAD)]
[INFO] AI 전송 diff: 파일 2개, 16줄, 333자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1175 / 출력 330 토큰, stop_reason=end_turn
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 1회

--- PR Title ---
feat: 덧셈 및 곱셈 함수 추가

--- PR Body ---
## Why
- 기본 산술 연산(덧셈, 곱셈)을 수행하는 함수가 필요하여 새 파일로 추가

## What
- practice.py: 두 인자를 받아 합산하는 `add(a, b)` 함수 추가
- practice2.py: 두 인자를 받아 곱셈하는 `mul(a, b)` 함수 추가 (현재 untracked 상태로, 커밋 포함 여부 확인 필요)

## How to Test
- Python 인터프리터에서 `from practice import add` 후 `add(2, 3)` 실행 → 결과 `5` 확인
- Python 인터프리터에서 `from practice2 import mul` 후 `mul(2, 3)` 실행 → 결과 `6` 확인
- practice2.py 가 untracked 상태이므로 `git add practice2.py` 후 커밋에 포함할지 팀원과 확인
------------------------------------------------------------
[검증 통과] PR 제목 19자 (최대 80) · Why 1 / What 2 / How to Test 3 불릿
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### P-6 비교 기준 브랜치 지정

연습용 기준 브랜치 `practice-base` 를 만들어 `--base` 로 지정합니다.

① Windows PowerShell
```powershell
git branch practice-base
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
py -3.13 main.py pr --base practice-base
# 정리
git switch main
git branch -D practice practice-base
```

② macOS
```bash
git branch practice-base
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
python3 main.py pr --base practice-base
# 정리
git switch main
git branch -D practice practice-base
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: practice (base: practice-base)
[INFO] Git status 수집 완료: 0개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [git diff practice-base...HEAD]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1087 / 출력 194 토큰, stop_reason=end_turn
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 1회

--- PR Title ---
feat: add 함수 추가

--- PR Body ---
## Why
- 두 숫자를 더하는 기본 연산 기능이 필요하여 구현

## What
- practice.py에 두 인자 a, b를 받아 합산 결과를 반환하는 add(a, b) 함수 추가

## How to Test
- python -c "from practice import add; print(add(1, 2))" 실행 후 출력값이 3인지 확인
- 음수, 0, 소수 등 다양한 입력값으로 add() 호출 시 올바른 합산 결과가 반환되는지 확인
------------------------------------------------------------
[검증 통과] PR 제목 15자 (최대 80) · Why 1 / What 1 / How to Test 2 불릿
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### P-7 없는 비교 기준 브랜치 지정

① Windows PowerShell
```powershell
py -3.13 main.py pr --base no-such-branch
```

② macOS
```bash
python3 main.py pr --base no-such-branch
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[ERROR] Git 오류: 비교 기준 브랜치 'no-such-branch' 를 찾을 수 없습니다. --base 옵션을 확인하세요.
```

##### P-8 변경 이유 함께 전달

`-c` 로 준 내용("계산 기능 요구사항")이 Why 에 반영됩니다.

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
py -3.13 main.py pr -c "계산 기능 요구사항 반영"
# 정리
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
python3 main.py pr -c "계산 기능 요구사항 반영"
# 정리
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: practice (base: main)
[INFO] Git status 수집 완료: 0개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [git diff main...HEAD]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1121 / 출력 250 토큰, stop_reason=end_turn
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 1회

--- PR Title ---
feat: 두 수를 더하는 add 함수 추가

--- PR Body ---
## Why
- 계산 기능 요구사항에 따라 두 수의 합을 반환하는 기본 연산 함수가 필요함

## What
- practice.py 신규 파일 생성
- 두 인자 a, b를 받아 합산 결과를 반환하는 add(a, b) 함수 구현

## How to Test
- Python 인터프리터 또는 REPL에서 `from practice import add` 로 함수 임포트
- `add(1, 2)` 호출 후 반환값이 `3` 인지 확인
- 음수·0 등 경계값(예: `add(-1, 1)`, `add(0, 0)`)으로도 결과가 올바른지 확인
------------------------------------------------------------
[검증 통과] PR 제목 24자 (최대 80) · Why 1 / What 2 / How to Test 3 불릿
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

##### P-9 결과를 파일로 저장

① Windows PowerShell
```powershell
git switch -c practice
Set-Content practice.py "def add(a, b):`n    return a + b" -Encoding ascii
git add practice.py
git commit -m "practice: add practice.py"
py -3.13 main.py pr --save PR.md
Get-Content PR.md -Encoding utf8
# 정리
Remove-Item PR.md
git switch main
git branch -D practice
```

② macOS
```bash
git switch -c practice
printf 'def add(a, b):\n    return a + b\n' > practice.py
git add practice.py
git commit -m "practice: add practice.py"
python3 main.py pr --save PR.md
cat PR.md
# 정리
rm PR.md
git switch main
git branch -D practice
```

③ CLI 출력 결과
```text
[INFO] .env 에서 환경변수 불러옴: AI_API_KEY, AI_API_BASE_URL, AI_MODEL
[INFO] 현재 브랜치: practice (base: main)
[INFO] Git status 수집 완료: 0개 파일 변경 감지
[INFO] Git diff 수집 완료: 8줄 (+2/-0) [git diff main...HEAD]
[INFO] AI 전송 diff: 파일 1개, 8줄, 165자
[INFO] API 주소: https://copa.codyssey.kr
[INFO] 모델: claude-sonnet-4, temperature=model default, max_tokens=8000, effort=medium
[INFO] AI API 요청 중... (1/1)
[INFO] 응답 수신: 입력 1085 / 출력 207 토큰, stop_reason=end_turn
[DONE] PR 초안 생성 완료
[INFO] AI API 호출 횟수: 1회

--- PR Title ---
feat: 두 수를 더하는 add 함수 추가

--- PR Body ---
## Why
- 기본적인 덧셈 연산을 수행하는 유틸리티 함수가 필요하여 추가함

## What
- practice.py 에 두 인자 a, b 를 받아 합산 결과를 반환하는 add(a, b) 함수 신규 작성

## How to Test
- python -c "from practice import add; print(add(2, 3))" 실행 후 출력값이 5 인지 확인
- python -c "from practice import add; print(add(-1, 1))" 실행 후 출력값이 0 인지 확인
------------------------------------------------------------
[검증 통과] PR 제목 24자 (최대 80) · Why 1 / What 1 / How to Test 2 불릿
[INFO] 저장됨: PR.md (적용: PR 작성 화면에 붙여넣기)
[INFO] 생성 결과는 초안입니다. 내용을 검토한 뒤 적용하세요.
```

`PR.md` 파일 내용
```text
feat: 두 수를 더하는 add 함수 추가

## Why
- 기본적인 덧셈 연산을 수행하는 유틸리티 함수가 필요하여 추가함

## What
- practice.py 에 두 인자 a, b 를 받아 합산 결과를 반환하는 add(a, b) 함수 신규 작성

## How to Test
- python -c "from practice import add; print(add(2, 3))" 실행 후 출력값이 5 인지 확인
- python -c "from practice import add; print(add(-1, 1))" 실행 후 출력값이 0 인지 확인
```

---

#### 정리가 잘 됐는지 확인 (Windows / macOS 공통)

```bash
git status --short     # 아무것도 나오지 않아야 함
git branch             # main 만 있어야 함
```
