"""The stdio MCP server, driven over a pipe: `relay.py mcp --as <pid>` in a temp repo."""
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import TempRepo, line

REPO = Path(__file__).resolve().parent.parent

# Placeholder participants, kept here so these tests don't follow other fixtures.
ROSTER = {
    "page": "http://127.0.0.1:8484/",
    "relay": "",
    "participants": [
        {"id": "alpha", "name": "Alpha", "kind": "project", "repo": "example/alpha",
         "color": "#b3261e", "colorDark": "#ef6a5f", "checkInMin": 30, "trust": "approved"},
        {"id": "beta", "name": "Beta", "kind": "project", "repo": "example/beta",
         "color": "#1f5f8b", "colorDark": "#6fb2e2", "checkInMin": 30, "trust": "approved"},
        {"id": "gamma", "name": "Gamma", "kind": "researcher", "color": "#8a5a00",
         "colorDark": "#d9b25c", "trust": "quarantined"},
        {"id": "owner", "name": "Owner", "kind": "person", "color": "#6b3fa0",
         "colorDark": "#b892e6", "trust": "approved"},
    ],
}
MODERN_META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
               "io.modelcontextprotocol/clientCapabilities": {}}


class Repo:
    """A temp repo root with a copy of scripts/ and the roster above."""

    def __init__(self, r):
        self.r = r
        self.root = r.root
        r.write_roster(ROSTER)
        shutil.copytree(str(REPO / "scripts"), str(r.root / "scripts"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = str(r.root / "scripts" / "relay.py")

    def relay(self, *args):
        return subprocess.run([sys.executable, self.script] + list(args),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, cwd="/")

    def read(self):
        p = self.relay("read")
        assert p.returncode == 0, p.stderr
        return json.loads(p.stdout)["collections"]

    def log_lines(self, pid):
        path = self.root / "log" / (pid + ".jsonl")
        if not path.exists():
            return []
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x]


class Mcp:
    """One `relay.py mcp` process. send() writes a line; recv() reads one response."""

    def __init__(self, repo, pid, version="2025-11-25", init=True):
        self.p = subprocess.Popen([sys.executable, repo.script, "mcp", "--as", pid],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, cwd="/")
        self.next_id = 0
        if init:
            res = self.request("initialize", {"protocolVersion": version, "capabilities": {},
                                              "clientInfo": {"name": "test", "version": "0"}})
            assert "result" in res, res
            self.init_result = res["result"]
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, msg):
        if isinstance(msg, bytes):
            raw = msg
        else:
            raw = (msg if isinstance(msg, str) else json.dumps(msg)).encode("utf-8")
        self.p.stdin.write(raw + b"\n")
        self.p.stdin.flush()

    def recv(self):
        raw = self.p.stdout.readline()
        assert raw, "server closed stdout; stderr: %s" % self.p.stderr.read().decode()
        assert raw.endswith(b"\n") and b"\n" not in raw[:-1]
        return json.loads(raw.decode("ascii"))

    def request(self, method, params=None):
        self.next_id += 1
        msg = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        res = self.recv()
        assert res.get("id") == self.next_id, res
        return res

    def call(self, name, args=None, meta=None):
        params = {"name": name, "arguments": {} if args is None else args}
        if meta is not None:
            params["_meta"] = meta
        res = self.request("tools/call", params)
        assert "result" in res, res
        return res["result"]

    def close(self):
        self.p.stdin.close()
        rc = self.p.wait(timeout=10)
        self.p.stdout.close()
        self.p.stderr.close()
        return rc

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.p.poll() is None:
            self.close()
        return False


