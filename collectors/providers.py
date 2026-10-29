"""AI 도구 공급자 플러그인 (v2). 표준 라이브러리만 사용 — 원격 PC에도 소스 그대로 보내 실행한다.

원격 에이전트에서는 claude_usage.py, sessions.py, codex_usage.py, gemini_usage.py 뒤에 이어 붙여 실행되므로
그 파일들의 이름(UsageLog, ClaudeUsage, Sessions, read_limits, CodexUsage, GeminiUsage ...)을 그대로 쓴다.

공급자 하나 = ProviderRunner. tick() 한 번에 화면용 결과를 돌려주고, drain_events()로 DB용 새 사용량 레코드를 넘긴다.
  tick() -> {"usage": UsageLog.snapshot(), "sessions": [...], "limits": {...}|None, "account": str|None}
인증 토큰 파일은 읽지 않는다. 계정 식별값은 해시 일부만 쓴다.
여러 계정: 같은 도구를 다른 로그인 폴더로 쓰면(CLAUDE_CONFIG_DIR, CODEX_HOME) 폴더마다 Runner 하나 (root=그 폴더).
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

try:   # 이 PC에서는 모듈로 import, 원격 에이전트에서는 이미 같은 이름이 정의되어 있음
    from collectors.claude_usage import ClaudeUsage, account_hash
    from collectors.codex_usage import CodexUsage, codex_limits, codex_sessions
    from collectors.gemini_usage import GeminiUsage, gemini_limits, gemini_sessions
    from collectors.sessions import Sessions, read_limits
except ImportError:
    pass


def _cache_path(provider, root):
    tag = hashlib.md5(str(root).encode()).hexdigest()[:8]
    name = f"pc-dashboard-{provider}-cache-{tag}.json"
    if provider == "claude":   # v1 원격 캐시 이름과 맞춰 첫 집계를 다시 하지 않음
        name = f"pc-dashboard-claude-cache-{tag}.json"
    return Path(tempfile.gettempdir()) / name


class ProviderRunner:
    id = "?"
    label = "?"

    def __init__(self, home, pricing=None, cache=True, root=None):
        self.home = Path(home)
        self.custom = bool(root)                       # 기본 폴더가 아닌 다른 계정 폴더
        self.dir = Path(root) if root else self.data_dir(home)
        self.pricing = pricing
        self.log = self._make_log(cache)

    @classmethod
    def data_dir(cls, home):
        raise NotImplementedError

    @classmethod
    def detect(cls, home):
        return cls.data_dir(home).is_dir()

    def _make_log(self, cache, keep_days=8):
        raise NotImplementedError

    RECENT_EVERY = 60   # 최근 세션 요약(닫힌 세션 포함)은 1분마다만 보냄 (크기 절약)

    def tick(self):
        usage = self.log.snapshot()
        summ = {s["session"]: s for s in self.log.session_summaries()}
        sessions = self.sessions()
        for s in sessions:   # 열린 세션에 제목·하위 에이전트 붙이기
            x = summ.get(s.get("session")) or {}
            s["title"] = s.get("title") or x.get("title")
            s["agents"] = x.get("agents") or []
        out = {"usage": usage, "sessions": sessions, "limits": self.limits(), "account": self.account(),
               "account_info": self.account_info()}
        if time.time() - getattr(self, "_recent_t", 0) >= self.RECENT_EVERY:
            self._recent_t = time.time()
            out["recent"] = list(summ.values())[:200]
        return out

    def sessions(self):
        return []

    def limits(self):
        return None

    def account(self):
        return None

    def account_info(self):
        """화면 표시용 로그인 계정 정보 {email, org, plan} (없으면 None). 토큰 파일은 읽지 않는다."""
        return None

    def drain_events(self):
        return self.log.drain_events()

    def all_events(self, since=0):
        return self.log.all_events(since)

    def history_events(self, days, before):
        """과거 기록 채우기(한 번만): 보관 기간을 days 일로 넓힌 일회용 로그로 before 이전 레코드를 모두 읽음."""
        log = self._make_log(False, keep_days=days)
        log.snapshot()
        return [e for e in log.all_events(0) if e["ts"] < before]


class ClaudeProvider(ProviderRunner):
    id, label = "claude", "Claude Code"

    def __init__(self, home, pricing=None, cache=True, root=None):
        super().__init__(home, pricing, cache, root)
        self.sess = Sessions(self.home, claude_dir=self.dir)
        self._acct = (0.0, None)
        self._info = None

    @classmethod
    def data_dir(cls, home):
        return Path(home) / ".claude"

    def _make_log(self, cache, keep_days=8):
        root = self.dir / "projects"
        return ClaudeUsage(root, pricing=self.pricing, cache_path=_cache_path("claude", root) if cache else None, keep_days=keep_days)

    def sessions(self):
        return self.sess.snapshot(self.log.session_totals())

    def limits(self):
        return read_limits(None, self.home, claude_dir=self.dir, desktop=not self.custom)

    def account(self):
        # ~/.claude.json (다른 계정 폴더면 그 폴더 안 .claude.json) 의 oauthAccount.organizationUuid (토큰 아님). 1분에 한 번만 읽음
        t, v = self._acct
        if time.time() - t > 60:
            try:
                f = self.dir / ".claude.json" if self.custom else self.home / ".claude.json"
                d = json.loads(f.read_text(encoding="utf-8"))
                oa = d.get("oauthAccount") or {}
                v = account_hash(oa.get("organizationUuid"), "claude")
                self._info = {"email": oa.get("emailAddress"), "org": oa.get("organizationName"),
                              "plan": oa.get("organizationType") or oa.get("billingType")} if oa else None
            except Exception:
                v, self._info = None, None
            self._acct = (time.time(), v)
        return v

    def account_info(self):
        self.account()
        return self._info


class CodexProvider(ProviderRunner):
    id, label = "codex", "GPT (Codex)"

    @classmethod
    def data_dir(cls, home):
        env = os.environ.get("CODEX_HOME")
        if env and Path(home) == Path.home():
            return Path(env)
        return Path(home) / ".codex"

    def _make_log(self, cache, keep_days=8):
        root = self.dir
        return CodexUsage(root, pricing=self.pricing, cache_path=_cache_path("codex", root) if cache else None, keep_days=keep_days)

    def sessions(self):
        return codex_sessions(self.log)

    def limits(self):
        return codex_limits(self.log)

    def account(self):
        return self.log.account


class GeminiProvider(ProviderRunner):
    id, label = "gemini", "Gemini"

    @classmethod
    def data_dir(cls, home):
        return Path(home) / ".gemini"

    @classmethod
    def detect(cls, home):
        return (cls.data_dir(home) / "tmp").is_dir()

    def _make_log(self, cache, keep_days=8):
        root = self.dir / "tmp"
        return GeminiUsage(root, pricing=self.pricing, cache_path=_cache_path("gemini", root) if cache else None, keep_days=keep_days)

    def sessions(self):
        return gemini_sessions(self.log)

    def limits(self):
        return gemini_limits(self.log)


PROVIDERS = {p.id: p for p in (ClaudeProvider, CodexProvider, GeminiProvider)}


# 다른 계정 로그인 폴더 찾기: 홈 바로 아래 '.claude*', '.codex*', '.gemini*' 폴더 중 기록이 있는 것
_PROFILE_MARK = {"claude": ("projects",), "codex": ("sessions", "auth.json"), "gemini": ("tmp",)}


def find_profiles(home):
    """{공급자: [기본 폴더가 아닌 계정 폴더 경로, ...]}"""
    home, out = Path(home), {}
    try:
        entries = sorted(home.iterdir())
    except OSError:
        return out
    for pid, cls in PROVIDERS.items():
        default = cls.data_dir(home)
        for d in entries:
            if (d.name.startswith("." + pid) and d != default and d.is_dir()
                    and any((d / m).exists() for m in _PROFILE_MARK[pid])):
                out.setdefault(pid, []).append(str(d))
    return out


def profile_detect(pid, root):
    """계정 폴더에 그 도구 기록이 있는지."""
    return any((Path(root) / m).exists() for m in _PROFILE_MARK[pid])


def detect_providers(home, enabled=None):
    """그 홈 폴더에 기록이 있는 공급자 id 목록 (enabled 가 주어지면 그 안에서만)."""
    return [pid for pid, cls in PROVIDERS.items()
            if (enabled is None or pid in enabled) and cls.detect(home)]
