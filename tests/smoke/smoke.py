"""The smoke suite's driver; `sh tests/smoke/run.sh` runs it, not unittest.

`smoke.py fixture TMP` builds a data root in TMP/data with relay.py commands.
`smoke.py check TMP SOCKS` checks the server that run.sh started on it: the
routes, appends through the command line and through HTTP, a live notice to a
fake session socket in SOCKS, and the auto-granter's GRANTED. Standard library
only. Tokens and the PIN stay in TMP and are never printed.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
RELAY = REPO / "scripts" / "relay.py"
PIN = "468024"
ASK_ID = "alpha-smoke-ask"
WAIT = 30.0
LOAD_LIMIT = 5  # seconds a read may take while appends go in


class Failed(Exception):
    pass


def relay(data, *args, stdin=None, env=None):
    """Run relay.py on the data root; the CompletedProcess, or Failed on a non-zero exit."""
    p = subprocess.run([sys.executable, str(RELAY), "--data-root", str(data)] + list(args),
                       input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, cwd=str(REPO), env=env)
    if p.returncode != 0:
        raise Failed("relay.py %s exited %d: %s" % (args[0], p.returncode, p.stderr.strip()[-300:]))
    return p


def fixture(tmp):
    data = tmp / "data"
    data.mkdir()
    tokens = {}
    for args in (("alpha", "--name", "Alpha", "--kind", "project"),
                 ("relay-bot", "--name", "Relay bot", "--kind", "tool"),
                 ("beta", "--name", "Beta", "--kind", "agent", "--parent", "alpha")):
        tokens[args[0]] = relay(data, "onboard", *args).stdout.strip().splitlines()[-1]
    roster_file = data / "participants.json"
    roster = json.loads(roster_file.read_text(encoding="utf-8"))
    for p in roster["participants"]:
        p["trust"] = "approved"
    roster["relay"] = "relay-bot"  # the coordinator, so the auto-granter has someone to post as
    roster_file.write_text(json.dumps(roster, indent=2) + "\n", encoding="utf-8")
    relay(data, "set-pin", PIN)
    relay(data, "append", "--from", "alpha", "--op", "set", "--collection", "messages",
          "--id", ASK_ID, "--data", json.dumps(
              {"from": "alpha", "kind": "request", "summary": "TURN ASK alpha smoke-1112",
               "to": ["relay-bot"]}))
    secrets = tmp / "secrets.json"
    fd = os.open(str(secrets), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(tokens, f)


# -- the checks --------------------------------------------------------------

def http(url, method="GET", body=None, headers=None, raw=False, timeout=10):
    """(status, body): JSON decoded unless raw, then bytes."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    hdrs = dict(headers or {})
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, payload, got = resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        status, payload, got = e.code, e.read(), dict(e.headers)
    if raw:
        return status, payload, got
    return status, (json.loads(payload.decode("utf-8")) if payload else None)


def wait_for(what, pred):
    deadline = time.time() + WAIT
    while time.time() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(0.2)
    raise Failed("timed out after %ds waiting for %s" % (WAIT, what))


def expect(cond, what):
    if not cond:
        raise Failed(what)


