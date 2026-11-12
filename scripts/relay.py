#!/usr/bin/env python3
"""Command line and server for Crosstalk: read and append to the log, onboard participants, run the page and API server. Each command has --help."""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict

CODE_ROOT = Path(__file__).resolve().parent.parent  # the code: scripts/, index.html, the git checkout
ROOT = CODE_ROOT  # the data: log/, participants.json, tokens.json, kits/; main() sets it
sys.path.insert(0, str(CODE_ROOT / "scripts"))

from crosstalk.consults import (  # noqa: E402
    ack_item, adopt_agreement, post_consult, post_question, post_reply, question_warning, withdraw_question)
from crosstalk import deploy as deploy_mod  # noqa: E402
from crosstalk.exporter import export_page  # noqa: E402
from crosstalk.landqueue import land_queue  # noqa: E402
from crosstalk import hermes as hermes_mod  # noqa: E402
from crosstalk import live as live_mod  # noqa: E402
from crosstalk import mcp as mcp_mod  # noqa: E402
from crosstalk import reviews as reviews_mod  # noqa: E402
from crosstalk import adoption as adoption_mod  # noqa: E402
from crosstalk import schedules as schedules_mod  # noqa: E402
from crosstalk import state as state_mod  # noqa: E402
from crosstalk import buildhost as buildhost_mod  # noqa: E402
from crosstalk import config as config_mod  # noqa: E402
from crosstalk import urgent as urgent_mod  # noqa: E402
from crosstalk.logs import append_line, check_all, message_problems  # noqa: E402
from crosstalk.onboard import onboard, regenerate_kit  # noqa: E402
from crosstalk.ownerpin import clear_pin, set_pin  # noqa: E402
from crosstalk.participants import load_roster  # noqa: E402
from crosstalk.posters import claim_file, land_sync, post_incident, release_file  # noqa: E402
from crosstalk.replay import enrich_messages, load_all_lines, replay  # noqa: E402
from crosstalk.requests import no_answer, speaker_fields  # noqa: E402
from crosstalk import inboxstate  # noqa: E402
from crosstalk.reports import (inbox_report, load_docs, load_docs_and_times, new_report,  # noqa: E402
                               questions_report)
from crosstalk.serve import serve  # noqa: E402
from crosstalk.service import service_block, service_for  # noqa: E402
from crosstalk.stalls import find_stalls  # noqa: E402
from crosstalk.tokens import TOKEN_FILE, Tokens, load_tokens  # noqa: E402


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
    if args.no_answer and not (args.collection == "messages" and args.op == "set"):
        err("--no-answer is for a new message")
        return 1
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
    if args.collection == "messages" and args.op == "set":
        for name, value in speaker_fields(args).items():
            data.setdefault(name, value)
        if args.no_answer:
            no_answer(data)
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


def _row_time(doc):
    """A row's time, whichever field it keeps it in; "" when it has none."""
    return str(doc.get("t") or doc.get("since") or "")


