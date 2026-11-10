"""A fresh install has no participants.json: each first step must still work."""
import json
import unittest

from helpers import RunningServer, TempRepo
from test_serve import request, request_with_headers

from crosstalk.exporter import build_snapshot
from crosstalk.onboard import kit_text
from crosstalk.participants import DEFAULT_ROSTER


class NoRosterTests(unittest.TestCase):
    def test_the_first_snapshot_request_holds_the_owner_alone(self):
        with TempRepo() as r:
            with RunningServer(r.root) as srv:
                status, snap = request(srv.url + "/api/snapshot")
            self.assertEqual(status, 200, snap)
            self.assertEqual(list(snap["roster"]), ["owner"])
            self.assertTrue((r.root / "participants.json").is_file())

    def test_the_owner_roster_write_reads_a_default_roster(self):
        with TempRepo() as r:
            with RunningServer(r.root) as srv:
                _status, _body, headers = request_with_headers(srv.url + "/api/login", "POST", {"pin": srv.pin})
                cookie = headers.get("Set-Cookie", "").split(";")[0]
                status, body = request(srv.url + "/api/append", "POST",
                                       {"from": "owner", "op": "update", "collection": "participants",
                                        "id": "nobody", "data": {"trust": "approved"}},
                                       {"Cookie": cookie})
            # The roster is read (400: no such participant), not missing (500).
            self.assertEqual(status, 400, body)
            self.assertIn("nobody", json.dumps(body))

    def test_a_kit_names_the_default_page(self):
        with TempRepo() as r:
            entry = {"id": "first", "name": "First", "kind": "project", "color": "#b3261e",
                     "colorDark": "#ef6a5f", "trust": "quarantined"}
            self.assertIn(DEFAULT_ROSTER["page"], kit_text(r.root, "first", "tok", entry))

    def test_the_export_snapshot_holds_the_owner_alone(self):
        with TempRepo() as r:
            snap = build_snapshot(r.root)
            self.assertEqual([p["id"] for p in snap["roster"]["participants"]], ["owner"])
            self.assertEqual(snap["collections"], {})


if __name__ == "__main__":
    unittest.main()
