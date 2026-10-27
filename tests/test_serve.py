import json
import os
import re
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
from test_notify import FakeSession

from crosstalk import live
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

    def test_the_permanent_pin_logs_in_again_and_again(self):
        from crosstalk import ownerpin
        with TempRepo() as r:
            r.write_roster()
            ownerpin.set_pin(r.root, "819797")
            stored = (r.root / "owner-pin.json").read_text(encoding="utf-8")
            self.assertNotIn("819797", stored)
            self.assertEqual(os.stat(r.root / "owner-pin.json").st_mode & 0o777, 0o600)
            with self.server(r, no_pin=True) as srv:
                srv.state.pin_expires = 0.0
                for _ in range(3):
                    status, _body, cookie = self.login(srv, {"pin": "819797"})
                    self.assertEqual(status, 200)
                    self.assertIn("HttpOnly", cookie)
                status, _body, _cookie = self.login(srv, {"pin": "819798"})
                self.assertEqual(status, 401)

    def test_a_permanent_pin_change_or_removal_applies_without_restart(self):
        from crosstalk import ownerpin
        with TempRepo() as r:
            r.write_roster()
            ownerpin.set_pin(r.root, "111111")
            with self.server(r, no_pin=True) as srv:
                ownerpin.set_pin(r.root, "222222")
                self.assertEqual(self.login(srv, {"pin": "111111"})[0], 401)
                self.assertEqual(self.login(srv, {"pin": "222222"})[0], 200)
                ownerpin.clear_pin(r.root)
                self.assertEqual(self.login(srv, {"pin": "222222"})[0], 401)

    def test_the_startup_pin_still_works_beside_a_permanent_one(self):
        from crosstalk import ownerpin
        with TempRepo() as r:
            r.write_roster()
            ownerpin.set_pin(r.root, "819797")
            with self.server(r) as srv:
                self.assertEqual(self.login(srv, {"pin": srv.pin})[0], 200)

    def test_permanent_pin_rules(self):
        from crosstalk import ownerpin
        with TempRepo() as r:
            for bad in ["12345", "1234567", "abcdef", "", " 81979"]:
                with self.assertRaises(ValueError):
                    ownerpin.set_pin(r.root, bad)
            self.assertFalse(ownerpin.check_pin(r.root, "819797"))  # no file
            (r.root / "owner-pin.json").write_text("{not json", encoding="utf-8")
            self.assertFalse(ownerpin.check_pin(r.root, "819797"))
            (r.root / "owner-pin.json").write_text('{"scheme": "pbkdf2_sha256", "iterations": true, "salt": "00", "hash": "00"}', encoding="utf-8")
            self.assertFalse(ownerpin.check_pin(r.root, "819797"))
            ownerpin.set_pin(r.root, "819797")
            self.assertFalse(ownerpin.check_pin(r.root, None))
            self.assertFalse(ownerpin.check_pin(r.root, 819797))

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
            # Keep the real ~/.crosstalk-* files (and the notifier lock) out of it.
            env = dict(os.environ, CROSSTALK_NOTIFY_STATE="", CROSSTALK_SESSIONS="",
                       CROSSTALK_LIVE_DIR=str(r.root / "live"))
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


