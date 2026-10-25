#!/usr/bin/env python3
"""Command line for the Crosstalk log: check, append, import, pull, pending, restore, sent, token, onboard, kit, serve."""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from crosstalk.courier import pending, restore, sent  # noqa: E402
from crosstalk.exporter import export_page  # noqa: E402
from crosstalk.importer import import_snapshots, merge_snapshots  # noqa: E402
from crosstalk.logs import append_line, check_all, message_problems  # noqa: E402
from crosstalk.onboard import onboard, regenerate_kit  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.pull import pull  # noqa: E402
from crosstalk.replay import enrich_messages, load_all_lines, replay  # noqa: E402
from crosstalk.requests import all_requests_for, inbox_for  # noqa: E402
from crosstalk.serve import serve  # noqa: E402
from crosstalk.service import SERVICE  # noqa: E402
from crosstalk.tokens import TOKEN_FILE, Tokens, load_tokens  # noqa: E402


MISSING_SHOWN = 10


def err(msg):
    print(msg, file=sys.stderr)


def run_check(stream):
    """Print problems to stream; returns (fatal_count, problems)."""
    roster = load_roster(ROOT / "participants.json")
    problems = check_all(ROOT, roster)
    fatal = 0
    for p in problems:
        if p.fatal:
            fatal += 1
        print("%s%s:%d: %s" % ("" if p.fatal else "warning: ", p.file, p.line, p.message),
              file=stream)
    return fatal, problems


def cmd_check(args):
    fatal, _ = run_check(sys.stdout)
    if fatal:
        return 1
    files = sorted((ROOT / "log").glob("*.jsonl"))
    n = 0
    for f in files:
        with open(str(f), encoding="utf-8") as fh:
            n += sum(1 for ln in fh if ln.strip())
    print("ok: %d lines in %d files" % (n, len(files)))
    return 0


def cmd_append(args):
    try:
        if args.data is not None:
            text = args.data
        else:
            text = Path(args.data_file).read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, ValueError) as e:
        err("bad data: %s" % e)
        return 1
    if not isinstance(data, dict):
        err("bad data: expected a JSON object")
        return 1
    roster = load_roster(ROOT / "participants.json")
    for w in message_problems(args.collection, args.op, data, roster)[1]:
        err("warning: " + w)
    try:
        ln = append_line(ROOT, args.from_, args.op, args.collection, args.id, data,
                         roster=roster)
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.seq)
    return 0


