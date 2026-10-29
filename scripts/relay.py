#!/usr/bin/env python3
"""Command line for the Crosstalk log: check, append, import, pull, pending, restore, sent, token, onboard, kit, serve, mcp, urgent."""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from crosstalk.consults import adopt_agreement, post_consult, post_reply  # noqa: E402
from crosstalk.courier import pending, restore, sent  # noqa: E402
from crosstalk.exporter import export_page  # noqa: E402
from crosstalk.importer import import_snapshots, merge_snapshots  # noqa: E402
from crosstalk import live as live_mod  # noqa: E402
from crosstalk import mcp as mcp_mod  # noqa: E402
from crosstalk import urgent as urgent_mod  # noqa: E402
from crosstalk.logs import append_line, check_all, message_problems  # noqa: E402
from crosstalk.onboard import onboard, regenerate_kit  # noqa: E402
from crosstalk.ownerpin import clear_pin, set_pin  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.posters import claim_file, land_sync, post_incident, release_file  # noqa: E402
from crosstalk.pull import pull  # noqa: E402
from crosstalk.replay import enrich_messages, load_all_lines, replay  # noqa: E402
from crosstalk.reports import inbox_report, load_docs  # noqa: E402
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


def cmd_inbox(args):
    """Print the open requests addressed to a participant as JSON."""
    roster = load_roster(ROOT / "participants.json")
    if args.pid not in roster.participants:
        err("unknown participant %r" % args.pid)
        return 1
    docs, fatal = load_docs(ROOT, roster)
    if fatal:
        for p in fatal:
            err("%s:%d: %s" % (p.file, p.line, p.message))
        return 1
    out = inbox_report(docs, args.pid, roster, include_answered=args.all,
                       now_iso=getattr(args, "now", None))
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
    serve(ROOT, host=args.host, port=args.port, no_pin=args.no_pin, notify=not args.no_notify)
    return 0


def cmd_live(args):
    """live register|unregister|status|hooks. register/unregister never fail a session."""
    if args.live_cmd == "register":
        try:
            live_mod.register(ROOT, args.pid, os.environ, sys.stdin.read(), time.time())
        except Exception as e:  # a broken setup must never block a session
            err("live: register failed: %s" % e.__class__.__name__)
        return 0
    if args.live_cmd == "unregister":
        try:
            live_mod.unregister(args.pid, sys.stdin.read())
        except Exception as e:
            err("live: unregister failed: %s" % e.__class__.__name__)
        return 0
    if args.live_cmd == "hooks":
        print(live_mod.hooks_json(ROOT, args.pid))
        return 0
    regs, problems = live_mod.load_registrations(ROOT)
    for r in regs:
        print("%s  %s  registered %s" % (r.pid, r.session_id, r.registered))
    for p in problems:
        err("live: ignored " + p)
    return 0


def cmd_mcp(args):
    """Run the stdio MCP server as one participant until stdin closes."""
    return mcp_mod.run(ROOT, args.as_)


def cmd_set_pin(args):
    """Set (or with --clear, remove) the owner's permanent six-digit login PIN."""
    if args.clear:
        print("removed" if clear_pin(ROOT) else "no permanent PIN was set")
        return 0
    pin = args.pin
    if pin is None:
        import getpass
        pin = getpass.getpass("New six-digit PIN: ")
    try:
        path = set_pin(ROOT, pin.strip())
    except ValueError as e:
        err(str(e))
        return 1
    print("permanent PIN saved (hashed) in %s" % path.name)
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
                         color=args.color, color_dark=args.color_dark, harness=args.harness)
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


