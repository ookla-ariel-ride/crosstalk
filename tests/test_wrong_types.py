"""A row with a wrong-typed field never fails a v2 read route.

logs._validate checks the envelope of a line, not the types inside `data`. So
a row such as {"kind": []} can reach the replay. Each case here writes a log
that _validate accepts, with one such row among good rows, then calls every v2
read route. No route may fail, and the good rows must still be there.
"""
import contextlib
import copy
import io
import json
import unittest
from unittest import mock

from helpers import SPEC_ROSTER, RunningServer, TempRepo, line
from test_serve import request

from crosstalk import events as events_mod
from crosstalk import serve as serve_mod
from crosstalk.logs import read_log
from crosstalk.participants import load_roster
from crosstalk.sections import COLLECTION, SECTIONS
from crosstalk.tokens import token_hash

OWNER_TOKEN = "owner-token-for-wrong-types"
AUTH = {"Authorization": "Bearer " + OWNER_TOKEN}
T = "2026-10-21T10:00:00Z"

WRONG = {"list": [], "object": {"a": 1}, "number": 7, "boolean": True, "null": None, "empty text": ""}
# The fields that hold a list: these also get a string.
LIST_FIELDS = ("to", "positions", "objections", "findings", "branches", "options")


def wrong(field):
    return dict(WRONG, text="x") if field in LIST_FIELDS else WRONG

# The fields the v2 server code reads from a row, by the row's base data.
MESSAGE_FIELDS = ("kind", "summary", "body", "from", "to", "t", "replyTo", "status", "verdict", "id",
                  "project", "source", "question", "resolution", "position", "positions", "objections",
                  "findings", "reviewer", "report", "options", "answered", "withdrawn", "seen", "branches")
MESSAGE_BASES = {
    "question": {"kind": "question", "summary": "bad question", "to": ["owner"]},
    "request": {"kind": "request", "summary": "bad request", "to": ["owner"]},
    "reply": {"kind": "reply", "summary": "bad reply", "replyTo": "alpha-root"},
    "land": {"kind": "request", "summary": "LAND START alpha-fix-branch", "to": ["rev"]},
    "granted": {"kind": "reply", "summary": "GRANTED alpha-fix-branch", "replyTo": "alpha-root"},
    "review": {"kind": "disagreement", "summary": "bad override", "source": "codex-review", "project": "alpha"},
}
SECTION_FIELDS = {
    "agreements": ("title", "status", "builder", "owner", "adopter", "summary", "body", "t", "id", "from"),
    "decisions": ("title", "project", "source", "summary", "body", "t"),
    "lessons": ("title", "fix", "summary", "body", "t"),
    "incidents": ("title", "fix", "project", "end", "summary", "body", "t"),
    "locks": ("path", "note", "repo", "project", "released", "summary", "t"),
    "progress": ("item", "game", "note", "summary", "t"),
    "schedules": ("name", "about", "cron", "removed", "summary", "t"),
    "adoptions": ("item", "note", "ref", "summary", "t"),
    "waiting": ("project", "summary", "body", "done", "t", "from", "replyTo"),
}


def roster():
    data = copy.deepcopy(SPEC_ROSTER)
    data["relay"] = "rev"  # the coordinator: its GRANTED rows and review rows count
    return data


def cases():
    """(name, author, collection, data) for each bad row."""
    out = []
    for base_name, base in MESSAGE_BASES.items():
        author = "rev" if base_name in ("granted", "review") else "alpha"
        for field in MESSAGE_FIELDS:
            for type_name, value in wrong(field).items():
                out.append(("messages/%s.%s=%s" % (base_name, field, type_name), author, "messages",
                            dict(base, **{field: value})))
    for coll, fields in SECTION_FIELDS.items():
        for field in fields:
            for type_name, value in wrong(field).items():
                out.append(("%s.%s=%s" % (coll, field, type_name), "alpha", coll,
                            {"summary": "bad row", field: value}))
    return out


def good_lines():
    """alpha's good rows: a root question, then one good row in each other collection."""
    rows = [line(1, "alpha", id_="alpha-root",
                 data={"kind": "question", "summary": "good root", "to": ["owner"]}, t=T)]
    for i, coll in enumerate(SECTION_FIELDS, start=2):
        rows.append(line(i, "alpha", coll, id_="alpha-good-" + coll,
                         data={"summary": "good " + coll, "title": "good"}, t=T))
    return rows


