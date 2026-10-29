"""Claude 플랜 사용량(5시간·주간, 정확한 리셋 시각)을 Claude Code CLI 로그인 정보로 조회.

- 이 PC에서 `claude auth login`으로 로그인해 둔 ~/.claude/.credentials.json 을 사용한다.
- 공식 문서화된 API가 아니다(Claude Code가 /usage 에 쓰는 내부 엔드포인트). 실패하면 None을 돌려주고,
  대시보드는 데스크톱 앱 기록·로그 추정으로 자동으로 돌아간다.
- 토큰은 이 모듈 밖으로 내보내지 않는다(반환값·로그에 포함하지 않음).
- 이 PC에서는 CLI를 따로 쓰지 않으므로 대시보드가 토큰 갱신을 맡고, 갱신된 토큰을 같은 파일에 다시 저장한다.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CRED = Path.home() / ".claude" / ".credentials.json"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # Claude Code CLI의 공개 OAuth 클라이언트 ID
TOKEN_URLS = ["https://platform.claude.com/v1/oauth/token", "https://console.anthropic.com/v1/oauth/token"]
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
UA_FALLBACK = "claude-code/2.1.303"
_ua_cache = [0.0, None]


def find_claude_cli():
    """이 PC의 Claude Code CLI 실행 파일 (공식 설치판 우선, 다음 npm 설치판, PATH). 없으면 None."""
    home = Path.home()
    cands = [home / ".local" / "bin" / ("claude.exe" if sys.platform == "win32" else "claude")]
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        cands.append(Path(os.environ["APPDATA"]) / "npm" / "claude.cmd")
    for c in cands:
        if c.exists():
            return str(c)
    return shutil.which("claude")


def user_agent():
    """설치된 CLI 버전으로 User-Agent (예전 고정 버전으로 보내면 토큰 서버가 429를 줬을 가능성). 1시간마다 다시 확인."""
    if time.time() - _ua_cache[0] > 3600:
        ua = UA_FALLBACK
        exe = find_claude_cli()
        if exe:
            try:
                out = subprocess.run([exe, "--version"], capture_output=True, timeout=20,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.decode("utf-8", "replace")
                m = re.search(r"\d+\.\d+\.\d+", out)
                if m:
                    ua = f"claude-code/{m.group(0)}"
            except Exception:
                pass
        _ua_cache[:] = [time.time(), ua]
    return _ua_cache[1]


def _iso_to_epoch(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _post_json(url, body, timeout=15):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


CLI_RENEW_GAP = 600      # CLI로 대신 갱신하는 시도는 최소 10분 간격
CLI_RENEW_WAIT = 25      # 자격 증명 파일이 바뀌기를 기다리는 최대 시간(초)


def _log(msg):
    print(time.strftime("%m-%d %H:%M:%S"), "plan_api:", msg, flush=True)


class PlanAPI:
    def __init__(self, cred_path=CRED, min_interval=180):
        self.cred_path = Path(cred_path)
        self.min_interval = min_interval     # 너무 자주 부르면 429가 난다는 보고가 있어 최소 3분 간격
        self.last_call = 0.0
        self.last = None
        self.error = None
        self.backoff_until = 0.0
        self.fail_count = 0          # 연속 실패 횟수 (실패할수록 더 오래 쉼)
        self.cred_mtime = None       # 다시 로그인하면(파일이 바뀌면) 대기를 풀고 바로 재시도
        self.login_required = False
        self._own_write = False
        self.manual_fails = 0        # '새로고침' 연속 실패 횟수 (3번 이상이면 화면에 '다시 로그인' 버튼)
        self.cli_renew_at = 0.0      # 마지막으로 CLI에 갱신을 맡긴 시각
        self.cli_renew_ok = None     # 그 결과 (True/False)

    def _load(self):
        d = json.loads(self.cred_path.read_text(encoding="utf-8"))
        return d, d.get("claudeAiOauth") or {}

    def _refresh(self, d, o):
        last_err = None
        for url in TOKEN_URLS:
            try:
                r = _post_json(url, {"grant_type": "refresh_token", "refresh_token": o["refreshToken"],
                                     "client_id": CLIENT_ID})
                o = {**o, "accessToken": r["access_token"],
                     "refreshToken": r.get("refresh_token", o["refreshToken"]),
                     "expiresAt": int((time.time() + int(r.get("expires_in", 3600))) * 1000)}
                d = {**d, "claudeAiOauth": o}
                tmp = self.cred_path.with_suffix(".tmp")
                tmp.write_text(json.dumps(d), encoding="utf-8")
                os.replace(tmp, self.cred_path)
                self._own_write = True
                return o
            except urllib.error.HTTPError as e:
                if e.code in (400, 401):     # 갱신 토큰이 무효: 다시 로그인해야 함
                    self.login_required = True
                    raise RuntimeError(f"토큰 갱신 거부({e.code}) · 다시 로그인 필요")
                last_err = e                 # 429(요청 제한)·404 등: 다른 주소로 한 번씩만 (2026-10-19: 첫 주소가 계속 429)
            except Exception as e:
                last_err = e
        if isinstance(last_err, urllib.error.HTTPError) and last_err.code == 429:
            raise RuntimeError("토큰 갱신 요청 제한(429) · 잠시 후 재시도")
        raise RuntimeError(f"토큰 갱신 실패: {type(last_err).__name__}"
                           + (f" {last_err.code}" if isinstance(last_err, urllib.error.HTTPError) else ""))

    def cli_env(self):
        """다른 계정 폴더의 자격 증명이면 CLI도 그 폴더를 쓰게 (CLAUDE_CONFIG_DIR)."""
        d = self.cred_path.parent
        return {} if d == Path(CRED).parent else {"CLAUDE_CONFIG_DIR": str(d)}

    def _cli_renew(self):
        """대시보드의 토큰 갱신이 429로 막힐 때: 공식 CLI(`claude auth login`)를 창·브라우저 없이 실행.
        CLI는 시작하면서 만료된 토큰을 스스로 갱신해 자격 증명 파일에 저장한다(2026-10-28 관찰: 버튼으로 로그인 창만 띄워도 복구됨).
        파일이 바뀌면 바로 CLI를 끝내고 True. 브라우저는 BROWSER=none 으로 막는다(시험: 브라우저 안 뜸). 토큰 내용은 보지 않음."""
        now = time.time()
        if now - self.cli_renew_at < CLI_RENEW_GAP:
            return False
        self.cli_renew_at = now
        exe = find_claude_cli()
        if not exe:
            _log("CLI 없음 — 대신 갱신 불가")
            self.cli_renew_ok = False
            return False
        try:
            m0 = self.cred_path.stat().st_mtime
            p = subprocess.Popen([exe, "auth", "login"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env={**os.environ, "BROWSER": "none", **self.cli_env()},
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            _log(f"CLI 실행 실패: {type(e).__name__}")
            self.cli_renew_ok = False
            return False
        ok = False
        try:
            end = now + CLI_RENEW_WAIT
            while time.time() < end:
                if self.cred_path.stat().st_mtime != m0:
                    ok = True
                    time.sleep(1)   # 파일 쓰기가 끝나도록 잠깐
                    break
                if p.poll() is not None:
                    break
                time.sleep(0.3)
        finally:
            try:
                p.kill()
            except Exception:
                pass
        self.cli_renew_ok = ok
        if ok:
            self.cred_mtime = self.cred_path.stat().st_mtime   # 우리가 일으킨 변경: 재로그인으로 오인하지 않게
        _log(f"CLI로 대신 갱신 {'성공' if ok else '실패(파일 변화 없음)'} ({time.time() - now:.1f}초)")
        return ok

    def _refresh_or_cli(self, d, o):
        """직접 갱신 → 429면 CLI에 맡기고 새로 저장된 토큰을 읽음."""
        try:
            return self._refresh(d, o)
        except RuntimeError as e:
            if "429" not in str(e) or not self._cli_renew():
                raise
            d, o = self._load()
            if (o.get("expiresAt") or 0) / 1000 < time.time() + 60:
                raise RuntimeError("CLI 갱신 후에도 토큰 만료 상태")
            return o

    def _fetch(self):
        d, o = self._load()
        if not o.get("accessToken"):
            raise RuntimeError("CLI 로그인 정보 없음")
        if (o.get("expiresAt") or 0) / 1000 < time.time() + 300:
            o = self._refresh_or_cli(d, o)
        req = urllib.request.Request(USAGE_URL, headers={
            "Authorization": f"Bearer {o['accessToken']}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": user_agent(),
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:   # 만료 표시가 틀린 경우 한 번 갱신 후 재시도
                o = self._refresh_or_cli(d, o)
                req.add_header("Authorization", f"Bearer {o['accessToken']}")
                with urllib.request.urlopen(req, timeout=15) as r:
                    return json.loads(r.read().decode())
            raise

    def snapshot(self):
        """{'five_hour': {...}, 'seven_day': {...}, 'captured_at'} 또는 None (사용 불가)."""
        now = time.time()
        if not self.cred_path.exists():
            self.error = "CLI 로그인 안 됨"
            return None
        mtime = self.cred_path.stat().st_mtime
        if self.cred_mtime is not None and mtime != self.cred_mtime and not self._own_write:
            # 사용자가 다시 로그인함 → 대기·실패 기록을 지우고 바로 조회
            self.backoff_until, self.fail_count, self.login_required, self.last_call = 0.0, 0, False, 0.0
            self.manual_fails = 0
        self.cred_mtime, self._own_write = mtime, False
        if self.login_required:
            return self.last
        if now < self.backoff_until or now - self.last_call < self.min_interval:
            return self.last
        self.last_call = now
        try:
            raw = self._fetch()
            out = {"source": "api", "captured_at": now}
            for key in ("five_hour", "seven_day"):
                w = raw.get(key) or {}
                if w.get("utilization") is not None:
                    out[key] = {"used_percentage": float(w["utilization"]), "resets_at": _iso_to_epoch(w.get("resets_at"))}
            self.last, self.error, self.fail_count = out, None, 0
        except urllib.error.HTTPError as e:
            self._fail(now, f"사용량 조회 실패(HTTP {e.code})")
        except Exception as e:
            self._fail(now, str(e)[:120])
        return self.last

    def force(self, min_gap=60):
        """화면의 '새로고침': 대기(백오프)를 무시하고 지금 바로 토큰 갱신·조회를 한 번 시도.
        요청 제한(429)을 더 키우지 않도록 수동 시도끼리는 최소 min_gap 초 간격."""
        now = time.time()
        wait = int(min_gap - (now - getattr(self, "_manual_at", 0)))
        if wait > 0:
            return {"ok": False, "message": f"{wait}초 뒤에 다시 시도할 수 있어요", **self.status()}
        self._manual_at = now
        self.backoff_until, self.last_call, self.login_required = 0.0, 0.0, False
        before = self.last["captured_at"] if self.last else None
        self.snapshot()
        ok = bool(self.last and self.last["captured_at"] != before and not self.error)
        self.manual_fails = 0 if ok else self.manual_fails + 1
        msg = "갱신했어요" if ok else (self.error or "실패")
        if not ok and self.manual_fails >= 3:
            msg += " · 3번 연속 실패 — '다시 로그인'을 눌러 주세요"
        return {"ok": ok, "message": msg, **self.status()}

    def _fail(self, now, msg):
        """실패할수록 더 오래 쉼: 15분 → 30분 → 1시간 → 최대 2시간 (계속 두드려 요청 제한에 걸리지 않게)"""
        self.fail_count += 1
        self.error = msg
        self.backoff_until = now + min(7200, 900 * 2 ** (self.fail_count - 1))

    def status(self):
        """화면 표시용 상태 (토큰 등 민감 정보 없음)"""
        return {"error": self.error, "login_required": self.login_required, "manual_fails": self.manual_fails,
                "cli_renew_at": self.cli_renew_at or None, "cli_renew_ok": self.cli_renew_ok,
                "retry_in_sec": max(0, int(self.backoff_until - time.time())) if self.error else 0,
                "last_ok": self.last["captured_at"] if self.last else None}


if __name__ == "__main__":
    api = PlanAPI(min_interval=0)
    s = api.snapshot()
    if s:
        for k in ("five_hour", "seven_day"):
            w = s.get(k)
            if w:
                r = datetime.fromtimestamp(w["resets_at"]).strftime("%m-%d %H:%M") if w.get("resets_at") else "-"
                print(f"{k}: {w['used_percentage']}% used, resets {r}")
    else:
        print("failed:", api.error)