def _brief_line(label, doc_id, doc, t):
    """One line for a row: `relay.py read --brief` and `inbox --brief`."""
    summary = " ".join(str(doc.get("summary") or doc.get("title") or "").split())
    return "%s %s %s %s: %s" % (t[:16] or "-", label, doc.get("from", "?"), doc_id, summary[:200])


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
    if args.since:
        collections = {c: {i: d for i, d in v.items() if _row_time(d) >= (args.since or "")}
                       for c, v in collections.items()}
    if args.brief:
        rows = [(_row_time(d), c, i, d) for c, v in collections.items() for i, d in v.items()]
        for t, c, i, d in sorted(rows, key=lambda r: r[:3]):
            print(_brief_line(c, i, d, t))
        return 0
    print(json.dumps({"collections": collections, "service": service_block(CODE_ROOT, data_root=ROOT)},
                     ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def cmd_inbox(args):
    """Print the open requests addressed to a participant as JSON.

    `--new` cuts the answer at the participant's place, `--ack` then moves the
    place to `next` once the output is flushed, and `--ack-to` moves it alone.
    """
    roster = load_roster(ROOT / "participants.json")
    if args.pid not in roster.participants:
        err("unknown participant %r" % args.pid)
        return 1
    if args.ack_to is not None and (args.new or args.ack or args.all or args.brief):
        err("--ack-to goes alone, without --new, --ack, --all or --brief")
        return 1
    docs, times, fatal = load_docs_and_times(ROOT, roster)
    if fatal:
        for p in fatal:
            err("%s:%d: %s" % (p.file, p.line, p.message))
        return 1
    nxt = inboxstate.newest(times)
    if args.ack_to is not None:
        return _inbox_ack_to(args.pid, args.ack_to, nxt)
    out = inbox_report(docs, args.pid, roster, include_answered=args.all,
                       now_iso=getattr(args, "now", None), service=service_for(CODE_ROOT, ROOT))
    if args.new:
        out = new_report(out, times, inboxstate.load(ROOT).get(args.pid), nxt)
    try:
        _print_inbox(out, args)
        sys.stdout.flush()
    except BrokenPipeError:
        # Python flushes stdout again at exit; point it at devnull so that
        # flush does not fail too. The place stays where it was.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 1
    if args.ack and nxt is not None:
        inboxstate.move(ROOT, args.pid, nxt)
    return 0


def _print_inbox(out, args):
    if not args.brief:
        # `--new` is the check-in of every session, so it prints compact JSON;
        # the full view keeps the indented form it always had.
        layout = {"separators": (",", ":")} if args.new else {"indent": 2}
        print(json.dumps(out, ensure_ascii=False, sort_keys=True, **layout))
        return
    sections = (("owner_messages", "owner"), ("requests", "request"), ("thread_replies", "reply"))
    for key, label in sections:
        for item in out.get(key, []):
            doc = item.get("request") or item.get("message") or item
            line = _brief_line(label, doc.get("id", "?"), doc, _row_time(doc))
            print(("!" if item.get("needs_answer") else " ") + line if args.new else line)
    if args.new:
        for doc in out.get("agreements_to_adopt", []):
            print(" " + _brief_line("adopt", doc.get("id", "?"), doc, _row_time(doc)))
        print("still open %d, agreements open %d, stale locks %d" % (
            len(out["still_open"]) + out["still_open_more"], out["agreements_open"],
            len(out.get("stale_locks", []))))
    else:
        print("adopt %d, stale locks %d" % (len(out.get("agreements_to_adopt", [])),
                                            len(out.get("stale_locks", []))))


def _inbox_ack_to(pid, raw, nxt):
    """`inbox <pid> --ack-to PLACE`: move the place to a `next` read earlier."""
    try:
        place = inboxstate.clean_place(json.loads(raw))
    except (ValueError, RecursionError):
        place = None
    if place is None:
        err('--ack-to: not a place; give the JSON object {"t": ..., "seen": [...]} '
            "that inbox --new printed as next")
        return 1
    stored = inboxstate.load(ROOT).get(pid)
    if stored is not None and place["t"] < stored["t"]:
        err("--ack-to: %s is behind the stored place %s" % (place["t"], stored["t"]))
        return 1
    if nxt is None or place["t"] > nxt["t"]:
        err("--ack-to: %s is ahead of the newest row of the log (%s)"
            % (place["t"], nxt["t"] if nxt else "none"))
        return 1
    print(json.dumps(inboxstate.move(ROOT, pid, place), sort_keys=True))
    return 0


def cmd_stalls(args):
    """Print agents whose last transcript row is a tool call with no result."""
    ids = load_roster(ROOT / "participants.json").participants
    found = find_stalls(Path(args.root).expanduser(), args.minutes, time.time(), ids=ids)
    for s in found:
        print("%s %s %s: %s, %d min" % (s["project"], s["session"], s["agent"], s["tool"], s["minutes"]))
    print("%d stalled over %d min" % (len(found), args.minutes))
    return 0


def cmd_export_page(args):
    try:
        info = export_page(ROOT, Path(args.file), code_root=CODE_ROOT)
    except (RuntimeError, OSError, ValueError) as e:
        err(str(e))
        return 1
    print("%s: %d documents (commit %s)" % (info["path"], info["documents"], info["commit"]))
    return 0


def cmd_deploy(args):
    return deploy_mod.run(CODE_ROOT, args)


def cmd_serve(args):
    """Run the page host and API server in the foreground until killed."""
    serve(ROOT, host=args.host, port=args.port, no_pin=args.no_pin, notify=not args.no_notify,
          autogrant=not args.no_autogrant, code_root=CODE_ROOT)
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
        if args.harness == "hermes":
            print(hermes_mod.setup_text(ROOT, args.pid, code_root=CODE_ROOT), end="")
        else:
            print(live_mod.hooks_json(ROOT, args.pid, code_root=CODE_ROOT))
        return 0
    regs, problems = live_mod.load_registrations(ROOT)
    for r in regs:
        print("%s  %s  registered %s%s" % (r.pid, r.session_id, r.registered,
                                           "" if r.harness == live_mod.CLAUDE
                                           else "  (%s)" % r.harness))
    for p in problems:
        err("live: ignored " + p)
    return 0


def cmd_reviews(args):
    """reviews scan [--dry-run] [--state PATH] | reviews open: the Codex review scanner by hand."""
    docs, roster = urgent_mod.load_snapshot(ROOT)
    if args.reviews_cmd == "open":
        print(json.dumps(reviews_mod.open_overrides(docs, roster), indent=2, sort_keys=True))
        return 0
    state = Path(args.state) if args.state else reviews_mod.state_path_from_env()
    if state is None and not args.dry_run:
        err("reviews scan: no state file (CROSSTALK_REVIEWS_STATE is empty); pass --state PATH")
        return 2
    try:
        plan = reviews_mod.Scanner(ROOT, state).scan(docs, roster, dry_run=args.dry_run)
    except OSError as e:
        err("reviews scan: %s" % e)
        return 1
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


def cmd_coordinator(args):
    """Print the coordinator's participant id (the roster's `relay`); exit 1 when none is set."""
    pid = config_mod.coordinator(load_roster(ROOT / "participants.json"))
    if not pid:
        err("no coordinator: set \"relay\" in participants.json to a participant id")
        return 1
    print(pid)
    return 0


def cmd_buildhost(args):
    """buildhost probe | buildhost poll [--dry-run] [--state PATH]: the build host's state."""
    roster = load_roster(ROOT / "participants.json")
    if args.buildhost_cmd == "probe":
        host = args.host or config_mod.of(roster)["buildHost"]["ssh"]
        print(json.dumps(buildhost_mod.probe(host), indent=2, sort_keys=True))
        return 0
    state = Path(args.state) if args.state else buildhost_mod.state_path_from_env()
    try:
        plan = buildhost_mod.Poller(ROOT, state, host=args.host).poll(roster, dry_run=args.dry_run)
    except (OSError, ValueError) as e:
        err("buildhost poll: %s" % e)
        return 1
    print(json.dumps(plan, indent=2, sort_keys=True))
    if not args.state and not args.dry_run:
        _report_poll(state, roster)
    return 0


def _report_poll(state, roster):
    """Tell the Schedules tab the launchd poll runs, hourly: a log line every 2 minutes would be noise."""
    stamp = Path(str(state) + ".reported")
    now = datetime.now(timezone.utc)
    try:
        if stamp.exists() and now.timestamp() - stamp.stat().st_mtime < 3300:
            return
        schedules_mod.mark_ran(ROOT, config_mod.coordinator(roster), "buildhost-poll-launchd", "ok", note="hourly report of a 2-minute poll",
                               next_=(now + timedelta(minutes=65)).strftime("%Y-%m-%dT%H:%M:%SZ"), roster=roster)
        stamp.touch()
    except (OSError, ValueError) as e:
        err("buildhost poll: could not report the run: %s" % e)


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
            result = regenerate_kit(ROOT, args.pid, code_root=CODE_ROOT)
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
                         color=args.color, color_dark=args.color_dark, harness=args.harness,
                         code_root=CODE_ROOT)
    except ValueError as e:
        err(str(e))
        return 1
    print(result["kit_path"])
    print(result["token"])
    return 0


def cmd_kit(args):
    """Regenerate kits/<pid>.md with a fresh key; prints the kit path and the token once."""
    try:
        result = regenerate_kit(ROOT, args.pid, code_root=CODE_ROOT)
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
                        agreement_id=args.agreement_id, body=args.body,
                        speaker=speaker_fields(args))
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
            speaker=speaker_fields(args),
            no_answer=args.no_answer,
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
            speaker=speaker_fields(args),
            no_answer=args.no_answer,
        )
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_ask(args):
    """Post a question for the owner; prints the new message id."""
    if args.no_answer:
        err("a question to the owner needs an answer; ask takes no --no-answer")
        return 1
    recommend = None
    if args.recommend is not None:
        n = len(args.options or [])
        if not n:
            err("--recommend needs --option")
            return 1
        if not 1 <= args.recommend <= n:
            err("--recommend must be 1 to %d (1 is the first --option)" % n)
            return 1
        recommend = args.recommend - 1
    try:
        ln = post_question(
            ROOT,
            args.from_,
            args.summary,
            body=args.body,
            refs=_flatten(args.refs),
            msg_id=args.id,
            speaker=speaker_fields(args),
            options=args.options,
            recommend=recommend,
        )
    except ValueError as e:
        err(str(e))
        return 1
    warning = question_warning(ROOT, args.from_)
    if warning:
        err("warning: " + warning)
    print(ln.id)
    return 0