class WrongTypeTests(unittest.TestCase):
    def setUp(self):
        self.repo = TempRepo().__enter__()
        self.repo.write_roster(roster())
        self.roster = load_roster(self.repo.root / "participants.json")

    def tearDown(self):
        self.repo.__exit__(None, None, None)

    def write(self, author, coll, data):
        """Write the logs; False when _validate refuses the bad line."""
        alpha = good_lines()
        beta = [line(1, "beta", id_="beta-reply",
                     data={"kind": "reply", "summary": "good reply", "replyTo": "alpha-root"}, t=T)]
        rev = []
        bad_id = author + "-bad"
        own = {"alpha": alpha, "rev": rev}[author]
        own.append(line(len(own) + 1, author, coll, id_=bad_id, data=data, t="2026-10-21T10:01:00Z"))
        self.repo.write_log("alpha", alpha)
        self.repo.write_log("beta", beta)
        self.repo.write_log("rev", rev)
        return not read_log(self.repo.root / "log" / (author + ".jsonl"), self.roster)[1]

    def get(self, srv, route):
        try:
            status, body = request(srv.url + route, headers=AUTH)
        except Exception as e:  # a dropped connection, not an answer
            self.fail("%s: no answer (%s: %s)" % (route, type(e).__name__, e))
        self.assertLess(status, 500, "%s: %s" % (route, body))
        return body

    def check_routes(self, srv, coll):
        for order in ("needs", "new", "hot", "unanswered"):
            self.get(srv, "/api/threads?sort=" + order)
        roots = [t["root"] for t in self.get(srv, "/api/threads?sort=new&limit=200")["threads"]]
        self.assertIn("alpha-root", roots)
        thread = self.get(srv, "/api/thread/alpha-root")
        self.assertIn("beta-reply", [m["id"] for m in thread["messages"]])
        for bad in ("alpha-bad", "rev-bad"):
            self.get(srv, "/api/thread/" + bad)
        for name in SECTIONS:
            body = self.get(srv, "/api/section/" + name)
            if COLLECTION[name] != coll:
                continue
            self.get(srv, "/api/section/%s?q=x" % name)
            if coll != "messages":
                self.assertIn("alpha-good-" + coll, [r["id"] for r in body["rows"]], name)
        for route in ("/api/queue", "/api/vitals", "/api/snapshot", "/api/read-state", "/api/me",
                      "/api/health", "/api/palette", "/api/" + coll):
            self.get(srv, route)
        rows, _mark = events_mod.since(self.repo.root, {})
        self.assertTrue(any(r["id"] == "beta-reply" for r in rows))

    def test_no_v2_read_route_fails_on_a_wrong_typed_field(self):
        refused = []
        with RunningServer(self.repo.root, tokens={"owner": token_hash(OWNER_TOKEN)}) as srv:
            for name, author, coll, data in cases():
                if not self.write(author, coll, data):
                    refused.append(name)
                    continue
                with self.subTest(case=name):
                    self.check_routes(srv, coll)
        print("\nwrong-type cases the log refuses: %s" % json.dumps(refused))


class InternalErrorTests(unittest.TestCase):
    def test_an_exception_in_a_route_is_a_json_500_and_one_log_line(self):
        with TempRepo() as repo:
            repo.write_roster()
            repo.write_log("alpha", good_lines())
            err = io.StringIO()
            boom = mock.patch.object(serve_mod.threads_mod, "ThreadIndex", side_effect=TypeError("boom"))
            with boom, contextlib.redirect_stderr(err), RunningServer(repo.root) as srv:
                status, body = request(srv.url + "/api/threads?sort=new")
                # The connection stays usable: the next request on a new one is answered.
                again, _ = request(srv.url + "/api/health")
        self.assertEqual(status, 500)
        self.assertEqual(body, {"error": "internal error"})
        self.assertEqual(again, 200)
        logged = [ln for ln in err.getvalue().splitlines() if "internal error" in ln]
        self.assertEqual(len(logged), 1, err.getvalue())
        self.assertIn("GET /api/threads", logged[0])
        self.assertIn("TypeError", logged[0])
        self.assertNotIn("sort=new", logged[0])  # the path only: never a query or a header


if __name__ == "__main__":
    unittest.main()