class FakeSession:
    """A Claude Code inbox socket that keeps what the notifier sends it."""

    def __init__(self, path):
        self.got = b""
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(8)
        self.sock.setblocking(False)

    def drain(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return self.got
            conn.settimeout(5)
            with conn:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    self.got += chunk


def server_url(tmp):
    def find():
        for ln in (tmp / "serve.out").read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("Crosstalk is serving") and " on http://" in ln:
                return ln.rsplit(" on ", 1)[1].strip()
        return None
    return wait_for("the server's serving line", find)


def log_rows(data):
    rows = []
    for path in sorted((data / "log").glob("*.jsonl")):
        rows += [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return rows


def check(tmp, socks):
    data = tmp / "data"
    tokens = json.loads((tmp / "secrets.json").read_text(encoding="utf-8"))
    url = server_url(tmp)
    steps = []

    def step(name, fn):
        fn()
        steps.append(name)
        print("ok  %s" % name, flush=True)

    def health():
        status, body = http(url + "/api/health")
        expect(status == 200 and body.get("ok") is True, "health: %s %s" % (status, body))
    step("GET /api/health", health)

    def reads():
        for path, want in (("/api/snapshot", 200), ("/api/me", 200), ("/api/palette", 200),
                           ("/api/queue", 200), ("/api/threads", 200), ("/api/vitals", 200),
                           ("/api/thread/" + ASK_ID, 200), ("/api/section/agreements", 200),
                           ("/api/messages", 200), ("/api/read-state", 403), ("/api/nope", 404)):
            status, _body = http(url + path)
            expect(status == want, "GET %s answered %d, want %d" % (path, status, want))
        _s, snap = http(url + "/api/snapshot")
        expect(sorted(snap["roster"]) == ["alpha", "beta", "owner", "relay-bot"],
               "snapshot roster: %s" % sorted(snap["roster"]))
        expect(http(url + "/api/me")[1]["viewer"] == "viewer", "/api/me is not the viewer")
        expect(http(url + "/api/palette")[1]["pairs"], "/api/palette has no pairs")
        for path, mark in (("/", b"<title>Crosstalk</title>"), ("/v2/", b'<div id="root"')):
            status, body, got = http(url + path, raw=True)
            expect(status == 200 and got.get("Content-Type", "").startswith("text/html")
                   and mark in body, "GET %s: %d %s" % (path, status, got.get("Content-Type")))
    step("GET routes as a viewer, / and /v2/", reads)

    def events():
        req = urllib.request.Request(url + "/api/events")
        with urllib.request.urlopen(req, timeout=10) as resp:
            expect(resp.headers.get("Content-Type", "").startswith("text/event-stream"),
                   "/api/events content type")
            for _ in range(20):
                if resp.readline().strip() == b"event: ready":
                    return
        raise Failed("/api/events sent no ready event")
    step("GET /api/events sends ready", events)

    def granted():
        def grant():
            return next((r for r in log_rows(data) if r["from"] == "relay-bot"
                         and r["data"].get("replyTo") == ASK_ID
                         and str(r["data"].get("summary", "")).startswith("GRANTED")), None)
        wait_for("the auto-granter's GRANTED for " + ASK_ID, grant)
        queue = json.loads(relay(data, "queue").stdout)
        expect([w["id"] for w in queue["waiting"] if w.get("granted")] == [ASK_ID],
               "relay.py queue does not show the grant: %s" % queue["waiting"])
        status, body = http(url + "/api/queue")
        expect(status == 200 and body["waiting"][0].get("granted"), "/api/queue has no grant")
    step("auto-granter posts GRANTED; queue shows it", granted)

    fake = FakeSession(socks / "smoke.sock")

    def live_register():
        env = dict(os.environ, CLAUDE_CODE_MESSAGING_SOCKET=str(socks / "smoke.sock"),
                   CLAUDE_CODE_MESSAGING_TOKEN="smoke-session-token")
        relay(data, "live", "register", "beta", stdin=json.dumps({"session_id": "smoke-1"}), env=env)
        expect("smoke-1" in relay(data, "live", "status").stdout, "live status lacks the session")
        wait_for("beta in the snapshot's live block",
                 lambda: http(url + "/api/snapshot")[1]["live"].get("beta"))
    step("live register; the server sees the session", live_register)

    cookie = {}

    def login():
        status, _b, got = http(url + "/api/login", "POST", {"pin": "000000"}, raw=True)
        expect(status == 401, "a wrong PIN answered %d" % status)
        status, _b, got = http(url + "/api/login", "POST", {"pin": PIN}, raw=True)
        expect(status == 200 and "Set-Cookie" in got, "the set-pin PIN answered %d" % status)
        cookie["Cookie"] = got["Set-Cookie"].split(";")[0]
        expect(http(url + "/api/me", headers=cookie)[1]["viewer"] == "owner", "the cookie is not the owner's")
        status, _b = http(url + "/api/read-state", headers=cookie)
        expect(status == 200, "owner GET /api/read-state answered %d" % status)
    step("POST /api/login with the set-pin PIN", login)

    def owner_notice():
        status, body = http(url + "/api/append", "POST",
                            {"from": "owner", "op": "set", "collection": "messages", "id": "owner-smoke-1",
                             "data": {"from": "owner", "kind": "owner", "summary": "smoke: please look"}},
                            cookie)
        expect(status == 201 and body.get("via") == "page", "owner append: %s %s" % (status, body))
        wait_for("the live notice on the session socket", lambda: b"owner-smoke-1" in fake.drain())
        last = wait_for("a told status", lambda: http(url + "/api/snapshot")[1]["live"]
                        .get("beta", {}).get("last"))
        expect(last.get("status") == "told", "live status: %s" % last)
        expect(b"smoke-session-token" not in json.dumps(http(url + "/api/snapshot", headers=cookie)[1])
               .encode("utf-8"), "the session token shows in the snapshot")
    step("owner HTTP append reaches the live session", owner_notice)

    def appends():
        status, body = http(url + "/api/append", "POST",
                            {"from": "alpha", "op": "set", "collection": "messages", "id": "alpha-smoke-http",
                             "data": {"from": "alpha", "kind": "request", "summary": "smoke over HTTP",
                                      "to": ["beta"]}},
                            {"Authorization": "Bearer " + tokens["alpha"]})
        expect(status == 201, "token append: %s %s" % (status, body))
        status, _b = http(url + "/api/append", "POST",
                          {"from": "beta", "op": "set", "collection": "messages", "id": "beta-smoke-x",
                           "data": {"from": "beta", "kind": "reply", "summary": "x"}},
                          {"Authorization": "Bearer " + tokens["alpha"]})
        expect(status == 403, "a token wrote as another participant: %d" % status)
        seq = relay(data, "append", "--from", "beta", "--op", "set", "--collection", "messages",
                    "--id", "beta-smoke-cli", "--data", json.dumps(
                        {"from": "beta", "kind": "request", "summary": "smoke on the CLI",
                         "to": ["alpha"]})).stdout.strip()
        expect(seq.isdigit(), "relay.py append printed %r" % seq)
        wait_for("both appends in /api/messages",
                 lambda: {"alpha-smoke-http", "beta-smoke-cli"} <= set(http(url + "/api/messages")[1]["docs"]))
    step("append through the CLI and through HTTP", appends)

    def body_checks():
        host, port = url.rsplit("/", 1)[1].rsplit(":", 1)
        req = urllib.request.Request(url + "/api/append", data=b"null", method="POST",
                                     headers={"Authorization": "Bearer " + tokens["alpha"],
                                              "Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            raise Failed("a null body was taken")
        except urllib.error.HTTPError as e:
            expect(e.code == 400, "a null body answered %d" % e.code)
        second = b"GET /api/me HTTP/1.1\r\nHost: x\r\n\r\n"
        out = b""
        with socket.create_connection((host, int(port)), timeout=10) as s:
            s.sendall(b"POST /api/nowhere HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n%s"
                      % (len(second), second))
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                out += chunk
        expect(out.count(b"HTTP/1.1 ") == 1 and out.startswith(b"HTTP/1.1 404 "),
               "an unread body was read as a second request: %r" % out[:200])
    step("a null body is 400; an unread body closes the connection", body_checks)

    def load():
        # Each append changes the log, so each read after it may rebuild the snapshot.
        bad = []
        start = threading.Barrier(21, timeout=WAIT)

        def reader(n):
            try:
                start.wait()
            except threading.BrokenBarrierError:
                bad.append("reader %d: the start barrier broke" % n)
                return
            for path in ("/api/snapshot", "/api/threads", "/api/health"):
                began = time.monotonic()
                try:
                    status = http(url + path, timeout=LOAD_LIMIT)[0]
                except OSError as e:
                    status = repr(e)
                took = time.monotonic() - began
                if status != 200 or took > LOAD_LIMIT:
                    bad.append("reader %d %s: %s in %.1f s" % (n, path, status, took))

        readers = [threading.Thread(target=reader, args=(n,), daemon=True) for n in range(20)]
        for t in readers:
            t.start()
        try:
            start.wait()
        except threading.BrokenBarrierError:
            raise Failed("the readers did not all start within %ds" % WAIT)
        for i in range(20):
            status, body = http(url + "/api/append", "POST",
                                {"from": "alpha", "op": "set", "collection": "messages",
                                 "id": "alpha-smoke-load-%d" % i,
                                 "data": {"from": "alpha", "kind": "reply", "summary": "load %d" % i}},
                                {"Authorization": "Bearer " + tokens["alpha"]})
            expect(status == 201, "load append %d: %s %s" % (i, status, body))
        for t in readers:
            t.join(WAIT)
        expect(not any(t.is_alive() for t in readers), "a reader still runs after %ds" % WAIT)
        expect(not bad, "readers under load: %s" % "; ".join(bad[:5]))
    step("20 readers while 20 appends go in, each read 200 within %d s" % LOAD_LIMIT, load)

    def commands():
        out = relay(data, "inbox", "alpha").stdout
        expect("beta-smoke-cli" in out, "inbox alpha lacks the CLI request")
        out = relay(data, "inbox", "beta").stdout
        expect("alpha-smoke-http" in out, "inbox beta lacks the HTTP request")
        out = relay(data, "check").stdout
        expect(out.startswith("ok: "), "check: %r" % out)
        state = json.loads(relay(data, "state").stdout)
        expect(isinstance(state, dict), "state is not a JSON object")
        expect(relay(data, "queue", "--next").returncode == 0, "queue --next failed")
    step("inbox, check, state, queue on the command line", commands)

    fake.sock.close()
    return steps


def main(argv):
    if len(argv) < 2 or argv[0] not in ("fixture", "check"):
        print("usage: smoke.py fixture TMP | smoke.py check TMP SOCKS", file=sys.stderr)
        return 2
    tmp = Path(argv[1])
    try:
        if argv[0] == "fixture":
            fixture(tmp)
        else:
            check(tmp, Path(argv[2]))
    except (Failed, OSError, ValueError, KeyError, TypeError) as e:
        print("FAIL %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
