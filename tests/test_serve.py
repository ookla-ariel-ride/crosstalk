import json
import os
import shutil
import subprocess
import unittest
import urllib.error
import urllib.request

from helpers import RunningServer, TempRepo, line

from crosstalk.serve import repo_commit


def request(url, method="GET", body=None, headers=None):
    """(status, decoded JSON body) for one request against the served API."""
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        return e.code, (json.loads(raw) if raw else None)


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


if __name__ == "__main__":
    unittest.main()
