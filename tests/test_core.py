"""핵심 로직 자동 시험 (실제 데이터·자격 증명은 건드리지 않음, 임시 폴더만 사용).
실행: py -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from store import Store  # noqa: E402
from collectors.claude_usage import ClaudeUsage  # noqa: E402
from collectors.codex_usage import CodexUsage, codex_limits  # noqa: E402
from collectors.gemini_usage import GeminiUsage, _replay_jsonl  # noqa: E402
from collectors import plan_api  # noqa: E402
from machines import framed, normalize_target, validate_distro  # noqa: E402


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_lines(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.s.close()
        self.tmp.cleanup()

    def test_migration_once(self):
        cfg = {"remote": {"label": "box", "hosts": ["box", "box-ext"], "wsl_distro": "Ubuntu"}, "local_interval_sec": 2}
        self.assertTrue(self.s.migrate_v1_config(cfg))
        self.assertFalse(self.s.migrate_v1_config(cfg))
        m = [x for x in self.s.machines() if x["kind"] == "ssh"]
        self.assertEqual((m[0]["ssh_target"], m[0]["ssh_fallbacks"]), ("box", ["box-ext"]))
        self.assertTrue(self.s.get("setup.done"))   # v1 사용자는 첫 실행 마법사 생략

    def test_sys_minute_and_hour_rollup(self):
        base = int(time.time()) // 3600 * 3600 - 2 * 3600
        for i in range(0, 3600, 10):
            self.s.record_sys("local", base + i, cpu=40, mem=50, net_down=100)
        self.s.flush()
        self.s.rollup()
        h = self.s.query("SELECT * FROM sys_hour WHERE ts=?", (base,))[0]
        self.assertEqual(h["n"], 60)
        self.assertAlmostEqual(h["cpu_avg"], 40)
        self.assertAlmostEqual(h["net_down_sum"], 100 * 3600)

    def test_profiles(self):
        a = self.s.add_profile("local", "claude", "", "회사")              # 기본 폴더에 이름만
        self.assertEqual(self.s.add_profile("local", "claude", "", "회사2"), a)   # 다시 하면 이름만 바뀜
        b = self.s.add_profile("local", "claude", "C:/x/.claude-p", "개인")
        rows = self.s.profiles("local")
        self.assertEqual([(r["path"], r["label"]) for r in rows], [("", "회사2"), ("C:/x/.claude-p", "개인")])
        self.s.update_profile(b, enabled=False)
        self.assertFalse(self.s.profile(b)["enabled"])
        self.s.delete_profile(b)
        self.assertEqual(len(self.s.profiles()), 1)
        # 자동 찾기: 새 폴더는 이름 없이 등록, 사용자가 지운 폴더는 다시 넣지 않음
        found = {"claude": ["C:/x/.claude-p", "C:/x/.claude-q"]}
        self.assertEqual(self.s.auto_profiles("local", found), 1)
        self.assertEqual(self.s.auto_profiles("local", found), 0)

    def test_net_minute(self):
        base = int(time.time()) // 60 * 60 - 120
        for i, (ok, rtt) in enumerate([(True, 40), (True, 60), (False, None), (True, 200), (True, 20), (True, 20)]):
            self.s.record_net("box", base + i * 10, ok, 5, rtt)
        self.s.record_net("box", base + 60, True, 5, 30)          # 다음 분 → 앞 분 기록
        self.s.flush()
        r = self.s.query("SELECT * FROM net_minute WHERE ts=?", (base,))[0]
        self.assertEqual((r["n"], r["ok"], r["rtt_max"]), (6, 5, 200))
        self.assertAlmostEqual(r["rtt_ms"], 68.0)

    def test_usage_dedupe_additive_rollup_survives_cleanup(self):
        t = int(time.time()) - 3600
        ev = [dict(provider="claude", machine_id="local", ts=t + k, model="m", input=10, output=5, event_key=f"k{k}")
              for k in range(4)]
        self.assertEqual(self.s.add_usage_events(ev), 4)
        self.assertEqual(self.s.add_usage_events(ev), 0)       # 같은 키는 다시 안 들어감
        self.s.rollup()
        self.s.rollup()                                        # 두 번 돌려도 두 번 더해지지 않음
        self.s.set("retention.usage_event_days", 0.00001)
        self.s.cleanup()
        self.s.rollup()
        self.assertEqual(self.s.query("SELECT SUM(input) t FROM usage_day")[0]["t"], 40)
        self.assertEqual(self.s.query("SELECT COUNT(*) c FROM usage_event")[0]["c"], 0)

    def test_backfill_marks(self):
        self.assertIn("claude", self.s.backfill_pending("local", ["claude"]))
        self.s.backfill_done("local", "claude", 3)
        self.assertEqual(self.s.backfill_pending("local", ["claude"]), {})

    def test_session_upsert_keeps_title(self):
        row = {"provider": "claude", "machine_id": "local", "session_id": "s1", "project": "p", "started_at": 100,
               "last_activity": 200, "state": "working", "tokens": 50, "title": "제목", "agents": [{"agent": "a"}]}
        self.s.upsert_sessions([row])
        self.s.upsert_sessions([{**row, "title": None, "agents": None, "state": "ended", "last_activity": 150, "tokens": 40}])
        r = self.s.query("SELECT * FROM session")[0]
        self.assertEqual((r["title"], r["state"], r["last_activity"], r["tokens"]), ("제목", "ended", 200, 50))
        self.assertEqual(json.loads(r["agents"]), [{"agent": "a"}])

    def test_provider_toggle(self):
        self.assertEqual(self.s.enabled_providers(["claude", "codex"]), ["claude", "codex"])
        self.s.set_provider("codex", enabled=False)
        self.assertEqual(self.s.enabled_providers(["claude", "codex"]), ["claude"])


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = time.time() - 60

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude(self):
        rows = [{"type": "assistant", "timestamp": iso(self.now), "sessionId": "s1", "cwd": "C:\\work\\proj",
                 "message": {"id": "msg1", "model": "claude-x",
                             "usage": {"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 30,
                                       "cache_read_input_tokens": 40}}}] * 2   # 같은 메시지 두 번 → 한 번만
        write_lines(self.root / "projects" / "p" / "s1.jsonl", rows)
        u = ClaudeUsage(self.root / "projects")
        snap = u.snapshot()
        self.assertEqual(snap["today"]["total"], 100)
        ev = u.drain_events()
        self.assertEqual((len(ev), ev[0]["key"], ev[0]["project"]), (1, "msg1", "proj"))
        self.assertEqual(u.drain_events(), [])

    def test_account_folders(self):
        from collectors.providers import ClaudeProvider, find_profiles
        usage = {"input_tokens": 5, "output_tokens": 5, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        for folder, n in ((".claude", 1), (".claude-personal", 3)):
            write_lines(self.root / folder / "projects" / "p" / "s.jsonl",
                        [{"type": "assistant", "timestamp": iso(self.now), "sessionId": "s",
                          "message": {"id": f"m{i}", "model": "x", "usage": usage}} for i in range(n)])
        (self.root / ".claude-empty").mkdir()                       # 기록 없는 폴더는 후보 아님
        self.assertEqual(find_profiles(self.root), {"claude": [str(self.root / ".claude-personal")]})
        (self.root / ".claude-personal" / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationUuid": "org-b"}}))
        (self.root / ".claude.json").write_text(json.dumps({"oauthAccount": {"organizationUuid": "org-a"}}))
        a = ClaudeProvider(self.root, cache=False)
        b = ClaudeProvider(self.root, cache=False, root=self.root / ".claude-personal")
        self.assertEqual((a.tick()["usage"]["today"]["total"], b.tick()["usage"]["today"]["total"]), (10, 30))
        self.assertNotEqual(a.account(), b.account())                # 폴더마다 자기 .claude.json

    def test_claude_titles_and_subagents(self):
        sid = "s9"
        usage = {"input_tokens": 1, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        main = self.root / "projects" / "p" / f"{sid}.jsonl"
        write_lines(main, [
            {"type": "agent-name", "agentName": "임시 이름", "sessionId": sid},
            {"type": "custom-title", "customTitle": "메일 서버 이전", "sessionId": sid},
            {"type": "assistant", "timestamp": iso(self.now), "sessionId": sid, "message": {"id": "m1", "model": "x", "usage": usage}}])
        sub = self.root / "projects" / "p" / sid / "subagents" / "agent-a1.jsonl"
        write_lines(sub, [{"type": "assistant", "timestamp": iso(self.now), "sessionId": sid, "isSidechain": True,
                           "agentId": "a1", "message": {"id": "m2", "model": "y", "usage": usage}}])
        (sub.parent / "agent-a1.meta.json").write_text(json.dumps({"description": "로그 형식 조사", "agentType": "general"}),
                                                      encoding="utf-8")
        u = ClaudeUsage(self.root / "projects")
        u.snapshot()
        s = u.session_summaries()[0]
        self.assertEqual((s["title"], s["tokens"], s["model"]), ("메일 서버 이전", 4, "x"))   # 부모 모델은 하위 에이전트 것 제외
        self.assertEqual((s["agents"][0]["agent"], s["agents"][0]["description"], s["agents"][0]["tokens"]),
                         ("a1", "로그 형식 조사", 2))

    def test_codex(self):
        meta = {"type": "session_meta", "timestamp": iso(self.now), "payload": {"id": "c1", "cwd": "/w/app", "creator_account_id": "acct"}}
        ctx = {"type": "turn_context", "timestamp": iso(self.now), "payload": {"model": "gpt-x"}}

        def tc(total, inp, cached, out, used):
            return {"type": "event_msg", "timestamp": iso(self.now), "payload": {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
                                     "reasoning_output_tokens": 1},
                "total_token_usage": {"total_tokens": total}, "model_context_window": 1000},
                "rate_limits": {"primary": {"used_percent": used, "window_minutes": 10080, "resets_at": int(self.now) + 3600},
                                "secondary": None, "plan_type": "pro"}}}
        write_lines(self.root / "sessions" / "2026" / "10" / "29" / "rollout-x.jsonl",
                    [meta, ctx, tc(100, 80, 30, 20, 10.0), tc(100, 80, 30, 20, 10.0), tc(250, 120, 100, 30, 12.0)])
        log = CodexUsage(self.root)
        snap = log.snapshot()
        self.assertEqual(snap["today"]["total"], 250)   # 반복 줄(누적값 같음)은 한 번만
        self.assertTrue(log.account.startswith("codex:"))
        lim = codex_limits(log)
        self.assertEqual((lim["windows"][0]["key"], lim["windows"][0]["used_pct"]), ("seven_day", 12.0))

    def test_gemini_json_and_jsonl_replay(self):
        conv = {"sessionId": "g1", "startTime": iso(self.now), "lastUpdated": iso(self.now), "messages": [
            {"id": "a", "type": "user", "timestamp": iso(self.now)},
            {"id": "b", "type": "gemini", "timestamp": iso(self.now), "model": "gem",
             "tokens": {"input": 100, "output": 10, "cached": 40, "thoughts": 5, "tool": 0, "total": 115}}]}
        p = self.root / "tmp" / "proj" / "chats" / "session-1.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps(conv), encoding="utf-8")
        g = GeminiUsage(self.root / "tmp")
        self.assertEqual(g.snapshot()["today"]["total"], 115)
        replayed = _replay_jsonl("\n".join(json.dumps(x) for x in [
            {"sessionId": "g2"}, {"id": "1", "type": "user"}, {"id": "2", "type": "gemini"}, {"$rewindTo": "1"},
            {"$set": {"summary": "hi"}}]))
        self.assertEqual(([m["id"] for m in replayed["messages"]], replayed["summary"]), (["1"], "hi"))


class PlanApiTest(unittest.TestCase):
    """토큰 갱신이 429면 CLI 대신 갱신으로 넘어가는지 (네트워크·실제 자격 증명 없이)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cred = Path(self.tmp.name) / "cred.json"
        self.cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "old", "refreshToken": "r", "expiresAt": 0}}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_429_falls_back_to_cli(self):
        api = plan_api.PlanAPI(cred_path=self.cred, min_interval=0)

        def refresh(d, o):
            raise RuntimeError("토큰 갱신 요청 제한(429) · 잠시 후 재시도")

        def cli():
            self.cred.write_text(json.dumps({"claudeAiOauth": {"accessToken": "new", "refreshToken": "r2",
                                                               "expiresAt": int((time.time() + 3600) * 1000)}}))
            return True
        api._refresh, api._cli_renew = refresh, cli
        d, o = api._load()
        self.assertEqual(api._refresh_or_cli(d, o)["accessToken"], "new")

    def test_cli_failure_keeps_error(self):
        api = plan_api.PlanAPI(cred_path=self.cred, min_interval=0)
        api._refresh = lambda d, o: (_ for _ in ()).throw(RuntimeError("토큰 갱신 요청 제한(429)"))
        api._cli_renew = lambda: False
        d, o = api._load()
        with self.assertRaises(RuntimeError):
            api._refresh_or_cli(d, o)

    def test_manual_refresh_gap(self):
        api = plan_api.PlanAPI(cred_path=self.cred, min_interval=0)
        api.snapshot = lambda: None
        api.force()
        self.assertIn("초 뒤", api.force()["message"])   # 1분 안에 다시 누르면 막힘