def cmd_read(args):
    """Print the replay as JSON: the shared memory for any session that speaks JSON."""
    roster = load_roster(ROOT / "participants.json")
    problems = check_all(ROOT, roster)
    fatal = [p for p in problems if p.fatal]
    if fatal:
        for p in fatal:
            err("%s:%d: %s" % (p.file, p.line, p.message))
        return 1
    lines = load_all_lines(ROOT, roster)
    docs = enrich_messages(replay(lines), lines)
    collections = {}  # type: Dict[str, Dict[str, dict]]
    for (coll, did), fields in sorted(docs.items()):
        collections.setdefault(coll, {})[did] = fields
    if args.collection:
        wanted = set(args.collection)
        collections = {c: v for c, v in collections.items() if c in wanted}
    print(json.dumps({"collections": collections, "service": SERVICE},
                     ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def _trust(pid, roster):
    """The trust of a participant id: approved, quarantined, or unknown when absent."""
    p = roster.participants.get(pid)
    return p.trust if p is not None else "unknown"


def cmd_inbox(args):
    """Print the open requests addressed to a participant as JSON."""
    roster = load_roster(ROOT / "participants.json")
    if args.pid not in roster.participants:
        err("unknown participant %r" % args.pid)
        return 1
    problems = check_all(ROOT, roster)
    fatal = [p for p in problems if p.fatal]
    if fatal:
        for p in fatal:
            err("%s:%d: %s" % (p.file, p.line, p.message))
        return 1
    lines = load_all_lines(ROOT, roster)
    docs = enrich_messages(replay(lines), lines)
    requests = all_requests_for(docs, args.pid) if args.all else inbox_for(docs, args.pid)
    out = {
        "pid": args.pid,
        "roster": {pid: {"trust": p.trust} for pid, p in roster.participants.items()},
        "requests": [
            {
                "request": dict(doc, id=did),
                "trust": _trust(doc.get("from"), roster),
                "replies": [dict(r, trust=_trust(r.get("from"), roster)) for r in replies],
            }
            for (did, doc, replies) in requests
        ],
        "service": SERVICE,
    }
    print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def cmd_export_page(args):
    try:
        info = export_page(ROOT, Path(args.file))
    except (RuntimeError, OSError, ValueError) as e:
        err(str(e))
        return 1
    print("%s: %d documents (commit %s)" % (info["path"], info["documents"], info["commit"]))
    return 0


def cmd_import(args):
    dirs = [Path(d) for d in args.dirs]
    try:
        if args.merge:
            res = merge_snapshots(ROOT, dirs)
            counts, skipped = res["counts"], res["skipped"]
        else:
            counts, skipped = import_snapshots(ROOT, dirs), []
    except RuntimeError as e:
        err(str(e))
        return 1
    for pid in sorted(counts):
        print("%s: %d" % (pid, counts[pid]))
    for name in skipped:
        err("warning: skipped %s (already in the log)" % name)
    return 0


def cmd_pull(args):
    try:
        res = pull(ROOT, Path(args.export_dir))
    except RuntimeError as e:
        err(str(e))
        return 1
    print("owner lines: %d, approvals: %s, skipped: %s, versions: %d"
          % (res.owner_lines, ",".join(res.approvals) or "-", ",".join(res.skipped) or "-",
             len(res.versions)))
    for s in res.skipped:
        err("warning: skipped %s (not in any log)" % s)
    for name in res.missing_listings:
        coll = name[:-len(".listing.txt")]
        n = sum(1 for m in res.missing_versions if m.startswith(coll + "/"))
        err("warning: no listing %s for %s/ (%d docs)" % (name, coll, n))
    for m in res.missing_versions[:MISSING_SHOWN]:
        err("warning: no version for %s" % m)
    if len(res.missing_versions) > MISSING_SHOWN:
        err("... and %d more" % (len(res.missing_versions) - MISSING_SHOWN))
    return 0


def cmd_batches(args):
    fatal, _ = run_check(sys.stderr)
    fn = pending if args.cmd == "pending" else restore
    for path in fn(ROOT, Path(args.out_dir)):
        print(path)
    return 1 if fatal else 0


def cmd_sent(args):
    try:
        sent(ROOT, Path(args.batch))
    except (RuntimeError, OSError, ValueError) as e:
        err(str(e))
        return 1
    return 0


def cmd_serve(args):
    """Run the page host and API server in the foreground until killed."""
    serve(ROOT, host=args.host, port=args.port, no_pin=args.no_pin)
    return 0


def cmd_token(args):
    """Mint an API token for a participant and print it once.

    Without `--new`, refuses when the participant already has a key; `--new`
    rotates the key (mint a fresh one); `--kit` also regenerates kits/<pid>.md
    with the new key.
    """
    try:
        roster = load_roster(ROOT / "participants.json")
    except ValueError as e:
        err(str(e))
        return 1
    if args.pid not in roster.participants:
        err("unknown participant %r" % args.pid)
        return 1
    if args.kit:
        try:
            result = regenerate_kit(ROOT, args.pid)
        except ValueError as e:
            err(str(e))
            return 1
        print(result["token"])
        print(result["kit_path"])
        return 0
    store = load_tokens(ROOT / TOKEN_FILE) or Tokens(ROOT / TOKEN_FILE)
    if args.pid in store.entries and not args.new:
        err("already has a key; use --new to rotate")
        return 1
    try:
        token = store.mint(args.pid)
    except OSError as e:
        err("cannot write %s: %s" % (TOKEN_FILE, e))
        return 1
    print(token)
    return 0


def cmd_onboard(args):
    """Add a participant: roster entry, minted key, kit. Prints the kit path and the token once."""
    try:
        result = onboard(ROOT, args.pid, args.name, args.kind, repo=args.repo,
                         check_in_min=args.check_in_min, parent=args.parent,
                         color=args.color, color_dark=args.color_dark)
    except ValueError as e:
        err(str(e))
        return 1
    print(result["kit_path"])
    print(result["token"])
    return 0


def cmd_kit(args):
    """Regenerate kits/<pid>.md with a fresh key; prints the kit path and the token once."""
    try:
        result = regenerate_kit(ROOT, args.pid)
    except ValueError as e:
        err(str(e))
        return 1
    print(result["kit_path"])
    print(result["token"])
    return 0


def build_parser():
    ap = argparse.ArgumentParser(prog="relay.py", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate every log").set_defaults(fn=cmd_check)

    a = sub.add_parser("append", help="append one line to a log; prints the new seq")
    a.add_argument("--from", dest="from_", required=True)
    a.add_argument("--op", required=True, choices=["set", "update"])
    a.add_argument("--collection", required=True)
    a.add_argument("--id", required=True)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--data", help="JSON object")
    g.add_argument("--data-file", help="path to a JSON object file")
    a.set_defaults(fn=cmd_append)

    i = sub.add_parser("import", help="import page snapshots into an empty log")
    i.add_argument("dirs", nargs="+", metavar="DIR")
    i.add_argument("--merge", action="store_true",
                   help="merge into a live log: skip the rows it already has")
    i.set_defaults(fn=cmd_import)

    x = sub.add_parser("export-page", help="write one self-contained HTML page with the log inlined")
    x.add_argument("file", metavar="FILE", help="the HTML file to write")
    x.set_defaults(fn=cmd_export_page)

    rd = sub.add_parser("read", help="print the replay as JSON")
    rd.add_argument("--collection", action="append", metavar="C",
                    help="only this collection (may be given more than once)")
    rd.set_defaults(fn=cmd_read)

    ib = sub.add_parser("inbox", help="print the open requests addressed to a participant")
    ib.add_argument("pid", metavar="PID")
    ib.add_argument("--all", action="store_true", help="include answered requests")
    ib.set_defaults(fn=cmd_inbox)

    p = sub.add_parser("pull", help="record the owner's page changes")
    p.add_argument("export_dir", metavar="EXPORT_DIR")
    p.set_defaults(fn=cmd_pull)

    for name in ("pending", "restore"):
        b = sub.add_parser(name, help="write batch files; prints their paths")
        b.add_argument("out_dir", metavar="OUT_DIR")
        b.set_defaults(fn=cmd_batches)

    s = sub.add_parser("sent", help="record that a batch went through")
    s.add_argument("batch", metavar="BATCH")
    s.set_defaults(fn=cmd_sent)

    t = sub.add_parser("token", help="mint an API token for a participant; prints it once")
    t.add_argument("pid", metavar="PID")
    t.add_argument("--new", action="store_true", help="rotate the key (mint a fresh one)")
    t.add_argument("--kit", action="store_true",
                   help="regenerate kits/<pid>.md with the new key")
    t.set_defaults(fn=cmd_token)

    o = sub.add_parser("onboard", help="add a participant: roster entry, key, kit")
    o.add_argument("pid", metavar="PID")
    o.add_argument("--name", required=True, help="the participant's display name")
    o.add_argument("--kind", required=True, help="project, agent, reviewer, researcher, tool or person")
    o.add_argument("--repo", help="the participant's repo (owner/repo)")
    o.add_argument("--checkInMin", dest="check_in_min", type=int,
                   help="check-in interval in minutes")
    o.add_argument("--parent", help="the parent participant id (agents need one)")
    o.add_argument("--color", help="light-theme color, #rrggbb")
    o.add_argument("--colorDark", dest="color_dark", help="dark-theme color, #rrggbb")
    o.set_defaults(fn=cmd_onboard)

    k = sub.add_parser("kit", help="regenerate a participant's kit")
    k.add_argument("pid", metavar="PID")
    k.set_defaults(fn=cmd_kit)

    sv = sub.add_parser("serve", help="run the page host and API server")
    sv.add_argument("--port", type=int, default=8484,
                    help="port to bind (default 8484; 0 = ephemeral)")
    sv.add_argument("--host", default="127.0.0.1",
                    help="address to bind (default 127.0.0.1)")
    sv.add_argument("--no-pin", action="store_true",
                    help="do not print a startup login PIN")
    sv.set_defaults(fn=cmd_serve)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as e:  # bad participants.json
        err(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
