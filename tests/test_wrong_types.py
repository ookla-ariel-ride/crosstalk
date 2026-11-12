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
import os
import subprocess
import sys
import unittest
from unittest import mock

from helpers import SPEC_ROSTER, RunningServer, TempRepo, line, relay_in
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

# A lone surrogate is a valid JSON string escape ("\ud800") that no UTF-8 encoder takes.
SURROGATE = "bad \ud800 x"
WRONG = {"list": [], "object": {"a": 1}, "number": 7, "boolean": True, "null": None, "empty text": "",
         "lone surrogate": SURROGATE}
# The fields that hold a list: these also get a string, and a list of wrong elements.
LIST_FIELDS = ("to", "positions", "objections", "findings", "branches", "options", "commits")


def wrong(field):
    if field not in LIST_FIELDS:
        return WRONG
    return dict(WRONG, text="x", **{"list of wrong": [[], {"a": 1}, 7, True, None, "", SURROGATE]})

# The fields the v2 server code reads from a row, by the row's base data.
MESSAGE_FIELDS = ("kind", "summary", "body", "from", "to", "t", "replyTo", "status", "verdict", "id",
                  "project", "source", "question", "resolution", "position", "positions", "objections",
                  "findings", "reviewer", "report", "options", "answered", "withdrawn", "seen", "branches",
                  "harness", "model", "recommend", "commits", "hold", "updated")
MESSAGE_BASES = {
    "question": {"kind": "question", "summary": "bad question", "to": ["owner"]},
    "request": {"kind": "request", "summary": "bad request", "to": ["owner"]},
    "reply": {"kind": "reply", "summary": "bad reply", "replyTo": "alpha-root"},
    "land": {"kind": "request", "summary": "LAND START alpha-fix-branch", "to": ["rev"]},
    "granted": {"kind": "reply", "summary": "GRANTED alpha-fix-branch", "replyTo": "alpha-root"},
    "review": {"kind": "disagreement", "summary": "bad override", "source": "codex-review", "project": "alpha"},
}
SECTION_FIELDS = {
    "agreements": ("title", "status", "builder", "owner", "adopter", "summary", "body", "t", "id", "from",
                   "hold", "holder", "updated", "commits"),
    "decisions": ("title", "project", "source", "summary", "body", "t"),
    "lessons": ("title", "fix", "summary", "body", "t"),
    "incidents": ("title", "fix", "project", "end", "summary", "body", "t"),
    "locks": ("path", "note", "repo", "project", "released", "summary", "t"),
    "progress": ("item", "game", "note", "summary", "t"),
    "schedules": ("name", "about", "cron", "removed", "summary", "t"),
    "adoptions": ("item", "note", "ref", "summary", "t"),
    "waiting": ("project", "summary", "body", "done", "t", "from", "replyTo"),
}


BAD_T = "2026-10-21T10:01:00Z"  # the time of the bad line
# The inbox side's collections: their ids are fixed by the writer (logs.ack_problem,
# logs.state_problem), so each case carries its id.
INBOX_CASES = {
    "acks": ("alpha", "alpha--alpha-root", ("none", "item", "from", "t", "note"),
             {"from": "alpha", "item": "alpha-root", "none": True, "t": T}),
    # rev is the coordinator: its record counts, so the state row reads it.
    "state": ("rev", "rev-wind-down", ("step", "started", "by", "reason", "from", "t"),
              {"from": "rev", "step": 1, "started": BAD_T, "by": "rev", "reason": "test", "t": BAD_T}),
}


def roster():
    data = copy.deepcopy(SPEC_ROSTER)
    data["relay"] = "rev"  # the coordinator: its GRANTED rows and review rows count
    return data


