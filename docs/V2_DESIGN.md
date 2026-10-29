# PC Dashboard v2 — 범용화 설계

> 목표: 누가 설치해도 **자기 PC부터 바로** 쓰고, **원격 PC와 AI 도구(Claude Code / GPT Codex / Gemini)를 화면에서 추가**해 쓰는 범용 대시보드.
> 특정 호스트·경로·계정이 코드에 박혀 있지 않아야 한다. 설정과 기록은 내장 DB(SQLite)에 저장한다.
> 작성: 2026-10-16. v1(현재 코드)의 수집 로직은 최대한 재사용한다.

---

## 1. 지금(v1)과 무엇이 달라지나

| | v1 (현재) | v2 |
|---|---|---|
| 대상 PC | 이 PC + `원격 PC` 고정 (config.json) | **이 PC 기본** + 화면에서 원격 PC 추가/삭제 (N대) |
| AI 도구 | Claude Code 고정 | **공급자 플러그인**: Claude Code, GPT(Codex CLI), Gemini CLI. 설치된 것 자동 감지 |
| 화면 | 컴퓨터 / Claude 2영역 | **컴퓨터** / **AI 사용량** → 공급자 선택 → 전체 요약 → PC별 → 세션 상세 |
| 저장 | 메모리 + state_cache.json | **SQLite(내장)**: 설정, PC 목록, 기록(보관 기간 정책) |
| 설정 | config.json 직접 수정 | **설정 화면** (PC 추가, 공급자 켜기/끄기, 보관 기간, 주기) |
| 하드코딩 | `SDC` 모니터, `/mnt/c/Users/<사용자>`, `원격 PC` 이름 등 | 전부 설정값 또는 자동 감지 |

---

## 2. 전체 구조

```
[트레이 앱 tray.py]
   └─ [서버 app.py (FastAPI)]
        ├─ Scheduler ── 각 Machine × 각 Collector 를 주기적으로 실행
        ├─ Machines
        │    ├─ LocalMachine   (이 PC: psutil + 각 OS 프로브)
        │    └─ RemoteMachine  (SSH: 연결 유지 스트림 러너, OS/WSL 자동 감지)
        ├─ Providers (플러그인)
        │    ├─ ClaudeCodeProvider  ~/.claude
        │    ├─ CodexProvider       ~/.codex (CODEX_HOME)
        │    └─ GeminiProvider      ~/.gemini
        ├─ Store (SQLite, WAL)      설정·PC 목록·기록·롤업·보관 정책
        └─ API  /api/...            화면에 필요한 조회 + 설정 변경
[화면 frontend (React)]
```

### 2.1 Machine (PC) 추상화
```python
class Machine:            # 공통 인터페이스
    id: str               # DB 키 (local 은 "local")
    name: str             # 표시 이름 (사용자 지정)
    kind: "local" | "ssh"
    def system_snapshot(self) -> SystemSnapshot      # CPU/메모리/디스크/네트워크/GPU/프로세스/도커
    def run_provider(self, provider) -> ProviderSnapshot  # 그 PC에서 AI 로그 집계
```
- **LocalMachine**: v1 `collectors/local.py`, `local_extras.py` 재사용. OS별 분기(Windows/macOS/Linux).
- **RemoteMachine(SSH)**: 설정값 `ssh_target`(예: `user@host:port` 또는 `~/.ssh/config` 별칭), 대체 주소 목록, `wsl_distro`(선택), `home_override`(선택).
  - 처음 연결 시 **OS 자동 감지**(`uname` / `ver`) → Windows면 PowerShell 프로브, Linux/macOS면 sh 프로브.
  - 원격 Windows에서 Python이 없거나 Store판이라 못 쓰면 WSL python3로 대체(v1에서 겪음). 둘 다 없으면 **PowerShell 전용 수집기**로 축소 동작.
  - 원격 수집은 v1 `remote_claude.py`처럼 **연결 하나를 유지하는 스트림 러너**(N초마다 JSON 한 줄, 부모 감시 watchdog). 시스템 지표와 AI 집계를 한 러너에서 같이 보낸다.
  - 원격 사용자 홈 경로는 자동 감지(`~` / `%USERPROFILE%`), SSH 계정과 실제 사용 계정이 다르면 `home_override`로 지정(v1 원격 PC 사례).