def cmd_questions(args):
    """Print the open questions for the owner as JSON (with --all, answered and withdrawn too)."""
    roster = load_roster(ROOT / "participants.json")
    docs, fatal = load_docs(ROOT, roster)
    if fatal:
        for p in fatal:
            err("%s:%d: %s" % (p.file, p.line, p.message))
        return 1
    out = questions_report(docs, roster, include_closed=args.all)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def cmd_queue(args):
    """Print the land queue of the build host as JSON; with --next, only the next project id."""
    roster = load_roster(ROOT / "participants.json")
    docs, fatal = load_docs(ROOT, roster)
    if fatal:
        for p in fatal:
            err("%s:%d: %s" % (p.file, p.line, p.message))
        return 1
    out = land_queue(docs, projects=roster.participants, now=time.time(),
                     coordinator=config_mod.coordinator(roster))
    if args.next:
        if out["next"]:
            print(out["next"])
        return 0
    print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


def cmd_adoption(args):
    """adoption set records a project's state; adoption gaps and adoption summary print JSON."""
    roster = load_roster(ROOT / "participants.json")
    docs, fatal = load_docs(ROOT, roster)
    if fatal:
        err("the log has a fatal problem: %s" % fatal[0].message)
        return 1
    if args.adoption_cmd == "summary":
        print(json.dumps(adoption_mod.summary(docs, roster), sort_keys=True, indent=2))
        return 0
    if args.from_ not in roster.participants:
        err("unknown participant %r" % args.from_)
        return 1
    if args.adoption_cmd == "gaps":
        print(json.dumps(adoption_mod.gaps(docs, roster, args.from_), ensure_ascii=False, indent=2))
        return 0
    try:
        print(len(adoption_mod.record(ROOT, args.from_, args.item, args.state, ref=args.ref, note=args.note,
                                      roster=roster, docs=docs)))
    except ValueError as e:
        err(str(e))
        return 1
    return 0


