#!/usr/bin/env python3
"""Command line for the Crosstalk log: check, append, import, pull, pending, restore, sent, token."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from crosstalk.courier import pending, restore, sent  # noqa: E402
from crosstalk.importer import import_snapshots  # noqa: E402
from crosstalk.logs import append_line, check_all, message_problems  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.pull import pull  # noqa: E402
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


def cmd_import(args):
    try:
        counts = import_snapshots(ROOT, [Path(d) for d in args.dirs])
    except RuntimeError as e:
        err(str(e))
        return 1
    for pid in sorted(counts):
        print("%s: %d" % (pid, counts[pid]))
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


def cmd_token(args):
    """Mint an API token for a participant and print it once."""
    try:
        roster = load_roster(ROOT / "participants.json")
    except ValueError as e:
        err(str(e))
        return 1
    if args.pid not in roster.participants:
        err("unknown participant %r" % args.pid)
        return 1
    store = load_tokens(ROOT / TOKEN_FILE) or Tokens(ROOT / TOKEN_FILE)
    try:
        token = store.mint(args.pid)
    except OSError as e:
        err("cannot write %s: %s" % (TOKEN_FILE, e))
        return 1
    print(token)
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
    i.set_defaults(fn=cmd_import)

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
    t.set_defaults(fn=cmd_token)
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