def cases():
    """(name, author, collection, data, id) for each bad row."""
    out = []
    for base_name, base in MESSAGE_BASES.items():
        author = "rev" if base_name in ("granted", "review") else "alpha"
        for field in MESSAGE_FIELDS:
            for type_name, value in wrong(field).items():
                out.append(("messages/%s.%s=%s" % (base_name, field, type_name), author, "messages",
                            dict(base, **{field: value}), author + "-bad"))
    for coll, fields in SECTION_FIELDS.items():
        for field in fields:
            for type_name, value in wrong(field).items():
                out.append(("%s.%s=%s" % (coll, field, type_name), "alpha", coll,
                            {"summary": "bad row", field: value}, "alpha-bad"))
    for coll, (author, did, fields, base) in INBOX_CASES.items():
        for field in fields:
            for type_name, value in wrong(field).items():
                out.append(("%s.%s=%s" % (coll, field, type_name), author, coll,
                            dict(base, **{field: value}), did))
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

    def write(self, author, coll, data, bad_id):
        """Write the logs; False when _validate refuses the bad line."""
        alpha = good_lines()
        beta = [line(1, "beta", id_="beta-reply",
                     data={"kind": "reply", "summary": "good reply", "replyTo": "alpha-root"}, t=T)]
        rev = []
        own = {"alpha": alpha, "rev": rev}[author]
        own.append(line(len(own) + 1, author, coll, id_=bad_id, data=data, t=BAD_T))
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
        # The good root is a question to the owner that nobody answered: it needs the owner.
        for order in ("needs", "new"):
            roots = [t["root"] for t in self.get(srv, "/api/threads?sort=%s&limit=200" % order)["threads"]]
            self.assertIn("alpha-root", roots, order)
        for order in ("hot", "unanswered"):
            self.get(srv, "/api/threads?sort=" + order)
        thread = self.get(srv, "/api/thread/alpha-root")
        self.assertEqual(["alpha-root", "beta-reply"], [m["id"] for m in thread["messages"]][:2])
        for bad in ("alpha-bad", "rev-bad"):
            self.get(srv, "/api/thread/" + bad)
        for name in SECTIONS:
            routes = ["/api/section/" + name] + (["/api/section/%s?q=good" % name] if COLLECTION[name] == coll else [])
            for route in routes:
                body = self.get(srv, route)
                if COLLECTION[name] in SECTION_FIELDS:
                    self.assertIn("alpha-good-" + COLLECTION[name], [r["id"] for r in body["rows"]], route)
        snapshot = self.get(srv, "/api/snapshot")["collections"]
        self.assertIn("alpha-root", snapshot["messages"])
        for other in SECTION_FIELDS:
            self.assertIn("alpha-good-" + other, snapshot[other])
        docs = self.get(srv, "/api/" + coll)["docs"]
        if coll in INBOX_CASES:
            self.assertIn(INBOX_CASES[coll][1], docs)
        else:
            self.assertIn("alpha-root" if coll == "messages" else "alpha-good-" + coll, docs)
        # The state object of /api/vitals reads the wind-down record and the queue:
        # a field it cannot read is left out (null), and the request never fails.
        wind = self.get(srv, "/api/vitals")["state"]["wind_down"]
        self.assertIn(wind["step"], (0, 1, 2))
        for name in ("started", "by", "reason"):
            self.assertTrue(wind[name] is None or isinstance(wind[name], str), (name, wind[name]))
        for route in ("/api/queue", "/api/vitals", "/api/read-state", "/api/me", "/api/health", "/api/palette"):
            self.get(srv, route)
        rows, _mark = events_mod.since(self.repo.root, {})
        self.assertTrue(any(r["id"] == "beta-reply" for r in rows))

    def test_no_v2_read_route_fails_on_a_wrong_typed_field(self):
        refused = []
        with RunningServer(self.repo.root, tokens={"owner": token_hash(OWNER_TOKEN)}) as srv:
            for name, author, coll, data, did in cases():
                if not self.write(author, coll, data, did):
                    refused.append(name)
                    continue
                with self.subTest(case=name):
                    self.check_routes(srv, coll)
        print("\nwrong-type cases the log refuses: %s" % json.dumps(refused))


class CliTests(unittest.TestCase):
    """The inbox side's JSON and text output of relay.py never fails on a lone surrogate."""

    def test_inbox_and_state_print_a_lone_surrogate(self):
        with TempRepo() as repo:
            repo.write_roster()
            repo.write_log("beta", [line(1, "beta", data={"kind": "request", "summary": SURROGATE,
                                                          "to": ["alpha"]}, t=T)])
            repo.write_log("owner", [line(1, "owner", "state", id_="owner-wind-down", t=T, data={
                "from": "owner", "step": 1, "started": T, "by": "owner", "reason": SURROGATE, "t": T})])
            script = str(relay_in(repo.root))
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            for args in (["inbox", "alpha"], ["inbox", "alpha", "--new"], ["inbox", "alpha", "--brief"],
                         ["inbox", "alpha", "--new", "--brief"], ["state"],
                         ["state", "--get", "wind_down.reason"]):
                with self.subTest(args=args):
                    p = subprocess.run([sys.executable, script] + args, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, cwd="/", env=env)
                    self.assertEqual(p.returncode, 0, p.stderr.decode("utf-8", "replace"))
                    text = p.stdout.decode("utf-8")  # valid UTF-8
                    if "--brief" not in args and "--get" not in args:
                        self.assertIsInstance(json.loads(text), dict)


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