class NotifyServeTests(unittest.TestCase):
    """The notifier runs inside serve; its status shows in the snapshot, its token never does."""

    OWNER_TOKEN = "the-owner-token"
    SESSION_TOKEN = "tok-secret-123"

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.r.write_roster()
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-serve-notify-"))
        self.socks = tempfile.mkdtemp(dir="/tmp", prefix="cc-socks-")
        env = mock.patch.dict(os.environ, {"CROSSTALK_LIVE_DIR": str(self.tmp / "live")})
        env.start()
        self.addCleanup(env.stop)
        res = mock.patch.object(live, "SOCKET_DIR_RES", list(live.SOCKET_DIR_RES) + [
            re.compile("^" + re.escape(self.socks) + "$")])
        res.start()
        self.addCleanup(res.stop)
        self.fakes = []

    def tearDown(self):
        for f in self.fakes:
            f.close()
        shutil.rmtree(self.socks, ignore_errors=True)
        shutil.rmtree(str(self.tmp), ignore_errors=True)
        self.r.__exit__(None, None, None)

    def server(self, **kwargs):
        kwargs.setdefault("tokens", {"owner": token_hash(self.OWNER_TOKEN)})
        kwargs.setdefault("notify", True)
        kwargs.setdefault("notify_state", self.tmp / "state.json")
        kwargs.setdefault("notify_poll_sec", 0.1)
        return RunningServer(self.r.root, **kwargs)

    def owner_cookie(self, srv):
        status, _body, headers = request_with_headers(srv.url + "/api/login", "POST",
                                                      {"token": self.OWNER_TOKEN})
        self.assertEqual(status, 200)
        return headers.get("Set-Cookie", "").split(";")[0]

    def session(self, sid="s1", pid="vectorcraft"):
        fake = FakeSession(os.path.join(self.socks, sid + ".sock"))
        self.fakes.append(fake)
        env = {"CLAUDE_CODE_MESSAGING_SOCKET": fake.path,
               "CLAUDE_CODE_MESSAGING_TOKEN": self.SESSION_TOKEN}
        path = live.register(self.r.root, pid, env, json.dumps({"session_id": sid}), time.time())
        self.assertIsNotNone(path)
        return fake

    def wait_for(self, what, pred, timeout=10.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            value = pred()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for " + what)

    def test_snapshot_has_live_block_and_no_wake(self):
        with self.server() as srv:
            _code, snap = request(srv.url + "/api/snapshot")
        self.assertEqual(snap["live"], {})
        self.assertNotIn("wake", snap)

    def test_owner_message_reaches_a_registered_session(self):
        fake = self.session()
        with self.server() as srv:
            # Let the first pass seed, so the message below is not swallowed as backlog.
            self.wait_for("the session in the live block",
                          lambda: request(srv.url + "/api/snapshot")[1]["live"])
            status, _body = request(srv.url + "/api/append", "POST",
                                    {"from": "owner", "op": "set", "collection": "messages",
                                     "id": "own-1",
                                     "data": {"from": "owner", "kind": "owner",
                                              "summary": "please look"}},
                                    {"Cookie": self.owner_cookie(srv)})
            self.assertEqual(status, 201, _body)
            self.wait_for("the session to be told", fake.drain)
            snap = self.wait_for("a told status",
                                 lambda: (request(srv.url + "/api/snapshot")[1]["live"]
                                          .get("vectorcraft", {}).get("last")))
        self.assertEqual(snap["status"], "told")
        self.assertIn("own-1", json.dumps(fake.lines))

    def test_live_block_shape_and_no_token(self):
        self.session()
        with self.server() as srv:
            block = self.wait_for("the session in the live block",
                                  lambda: request(srv.url + "/api/snapshot")[1]["live"])
            body = json.dumps(request(srv.url + "/api/snapshot",
                                      headers={"Cookie": self.owner_cookie(srv)})[1])
        self.assertEqual(block["vectorcraft"]["sessions"], 1)
        self.assertNotIn(self.SESSION_TOKEN, body)
        self.assertNotIn(self.socks, body)

    def test_notify_off_leaves_live_empty_and_starts_no_thread(self):
        self.session()
        before = {t.name for t in threading.enumerate()}
        with self.server(notify=False) as srv:
            time.sleep(0.4)
            _code, snap = request(srv.url + "/api/snapshot")
            started = {t.name for t in threading.enumerate()} - before
            self.assertIsNone(srv.state.notifier)
        self.assertEqual(snap["live"], {})
        self.assertFalse([n for n in started if "notif" in n.lower()], started)

    def test_snapshot_roster_hides_stale_wake_key(self):
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] == "vectorcraft":
                p["wake"] = {"command": ["secret-agent-cmd"], "cwd": "/secret/dir"}
        self.r.write_roster(data)
        with self.server(notify=False) as srv:
            _code, snap = request(srv.url + "/api/snapshot")
        self.assertIn("vectorcraft", snap["roster"])
        self.assertNotIn("wake", snap["roster"]["vectorcraft"])
        self.assertNotIn("/secret/dir", json.dumps(snap))

    def test_append_rejects_wake_field(self):
        with self.server(notify=False) as srv:
            status, body = request(srv.url + "/api/append", "POST",
                                   {"from": "owner", "op": "update", "collection": "participants",
                                    "id": "vectorcraft",
                                    "data": {"wake": {"command": ["true"], "cwd": "/"}}},
                                   {"Cookie": self.owner_cookie(srv)})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unknown roster field: wake")

    def test_roster_update_keeps_stale_wake_out_of_log(self):
        data = copy.deepcopy(SPEC_ROSTER)
        for p in data["participants"]:
            if p["id"] == "vectorcraft":
                p["wake"] = {"command": ["secret-agent-cmd"], "cwd": "/secret/dir"}
        self.r.write_roster(data)
        with self.server(notify=False) as srv:
            status, body = request(srv.url + "/api/append", "POST",
                                   {"from": "owner", "op": "update", "collection": "participants",
                                    "id": "vectorcraft", "data": {"name": "VectorCraft II"}},
                                   {"Cookie": self.owner_cookie(srv)})
            self.assertEqual(status, 201)
            self.assertNotIn("wake", body["data"])
        lines = [json.loads(x) for x in
                 (self.r.root / "log" / "owner.jsonl").read_text(encoding="utf-8").splitlines()
                 if x.strip()]
        self.assertNotIn("wake", lines[-1]["data"])


