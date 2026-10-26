import json
import os
import select
import shutil
import copy
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from helpers import SPEC_ROSTER, RunningServer, TempRepo, line

from crosstalk.logs import append_line, check_all
from crosstalk.participants import load_roster
from crosstalk.serve import CrosstalkHandler, repo_build_info, repo_commit
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

    def test_snapshot_carries_the_service_block(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/snapshot")
            self.assertEqual(status, 200)
            svc = body["service"]
            self.assertEqual(svc["name"], "Crosstalk")
            for key in ("purpose", "capabilities", "commands", "protocol"):
                self.assertIn(key, svc)

    def test_messages_are_enriched_with_writer_and_time(self):
        # Rows may omit data.from and data.t (the protocol says the line's own t
        # is the clock). The snapshot fills both from the writing line so the
        # page can attribute and sort the message.
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"kind": "reply", "summary": "hi"})])
            with RunningServer(r.root) as srv:
                _, body = request(srv.url + "/api/snapshot")
            doc = body["collections"]["messages"]["codex-0001"]
            self.assertEqual(doc["from"], "codex")
            self.assertEqual(doc["t"], "2026-10-21T18:00:00Z")

    def test_enrichment_keeps_explicit_fields(self):
        with TempRepo() as r:
            r.write_roster()
            r.write_log("codex", [line(1, "codex", data={"from": "codex", "kind": "reply",
                                                          "summary": "hi",
                                                          "t": "2026-10-20T09:00:00Z"})])
            with RunningServer(r.root) as srv:
                _, body = request(srv.url + "/api/snapshot")
            doc = body["collections"]["messages"]["codex-0001"]
            self.assertEqual(doc["from"], "codex")
            self.assertEqual(doc["t"], "2026-10-20T09:00:00Z")

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
                    self.assertEqual(resp.headers.get("Cache-Control"), "no-cache")

    def test_favicon_and_logo_served(self):
        with TempRepo() as r:
            r.write_roster()
            root = Path(__file__).resolve().parent.parent
            for name in ("favicon.svg", "favicon.png", "favicon.ico", "logo.svg"):
                shutil.copy(str(root / name), str(r.root / name))
            with RunningServer(r.root) as srv:
                for path in ("/favicon.svg", "/logo.svg"):
                    with urllib.request.urlopen(srv.url + path, timeout=10) as resp:
                        self.assertEqual(resp.status, 200, path)
                        self.assertIn("image/svg+xml", resp.headers.get("Content-Type", ""), path)
                        self.assertIn("<svg", resp.read().decode("utf-8"), path)
                with urllib.request.urlopen(srv.url + "/favicon.ico", timeout=10) as resp:
                    self.assertEqual(resp.status, 200)
                    self.assertIn("image/x-icon", resp.headers.get("Content-Type", ""))
                    self.assertTrue(resp.read().startswith(b"\x00\x00\x01\x00"))
                for path in ("/favicon.png", "/apple-touch-icon.png"):
                    with urllib.request.urlopen(srv.url + path, timeout=10) as resp:
                        self.assertEqual(resp.status, 200, path)
                        self.assertIn("image/png", resp.headers.get("Content-Type", ""), path)
                        self.assertTrue(resp.read().startswith(b"\x89PNG\r\n\x1a\n"), path)


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

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_the_commit_is_cached_between_snapshots(self):
        with TempRepo() as r:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "init", "-q", str(r.root)], check=True, env=env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "one"],
                           cwd=str(r.root), check=True, env=env)
            import crosstalk.serve as serve_mod
            calls = []
            real_run = serve_mod.subprocess.run

            def counting(*args, **kwargs):
                calls.append(args)
                return real_run(*args, **kwargs)

            serve_mod.subprocess.run = counting
            try:
                first = repo_commit(r.root)
                second = repo_commit(r.root)
            finally:
                serve_mod.subprocess.run = real_run
            self.assertEqual(first, second)
            self.assertEqual(len(calls), 1, "the second read must reuse the cache")

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_the_commit_cache_invalidates_when_head_changes(self):
        with TempRepo() as r:
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "init", "-q", str(r.root)], check=True, env=env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "one"],
                           cwd=str(r.root), check=True, env=env)
            import crosstalk.serve as serve_mod
            calls = []
            real_run = serve_mod.subprocess.run

            def counting(*args, **kwargs):
                calls.append(args)
                return real_run(*args, **kwargs)

            serve_mod.subprocess.run = counting
            try:
                first = repo_commit(r.root)
                self.assertEqual(len(calls), 1)
                head = r.root / ".git" / "HEAD"
                st = head.stat()
                os.utime(str(head), (st.st_atime, st.st_mtime + 10))
                second = repo_commit(r.root)
                self.assertEqual(len(calls), 2, "a HEAD change must re-read the commit")
            finally:
                serve_mod.subprocess.run = real_run
            self.assertEqual(first, second)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_snapshot_carries_build_number_and_commit_date(self):
        with TempRepo() as r:
            r.write_roster()
            self.assertEqual(repo_build_info(r.root),
                             {"commit": None, "build_number": None, "commit_date": None})
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
                       GIT_COMMITTER_DATE="2026-10-25T14:30:00Z",
                       GIT_AUTHOR_DATE="2026-10-25T14:30:00Z")
            subprocess.run(["git", "init", "-q", str(r.root)], check=True, env=env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "one"],
                           cwd=str(r.root), check=True, env=env)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "two"],
                           cwd=str(r.root), check=True, env=env)
            with RunningServer(r.root) as srv:
                _, body = request(srv.url + "/api/snapshot")
            self.assertRegex(body["commit"], r"^[0-9a-f]{7,40}$")
            self.assertEqual(body["build_number"], 2)
            self.assertEqual(body["commit_date"], "2026-10-25T14:30:00Z")


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
                "data": {"from": "codex", "kind": "reply", "summary": "hi"}}
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
            self.assertEqual(body["data"], {"from": "codex", "kind": "reply", "summary": "hi"})
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
                                {"from": "codex", "kind": "reply", "summary": "local"},
                                roster=ro)

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

    def test_an_owner_token_bearer_acts_as_owner(self):
        # The owner token is the credential that outlives the PIN; as a bearer it
        # must carry the owner role (write participants / act as owner).
        with TempRepo() as r:
            roster = json.loads(json.dumps(SPEC_ROSTER))
            for entry in roster["participants"]:
                if entry["id"] == "codex":
                    entry["trust"] = "quarantined"
            r.write_roster(roster)
            with self.server(r) as srv:
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "update",
                                        "collection": "participants",
                                        "id": "codex", "data": {"trust": "approved"}},
                                       {"Authorization": "Bearer " + self.OWNER_TOKEN})
                self.assertEqual(status, 201)
            on_disk = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
            entry = [pp for pp in on_disk["participants"] if pp["id"] == "codex"][0]
            self.assertEqual(entry["trust"], "approved")

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

    def test_login_is_throttled_after_five_failures(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                for _ in range(5):
                    status, body, cookie = self.login(srv, {"pin": "000000"})
                    self.assertEqual(status, 401)
                # The sixth attempt is refused even with the correct PIN.
                status, body, headers = request_with_headers(
                    srv.url + "/api/login", "POST", {"pin": srv.pin})
                self.assertEqual(status, 429)
                self.assertIn("Retry-After", headers)
                # After the window clears, the correct PIN works again.
                srv.state.login_failures = [time.time() - 601.0] * 5
                status, body, cookie = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 200)

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
                                        "data": {"from": "owner", "kind": "owner",
                                                 "summary": "a note"}},
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
                                       {"from": "owner", "op": "update", "collection": "participants",
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

    def test_a_roster_set_is_refused(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "set", "collection": "participants",
                                        "id": "codex", "data": {"trust": "approved"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 400)
                self.assertIn("update", json.dumps(body))

    def test_a_roster_write_of_an_unknown_participant_is_400(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "update", "collection": "participants",
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
                                        "id": "own-1", "data": {"from": "owner", "kind": "owner", "summary": "hi"}},
                                       {"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 201)

    def test_the_server_prints_its_startup_pin(self):
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        with TempRepo() as r:
            r.write_roster()
            code = ("import sys; sys.path.insert(0, %r); from crosstalk.serve import serve; "
                    "serve(%r, port=0)" % (scripts, str(r.root)))
            # Keep the real ~/.crosstalk-* files (and the wake lock) out of it.
            env = dict(os.environ, CROSSTALK_WAKE_STATE="", CROSSTALK_SESSIONS="",
                       CROSSTALK_WAKE_LOGS=str(r.root / "wake-logs"))
            proc = subprocess.Popen([sys.executable, "-u", "-c", code], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, universal_newlines=True, env=env)
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

    def test_a_session_survives_a_restart(self):
        import tempfile as _tf
        with TempRepo() as r:
            r.write_roster()
            sessions = Path(_tf.mkdtemp()) / "sessions.json"
            with self.server(r, sessions=sessions) as srv:
                status, _body, cookie = self.login(srv, {"pin": srv.pin})
                self.assertEqual(status, 200)
            # A second server over the same root + sessions file: the cookie holds.
            with self.server(r, sessions=sessions) as srv2:
                status, body = request(srv2.url + "/api/me", "GET", headers={"Cookie": self.cookie_of(cookie)})
                self.assertEqual(status, 200)
                self.assertTrue(body["isOwner"])

    def test_an_expired_session_is_dropped_when_loaded(self):
        import json as _json
        import time as _time
        import tempfile as _tf
        with TempRepo() as r:
            r.write_roster()
            sessions = Path(_tf.mkdtemp()) / "sessions.json"
            stale = "x" * 32
            sessions.write_text(_json.dumps({stale: _time.time() - 10}), encoding="utf-8")
            with self.server(r, sessions=sessions) as srv:
                status, body = request(srv.url + "/api/me", "GET",
                                       headers={"Cookie": "crosstalk_session=" + stale})
                self.assertEqual(status, 200)
                self.assertFalse(body["isOwner"])

    def test_the_sessions_file_stays_out_of_the_repo(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                self.login(srv, {"pin": srv.pin})
                self.assertEqual(sorted(p.name for p in r.root.iterdir()), ["log", "participants.json"])


class OnboardTests(unittest.TestCase):
    """POST /api/onboard: the owner session adds a participant."""

    OWNER_TOKEN = "the-owner-token"

    def server(self, r, **kwargs):
        kwargs.setdefault("tokens", {"owner": token_hash(self.OWNER_TOKEN)})
        return RunningServer(r.root, **kwargs)

    def login(self, srv):
        status, body, headers = request_with_headers(srv.url + "/api/login", "POST", {"pin": srv.pin})
        return headers.get("Set-Cookie", "").split(";")[0]

    def onboard(self, srv, body, cookie=None, token=None):
        headers = {}
        if cookie:
            headers["Cookie"] = cookie
        if token:
            headers["Authorization"] = "Bearer " + token
        return request(srv.url + "/api/onboard", "POST", body, headers)

    def payload(self, **over):
        body = {"id": "smoke-agent", "name": "Smoke Agent", "kind": "agent", "parent": "bewareilive"}
        body.update(over)
        return body

    def test_a_viewer_is_401(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.onboard(srv, self.payload())
            self.assertEqual(status, 401)
            self.assertIn("error", body)
            self.assertFalse((r.root / "kits" / "smoke-agent.md").exists())

    def test_an_editor_bearer_token_is_403(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root, tokens={"codex": token_hash("codex-token")}) as srv:
                status, body = self.onboard(srv, self.payload(), token="codex-token")
            self.assertEqual(status, 403)
            self.assertIn("error", body)
            self.assertFalse((r.root / "kits" / "smoke-agent.md").exists())

    def test_the_owner_token_bearer_may_onboard(self):
        # The owner's own token carries the owner role (as /api/append already
        # treats it), so it may onboard too, not just the session cookie.
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                status, body = self.onboard(srv, self.payload(), token=self.OWNER_TOKEN)
            self.assertEqual(status, 200)
            self.assertIn("token", body)
            self.assertIn("kit", body)
            self.assertTrue((r.root / "kits" / "smoke-agent.md").exists())

    def test_invalid_input_is_400(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                cookie = self.login(srv)
                status, body = self.onboard(srv, self.payload(kind="nope"), cookie)
            self.assertEqual(status, 400)
            self.assertIn("kind", json.dumps(body))
            self.assertFalse((r.root / "kits" / "smoke-agent.md").exists())

    def test_the_owner_session_adds_a_quarantined_participant(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                cookie = self.login(srv)
                status, body = self.onboard(srv, self.payload(), cookie)
                self.assertEqual(status, 200)
                self.assertIn("token", body)
                self.assertIn("kit", body)
                self.assertIn("smoke-agent", body["kit"])
                self.assertIn(body["token"], body["kit"])
                # The roster of record gains the entry, quarantined.
                on_disk = json.loads((r.root / "participants.json").read_text(encoding="utf-8"))
                entry = [p for p in on_disk["participants"] if p["id"] == "smoke-agent"][0]
                self.assertEqual(entry["trust"], "quarantined")
                self.assertEqual(entry["kind"], "agent")
                self.assertEqual(entry["parent"], "bewareilive")
                # The next snapshot serves it dimmable (trust quarantined).
                status, snap = request(srv.url + "/api/snapshot")
                self.assertEqual(snap["roster"]["smoke-agent"]["trust"], "quarantined")
                # The token is kept only as a hash at rest, and never in the snapshot.
                tokens = json.loads((r.root / "tokens.json").read_text(encoding="utf-8"))
                self.assertEqual(tokens["smoke-agent"], token_hash(body["token"]))
                self.assertNotIn(body["token"], json.dumps(tokens))
                self.assertNotIn(body["token"], json.dumps(snap))

    def test_the_token_never_reaches_an_error_body(self):
        with TempRepo() as r:
            r.write_roster()
            with self.server(r) as srv:
                cookie = self.login(srv)
                status, body = self.onboard(srv, self.payload(), cookie)
                token = body["token"]
                # A duplicate id is refused; the error must not echo the token.
                status, err = self.onboard(srv, self.payload(), cookie)
                self.assertEqual(status, 400)
                self.assertNotIn(token, json.dumps(err))

    def test_append_roster_locks_onboard(self):
        import crosstalk.serve as serve_mod

        entered = []
        real_lock = getattr(serve_mod, "_locked_onboard", None)
        self.assertIsNotNone(real_lock, "_locked_onboard should be imported in serve")

        from contextlib import contextmanager

        @contextmanager
        def tracking_lock(root):
            entered.append(Path(root))
            with real_lock(root):
                yield

        with TempRepo() as r:
            r.write_roster()
            serve_mod._locked_onboard = tracking_lock
            try:
                with self.server(r) as srv:
                    cookie = self.login(srv)
                    status, _body = request(
                        srv.url + "/api/append", "POST",
                        {"from": "owner", "op": "update", "collection": "participants",
                         "id": "codex", "data": {"trust": "quarantined"}},
                        {"Cookie": cookie},
                    )
                    self.assertEqual(status, 201)
            finally:
                serve_mod._locked_onboard = real_lock
            self.assertEqual(entered, [r.root])


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


class RobustnessTests(unittest.TestCase):
    def test_an_unsupported_method_is_501_json(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                status, body = request(srv.url + "/api/snapshot", "PUT")
            self.assertEqual(status, 501)
            self.assertIn("error", body)

    def test_an_internal_error_is_500_json(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                original = CrosstalkHandler._snapshot

                def boom(self):
                    raise RuntimeError("boom")

                CrosstalkHandler._snapshot = boom
                try:
                    status, body = request(srv.url + "/api/snapshot")
                finally:
                    CrosstalkHandler._snapshot = original
            self.assertEqual(status, 500)
            self.assertIn("error", body)

    def test_a_slow_client_is_timed_out(self):
        with TempRepo() as r:
            r.write_roster()
            with RunningServer(r.root) as srv:
                old = CrosstalkHandler.timeout
                CrosstalkHandler.timeout = 0.5
                try:
                    s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
                    s.sendall(b"GET /api/snapshot HTTP/1.1\r\nHost: x\r\n")
                    s.settimeout(5)
                    start = time.time()
                    try:
                        data = s.recv(4096)
                    except (ConnectionResetError, socket.timeout):
                        data = b""
                    elapsed = time.time() - start
                    s.close()
                    self.assertEqual(data, b"")
                    self.assertLess(elapsed, 3)
                finally:
                    CrosstalkHandler.timeout = old


FAKE = str(Path(__file__).resolve().parent / "fake_agent.py")


class WakeServeTests(unittest.TestCase):
    """The waker runs inside serve; its status shows in the snapshot, its config never does."""

    OWNER_TOKEN = "the-owner-token"

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-serve-wake-"))
        self.cwd = self.tmp / "work"
        self.cwd.mkdir()
        self.out = self.tmp / "calls.jsonl"
        env = mock.patch.dict(os.environ, {"FAKE_AGENT_OUT": str(self.out),
                                           "FAKE_AGENT_MODE": "ok"})
        env.start()
        self.addCleanup(env.stop)
        self.wake_entry = {"command": [sys.executable, FAKE, "{pid}", "{ids}"],
                           "cwd": str(self.cwd)}
        self.r.write_roster()
        (self.r.root / "wake.json").write_text(json.dumps({"vectorcraft": self.wake_entry}),
                                               encoding="utf-8")

    def stray_wake(self):
        """A `wake` key left in participants.json: ignored, and never served or logged."""
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] == "vectorcraft":
                p["wake"] = dict(self.wake_entry)
        self.r.write_roster(data)

    def tearDown(self):
        self.r.__exit__(None, None, None)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def server(self, **kwargs):
        kwargs.setdefault("tokens", {"owner": token_hash(self.OWNER_TOKEN)})
        kwargs.setdefault("wake", True)
        kwargs.setdefault("wake_state", self.tmp / "state.json")
        kwargs.setdefault("wake_logs", self.tmp / "logs")
        kwargs.setdefault("wake_poll_sec", 0.2)
        return RunningServer(self.r.root, **kwargs)

    def owner_cookie(self, srv):
        status, _body, headers = request_with_headers(srv.url + "/api/login", "POST",
                                                      {"token": self.OWNER_TOKEN})
        self.assertEqual(status, 200)
        return headers.get("Set-Cookie", "").split(";")[0]

    def wait_wake(self, srv, pid, status, headers=None, timeout=10.0):
        deadline = time.time() + timeout
        snap = None
        while time.time() < deadline:
            _code, snap = request(srv.url + "/api/snapshot", headers=headers)
            if snap.get("wake", {}).get(pid, {}).get("status") == status:
                return snap
            time.sleep(0.1)
        self.fail("wake.%s never reached %r; last snapshot wake: %r"
                  % (pid, status, (snap or {}).get("wake")))

    def test_snapshot_wake_block(self):
        with self.server() as srv:
            # Seeding first, so the new message below is not swallowed as backlog.
            self.wait_wake(srv, "vectorcraft", "idle")
            self.r.write_log("owner", [line(1, "owner", id_="owner-0001",
                                            data={"from": "owner", "kind": "owner",
                                                  "summary": "please look"})])
            snap = self.wait_wake(srv, "vectorcraft", "ok")
            block = snap["wake"]["vectorcraft"]
            self.assertEqual(block["ids"], ["owner-0001"])
            self.assertIn("since", block)
            self.assertNotIn("log", block)
            cookie = self.owner_cookie(srv)
            _code, owned = request(srv.url + "/api/snapshot", headers={"Cookie": cookie})
            self.assertIn("log", owned["wake"]["vectorcraft"])
            self.assertTrue(owned["wake"]["vectorcraft"]["log"].startswith(str(self.tmp / "logs")))

    def test_snapshot_without_waker_has_empty_wake_block(self):
        with RunningServer(self.r.root) as srv:
            _code, snap = request(srv.url + "/api/snapshot")
        self.assertEqual(snap["wake"], {})

    def test_snapshot_roster_hides_wake(self):
        self.stray_wake()
        with self.server() as srv:
            _code, anon = request(srv.url + "/api/snapshot")
            _code, owned = request(srv.url + "/api/snapshot",
                                   headers={"Cookie": self.owner_cookie(srv)})
        self.assertIn("vectorcraft", anon["roster"])
        self.assertNotIn("wake", anon["roster"]["vectorcraft"])
        self.assertNotIn("wake", owned["roster"]["vectorcraft"])
        self.assertNotIn(str(self.cwd), json.dumps(anon))
        # The roster of record keeps it.
        on_disk = json.loads((self.r.root / "participants.json").read_text(encoding="utf-8"))
        entry = [p for p in on_disk["participants"] if p["id"] == "vectorcraft"][0]
        self.assertIn("wake", entry)

    def test_append_rejects_wake_field(self):
        with self.server(wake=False) as srv:
            status, body = request(srv.url + "/api/append", "POST",
                                   {"from": "owner", "op": "update", "collection": "participants",
                                    "id": "vectorcraft",
                                    "data": {"wake": {"command": ["true"], "cwd": "/"}}},
                                   {"Cookie": self.owner_cookie(srv)})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unknown roster field: wake")

    def test_roster_update_keeps_wake_out_of_log(self):
        self.stray_wake()
        with self.server(wake=False) as srv:
            status, body = request(srv.url + "/api/append", "POST",
                                   {"from": "owner", "op": "update", "collection": "participants",
                                    "id": "vectorcraft", "data": {"name": "VectorCraft II"}},
                                   {"Cookie": self.owner_cookie(srv)})
            self.assertEqual(status, 201)
            self.assertNotIn("wake", body["data"])
        on_disk = json.loads((self.r.root / "participants.json").read_text(encoding="utf-8"))
        entry = [p for p in on_disk["participants"] if p["id"] == "vectorcraft"][0]
        self.assertEqual(entry["name"], "VectorCraft II")
        self.assertEqual(entry["wake"]["cwd"], str(self.cwd))
        lines = [json.loads(x) for x in
                 (self.r.root / "log" / "owner.jsonl").read_text(encoding="utf-8").splitlines()
                 if x.strip()]
        self.assertEqual(lines[-1]["data"]["name"], "VectorCraft II")
        self.assertNotIn("wake", lines[-1]["data"])

    def test_participants_json_wake_is_not_used(self):
        self.stray_wake()
        (self.r.root / "wake.json").unlink()
        with self.server() as srv:
            time.sleep(0.6)  # a few polls
            _code, snap = request(srv.url + "/api/snapshot")
        self.assertEqual(snap["wake"], {})

    def test_log_doc_wake_ignored(self):
        self.r.write_log("owner", [line(1, "owner", collection="participants", id_="codex",
                                        op="update",
                                        data={"wake": {"command": [sys.executable, FAKE],
                                                       "cwd": str(self.cwd)}})])
        with self.server() as srv:
            snap = self.wait_wake(srv, "vectorcraft", "idle")
        self.assertNotIn("codex", snap["wake"])
        self.assertEqual(sorted(snap["wake"]), ["vectorcraft"])


class ServeSignalTests(unittest.TestCase):
    """`relay.py serve` under launchd is stopped with SIGTERM: it must shut down
    as cleanly as on Ctrl-C, ending a running agent and recording it."""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-serve-sigterm-"))
        (self.tmp / "work").mkdir()
        self.state = self.tmp / "state.json"
        self.out = self.tmp / "calls.jsonl"
        self.r.write_roster()
        (self.r.root / "wake.json").write_text(json.dumps({"vectorcraft": {
            "command": [sys.executable, FAKE, "{pid}", "{ids}"],
            "cwd": str(self.tmp / "work")}}), encoding="utf-8")
        self.proc = None
        self.child = None

    def tearDown(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        if self.child is not None:
            try:
                os.killpg(self.child, 9)
            except OSError:
                pass
        self.r.__exit__(None, None, None)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def wait_for(self, what, pred, timeout=20.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = pred()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for " + what)

    def saved_last(self):
        try:
            return json.loads(self.state.read_text())["vectorcraft"]["last"]
        except (OSError, ValueError, KeyError):
            return None

    def test_sigterm_ends_running_agent_and_records_interrupted(self):
        import signal
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from crosstalk.serve import serve\n"
                "serve(%r, port=0, no_pin=True, sessions='')\n" % (scripts, str(self.r.root)))
        env = dict(os.environ, CROSSTALK_WAKE_STATE=str(self.state),
                   CROSSTALK_WAKE_LOGS=str(self.tmp / "logs"), CROSSTALK_SESSIONS="",
                   FAKE_AGENT_OUT=str(self.out), FAKE_AGENT_MODE="sleep:30")
        self.proc = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(self.tmp),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        # The first pass seeds (the file appears); then a new message rings.
        self.wait_for("seeding", lambda: self.state.exists())
        self.r.write_log("owner", [line(1, "owner", id_="owner-0001",
                                        data={"from": "owner", "kind": "owner",
                                              "summary": "please look"})])
        last = self.wait_for("a running agent",
                             lambda: (self.saved_last() or {}).get("status") == "running"
                             and self.saved_last())
        self.child = last["pgid"]
        self.wait_for("the agent to start", lambda: self.out.exists())
        os.kill(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("serve did not exit on SIGTERM")
        stderr = self.proc.stderr.read().decode("utf-8", "replace")
        self.proc.stderr.close()
        self.assertEqual(self.proc.returncode, 128 + signal.SIGTERM, stderr)
        with self.assertRaises(ProcessLookupError):
            os.killpg(self.child, 0)
        last = self.saved_last()
        self.assertEqual(last["status"], "interrupted", stderr)
        self.assertNotIn("pgid", last)


if __name__ == "__main__":
    unittest.main()