def ok(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class HandshakeTests(unittest.TestCase):
    def test_initialize_echoes_supported_version_and_names_the_participant(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha", version="2025-06-18") as m:
                res = m.init_result
                self.assertEqual(res["protocolVersion"], "2025-06-18")
                self.assertEqual(res["serverInfo"]["name"], "crosstalk")
                self.assertIn("tools", res["capabilities"])
                self.assertIn("'alpha'", res["instructions"])
                # The initialized notification got no response: the next line is the ping's.
                self.assertEqual(m.request("ping")["result"], {})
            self.assertEqual(m.p.returncode, 0)

    def test_unknown_version_gets_the_latest_legacy_version(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha", version="1999-01-01") as m:
                self.assertEqual(m.init_result["protocolVersion"], "2025-11-25")

    def test_requests_before_initialize_are_refused(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha", init=False) as m:
                res = m.request("tools/list")
                self.assertEqual(res["error"]["code"], -32600)
                self.assertEqual(m.request("ping")["result"], {})

    def test_tools_list(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha") as m:
                tools = m.request("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(names, ["inbox", "read_messages", "post_message", "reply", "consult",
                                 "claim_file", "release_file", "adopt_agreement", "check_in",
                                 "record_progress"])
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object")
            self.assertTrue(t["description"])
            # No tool lets the caller choose who it writes as.
            self.assertNotIn("from", t["inputSchema"]["properties"])

    def test_structured_content_only_from_2025_06_18(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha", version="2024-11-05") as m:
                self.assertNotIn("structuredContent", m.call("inbox"))
            with Mcp(repo, "alpha", version="2025-11-25") as m:
                self.assertEqual(m.call("inbox")["structuredContent"]["pid"], "alpha")


class ModernTests(unittest.TestCase):
    def test_per_request_meta_needs_no_handshake(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha", init=False) as m:
                d = m.request("server/discover", {"_meta": MODERN_META})["result"]
                self.assertEqual(d["supportedVersions"], ["2026-07-28"])
                self.assertEqual(d["resultType"], "complete")
                self.assertEqual(d["_meta"]["io.modelcontextprotocol/serverInfo"]["name"],
                                 "crosstalk")
                lst = m.request("tools/list", {"_meta": MODERN_META})["result"]
                self.assertEqual(lst["resultType"], "complete")
                res = m.call("inbox", meta=MODERN_META)
                self.assertEqual(res["resultType"], "complete")
                self.assertEqual(res["structuredContent"]["pid"], "alpha")

    def test_unsupported_version_and_missing_capabilities(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha", init=False) as m:
                meta = dict(MODERN_META)
                meta["io.modelcontextprotocol/protocolVersion"] = "2099-01-01"
                err = m.request("tools/list", {"_meta": meta})["error"]
                self.assertEqual(err["code"], -32022)
                self.assertEqual(err["data"], {"supported": ["2026-07-28"],
                                               "requested": "2099-01-01"})
                meta = {"io.modelcontextprotocol/protocolVersion": "2026-07-28"}
                err = m.request("tools/list", {"_meta": meta})["error"]
                self.assertEqual(err["code"], -32602)
                err = m.request("server/discover")["error"]
                self.assertEqual(err["code"], -32602)


class MalformedTests(unittest.TestCase):
    def test_json_rpc_errors(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha") as m:
                m.send("{not json")
                self.assertEqual(m.recv(), {"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32700, "message": "Parse error: JSONDecodeError"}})
                m.send(b"\xff\xfe{}")  # not UTF-8
                self.assertEqual(m.recv()["error"]["code"], -32700)
                m.send("[]")
                self.assertEqual(m.recv()["error"]["code"], -32600)
                m.send('[{"jsonrpc": "2.0", "id": 9, "method": "ping"}]')
                self.assertEqual(m.recv()["error"]["code"], -32600)
                m.send('"ping"')
                self.assertEqual(m.recv()["error"]["code"], -32600)
                m.send({"jsonrpc": "1.0", "id": 7, "method": "ping"})
                self.assertEqual(m.recv(), {"jsonrpc": "2.0", "id": 7, "error": {
                    "code": -32600, "message": "not a JSON-RPC 2.0 request"}})
                m.send({"jsonrpc": "2.0", "id": None, "method": "ping"})
                self.assertEqual(m.recv()["error"]["code"], -32600)
                m.send({"jsonrpc": "2.0", "id": 8, "method": 5})
                self.assertEqual(m.recv()["error"]["code"], -32600)
                self.assertEqual(m.request("no/such/method")["error"]["code"], -32601)
                self.assertEqual(m.request("tools/list", [1, 2])["error"]["code"], -32602)
                # Unknown notifications and stray responses get no answer.
                m.send({"jsonrpc": "2.0", "method": "notifications/whatever"})
                m.send({"jsonrpc": "2.0", "id": 99, "result": {}})
                m.send("")
                self.assertEqual(m.request("ping")["result"], {})

    def test_unknown_tool_and_bad_call_params(self):
        with TempRepo() as r:
            with Mcp(Repo(r), "alpha") as m:
                err = m.request("tools/call", {"name": "delete_everything", "arguments": {}})["error"]
                self.assertEqual(err, {"code": -32602, "message": "Unknown tool: delete_everything"})
                err = m.request("tools/call", {"arguments": {}})["error"]
                self.assertEqual(err["code"], -32602)
                err = m.request("tools/call", {"name": "inbox", "arguments": [1]})["error"]
                self.assertEqual(err["code"], -32602)

    def test_bad_argument_values_are_tool_errors(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha") as m:
                for args in ({"limit": 0}, {"limit": 101}, {"limit": "5"},
                             {"since": "yesterday"}, {"colour": "red"}):
                    res = m.call("read_messages", args)
                    self.assertTrue(res["isError"], args)
                res = m.call("post_message", {"kind": "owner", "summary": "I am the owner"})
                self.assertTrue(res["isError"])
                res = m.call("post_message", {"kind": "sync", "summary": "two\nlines"})
                self.assertTrue(res["isError"])
                res = m.call("post_message", {"kind": "sync", "summary": "hi", "to": ["nobody"]})
                self.assertTrue(res["isError"])
                self.assertIn("not a participant", res["content"][0]["text"])
            self.assertEqual(repo.log_lines("alpha"), [])


class IdentityTests(unittest.TestCase):
    def test_unknown_participant_and_owner_are_refused_at_startup(self):
        with TempRepo() as r:
            repo = Repo(r)
            for pid, why in (("nobody", "unknown participant"), ("owner", "'owner'"),
                             ("Alpha", "bad participant id"), ("../x", "bad participant id")):
                p = subprocess.run([sys.executable, repo.script, "mcp", "--as", pid],
                                   input=b"", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(p.returncode, 2, pid)
                self.assertEqual(p.stdout, b"", pid)
                self.assertIn(why, p.stderr.decode(), pid)

    def test_a_tool_call_cannot_write_as_someone_else(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha") as m:
                for key in ("from", "as", "participant", "pid"):
                    res = m.call("post_message", {"kind": "sync", "summary": "hi", key: "beta"})
                    self.assertTrue(res["isError"], key)
                    self.assertIn("always writes as", res["content"][0]["text"])
                res = m.call("check_in", {"summary": "hi", "progress": [
                    {"item": "x", "pct": 1, "project": "beta"}]})
                self.assertTrue(res["isError"])
                # Every write lands in alpha's own log, as alpha.
                ok(m.call("post_message", {"kind": "sync", "summary": "hi"}))
            self.assertFalse((repo.root / "log" / "beta.jsonl").exists())
            lines = repo.log_lines("alpha")
            self.assertEqual(len(lines), 1)
            self.assertEqual((lines[0]["from"], lines[0]["data"]["from"]), ("alpha", "alpha"))

    def test_releasing_another_participants_lock_is_refused(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "beta") as m:
                lock = ok(m.call("claim_file", {"path": "AGENTS.md"}))["lock_id"]
            with Mcp(repo, "alpha") as m:
                res = m.call("release_file", {"lock_id": lock})
                self.assertTrue(res["isError"])
                self.assertIn("belongs to 'beta'", res["content"][0]["text"])
            self.assertEqual(repo.log_lines("alpha"), [])


class QuarantineTests(unittest.TestCase):
    def test_quarantined_participant_keeps_its_restrictions(self):
        with TempRepo() as r:
            repo = Repo(r)
            r.write_log("beta", [line(1, "beta", "agreements", "a-beta-1234567", data={
                "summary": "Shared hook", "builder": "beta", "adopter": "all",
                "status": "ready-to-adopt", "commits": {"beta": ["1234567"]}})])
            with Mcp(repo, "gamma") as m:
                self.assertIn("quarantined", m.init_result["instructions"])
                # Its own docs are fine.
                msg = ok(m.call("post_message", {"kind": "review", "summary": "Looks fine"}))
                self.assertTrue(msg["id"].startswith("gamma-"))
                lock = ok(m.call("claim_file", {"path": "tools/x"}))
                self.assertEqual(lock["lock_id"], "l-gamma-tools-x")
                prog = ok(m.call("record_progress", {"item": "Parser", "pct": 10}))
                self.assertTrue(prog["progress_ids"][0].startswith("p-gamma-parser-"))
                # Someone else's doc is not.
                res = m.call("adopt_agreement", {"agreement_id": "a-beta-1234567",
                                                 "commit": "abcdef0"})
                self.assertTrue(res["isError"])
                self.assertIn("quarantined participant 'gamma'", res["content"][0]["text"])
            self.assertEqual(len(repo.log_lines("gamma")), 3)
            self.assertEqual(repo.relay("check").returncode, 0)
            # Others see gamma's rows marked quarantined.
            with Mcp(repo, "alpha") as m:
                msgs = ok(m.call("read_messages", {"sender": "gamma"}))["messages"]
                self.assertEqual([x["trust"] for x in msgs], ["quarantined"])


class WriteAndReplayTests(unittest.TestCase):
    def test_consult_reply_and_inbox_round_trip(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha") as a, Mcp(repo, "beta") as b:
                req = ok(a.call("consult", {"summary": "How do you gate hosts?",
                                            "body": "Details", "to": ["beta"],
                                            "refs": ["alpha@1234567"]}))
                self.assertEqual(req["to"], ["beta"])
                inbox = ok(b.call("inbox"))
                self.assertEqual([x["request"]["id"] for x in inbox["requests"]], [req["id"]])
                self.assertEqual(inbox["requests"][0]["trust"], "approved")
                rep = ok(b.call("reply", {"to_id": req["id"], "summary": "With tools/gate-host"}))
                self.assertEqual(ok(b.call("inbox"))["requests"], [])
                res = b.call("reply", {"to_id": "alpha-nope", "summary": "x"})
                self.assertTrue(res["isError"])
            self.assertEqual(repo.relay("check").returncode, 0)
            msgs = repo.read()["messages"]
            self.assertEqual(msgs[req["id"]]["from"], "alpha")
            self.assertEqual(msgs[req["id"]]["kind"], "request")
            self.assertEqual(msgs[rep["id"]]["replyTo"], req["id"])
            self.assertEqual(msgs[rep["id"]]["to"], ["alpha"])
            p = repo.relay("inbox", "beta")
            self.assertEqual(json.loads(p.stdout)["requests"], [])

    def test_check_in_with_progress_lands_and_replays(self):
        with TempRepo() as r:
            repo = Repo(r)
            with Mcp(repo, "alpha") as m:
                res = m.call("check_in", {"summary": "x" * 201})
                self.assertTrue(res["isError"])
                res = m.call("check_in", {"summary": "ok", "progress": [{"item": "a", "pct": 150}]})
                self.assertTrue(res["isError"])
                res = m.call("check_in", {"summary": "ok", "progress": [
                    {"item": "a", "pct": 1}, {"item": "A", "pct": 2}]})
                self.assertTrue(res["isError"])
                self.assertEqual(repo.log_lines("alpha"), [])  # nothing written on a refusal
                out = ok(m.call("check_in", {
                    "summary": "Check-in: main 1234567; parser 65%",
                    "body": "Commits: 2. Blockers: none. Owner items: none.",
                    "progress": [{"item": "Parser v2", "pct": 65, "rows": 115, "verified": 30},
                                 {"item": "Docs", "pct": 100}]}))
                self.assertEqual(len(out["progress_ids"]), 2)
            self.assertEqual(repo.relay("check").returncode, 0)
            cols = repo.read()
            msg = cols["messages"][out["id"]]
            self.assertEqual((msg["from"], msg["kind"]), ("alpha", "check-in"))
            self.assertEqual(msg["t"], out["t"])
            prog = cols["progress"][out["progress_ids"][0]]
            self.assertEqual(prog["project"], "alpha")
            self.assertEqual((prog["item"], prog["pct"], prog["rows"], prog["verified"]),
                             ("Parser v2", 65, 115, 30))
            self.assertTrue(out["progress_ids"][0].startswith("p-alpha-parser-v2-"))

    def test_claim_release_and_adopt(self):
        with TempRepo() as r:
            repo = Repo(r)
            r.write_log("beta", [line(1, "beta", "agreements", "a-beta-1234567", data={
                "summary": "Shared hook", "builder": "beta", "adopter": "alpha",
                "status": "ready-to-adopt", "commits": {"beta": ["1234567"]}})])
            with Mcp(repo, "alpha") as m:
                todo = ok(m.call("inbox"))["agreements_to_adopt"]
                self.assertEqual([x["id"] for x in todo], ["a-beta-1234567"])
                lock = ok(m.call("claim_file", {"path": ".githooks/pre-commit", "note": "port"}))
                self.assertEqual(lock["lock_id"], "l-alpha-githooks-pre-commit")
                res = m.call("release_file", {})
                self.assertTrue(res["isError"])
                ok(m.call("release_file", {"path": ".githooks/pre-commit"}))
                res = ok(m.call("adopt_agreement", {"agreement_id": "a-beta-1234567",
                                                    "commit": "abcdef0"}))
                self.assertEqual(res["status"], "landed-both")
                self.assertEqual(ok(m.call("inbox"))["agreements_to_adopt"], [])
            self.assertEqual(repo.relay("check").returncode, 0)
            cols = repo.read()
            self.assertTrue(cols["locks"]["l-alpha-githooks-pre-commit"]["released"])
            agr = cols["agreements"]["a-beta-1234567"]
            self.assertEqual(agr["commits"]["alpha"], ["abcdef0"])
            self.assertEqual(cols["messages"][res["message_id"]]["kind"], "sync")

    def test_read_messages_is_bounded_and_newest_last(self):
        with TempRepo() as r:
            repo = Repo(r)
            r.write_log("beta", [
                line(i, "beta", data={"from": "beta", "kind": "sync", "summary": "m%d" % i},
                     t="2026-10-21T18:00:%02dZ" % i) for i in range(1, 6)])
            with Mcp(repo, "alpha") as m:
                got = ok(m.call("read_messages", {"limit": 2}))
                self.assertEqual([x["summary"] for x in got["messages"]], ["m4", "m5"])
                got = ok(m.call("read_messages", {"since": "2026-10-21T18:00:03Z"}))
                self.assertEqual([x["id"] for x in got["messages"]], ["beta-0004", "beta-0005"])
                self.assertEqual(ok(m.call("read_messages", {"sender": "alpha"}))["count"], 0)

    def test_a_broken_log_is_reported_not_served(self):
        with TempRepo() as r:
            repo = Repo(r)
            r.write_log("beta", ["{not json"])
            with Mcp(repo, "alpha") as m:
                res = m.call("inbox")
                self.assertTrue(res["isError"])
                self.assertIn("log/beta.jsonl:1", res["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