class ServeSignalTests(unittest.TestCase):
    """`relay.py serve` under launchd is stopped with SIGTERM: it must shut down
    as cleanly as on Ctrl-C, stopping the notifier, and exit 143."""

    def setUp(self):
        self.r = TempRepo()
        self.r.__enter__()
        self.tmp = Path(tempfile.mkdtemp(prefix="crosstalk-serve-sigterm-"))
        self.r.write_roster()
        self.proc = None

    def tearDown(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.r.__exit__(None, None, None)
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_sigterm_stops_serve_and_exits_143(self):
        import signal
        scripts = str(Path(__file__).resolve().parent.parent / "scripts")
        state = self.tmp / "state.json"
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from crosstalk.serve import serve\n"
                "serve(%r, port=0, no_pin=True, sessions='')\n" % (scripts, str(self.r.root)))
        env = dict(os.environ, CROSSTALK_NOTIFY_STATE=str(state), CROSSTALK_SESSIONS="",
                   CROSSTALK_LIVE_DIR=str(self.tmp / "live"))
        self.proc = subprocess.Popen([sys.executable, "-c", code], env=env, cwd=str(self.tmp),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)
        # Wait until it is serving, and the notifier holds its state lock.
        ready, _w, _x = select.select([self.proc.stdout], [], [], 30)
        self.assertTrue(ready, "the server printed nothing")
        self.proc.stdout.readline()
        deadline = time.time() + 20
        lock = Path(str(state) + ".lock")
        while time.time() < deadline and not lock.exists():
            time.sleep(0.05)
        self.assertTrue(lock.exists(), "the notifier never started")
        os.kill(self.proc.pid, signal.SIGTERM)
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("serve did not exit on SIGTERM")
        stderr = self.proc.stderr.read().decode("utf-8", "replace")
        self.proc.stderr.close()
        self.proc.stdout.close()
        self.assertEqual(self.proc.returncode, 128 + signal.SIGTERM, stderr)


if __name__ == "__main__":
    unittest.main()