def cmd_schedule(args):
    """schedule set|ran|remove report a job; schedule list prints the view as JSON."""
    roster = load_roster(ROOT / "participants.json")
    if args.schedule_cmd == "list":
        docs, fatal = load_docs(ROOT, roster)
        if fatal:
            for p in fatal:
                err("%s:%d: %s" % (p.file, p.line, p.message))
            return 1
        out = schedules_mod.schedule_view(docs, datetime.now(timezone.utc))
        print(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.from_ not in roster.participants:
        err("unknown participant %r" % args.from_)
        return 1
    if args.schedule_cmd == "standard":
        docs, fatal = load_docs(ROOT, roster)
        if fatal:
            err("the log has a fatal problem: %s" % fatal[0].message)
            return 1
        view = schedules_mod.schedule_view(docs, datetime.now(timezone.utc))
        have = [] if args.all else [j["name"] for p in view["participants"] if p["id"] == args.from_ for j in p["jobs"] if j["status"] != "stale"]
        peers = [pid for pid, p in roster.participants.items() if p.kind == "project" and p.trust == "approved"]
        print(schedules_mod.standard_text(args.from_, roster.participants[args.from_].harness, have, peers,
                                            cfg=config_mod.of(roster), root=ROOT, code_root=CODE_ROOT))
        return 0
    try:
        if args.schedule_cmd == "set":
            ln = schedules_mod.set_job(ROOT, args.from_, args.name, args.cron, next_=args.next,
                                       every_min=args.every_min, roster=roster, about=args.about)
        elif args.schedule_cmd == "ran":
            ln = schedules_mod.mark_ran(ROOT, args.from_, args.name, args.result, note=args.note,
                                        next_=args.next, roster=roster)
        else:
            ln = schedules_mod.remove_job(ROOT, args.from_, args.name, roster=roster)
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_ack(args):
    """Record that you read a request and have nothing to do or say; prints the row id."""
    try:
        ln = ack_item(ROOT, args.from_, args.item)
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_withdraw(args):
    """Withdraw one of your own open questions; prints the new message id."""
    try:
        ln = withdraw_question(ROOT, args.from_, args.question, summary=args.summary,
                               msg_id=args.id, speaker=speaker_fields(args))
    except ValueError as e:
        err(str(e))
        return 1
    print(ln.id)
    return 0


def cmd_state(args):
    """state wind-down: the owner or the coordinator records the wind-down step; prints the row id."""
    try:
        ln = state_mod.set_wind_down(ROOT, args.from_, args.step, reason=args.reason)
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
            speaker=speaker_fields(args),
        )
    except ValueError as e:
        err(str(e))
        return 1
    print(json.dumps(res, ensure_ascii=False, sort_keys=True))
    return 0