def cmd_claim(args):
    try:
        ln = claim_file(ROOT, args.from_, args.path, repo=args.repo,
                        holder=args.holder, note=args.note)
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_release(args):
    try:
        ln = release_file(ROOT, args.from_, path=args.path, lock_id=args.lock_id)
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_land_sync(args):
    try:
        res = land_sync(ROOT, args.from_, args.commit, args.summary,
                        files=args.files, adopter=args.adopter,
                        agreement_id=args.agreement_id, body=args.body)
    except ValueError as e:
        err(str(e))
        return 1
    print(json.dumps(res, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def cmd_incident(args):
    try:
        ln = post_incident(ROOT, args.from_, args.slug, summary=args.summary,
                           body=args.body, resolve=args.resolve,
                           fix=args.fix, fix_refs=args.fix_refs)
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def _flatten(groups):
    if not groups:
        return None
    out = []
    for g in groups:
        if isinstance(g, list):
            out.extend(g)
        else:
            out.append(g)
    return out or None


def cmd_reply(args):
    try:
        ln = post_reply(
            ROOT,
            args.from_,
            args.to_id,
            args.summary,
            body=args.body,
            refs=_flatten(args.refs),
            msg_id=args.id,
        )
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_consult(args):
    try:
        ln = post_consult(
            ROOT,
            args.from_,
            args.summary,
            body=args.body,
            refs=_flatten(args.refs),
            to=_flatten(args.to),
            msg_id=args.id,
        )
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_adopt(args):
    try:
        res = adopt_agreement(
            ROOT,
            args.from_,
            args.agreement,
            args.commit,
            summary=args.summary,
        )
    except ValueError as e:
        err(str(e))
        return 1
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
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
    o.add_argument("--harness", help="the tool its sessions run in: claude-code, codex, gemini-cli, copilot, "
                                     "cursor, hermes, pi, or another lowercase id")
    o.set_defaults(fn=cmd_onboard)

    k = sub.add_parser("kit", help="regenerate a participant's kit")
    k.add_argument("pid", metavar="PID")
    k.set_defaults(fn=cmd_kit)

    cl = sub.add_parser("claim", help="claim a shared file lock; prints the lock doc id")
    cl.add_argument("--from", dest="from_", required=True)
    cl.add_argument("--path", required=True)
    cl.add_argument("--repo", help="repo override (owner/repo)")
    cl.add_argument("--holder", help="agent holding the lock")
    cl.add_argument("--note", help="note describing the claim")
    cl.set_defaults(fn=cmd_claim)

    rl = sub.add_parser("release", help="release a shared file lock; prints the lock doc id")
    rl.add_argument("--from", dest="from_", required=True)
    rl_g = rl.add_mutually_exclusive_group(required=True)
    rl_g.add_argument("--path", help="path of the locked file")
    rl_g.add_argument("--id", dest="lock_id", help="lock doc id")
    rl.set_defaults(fn=cmd_release)

    ls = sub.add_parser("land-sync", help="announce a landed shared commit, update agreement, and release locks")
    ls.add_argument("--from", dest="from_", required=True)
    ls.add_argument("--commit", required=True)
    ls.add_argument("--summary", required=True)
    ls.add_argument("--files", action="extend", nargs="+", metavar="PATH")
    ls.add_argument("--adopter", help="adopter participant id (default: all)")
    ls.add_argument("--agreement-id", dest="agreement_id", help="existing agreement doc id")
    ls.add_argument("--body", help="optional sync message body")
    ls.set_defaults(fn=cmd_land_sync)

    inc = sub.add_parser("incident", help="open or resolve a build/host incident; prints the incident doc id")
    inc.add_argument("--from", dest="from_", required=True)
    inc.add_argument("--slug", required=True)
    inc.add_argument("--summary", help="incident summary (required when opening)")
    inc.add_argument("--body", help="optional incident body")
    inc.add_argument("--resolve", action="store_true", help="mark the incident resolved")
    inc.add_argument("--fix", help="how the incident was fixed (with --resolve)")
    inc.add_argument("--fix-ref", dest="fix_refs", action="extend", nargs="+", metavar="REF",
                     help="commit or PR ref for the fix (with --resolve)")
    inc.set_defaults(fn=cmd_incident)

    rp = sub.add_parser("reply", help="reply to a message; prints the new message id")
    rp.add_argument("--from", dest="from_", required=True)
    rp.add_argument("--to-id", dest="to_id", required=True)
    rp.add_argument("--summary", required=True)
    rp.add_argument("--body")
    rp.add_argument("--ref", "--refs", dest="refs", action="append", nargs="+")
    rp.add_argument("--id")
    rp.set_defaults(fn=cmd_reply)

    cs = sub.add_parser("consult", help="post a peer consultation request; prints the new message id")
    cs.add_argument("--from", dest="from_", required=True)
    cs.add_argument("--summary", required=True)
    cs.add_argument("--body")
    cs.add_argument("--ref", "--refs", dest="refs", action="append", nargs="+")
    cs.add_argument("--to", dest="to", action="append", nargs="+")
    cs.add_argument("--id")
    cs.set_defaults(fn=cmd_consult)

    ad = sub.add_parser("adopt", help="record adoption of an agreement and post a sync message")
    ad.add_argument("--from", dest="from_", required=True)
    ad.add_argument("--agreement", "--agreement-id", dest="agreement", required=True)
    ad.add_argument("--commit", required=True)
    ad.add_argument("--summary")
    ad.set_defaults(fn=cmd_adopt)

    sp = sub.add_parser("set-pin", help="set the owner's permanent six-digit login PIN (stored hashed)")
    sp.add_argument("pin", nargs="?", metavar="PIN", help="six digits; prompts when left out")
    sp.add_argument("--clear", action="store_true", help="remove the permanent PIN")
    sp.set_defaults(fn=cmd_set_pin)
    lv = sub.add_parser("live", help="register running Claude Code sessions for live messages")
    lsub = lv.add_subparsers(dest="live_cmd", required=True)
    for name, h in (("register", "SessionStart hook: record this session's inbox socket"),
                    ("unregister", "SessionEnd hook: forget this session"),
                    ("hooks", "print the hook JSON to paste into settings.local.json")):
        lp = lsub.add_parser(name, help=h)
        lp.add_argument("pid", metavar="PARTICIPANT")
    lsub.add_parser("status", help="list live registrations (never the token)")
    lv.set_defaults(fn=cmd_live)
    mc = sub.add_parser("mcp", help="run a stdio MCP server that acts as one participant")
    mc.add_argument("--as", dest="as_", required=True, metavar="PID",
                    help="the participant every tool call reads and writes as")
    mc.set_defaults(fn=cmd_mcp)
    # Handled in main() before argparse, so it can never exit non-zero; listed for --help.
    sub.add_parser("urgent", help="harness hook: print an urgent-item note for a Codex "
                   "session (urgent --as PID --hook codex; reads the event on stdin)")
    sv = sub.add_parser("serve", help="run the page host and API server")
    sv.add_argument("--port", type=int, default=8484,
                    help="port to bind (default 8484; 0 = ephemeral)")
    sv.add_argument("--host", default="127.0.0.1",
                    help="address to bind (default 127.0.0.1)")
    sv.add_argument("--no-pin", action="store_true",
                    help="do not print a startup login PIN")
    sv.add_argument("--no-notify", action="store_true",
                    help="do not tell live Claude Code sessions about new inbox items")
    sv.set_defaults(fn=cmd_serve)
    return ap


def cmd_urgent(argv) -> int:
    """`urgent --as <pid> --hook codex`: a harness hook; reads the event JSON on stdin.

    Prints the hook's JSON only when an urgent item is new (crosstalk/urgent.py).
    It always exits 0 and prints nothing on any error, a bad argument included,
    because an exit status of 2 or text on stderr would be fed back to the model.
    """
    try:
        ap = argparse.ArgumentParser(prog="relay.py urgent", add_help=False)
        ap.add_argument("--as", dest="as_", required=True)
        ap.add_argument("--hook", required=True)
        with open(os.devnull, "w") as devnull:
            saved, sys.stderr = sys.stderr, devnull
            try:
                args = ap.parse_args(argv)
            finally:
                sys.stderr = saved
        stdin_text = sys.stdin.read()
        out = urgent_mod.hook_output(ROOT, args.as_, args.hook, stdin_text,
                                     urgent_mod.state_path_from_env())
        if out:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
    except BaseException:  # SystemExit from argparse too: the hook never fails
        pass
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["urgent"]:
        return cmd_urgent(argv[1:])
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ValueError as e:  # bad participants.json
        err(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