class AlertsTest(unittest.TestCase):
    def setUp(self):
        from alerts import Alerts
        self.sent = []
        self.a = Alerts(send=lambda t, m: self.sent.append(t), enabled=lambda k: True)
        self.names = {"local": "이 PC", "box": "box"}.get

    def ai(self, state, ctx=None, used=50):
        sess = {"session": "s1", "name": "메일 이전", "project": "p", "state": state, "context": ctx, "model": "claude-opus-5-5"}
        return {"claude": {"label": "Claude Code", "states": {"box": {"sessions": [sess]}},
                           "limits": {"windows": [{"key": "seven_day", "used_pct": used, "resets_at": 1, "reset_in_sec": 60}]}}}

    def test_session_done_only_after_long_work(self):
        self.a.check(self.ai("working"), [], {}, {}, self.names)
        self.a.work_since[("claude", "box", "s1")] -= 120      # 2분 작업한 것으로
        self.a.check(self.ai("idle"), [], {}, {}, self.names)
        self.assertEqual(self.sent, ["작업 끝 · 메일 이전"])

    def test_limit_and_context_once(self):
        for _ in range(3):
            self.a.check(self.ai("idle", ctx=950_000, used=93), [], {}, {}, self.names)
        self.assertEqual(sorted(self.sent), ["Claude Code 한도 7% 남음", "컨텍스트 95% · 메일 이전"])

    def test_waiting_after_15s_and_not_done(self):
        self.a.check(self.ai("working"), [], {}, {}, self.names)
        self.a.work_since[("claude", "box", "s1")] -= 120
        self.a.check(self.ai("waiting"), [], {}, {}, self.names)
        self.assertEqual(self.sent, [])                         # 기다린 지 15초 전
        self.a.wait_since[("claude", "box", "s1")] -= 20
        self.a.check(self.ai("waiting"), [], {}, {}, self.names)
        self.a.check(self.ai("waiting"), [], {}, {}, self.names)
        self.assertEqual(self.sent, ["입력 대기 · 메일 이전"])   # 한 번만, '작업 끝'은 아님

    def test_limit_reset_once(self):
        now = time.time()
        def lim(resets, used):
            return {"claude": {"label": "Claude Code", "states": {},
                               "limits": {"windows": [{"key": "five_hour", "used_pct": used, "resets_at": resets}]}}}
        self.a.check(lim(now - 5, 80), [], {}, {}, self.names)       # 리셋 시각이 막 지남 (아직 새 값 전)
        self.a.check(lim(now + 18000, 2), [], {}, {}, self.names)    # 새 창
        self.a.check(lim(now + 18000, 3), [], {}, {}, self.names)
        self.assertEqual(self.sent, ["Claude Code 5시간 한도 리셋"])

    def test_disabled_kind_not_sent(self):
        self.a.enabled = lambda k: k != "limits"
        self.a.check(self.ai("idle", used=95), [], {}, {}, self.names)
        self.assertEqual(self.sent, [])


class MachinesTest(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(normalize_target("me@box:2222"), "ssh://me@box:2222")
        self.assertEqual(normalize_target("box"), "box")
        for bad in ("-oProxyCommand=x", "a b", "x;rm", ""):
            with self.assertRaises(ValueError):
                normalize_target(bad)
        with self.assertRaises(ValueError):
            validate_distro("bad name")

    def test_framed(self):
        b = "한글".encode()
        self.assertEqual(framed(b), str(len(b)).encode() + b"\n" + b)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    unittest.main()