NO_ANSWER_HELP = 'set needs_answer: false and end the body with "No answer needed."'


def _speaker_flags(parser):
    """--harness and --model: who wrote the post.

    CROSSTALK_HARNESS and CROSSTALK_MODEL fill them when left out.
    """
    parser.add_argument("--harness", help="the harness that writes this post, a lowercase id like codex; "
                                          "default: $CROSSTALK_HARNESS")
    parser.add_argument("--model", help="the model that writes this post, 1 to 64 chars; "
                                        "default: $CROSSTALK_MODEL")


def build_parser():
    ap = argparse.ArgumentParser(prog="relay.py", description=__doc__)
    ap.add_argument("--data-root", metavar="PATH",
                    help="the folder that holds the data (log/, participants.json, tokens.json); "
                         "default: $CROSSTALK_DATA, else the folder of this checkout")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="validate every log").set_defaults(fn=cmd_check)

    a = sub.add_parser("append", help="append one line to a log; prints the new seq")
    _speaker_flags(a)
    a.add_argument("--from", dest="from_", required=True)
    a.add_argument("--op", required=True, choices=["set", "update"])
    a.add_argument("--collection", required=True)
    a.add_argument("--id", required=True)
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--data", help="JSON object")
    g.add_argument("--data-file", help="path to a JSON object file")
    a.add_argument("--no-answer", action="store_true", help=NO_ANSWER_HELP + " (--collection messages --op set only)")
    a.set_defaults(fn=cmd_append)

    x = sub.add_parser("export-page", help="write one self-contained HTML page with the log inlined")
    x.add_argument("file", metavar="FILE", help="the HTML file to write")
    x.set_defaults(fn=cmd_export_page)

    dp = sub.add_parser("deploy", help="copy one commit of this checkout into the folder the server runs from")
    dp.add_argument("--to", metavar="DIR", help="the deployment folder (default: git config crosstalk.deploydir)")
    dp.add_argument("--ref", default="HEAD", help="the commit to deploy (default HEAD)")
    dp.add_argument("--restart", action="store_true",
                    help="restart the service after the copy, wait for /api/health, roll back on failure")
    dp.add_argument("--dry-run", action="store_true", help="check and print the plan; change nothing")
    dp.add_argument("--status", action="store_true",
                    help="print the deployed commit and how far the checkout is ahead of it")
    dp.set_defaults(fn=cmd_deploy)

    rd = sub.add_parser("read", help="print the replay as JSON")
    rd.add_argument("--collection", action="append", metavar="C",
                    help="only this collection (may be given more than once)")
    rd.add_argument("--since", metavar="TIME",
                    help="only rows at or after this UTC time, such as 2026-11-06T13:00")
    rd.add_argument("--brief", action="store_true", help="one line per row, oldest first")
    rd.set_defaults(fn=cmd_read)

    ib = sub.add_parser("inbox", help="print the open requests addressed to a participant")
    ib.add_argument("pid", metavar="PID")
    ib.add_argument("--all", action="store_true", help="include answered requests")
    ib.add_argument("--brief", action="store_true", help="one line per item")
    ib.add_argument("--new", action="store_true",
                    help="only the items that came after the last ack")
    ib.add_argument("--ack", action="store_true",
                    help="after the output, move the place to the newest row this run read")
    ib.add_argument("--ack-to", metavar="PLACE",
                    help="move the place to PLACE, a `next` that inbox --new printed (JSON)")
    ib.set_defaults(fn=cmd_inbox)

    st = sub.add_parser("stalls", help="list agents stuck on a tool call with no result")
    st.add_argument("--minutes", type=int, default=30, help="how long a call must hang (30)")
    st.add_argument("--root", default="~/.claude/projects", help="Claude Code's transcripts")
    st.set_defaults(fn=cmd_stalls)

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
    o.add_argument("--harness", help="the tool its sessions run in: claude-code, codex, opencode, "
                                     "gemini-cli, hermes, pi, or another lowercase id such as cursor")
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
    _speaker_flags(ls)
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
    _speaker_flags(rp)
    rp.add_argument("--from", dest="from_", required=True)
    rp.add_argument("--to-id", dest="to_id", required=True)
    rp.add_argument("--summary", required=True)
    rp.add_argument("--body")
    rp.add_argument("--ref", "--refs", dest="refs", action="append", nargs="+")
    rp.add_argument("--id")
    rp.add_argument("--no-answer", action="store_true", help=NO_ANSWER_HELP)
    rp.set_defaults(fn=cmd_reply)

    cs = sub.add_parser("consult", help="post a peer consultation request; prints the new message id")
    _speaker_flags(cs)
    cs.add_argument("--from", dest="from_", required=True)
    cs.add_argument("--summary", required=True)
    cs.add_argument("--body")
    cs.add_argument("--ref", "--refs", dest="refs", action="append", nargs="+")
    cs.add_argument("--to", dest="to", action="append", nargs="+")
    cs.add_argument("--id")
    cs.add_argument("--no-answer", action="store_true", help=NO_ANSWER_HELP + "; the request waits for nobody")
    cs.set_defaults(fn=cmd_consult)

    ak = sub.add_parser("ask", help="post a question for the owner; prints the new message id")
    _speaker_flags(ak)
    ak.add_argument("--from", dest="from_", required=True)
    ak.add_argument("--summary", required=True, help="the question itself, one line")
    ak.add_argument("--body", help="context, the options, and your recommendation")
    ak.add_argument("--ref", "--refs", dest="refs", action="append", nargs="+")
    ak.add_argument("--id")
    ak.add_argument("--option", dest="options", action="append",
                    help="one choice for the owner; give 2 to 6, in order")
    ak.add_argument("--recommend", type=int,
                    help="the choice you recommend, by number: 1 is the first --option")
    ak.add_argument("--no-answer", action="store_true", help="refused: a question to the owner needs an answer")
    ak.set_defaults(fn=cmd_ask)

    qs = sub.add_parser("questions", help="print the open questions for the owner (JSON)")
    qs.add_argument("--all", action="store_true",
                    help="also list answered and withdrawn questions, with their answers")
    qs.set_defaults(fn=cmd_questions)

    qu = sub.add_parser("queue", help="print the land queue of the build host (JSON), derived from land messages")
    qu.add_argument("--next", action="store_true",
                    help="print only the next project id, or nothing when no ask waits")
    qu.set_defaults(fn=cmd_queue)

    sc = sub.add_parser("schedule", help="report scheduled jobs and list them (crosstalk/schedules.py)")
    scsub = sc.add_subparsers(dest="schedule_cmd", required=True)
    sc_set = scsub.add_parser("set", help="report a job, or change one; prints its id")
    sc_set.add_argument("--cron", required=True, help="five fields, on this machine's local time")
    sc_set.add_argument("--every-min", dest="every_min", type=int, metavar="M",
                        help="the job's period, when the cron can't give it")
    sc_set.add_argument("--about", metavar="TEXT",
                        help="what the job is for and what it does; the Schedules tab shows it")
    sc_ran = scsub.add_parser("ran", help="record one run and move the next run on; prints its id")
    sc_ran.add_argument("--result", required=True, choices=["ok", "failed", "skipped"])
    sc_ran.add_argument("--note", help="one line")
    sc_rm = scsub.add_parser("remove", help="say the job is gone; prints its id")
    for p in (sc_set, sc_ran, sc_rm):
        p.add_argument("--from", dest="from_", required=True)
        p.add_argument("--name", required=True, help="the job's name; one per participant")
    for p in (sc_set, sc_ran):
        p.add_argument("--next", metavar="ISO_UTC",
                       help="the next run, yyyy-mm-ddThh:mm:ssZ (needed when the cron can't give it)")
    scsub.add_parser("list", help="print each participant's jobs with status and last runs (JSON)")
    sc_std = scsub.add_parser("standard", help="print the setup for the standard jobs this participant does not report")
    sc_std.add_argument("--from", dest="from_", required=True)
    sc_std.add_argument("--all", action="store_true", help="print all five, to change the times of jobs you already run")
    sc.set_defaults(fn=cmd_schedule)

    ad = sub.add_parser("adoption", help="record and list which shared standards a project adopted (docs/adoption.md)")
    adsub = ad.add_subparsers(dest="adoption_cmd", required=True)
    ad_set = adsub.add_parser("set", help="record a state for one or more standards; prints the count")
    ad_set.add_argument("--state", required=True, choices=adoption_mod.STATES)
    ad_set.add_argument("--item", required=True, nargs="+", metavar="ID", help="agreement or decision ids")
    ad_set.add_argument("--ref", help="the commit that adopted it")
    ad_set.add_argument("--note", help="the reason, branch or date")
    ad_gaps = adsub.add_parser("gaps", help="print the standards this project has no record for (JSON)")
    for p in (ad_set, ad_gaps):
        p.add_argument("--from", dest="from_", required=True)
    adsub.add_parser("summary", help="print the counts per project (JSON)")
    ad.set_defaults(fn=cmd_adoption)

    ac = sub.add_parser("ack", help="record that a request needs nothing from you (--none); prints the row id")
    ac.add_argument("--from", dest="from_", required=True)
    ac.add_argument("item", metavar="ITEM", help="the request's message id")
    ac.add_argument("--none", action="store_true", required=True,
                    help="you read it and have nothing to do or say")
    ac.set_defaults(fn=cmd_ack)

    wd = sub.add_parser("withdraw", help="withdraw one of your own open questions; prints the new message id")
    _speaker_flags(wd)
    wd.add_argument("--from", dest="from_", required=True)
    wd.add_argument("--question", required=True, metavar="ID", help="the question's message id")
    wd.add_argument("--summary", help="optional one-line reason")
    wd.add_argument("--id")
    wd.set_defaults(fn=cmd_withdraw)

    sta = sub.add_parser("state", help="record the wind-down step (state wind-down)")
    stasub = sta.add_subparsers(dest="state_cmd", required=True)
    sta_wd = stasub.add_parser("wind-down", help="the owner or the coordinator sets the wind-down step; "
                                                 "prints the row id")
    sta_wd.add_argument("--from", dest="from_", required=True)
    sta_wd.add_argument("--step", required=True, type=int, choices=state_mod.WIND_DOWN_STEPS,
                        help="0: work may run; 1 or 2: wind down, and no GRANTED")
    sta_wd.add_argument("--reason", help="one line: why")
    sta.set_defaults(fn=cmd_state)

    ad = sub.add_parser("adopt", help="record adoption of an agreement and post a sync message")
    _speaker_flags(ad)
    ad.add_argument("--from", dest="from_", required=True)
    ad.add_argument("--agreement", "--agreement-id", dest="agreement", required=True)
    ad.add_argument("--commit", required=True)
    ad.add_argument("--summary")
    ad.set_defaults(fn=cmd_adopt)

    sp = sub.add_parser("set-pin", help="set the owner's permanent six-digit login PIN (stored hashed)")
    sp.add_argument("pin", nargs="?", metavar="PIN", help="six digits; prompts when left out")
    sp.add_argument("--clear", action="store_true", help="remove the permanent PIN")
    sp.set_defaults(fn=cmd_set_pin)
    lv = sub.add_parser("live", help="register running Claude Code and Hermes sessions for live messages")
    lsub = lv.add_subparsers(dest="live_cmd", required=True)
    for name, h in (("register", "SessionStart hook: record this session's inbox socket"),
                    ("unregister", "SessionEnd hook: forget this session"),
                    ("hooks", "print the hook JSON to paste into settings.local.json, or with "
                              "--harness hermes the Hermes profile setup")):
        lp = lsub.add_parser(name, help=h)
        lp.add_argument("pid", metavar="PARTICIPANT")
        if name == "hooks":
            lp.add_argument("--harness", choices=["claude-code", "hermes"], default="claude-code",
                            help="the participant's harness (default claude-code)")
    lsub.add_parser("status", help="list live registrations (never the token)")
    lv.set_defaults(fn=cmd_live)
    rv = sub.add_parser("reviews", help="the projects' Codex branch reviews (crosstalk/reviews.py)")
    rsub = rv.add_subparsers(dest="reviews_cmd", required=True)
    rs = rsub.add_parser("scan", help="one scanner pass: post new reviews and overrides; prints JSON")
    rs.add_argument("--dry-run", action="store_true",
                    help="post nothing and write no state; print what the pass would do")
    rs.add_argument("--state", metavar="PATH",
                    help="state file (default CROSSTALK_REVIEWS_STATE or ~/.crosstalk-reviews-state.json)")
    rsub.add_parser("open", help="list overrides the owner has not settled and no one agreed to (JSON)")
    rv.set_defaults(fn=cmd_reviews)
    sub.add_parser("coordinator", help="print the coordinator's participant id (the roster's relay)"
                   ).set_defaults(fn=cmd_coordinator)
    st = sub.add_parser("buildhost", help="the build host's state (crosstalk/buildhost.py)")
    ssub = st.add_subparsers(dest="buildhost_cmd", required=True)
    sp = ssub.add_parser("probe", help="ssh once and print the state as JSON; posts nothing")
    sl = ssub.add_parser("poll", help="probe, post an alert if the state changed; prints JSON")
    sl.add_argument("--dry-run", action="store_true",
                    help="post nothing and write no state; print what the poll would do")
    sl.add_argument("--state", metavar="PATH",
                    help="state file (default CROSSTALK_BUILDHOST_STATE or ~/.crosstalk-buildhost-state.json)")
    for p in (sp, sl):
        p.add_argument("--host", help="ssh host (default: buildHost.ssh in config.json)")
    st.set_defaults(fn=cmd_buildhost)
    mc = sub.add_parser("mcp", help="run a stdio MCP server that acts as one participant")
    mc.add_argument("--as", dest="as_", required=True, metavar="PID",
                    help="the participant every tool call reads and writes as")
    mc.set_defaults(fn=cmd_mcp)
    # Handled in main() before argparse, so it can never exit non-zero; listed for --help.
    sub.add_parser("urgent", help="harness hook: print the note for new items: urgent ones for a "
                   "Codex session (urgent --as PID --hook codex), every one for a Hermes session "
                   "(--hook hermes); reads the event on stdin")
    sv = sub.add_parser("serve", help="run the page host and API server")
    sv.add_argument("--port", type=int, default=8484,
                    help="port to bind (default 8484; 0 = ephemeral)")
    sv.add_argument("--host", default="127.0.0.1",
                    help="address to bind (default 127.0.0.1)")
    sv.add_argument("--no-pin", action="store_true",
                    help="do not print a startup login PIN")
    sv.add_argument("--no-notify", action="store_true",
                    help="do not tell live Claude Code sessions about new inbox items")
    sv.add_argument("--no-autogrant", action="store_true",
                    help="do not post GRANTED for free land slots on the build host")
    sv.set_defaults(fn=cmd_serve)
    return ap