### 2.2 Provider (AI 도구) 플러그인
```python
class Provider:
    id: "claude" | "codex" | "gemini"
    def detect(self, fs) -> bool                         # 데이터 폴더 존재 여부
    def scan(self, fs, state) -> list[UsageEvent]        # 증분 파싱(파일별 offset)
    def sessions(self, fs) -> list[Session]              # 열린/최근 세션
    def limits(self, fs) -> list[LimitWindow] | None     # 플랜 한도(있으면)
    def account_id(self, fs) -> str | None               # 같은 계정 판별용
```
- 원격에서도 같은 코드가 돌아야 하므로 **표준 라이브러리만** 사용(v1 방식 유지). `fs`는 로컬 경로 접근 래퍼.
- 공통 이벤트 형식(DB 저장 단위):
  `UsageEvent(provider, machine_id, account_id, session_id, ts, model, input, output, cache_read, cache_write, reasoning, event_key)`
  - `event_key`로 중복 제거(Claude `message.id`, Codex는 세션ID+누적 토큰 차이 시점 등).

#### Claude Code (v1에서 검증됨)
- 사용량: `~/.claude/projects/**/*.jsonl` 의 assistant `message.usage`, 중복 제거 키 `message.id`.
- 세션: `~/.claude/sessions/<pid>.json` + PID 생존.
- 한도: (a) 데스크톱 앱 `plan-usage-history.json`(앱 시작 시에만 기록 — 2.37337 기준), (b) 비공개 사용량 API(토큰 갱신 429 문제 미해결, CLAUDE.md 참고), (c) 로그 기반 5시간 창 추정. 우선순위 a/b/c 중 가장 최신·정확한 것.
- 계정: 세션 로그/`claude auth status`의 `orgId`.

#### GPT — Codex CLI (원격 PC 실데이터로 확인, 2026-10-16)
- 경로: `$CODEX_HOME` 또는 `~/.codex`. 세션 로그 `sessions/YYYY/MM/DD/rollout-*.jsonl`, `archived_sessions/`, 목록 `session_index.jsonl`.
- 사용량: `{"type":"event_msg","payload":{"type":"token_count","info":{...},"rate_limits":{...}}}`
  - `info.last_token_usage` (이번 요청분) / `info.total_token_usage` (세션 누적):
    `input_tokens, cached_input_tokens, cache_write_input_tokens, output_tokens, reasoning_output_tokens, total_tokens`, `info.model_context_window`
  - 같은 파일에 `token_usage_record` 타입 줄도 있음(형식은 구현 시 확인).
- **한도(정확값이 로그에 있음)**: `rate_limits.primary/secondary = {used_percent, window_minutes, resets_at(epoch)}`, `plan_type`, `credits{has_credits, unlimited, balance}`, `rate_limit_reached_type`.
  → 가장 최근 `token_count`의 `rate_limits`가 곧 현재 잔량(마지막 요청 시점 기준).
- 세션 메타: `{"type":"session_meta","payload":{id, session_id, timestamp, cwd, cli_version, model_provider, originator, creator_account_id, ...}}`, 모델은 `turn_context`.
- 계정: `session_meta.creator_account_id`.
- 진행 중 판단: 로그 최근 기록 시각(예: 20초 이내) + `thread-writer-locks/` 존재 여부(구현 시 확인).

