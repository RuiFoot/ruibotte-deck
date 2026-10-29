# 여러 계정 (로그인 폴더) 설계 — 2026-10-29

## 목표
한 PC에서 같은 AI 도구를 여러 계정(예: Claude 회사·개인, GPT 회사·개인)으로 쓰는 경우를 계정별로 나눠 보여 준다.
토큰은 복사·저장하지 않는다 — 각 계정의 **로그인 폴더**를 등록하고 대시보드는 그 폴더를 읽기만 한다.

## 로그인 폴더
| 도구 | 기본 폴더 | 다른 계정 폴더 쓰는 법 |
|---|---|---|
| Claude Code | `~/.claude` (+ `~/.claude.json`) | `CLAUDE_CONFIG_DIR=<폴더>` 로 실행 (폴더 안에 `.claude.json`·`.credentials.json`) |
| Codex CLI | `~/.codex` | `CODEX_HOME=<폴더>` |
| Gemini CLI | `~/.gemini` | (폴더 지정만 지원) |

## 모델: 프로필 = (PC, 도구, 폴더, 이름)
- DB `profiles(id, machine_id, provider, path, label, enabled, created_at)`. `path=''` = 그 PC의 기본 폴더(행이 없어도 자동으로 있음, 이름만 붙일 때 행 생김).
- **상태·DB 키**: 기본 폴더 = PC id 그대로(`local`, `work-pc`) → 기존 기록과 호환. 추가 폴더 = `<PC id>~<프로필 id>` (예: `local~p7k2`).
  화면의 [전체 | PC별] 탭은 이 키마다 하나씩 생김 → "이 PC · 개인".
- **계정 묶음 = 이름(label)**: 여러 PC에서 같은 이름이면 같은 계정으로 봄 (예: 이 PC·work-pc 모두 '회사'). 이름 없는 기본 폴더 = '기본'.
  화면 위쪽 계정 칩 [모든 계정 | 회사 | 개인]으로 탭·합계를 거르고, 한도 카드는 계정별.
- 실제 로그인 계정 식별은 기존 `account_hash`(조직/이메일 해시) 그대로 DB `account_id`에 남김.

## 수집
- 이 PC: 프로필마다 ProviderRunner(폴더 지정). Claude 정확한 한도(PlanAPI)도 프로필 폴더의 `.credentials.json`마다 하나.
- 원격: 에이전트 설정에 `profiles: {도구: [{k: 프로필 id, dir: 경로}]}` → 프로필마다 집계, `@@PROV`/`@@EV`/`@@BFDONE`에 `k` 포함.
  프로필이 바뀌면 그 PC 연결을 다시 시작. 과거 기록 채우기·since는 키(`PC~프로필`)별.
- 폴더 찾기: 홈에서 `.claude*`·`.codex*`·`.gemini*` 중 기록이 있는 폴더를 후보로 (원격은 연결 시험·에이전트 시작 정보에 포함).

## 화면
- ⚙ 설정 → **AI 계정**: PC·도구별 폴더 목록(기본 + 추가), 이름 바꾸기, 찾은 후보 추가, 경로 직접 추가, 삭제.
- AI 사용량: 계정이 2개 이상이면 계정 칩, 한도 카드는 계정 탭.