def cmd_urgent(argv) -> int:
    """`urgent --as <pid> --hook codex|hermes`: a harness hook; reads the event JSON on stdin.

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
                                     urgent_mod.state_path_from_env(), code_root=CODE_ROOT)
        if out:
            sys.stdout.write(out + "\n")
            sys.stdout.flush()
    except BaseException:  # SystemExit from argparse too: the hook never fails
        pass
    return 0


def split_data_root(argv):
    """A leading `--data-root PATH` (or `--data-root=PATH`): (PATH or None, the rest)."""
    if argv[:1] == ["--data-root"] and len(argv) > 1:
        return argv[1], argv[2:]
    if argv[:1] and argv[0].startswith("--data-root="):
        return argv[0][len("--data-root="):], argv[1:]
    return None, argv


def main(argv=None):
    global ROOT
    argv = sys.argv[1:] if argv is None else list(argv)
    flag, argv = split_data_root(argv)
    chosen = flag or os.environ.get("CROSSTALK_DATA")
    ROOT = Path(chosen).expanduser().resolve() if chosen else CODE_ROOT
    if not ROOT.is_dir():
        if argv[:1] == ["urgent"]:  # the hook never fails and never writes to stderr
            return 0
        err("relay.py: the data root %s is not a folder" % ROOT)
        return 2
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