#### Gemini CLI (공식 소스 기준 조사, 실데이터 미확인 — 구현 시 실제 파일로 검증)
- 경로: `~/.gemini/tmp/<projectHash>/chats/session-<YYYY-MM-DDTHH-MM>-<id8>.jsonl` (하위 에이전트는 `chats/<부모세션ID>/<id>.jsonl`). 예전 형식은 단일 JSON `session-*.json`.
- **JSONL이 "변경 기록" 형식**: 메타 레코드, 메시지 레코드(문자열 `id`), `{$set:{...}}`, `{$patch:{...}}`, `{$rewindTo:id}` → 줄 단위로 읽으면 안 되고 **파일을 처음부터 재생(replay)** 해서 최종 상태를 만든 뒤 집계해야 함. (증분 파싱은 "마지막 재생 상태 + 새 줄 적용"으로)
- 대화 레코드: `sessionId, projectHash, startTime, lastUpdated, messages[], summary?, directories?, kind?(main|subagent)`. 메시지별 `cwd` 없음 → `directories`로 프로젝트 표시.
- 메시지: `id, timestamp, type(user|gemini), model, tokens`. **토큰**: `tokens: {input, output, cached, thoughts?, tool?, total}` (API의 `*TokenCount`가 아니라 짧은 이름).
- **한도: 로컬 기록 없음.** 공식 조회 API도 없음. 무료 등급 일일 요청 한도(약 1,000회/일로 알려짐)를 기준으로 "오늘 요청 수 / 기준값"으로만 근사 표시(설정에서 기준값 변경). 화면에 "추정"으로 명시.
- 계정 식별: 로컬 기록에서 확인되지 않음 → PC 단위로만 묶고, 같은 계정 합산은 사용자가 설정에서 지정.
- 대안 출처: 사용자가 텔레메트리를 켠 경우(`telemetry.target=local`, `outfile`) `gemini_cli.api_response`의 `*_token_count` 사용 가능(옵트인이라 기본은 chats 파일).

### 2.3 Store (내장 DB: SQLite)
- 파일: 앱 데이터 폴더(`%LOCALAPPDATA%\PCDashboard\dashboard.db`, macOS `~/Library/Application Support/PCDashboard`, Linux `~/.local/share/pcdashboard`). WAL 모드. 표준 라이브러리 `sqlite3`.
- 테이블(초안)
```sql
settings(key TEXT PRIMARY KEY, value TEXT)                                  -- 주기, 보관 기간, 화면 옵션, 표시 모니터 등
machines(id TEXT PRIMARY KEY, name, kind, ssh_target, ssh_fallbacks, wsl_distro, home_override,
         enabled INT, sort INT, created_at)
providers(id TEXT PRIMARY KEY, enabled INT, options_json)                  -- 공급자별 켜기/옵션
sys_minute(machine_id, ts INT, cpu, mem, swap, net_down, net_up, gpu, PRIMARY KEY(machine_id, ts))
sys_hour(machine_id, ts INT, cpu_avg, cpu_max, mem_avg, mem_max, net_down_sum, net_up_sum, PRIMARY KEY(machine_id, ts))
usage_event(provider, machine_id, account_id, session_id, ts INT, model, input, output, cache_read,
            cache_write, reasoning, event_key TEXT, rolled INT, PRIMARY KEY(provider, event_key))  -- rolled: 합계 반영 여부
usage_hour(provider, machine_id, account_id, ts INT, model, input, output, cache_read, cache_write, reasoning, n,
           PRIMARY KEY(provider, machine_id, account_id, ts, model))
usage_day (같은 구조, ts=자정)
limit_snapshot(provider, account_id, ts INT, window TEXT, used_pct REAL, resets_at INT, source TEXT)
session(provider, machine_id, session_id, account_id, project, model, started_at, last_activity, state, tokens,
        PRIMARY KEY(provider, machine_id, session_id))
scan_state(provider, machine_id, file_path, offset INT, PRIMARY KEY(provider, machine_id, file_path))  -- 증분 파싱 위치
```
- **보관 정책(기본값, 설정에서 변경)**: `sys_minute` 7일 → `sys_hour` 90일 / `usage_event` 30일 → `usage_hour` 180일 → `usage_day` 무기한 / `limit_snapshot` 90일 / `session` 90일. 매시 정각 롤업 + 보관 기간 지난 행 삭제.
- 기록은 **대시보드가 켜져 있는 동안**만 쌓인다. 다만 AI 로그는 파일에 남아 있으므로 다시 켜면 그동안의 사용량은 증분 파싱으로 채워진다(시스템 지표의 빈 구간은 빈 구간으로 표시).

---

## 3. 화면(정보 구조)

