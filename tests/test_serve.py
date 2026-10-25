import json
import os
import select
import shutil
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from helpers import SPEC_ROSTER, RunningServer, TempRepo, line

from crosstalk.logs import append_line, check_all
from crosstalk.participants import load_roster
from crosstalk.serve import repo_commit
from crosstalk.tokens import token_hash


def request(url, method="GET", body=None, headers=None):
    """(status, decoded JSON body) for one request against the served API."""
    status, payload, _headers = request_with_headers(url, method, body, headers)
    return status, payload


def request_with_headers(url, method="GET", body=None, headers=None):
    """(status, decoded JSON body, response headers) for one request."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, (json.loads(raw) if raw else None), dict(e.headers)


class SnapshotTests(unittest.TestCase):
    def test_snapshot_shape(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/snapshot")
            self.assertEqual(status, 200)
            self.assertRegex(body["server_time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            self.assertIsNone(body["commit"])  # a temp repo is no checkout
            self.assertIn("codex", body["roster"])
            self.assertEqual(body["roster"]["codex"]["name"], "Codex")
            self.assertEqual(body["roster"]["codex"]["trust"], "approved")
            self.assertEqual(sorted(body["collections"]), ["messages"])
            self.assertEqual(body["collections"]["messages"]["codex-0001"]["summary"], "hi")

    def test_snapshot_replays_every_collection(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", collection="messages", id_="codex-0001",
                                       data={"kind": "reply", "summary": "hi"}),
                                  line(2, "codex", collection="lessons", id_="codex-l1",
                                       data={"summary": "note"})])
            with RunningServer(r.root) as srv:
                _, body = request(srv.url + "/api/snapshot")
            self.assertEqual(sorted(body["collections"]), ["lessons", "messages"])
            self.assertEqual(body["collections"]["lessons"]["codex-l1"]["summary"], "note")

    def test_collection_endpoint_returns_docs(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/messages")
            self.assertEqual(status, 200)
            self.assertEqual(sorted(body["docs"]), ["codex-0001"])
            self.assertEqual(body["docs"]["codex-0001"]["summary"], "hi")

    def test_known_collection_without_docs_is_empty(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/messages")
            self.assertEqual(status, 200)
            self.assertEqual(body, {"docs": {}})

    def test_unknown_collection_is_404_json(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/nope")
            self.assertEqual(status, 404)
            self.assertIn("error", body)

    def test_unknown_path_is_404_json(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/nothing/here")
            self.assertEqual(status, 404)
            self.assertIn("error", body)

    def test_index_is_served_from_the_checkout(self):
        with TempRepo() as r:
            r.write_roster()
            (r.root / "index.html").write_text("<!doctype html><title>crosstalk</title>",
                                               encoding="utf-8")
            with RunningServer(r.root) as srv:
                with urllib.request.urlopen(srv.url + "/", timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn("crosstalk", resp.read().decode("utf-8"))


class ReplayCacheTests(unittest.TestCase):
    def test_direct_append_shows_up_without_a_restart(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                _, first = request(srv.url + "/api/snapshot")
                self.assertEqual(sorted(first["collections"]["messages"]), ["codex-0001"])
                # A local session appends straight to the log file.
                r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"}),
                                      line(2, "codex", data={"kind": "reply", "summary": "new"})])
                _, second = request(srv.url + "/api/snapshot")
            self.assertEqual(sorted(second["collections"]["messages"]), ["codex-0001", "codex-0002"])
            self.assertEqual(second["collections"]["messages"]["codex-0002"]["summary"], "new")

    def test_collection_endpoint_sees_the_same_cache(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/messages")
                self.assertEqual(body, {"docs": {}})
                r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
                status, body = request(srv.url + "/api/messages")
            self.assertEqual(status, 200)
            self.assertEqual(sorted(body["docs"]), ["codex-0001"])


class RepoCommitTests(unittest.TestCase):
    def test_no_checkout_is_null(self):
        with TempRepo() as r:
            self.assertIsNone(repo_commit(r.root))

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_the_checkout_short_hash(self):
        with TempRepo() as r:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "init", "-q", str(r.root)], check=True, env=env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "one"],
                           cwd=str(r.root), check=True, env=env)
            commit = repo_commit(r.root)
            self.assertRegex(commit, r"^[0-9a-f]{7,40}$")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_the_snapshot_carries_the_commit(self):
        with TempRepo() as r:
            r.write_roster()
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "init", "-q", str(r.root)], check=True, env=env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "one"],
                           cwd=str(r.root), check=True, env=env)
            with RunningServer(r.root) as srv:
                _, body = request(srv.url + "/api/snapshot")
            self.assertRegex(body["commit"], r"^[0-9a-f]{7,40}$")


class AppendTests(unittest.TestCase):
    """POST /api/append: one bearer token, one line in the log."""

    TOKEN = "the-codex-token"

    def server(self, r, pid="codex", token=None):
        return RunningServer(r.root, tokens={pid: token_hash(token or self.TOKEN)})

    def append(self, srv, body, token=None):
        headers = {} if token is None else {"Authorization": "Bearer " + token}
        return request(srv.url + "/api/append", "POST", body, headers)

    def message(self, **over):
        body = {"from": "codex", "op": "set", "collection": "messages", "id": "codex-0001",
                "data": {"kind": "reply", "summary": "hi"}}
        body.update(over)
        return body

    def test_a_token_append_is_written_to_the_log(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(), self.TOKEN)
            self.assertEqual(status, 201)
            self.assertEqual(body["seq"], 1)
            self.assertEqual(body["from"], "codex")
            self.assertEqual(body["op"], "set")
            self.assertEqual(body["collection"], "messages")
            self.assertEqual(body["id"], "codex-0001")
            self.assertEqual(body["data"], {"kind": "reply", "summary": "hi"})
            raw = (r.root / "log" / "codex.jsonl").read_text(encoding="utf-8")
            written = json.loads(raw.strip())
            self.assertEqual(written["seq"], 1)
            self.assertEqual(written["data"]["summary"], "hi")

    def test_the_server_stamps_the_lines_time(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(), self.TOKEN)
            self.assertEqual(status, 201)
            self.assertRegex(body["t"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
            written = json.loads((r.root / "log" / "codex.jsonl").read_text(encoding="utf-8").strip())
            self.assertEqual(written["t"], body["t"])

    def test_a_client_supplied_line_time_is_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(t="2020-01-01T00:00:00Z"), self.TOKEN)
            self.assertEqual(status, 400)
            self.assertIn("t", json.dumps(body).lower())
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_no_token_is_401(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message())
            self.assertEqual(status, 401)
            self.assertIn("error", body)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_an_unknown_token_is_401(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(), "not-a-token")
            self.assertEqual(status, 401)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_a_token_of_another_participant_is_403(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(**{"from": "vectorcraft"}), self.TOKEN)
            self.assertEqual(status, 403)
            self.assertFalse((r.root / "log" / "vectorcraft.jsonl").exists())

    def test_a_bad_message_shape_is_400(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(data={"summary": "no kind"}), self.TOKEN)
            self.assertEqual(status, 400)
            self.assertIn("kind", json.dumps(body))
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_the_owner_only_collection_is_403(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(collection="participants",
                                                             id="vectorcraft",
                                                             data={"trust": "approved"}), self.TOKEN)
            self.assertEqual(status, 403)
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_a_quarantined_participant_is_held_to_its_own_prefix(self):
        with TempRepo() as r:
            roster = json.loads(json.dumps(SPEC_ROSTER))
            for entry in roster["participants"]:
                if entry["id"] == "codex":
                    entry["trust"] = "quarantined"
            r.write_roster(roster)
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(id="m-0001"), self.TOKEN)
            self.assertEqual(status, 400)
            self.assertIn("codex-", json.dumps(body))
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())

    def test_a_server_append_and_a_local_append_keep_the_seqs_unique(self):
        with TempRepo() as r:
            r.write_roster()
            ro = load_roster(r.root / "participants.json")
            statuses = []
            with self.server(r) as srv:
                def local():
                    append_line(r.root, "codex", "set", "messages", "codex-0002",
                                {"kind": "reply", "summary": "local"}, roster=ro)

                def remote():
                    statuses.append(self.append(srv, self.message(), self.TOKEN)[0])

                threads = [threading.Thread(target=local), threading.Thread(target=remote)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            self.assertEqual(statuses, [201])
            self.assertEqual(check_all(r.root, ro), [])
            lines = [json.loads(x) for x in
                     (r.root / "log" / "codex.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(sorted(x["seq"] for x in lines), [1, 2])
            self.assertEqual(sorted(x["id"] for x in lines), ["codex-0001", "codex-0002"])

    def test_the_token_never_appears_in_a_response_or_the_log(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.append(srv, self.message(), self.TOKEN)
                self.assertNotIn(self.TOKEN, json.dumps(body))
                status, body = self.append(srv, self.message(), "not-a-token")
            self.assertNotIn(self.TOKEN, json.dumps(body))
            raw = (r.root / "log" / "codex.jsonl").read_text(encoding="utf-8")
            self.assertNotIn(self.TOKEN, raw)

    def test_a_malformed_id_is_400_not_a_crash(self):
        # A quarantined (or unknown) participant posting a non-string id used to
        # crash the request thread in _trust_problem (id.startswith on a null).
        with TempRepo() as r:
            roster = json.loads(json.dumps(SPEC_ROSTER))
            for entry in roster["participants"]:
                if entry["id"] == "codex":
                    entry["trust"] = "quarantined"
            r.write_roster(roster)
            with self.server(r) as srv:
                for bad in (None, 5, [], {}):
                    status, body = self.append(srv, self.message(id=bad), self.TOKEN)
                    self.assertEqual(status, 400, (bad, body))
                    self.assertIn("id", json.dumps(body))
            self.assertFalse((r.root / "log" / "codex.jsonl").exists())


class OwnerTests(unittest.TestCase):
    """The owner on the page: a PIN or a token, then a session cookie."""

    OWNER_TOKEN = "the-owner-token"

    def server(self, r, **kwargs):
        kwargs.setdefault("tokens", {"owner": token_hash(self.OWNER_TOKEN)})
        return RunningServer(r.root, **kwargs)

    def login(self, srv, payload):
        status, body, headers = request_with_headers(srv.url + "/api/login", "POST", payload)
        cookie = headers.get("Set-Cookie", "")
        return status, body, cookie

    def cookie_of(self, cookie):
        return cookie.split(";")[0]

    def test_login_with_the_startup_pin(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                self.assertRegex(srv.pin, r"^\d{6}$")
                status, body, cookie = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 200)
                self.assertIn("HttpOnly", cookie)
                self.assertIn("SameSite=Strict", cookie)
                self.assertNotIn(srv.pin, cookie)
                status, me = request(srv.url + "/api/me", headers={"Cookie": self.cookie_of(cookie)})
            self.assertEqual(status, 200)
            self.assertEqual(me, {"name": "Owner", "isOwner": True, "canEdit": True,
                                  "viewer": "owner"})

    def test_login_with_the_owner_token(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body, cookie = self.login(srv, {"token": self.OWNER_TOKEN})
                self.assertEqual(status, 200)
                self.assertNotIn(self.OWNER_TOKEN, cookie)
                status, me = request(srv.url + "/api/me", headers={"Cookie": self.cookie_of(cookie)})
            self.assertEqual(status, 200)
            self.assertTrue(me["isOwner"])

    def test_a_wrong_pin_or_token_is_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body, cookie = self.login(srv, {"pin": "000000"})
                self.assertEqual(status, 401)
                self.assertEqual(cookie, "")
                status, body, cookie = self.login(srv, {"token": "not-the-owner-token"})
                self.assertEqual(status, 401)
                self.assertEqual(cookie, "")

    def test_login_without_a_pin_or_token_is_400(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body, cookie = self.login(srv, {})
                self.assertEqual(status, 400)
                self.assertEqual(cookie, "")

    def test_no_pin_means_no_pin_login(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r, no_pin=True) as srv:
                self.assertIsNone(srv.pin)
                status, body, cookie = self.login(srv, {"pin": "123456"})
                self.assertEqual(status, 401)

    def test_the_pin_expires(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                srv.state.pin_expires = 0.0  # the window has passed
                status, body, cookie = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 401)

    def test_the_pin_is_one_time_only(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 200)
                status, _body, cookie2 = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 401)

    def test_without_a_cookie_the_viewer_is_read_only(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, me = request(srv.url + "/api/me")
            self.assertEqual(status, 200)
            self.assertEqual(me, {"name": None, "isOwner": False, "canEdit": False,
                                  "viewer": "viewer"})

    def test_a_stale_cookie_is_just_a_viewer(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, me = request(srv.url + "/api/me",
                                     headers={"Cookie": "crosstalk_session=nope"})
            self.assertEqual(status, 200)
            self.assertEqual(me["viewer"], "viewer")
            self.assertFalse(me["isOwner"])

    def test_the_owner_may_write_as_owner(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "messages",
                                        "id": "own-20261024-1200-abcd",
                                        "data": {"kind": "owner", "summary": "a note"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 201)
                self.assertEqual(body["from"], "owner")
                self.assertEqual(body["via"], "page")
            self.assertTrue((r.root / "log" / "owner.jsonl").is_file())

    def test_a_non_owner_may_not_write_as_owner(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root, tokens={"codex": token_hash("codex-token")}) as srv:
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "messages",
                                        "id": "own-20261024-1200-abcd",
                                        "data": {"kind": "owner", "summary": "a note"}},
                                       {"Authorization": "Bearer codex-token"})
                self.assertEqual(status, 403)
                self.assertFalse((r.root / "log" / "owner.jsonl").exists())

    def test_a_viewer_may_not_write(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "messages",
                                        "id": "own-20261024-1200-abcd",
                                        "data": {"kind": "owner", "summary": "a note"}})
                self.assertEqual(status, 403)
                self.assertFalse((r.root / "log" / "owner.jsonl").exists())

    def test_the_approve_flow_updates_the_doc_the_roster_and_the_log(self):
        with TempRepo() as r:
            roster = json.loads(json.dumps(SPEC_ROSTER))
            for entry in roster["participants"]:
                if entry["id"] == "codex":
                    entry["trust"] = "quarantined"
            r.write_roster(roster)
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "participants",
                                        "id": "codex", "data": {"trust": "approved"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 201)
                status, snap = request(srv.url + "/api/snapshot")
            # The roster of record.
            on_disk = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
            entry = [p for p in on_disk["participants"] if p["id"] == "codex"][0]
            self.assertEqual(entry["trust"], "approved")
            # The replayed doc, and the roster the snapshot serves.
            self.assertEqual(snap["collections"]["participants"]["codex"]["trust"], "approved")
            self.assertEqual(snap["roster"]["codex"]["trust"], "approved")
            # The log line.
            lines = [json.loads(x) for x in
                     (r.root / "log" / "owner.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(lines[0]["collection"], "participants")
            self.assertEqual(lines[0]["id"], "codex")
            self.assertEqual(lines[0]["data"]["trust"], "approved")
            self.assertEqual(lines[0]["from"], "owner")

    def test_the_owner_may_write_the_participants_collection(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "update", "collection": "participants",
                                        "id": "codex", "data": {"trust": "quarantined"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 201)
            on_disk = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
            entry = [p for p in on_disk["participants"] if p["id"] == "codex"][0]
            self.assertEqual(entry["trust"], "quarantined")

    def test_a_roster_write_of_an_unknown_participant_is_400(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "participants",
                                        "id": "nobody", "data": {"trust": "approved"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 400)
                self.assertIn("nobody", json.dumps(body))

    def test_a_session_is_kept_in_memory_only(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 200)
            self.assertEqual(sorted(p.name for p in r.root.iterdir()), ["log", "participants.json"])

    def test_the_cookie_is_the_only_credential_the_owner_needs(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                # The cookie alone carries the owner: no bearer token is sent.
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "messages",
                                        "id": "own-1", "data": {"kind": "owner", "summary": "hi"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 201)

    def test_the_server_prints_its_startup_pin(self):
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        with TempRepo() as r:
            r.write_roster()
            code = ("import sys; sys.path.insert(0, %r); from crosstalk.serve import serve; "
                    "serve(%r, port=0)" % (scripts, str(r.root)))
            proc = subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True)
            try:
                ready, _w, _x = select.select([proc.stdout], [], [], 30)
                self.assertTrue(ready, "the server printed nothing")
                first = proc.stdout.readline()
            finally:
                proc.terminate()
                proc.wait(timeout=10)
            self.assertIn("Login PIN:", first)
            pin = first.split(":")[1].split()[0]
            self.assertRegex(pin, r"^\d{6}$")


class HealthTests(unittest.TestCase):
    def test_health_is_ok_when_the_log_is_clean(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/health")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["fatal"], [])
            self.assertRegex(body["server_time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")

    def test_health_reports_a_fatal_line(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(2, "codex", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/health")
            self.assertEqual(status, 200)
            self.assertFalse(body["ok"])
            self.assertEqual(len(body["fatal"]), 1)
            self.assertRegex(body["fatal"][0], r"^log/codex\.jsonl:1: seq 2, expected 1")

    def test_health_ignores_non_fatal_problems(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("ghost", [line(1, "ghost", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/health")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body["fatal"], [])


if __name__ == "__main__":
    unittest.main()