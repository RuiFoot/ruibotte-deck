<p align="center"><img src="assets/icon.png" width="96" alt="RuiBotte Deck"></p>

<h1 align="center">RuiBotte Deck</h1>

<p align="center">내 PC들의 상태와 <b>AI 코딩 도구(Claude Code · GPT Codex CLI · Gemini CLI)</b> 사용량·한도를 한 화면에.<br>
<sub>made by <b>RuiBotte</b> (루이보테)</sub></p>

이 PC는 설치하자마자 보이고, 원격 PC(SSH)와 여러 AI 계정은 화면에서 추가한다. 노트북 화면이나 보조 모니터에
전체 화면으로 띄워 두고 쓰는 용도로 만들었다. 낮에는 라이트, 밤에는 다크 테마로 자동 전환된다.

## 주요 기능
- **컴퓨터**: PC마다 CPU·메모리·코어별 잔디·추이(실시간~30일)·프로세스·디스크·GPU·Docker, 원격 PC 연결 품질(SSH 왕복 시간·끊김)
- **AI 사용량**: 5시간·주간 한도 잔량과 소진 예측, 오늘 흐름(10분 단위), 최근 7일·30일·90일·잔디, 열린 세션
  (프로젝트별 묶음·한글 제목·하위 에이전트·컨텍스트 %), 프로젝트 순위, 요일×시간 패턴, API 가격 환산 비용
- **여러 계정**: 같은 도구를 회사·개인 등 여러 계정으로 써도 계정별로 한도·사용량을 나눠 봄 (계정 = 로그인 폴더)
- **알림**(Windows 알림): 세션 작업 끝 · 입력 대기(승인·질문) · 한도 10% 이하 · 한도 리셋 · 원격 PC 끊김 · 디스크 · CPU · 컨텍스트 90%
- **트레이**: 마우스를 올리면 한도 잔량·오늘 사용량, 우클릭하면 리셋 시각·원격 PC 연결·최근 알림. 아이콘은 작업 표시줄 테마(밝음/어두움)에 맞춰 바뀜
- 자동 넘김(영역별 📌 고정 가능), 세로 화면(아이패드) 배치, 다른 기기에서 보기(토큰 필요)

## 설치 (Windows)
1. [Releases](../../releases)에서 `RuiBotteDeck-Setup-<버전>.exe`를 받아 실행 — 관리자 권한 없이 사용자 폴더에 설치된다.
2. 처음 켜면 **시작하기** 마법사가 이 PC 감지 → AI 도구 → (선택) 원격 PC → 화면·자동 실행 순서로 안내한다.
3. 제거: **설정 > 앱 > 설치된 앱 > RuiBotte Deck > 제거**. 기록(DB)은 `%LOCALAPPDATA%\PCDashboard`에 남아 다시 설치하면 이어서 쓴다
   (완전히 지우려면 그 폴더를 삭제).

소스로 실행하려면 아래 [개발](#개발) 참고. macOS·Linux 실행 분기는 작성돼 있지만 실기기 시험 전이다.

## 원격 PC
- ⚙ 설정 → **PC 관리** → 원격 PC 추가: SSH 대상(`~/.ssh/config` 별칭, `user@host`, `user@host:port`)을 넣고 **연결 테스트**.
  OS·WSL·Python·AI 도구 기록이 있는 홈 폴더를 자동 감지한다.
- SSH는 **키 인증만** 쓴다. 비밀번호는 묻지도 저장하지도 않는다.
- 원격에는 아무것도 설치하지 않는다. 연결할 때 표준 라이브러리만 쓰는 작은 수집 프로그램(`collectors/agent.py`)을 SSH로 보내
  연결 하나로 실행하고, 연결이 끊기면 스스로 끝난다. 원격에 Python 3가 필요하다(Windows는 WSL python3 또는 일반 설치 Python,
  Python이 없으면 시스템 지표만).

## AI 도구와 계정
⚙ 설정 → **AI 관리**에서 도구마다 켜고 끄고, PC별 계정을 관리한다.

| 도구 | 기본 폴더 | 다른 계정 폴더 | 한도 |
|---|---|---|---|
| Claude Code | `~/.claude` | `CLAUDE_CONFIG_DIR` | 계정 사용량 API → 데스크톱 앱 기록 → 로그 추정 |
| GPT Codex CLI | `~/.codex` | `CODEX_HOME` | 로그에 남은 정확한 값(마지막 요청 시점) |
| Gemini CLI | `~/.gemini` | — | 공식 값 없음 → 오늘 응답 수로 추정 |

- **+ 로그인해서 계정 추가**: 이름(예: 개인)을 넣으면 그 계정 전용 폴더(`~/.claude-개인`)를 만들고 그 폴더로 CLI 로그인 창을 연다.
  그 계정으로 쓰려면 `$env:CLAUDE_CONFIG_DIR="<폴더>"; claude` 처럼 폴더를 지정해 CLI를 실행한다.
- 이미 로그인된 폴더는 경로로 등록하거나, 찾은 후보 폴더를 **이 폴더 등록**으로 추가한다. 원격 PC도 같은 방식(경로 등록).
- **같은 이름 = 같은 계정**: PC가 달라도 이름이 같으면 한도·합계를 같이 본다. AI 사용량 화면 위쪽 계정 칩으로 계정별로 거른다.

## 개인정보 · 보안
- 각 도구의 기록 파일을 **읽기만** 한다. 로그인 토큰은 복사·저장·기록하지 않는다.
  (Claude 정확한 한도 조회만 그 계정 폴더의 CLI 로그인 정보를 메모리에서 사용한다.)
- 계정 식별은 조직/계정 ID의 해시 일부만 쓴다.
- 기본은 이 PC에서만 접속(`127.0.0.1`). 다른 기기에서 보려면 `%LOCALAPPDATA%\PCDashboard\config.json`(소스 실행은 프로젝트 폴더)의
  `"bind"`를 `"0.0.0.0"`으로 바꾸고 `http://<이 PC IP>:8787/?t=<token>`으로 연다. 데이터 API는 모두 토큰이 필요하다.

## 기록 · 보관 (설정에서 변경)
- 시스템 지표: 1분 평균 7일 → 1시간 평균 90일, 원격 연결 품질 30일
- AI 사용량: 응답 단위 30일 → 1시간 합계 180일 → 하루 합계 무기한. 처음 연결할 때 로그에 남은 최근 90일을 한 번 가져온다.
- 한도·세션 기록 90일

## 개발
- 백엔드: Python 3.10+ · FastAPI(`app.py`) · 내장 SQLite(`store.py`) · PC(`machines.py`) · 수집기(`collectors/`) · 알림(`alerts.py`) · OS 연동(`desktop.py`) · 트레이(`tray.py`)
- 화면: React 18 + TypeScript + Recharts, Vite (`frontend/`)
- 소스로 실행:
  ```
  py -m pip install -r requirements.txt
  cd frontend && npm install && npm run build && cd ..
  py tray.py --install     # 바탕화면·시작 메뉴 바로가기 (또는 pyw tray.py 로 바로 실행)
  ```
- 시험: `py -m unittest discover -s tests`
- 설치 파일 빌드: `powershell -File packaging\build.ps1` (PyInstaller + [Inno Setup 6](https://jrsoftware.org/isinfo.php)) → `dist\RuiBotteDeck-Setup-<버전>.exe`
- 아이콘: `py make_icon.py`
- 설계 문서: `docs/V2_DESIGN.md`, `docs/ACCOUNTS_DESIGN.md`

## 라이선스
MIT