```
상단: 시계 · [컴퓨터 | AI 사용량] · 자동 넘김 · 설정(⚙)

컴퓨터
  └ PC 선택 탭(이 PC + 추가한 원격 PC들, 경고 점) ─ v1 MachineView 그대로(코어 잔디, 5분 그래프, 프로세스, 시스템 정보, 디스크·GPU·Docker)
     + 기간 선택(1시간/24시간/7일)  ← DB 기록으로 과거 추이

AI 사용량
  ├ 공급자 선택: [Claude Code | GPT (Codex) | Gemini]  (감지된 것만, 각 버튼에 오늘 합계·한도 요약)
  ├ 공급자 요약 (선택 공급자, 같은 계정 기준으로 모든 PC 합산)
  │    한도 카드(5시간/주간 등 공급자별 창 · 페이스 바) · 오늘/7일/30일 토큰 · 실시간 60분 · 모델별 비율
  ├ PC별 (그 공급자를 쓰는 PC들)
  │    PC 카드: 오늘 토큰 · 활동 중 세션 수 · 마지막 활동
  └ 세션 상세 (PC 선택 → 세션 목록 → 세션 클릭)
       세션: 프로젝트/작업 폴더 · 모델 · 시작/마지막 활동 · 토큰 누적·분당 추이 · 컨텍스트 사용률

설정(⚙)
  PC 관리: 추가(SSH 대상/별칭, 대체 주소, WSL, 홈 경로) → [연결 테스트] → 저장 / 이름 변경 / 끄기 / 삭제
  공급자: 켜기/끄기, 경로 재정의(CODEX_HOME 등), Claude 정확 한도(API) 사용 여부
  기록: 보관 기간, 수집 주기 / 화면: 표시 모니터, 자동 넘김, 색 테마
  다른 기기 접속: 접속 토큰 재발급, 같은 네트워크 공개 여부
```
- **같은 계정 판별**: 공급자별 `account_id`(Claude `orgId`, Codex `creator_account_id`, Gemini는 조사 결과에 따라)로 묶는다. 다른 계정이면 요약에서 계정별로 분리 표시.
- 색 규칙은 v1 유지: 시작 색만 공급자/항목별로 다르고 끝 구간은 공통 주황→빨강.
  공급자 기본색 제안: Claude = 앰버, GPT = 청록, Gemini = 파랑-보라.

---

## 4. API (초안)
```
GET  /api/overview                         상단 요약(경고, 공급자별 오늘 합계)
GET  /api/machines                         PC 목록 + 최신 시스템 스냅샷
GET  /api/machines/{id}/history?range=24h  시스템 추이(DB)
GET  /api/providers                        감지된 공급자 + 계정 + 한도 요약
GET  /api/providers/{p}/summary?range=7d   합산 사용량·한도·모델 비율·60분
GET  /api/providers/{p}/machines           PC별 사용량
GET  /api/providers/{p}/sessions?machine=  세션 목록
GET  /api/providers/{p}/sessions/{sid}     세션 상세(추이)
GET/PUT /api/settings
POST /api/machines            (추가)   PUT/DELETE /api/machines/{id}
POST /api/machines/test       (연결 테스트: SSH 접속, OS 감지, 공급자 감지 결과 반환)
GET  /api/health
```
- 실시간 갱신: 지금처럼 2초 폴링으로 시작. 필요해지면 SSE(`/api/stream`)로 전환.

---

## 5. 범용화 체크리스트 (하드코딩 제거)
- [x] `config.json`의 원격 설정 → DB `machines`로 이전(최초 실행 시 자동 마이그레이션: 기존 `원격 PC` 항목을 PC로 등록)
- [x] `find-screen.ps1 -Monitor SDC` → 설정의 "표시 모니터"(목록에서 선택, 기본: 내장 화면 자동 감지, 없으면 주 모니터)
- [x] `/mnt/c/Users/<사용자>` → PC별 `home_override`(기본 자동 감지)
- [x] (macOS/Linux 미시험) Windows 전용 코드(PowerShell, pythonw, Edge) → OS별 분기. macOS/Linux는 브라우저 기본 앱 창으로 열기
- [x] (macOS/Linux 미시험) 트레이: pystray는 macOS/Linux도 지원 — 플랫폼별 자동 실행(Windows Startup, macOS LaunchAgent, Linux autostart .desktop)
- [x] 첫 실행 마법사: "이 PC 감지 결과 → 감지된 AI 도구 → (선택) 원격 PC 추가"
- [x] 비밀 정보: SSH는 키 기반만(비밀번호 저장 안 함). AI 토큰은 각 도구의 파일을 읽기만, DB에 저장 금지
- [x] (requirements.txt + `tray.py --install`, PyInstaller는 미정) 배포: 일단 폴더+설치 스크립트, 이후 PyInstaller 단일 실행 파일 검토

---

## 6. 구현 단계 (권장 순서)
1. ✅ (2026-10-16) **Store 도입**: SQLite 스키마, 설정/PC 테이블, config.json 마이그레이션, 기록·롤업·보관 정책. 기존 화면은 그대로 동작해야 함.
2. ✅ (2026-10-16) **Machine 일반화**: LocalMachine/RemoteMachine, PC 추가·연결 테스트 API, OS 자동 감지, 스트림 러너에 시스템 지표 통합.
3. ✅ (2026-10-16) **Provider 플러그인화**: Claude를 플러그인으로 옮기고(동작 동일), Codex 추가(사용량+정확 한도), Gemini 추가.
4. ✅ (2026-10-16) **화면 개편**: 상단 [컴퓨터 | AI 사용량], 공급자 선택 → 요약 → PC별 → 세션 상세, 설정 화면.
5. ✅ (2026-10-16) **기간 추이**: DB 기록으로 1시간/24시간/7일/30일 그래프.
6. ✅ (2026-10-16) **범용 마감**: 첫 실행 마법사, 표시 모니터 선택, OS별 실행/자동 실행, 문서.

각 단계 끝에서 "지금 쓰는 화면이 깨지지 않는지"를 확인하고 넘어간다.

---

## 7. 공급자 조사 메모 (2026-10-16)

| | Claude Code | GPT (Codex CLI) | Gemini CLI |
|---|---|---|---|
| 데이터 폴더 | `~/.claude` | `$CODEX_HOME` 또는 `~/.codex` | `~/.gemini` |
| 사용량 기록 | projects/**/*.jsonl `message.usage` | sessions/**/rollout-*.jsonl `token_count` | tmp/<hash>/chats/*.jsonl `tokens` (재생 필요) |
| 정확한 한도 | 로그에 없음 (앱 기록·비공개 API·추정) | **로그에 있음** `rate_limits.primary/secondary` | 없음 (요청 수로 근사) |
| 계정 식별 | orgId | `creator_account_id` | 없음 |
| 진행 중 세션 | sessions/<pid>.json + PID | 로그 최근 기록 시각(+ thread-writer-locks 확인 필요) | 파일 `lastUpdated` 최근 여부 |
| 확인 상태 | v1에서 검증 | 원격 PC 실데이터로 구현·검증 (.jsonl.zst 압축 포함) | 원격 PC 실데이터는 단일 JSON 형식으로 검증, JSONL 재생은 미확인 |

- Codex 추가 메모: 2025-09 이전 세션엔 `token_count` 없음. 예전 버전은 `resets_in_seconds`였을 수 있어 둘 다 처리. `codex app-server`의 JSON-RPC `account/rateLimits/read`로도 한도 조회 가능(커뮤니티 확인). `~/.codex/state_5.sqlite`의 `threads.tokens_used`도 참고 가능(스키마 버전 번호가 바뀔 수 있음).
- 출처
  - Codex 프로토콜(필드 정의): https://raw.githubusercontent.com/openai/codex/main/codex-rs/protocol/src/protocol.rs
  - Codex 설정(CODEX_HOME): https://raw.githubusercontent.com/openai/codex/main/docs/config.md
  - Codex 한도 커뮤니티 도구: https://github.com/xiangz19/codex-ratelimit
  - Gemini 기록 서비스: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/packages/core/src/services/chatRecordingService.ts , chatRecordingTypes.ts
  - Gemini 텔레메트리: https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/telemetry.md
  - 세 도구 한도 비교 글: https://ianlpaterson.com/blog/tracking-claude-codex-gemini-quotas-from-one-script/
