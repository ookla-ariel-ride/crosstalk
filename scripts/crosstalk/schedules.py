"""Agents' scheduled jobs, as they report them.

A session's crons die with the session and Crosstalk can't see them, so a job
here is only what its agent last said: `schedules/<pid>-sched-<slug>` holds the
cron, the next run, and the last RUNS_KEPT results. Nothing here knows whether
a job still exists, so the statuses never say it is running:

    scheduled  the next run is ahead, or just passed (inside the grace period)
    late       the next run passed more than the grace period ago
    stale      no report for a long time and no run ahead: the session has
               probably ended
    unknown    no next run could be worked out

Cron fields are read on the reporting machine's local clock (`tz=None`; a
Claude Code cron is local time on the Mac). Next runs are stored in UTC, so
the page's countdown needs no zone. Scanning UTC minutes and reading each on
the local clock handles daylight saving without special cases: a local time
that doesn't exist never matches, and a repeated one matches twice.

index.html repeats `status` (schedState) so a countdown can turn late between
polls; keep GRACE_MIN, STALE_MIN and STALE_NO_PERIOD_MIN in step with it.
"""
import os
import re
import shlex
import zlib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import config
from .logs import RUNS_KEPT, RUN_RESULTS, parse_utc, utc_text

UTC = timezone.utc
GRACE_MIN = 5
STALE_MIN = 120
STALE_NO_PERIOD_MIN = 1440
HORIZON = timedelta(days=9 * 366)  # past the longest gap between 29 Februaries
_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()
_DAYS = "sun mon tue wed thu fri sat".split()


class CronError(ValueError):
    """An expression this parser doesn't support."""


def _value(text: str, low: int, names: Optional[List[str]], offset: int) -> int:
    if names and text.lower() in names:
        return names.index(text.lower()) + offset
    if not text.isdigit():
        raise CronError("bad cron value %r" % text)
    return int(text)


def _field(text: str, low: int, high: int, names: Optional[List[str]] = None, offset: int = 0) -> set:
    out = set()  # type: set
    for part in text.split(","):
        body, slash, step_text = part.partition("/")
        if slash and (not step_text.isdigit() or int(step_text) == 0):
            raise CronError("bad cron step in %r" % part)
        if body == "*":
            first, last = low, high
        elif "-" in body:
            a, b = body.split("-", 1)
            first, last = _value(a, low, names, offset), _value(b, low, names, offset)
        else:
            if slash:
                raise CronError("a step needs * or a range: %r" % part)
            first = last = _value(body, low, names, offset)
        if first > last or first < low or last > high:
            raise CronError("cron value out of range in %r" % part)
        out.update(range(first, last + 1, int(step_text) if slash else 1))
    return out


def _parse(expr):
    """(minutes, hours, days, months, weekdays, day-is-*, weekday-is-*), or CronError."""
    if not isinstance(expr, str) or len(expr.split()) != 5:
        raise CronError("a cron expression has five fields: %r" % (expr,))
    mi, ho, dom, mo, dow = expr.split()
    weekdays = {d % 7 for d in _field(dow, 0, 7, _DAYS)}
    return (_field(mi, 0, 59), _field(ho, 0, 23), _field(dom, 1, 31), _field(mo, 1, 12, _MONTHS, 1),
            weekdays, dom.startswith("*"), dow.startswith("*"))


def next_run(expr, after: datetime, tz=None) -> Optional[datetime]:
    """The first time strictly after `after` that matches, in UTC; None if it never does.

    `tz=None` is the machine's local zone. As in Vixie cron, when day of month and
    weekday are both restricted either one matches.
    """
    minutes, hours, days, months, weekdays, any_day, any_weekday = _parse(expr)

    def day_ok(d: date) -> bool:
        if d.month not in months:
            return False
        dom_ok, dow_ok = d.day in days, (d.weekday() + 1) % 7 in weekdays
        return (dom_ok or dow_ok) if not (any_day or any_weekday) else (dom_ok and dow_ok)

    t = after.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = t + HORIZON
    while t < limit:
        local = t.astimezone(tz)
        if not day_ok(local.date()):
            midnight = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)
            t = midnight if midnight > t else t + timedelta(minutes=1)
        elif local.hour in hours and local.minute in minutes:
            return t
        else:
            t += timedelta(minutes=1)
    return None


def period_minutes(expr, after: datetime, tz=None) -> Optional[int]:
    """Minutes between the next two runs, or None if there aren't two."""
    first = next_run(expr, after, tz)
    second = next_run(expr, first, tz) if first else None
    return int((second - first).total_seconds() // 60) if second else None


def status(doc: dict, now: datetime) -> str:
    """`scheduled`, `late`, `stale` or `unknown` (see the module docstring)."""
    nxt, reported = parse_utc(doc.get("next")), parse_utc(doc.get("reported"))
    period = doc.get("periodMin")
    period = period if isinstance(period, (int, float)) and not isinstance(period, bool) else None
    window = max(STALE_MIN, 3 * period) if period else STALE_NO_PERIOD_MIN
    if (nxt is None or nxt < now) and (reported is None or now - reported > timedelta(minutes=window)):
        return "stale"
    if nxt is None:
        return "unknown"
    grace = max(GRACE_MIN, period / 2) if period else GRACE_MIN
    return "late" if now - nxt > timedelta(minutes=grace) else "scheduled"


def schedule_view(docs: Dict, now: datetime) -> dict:
    """Each participant's live jobs: name, cron, next run, status and the last runs."""
    by_pid = {}  # type: Dict[str, List[dict]]
    for (coll, did), doc in sorted(docs.items()):
        if coll != "schedules" or not isinstance(doc, dict) or doc.get("removed") \
                or not isinstance(doc.get("from"), str) or not isinstance(doc.get("name"), str):
            continue
        runs = [r for r in (doc.get("runs") or []) if isinstance(r, dict)][-RUNS_KEPT:][::-1]
        by_pid.setdefault(doc["from"], []).append({
            "id": did, "name": doc["name"], "cron": doc.get("cron"), "about": doc.get("about"), "next": doc.get("next"),
            "period_min": doc.get("periodMin"), "reported": doc.get("reported"),
            "status": status(doc, now), "last_run": runs[0] if runs else None, "runs": runs})
    return {"now": utc_text(now),
            "participants": [{"id": pid, "jobs": by_pid[pid]} for pid in sorted(by_pid)]}


# The jobs every project runs. index.html repeats the names for its Missing jobs line; docs/standard-jobs.md explains them.
def relay_command() -> str:
    """How a session runs this checkout's relay.py; the home folder is written as ~."""
    path = str(Path(__file__).resolve().parents[1] / "relay.py")
    home = str(Path.home())
    if path.startswith(home + os.sep) and shlex.quote(path) == path:
        return "python3 ~" + path[len(home):]
    return "python3 " + shlex.quote(path)


def _usage_prompt(usage: dict) -> str:
    """The usage-guard prompt, from the install's `usage` settings (config.py)."""
    source = ("Read the newest %s and check its mtime; ignore a stale file." % usage["glob"] if usage["glob"]
              else "Read your harness's weekly usage.")
    grace = " or %g minutes after step 1" % usage["graceMin"] if usage["graceMin"] > 0 else ""
    return ("%s Step 1 at weekly usage %g or higher: start no new agents, TURN ASKs, gates or lands. "
            "Step 2 at %g%s: stop all work and tell the owner. After a wind-down, restart below %g."
            % (source, usage["limit"], usage["stopLimit"], grace, usage["limit"]))


STANDARD_JOBS = (
    ("check-in", "{a},{a30} * * * *",
     "Reads the inbox, answers requests, and posts status every 30 minutes.",
     "Run {relay} inbox {pid} and act on what it lists. Post kind check-in with commits, work in progress, and blockers."),
    ("usage-guard", "{b},{b30} * * * *",
     "Reads weekly usage. Winds work down at the install's limit, stops at its stop limit and tells the owner.",
     "{usage}"),
    ("daily-docs-refresh", "{docs_m} {docs_h} * * *",
     "Corrects the README and docs that the last 24 hours of changes made out of date.",
     "Do a real docs pass in a subagent, without asking the owner; never report skipped without a reason in --note. "
     "Correct the README and docs that the last 24 hours of commits made out of date. Check the README against the "
     "project's commands and its docs files, and check that each plugin or tool folder has a README. Remove em-dashes and "
     "AI-writing patterns with the crafting-effective-readmes and humanizer skills, with no change to meaning. Run the tests, "
     "commit, and name the commit in the next check-in."),
    ("daily-retro", "{retro_m} 12 * * *",
     "Posts the daily retro with the backlog vote, the MOVE votes, and the drift list.",
     "Run {relay} adoption gaps --from {pid} and record or report what is open. "
     "Post the daily retro with the backlog vote, the MOVE votes, and the drift list."),
    ("monday-review", "{mon_m} {mon_h} * * 1",
     "Finds stale or missing rules in AGENTS.md and CLAUDE.md and proposes changes.",
     "Review AGENTS.md and CLAUDE.md against this week's decisions rows. Propose changes for stale or missing rules."),
)


def standard_jobs(pid: str, peers: Sequence[str] = (), cfg: Optional[dict] = None) -> list:
    """The standard jobs for one project. `peers` is every project: each gets its own even share of each window.
    `cfg` is the install's settings (config.py); the usage-guard prompt takes its limits from them."""
    cfg = cfg or config.DEFAULTS
    peers = sorted(set(peers) | {pid})
    i, n = peers.index(pid), len(peers)
    a = (2 + i * 30 // n) % 30
    b = (a + max(1, 15 // n)) % 30
    docs_h, docs_m = divmod(9 * 60 + 5 + i * 60 // n, 60)
    mon_h, mon_m = divmod(10 * 60 + 5 + i * 60 // n, 60)
    fill = {"a": a, "a30": a + 30, "b": b, "b30": b + 30, "docs_h": docs_h, "docs_m": docs_m, "mon_h": mon_h,
            "mon_m": mon_m, "retro_m": 25 + i * 30 // n, "relay": relay_command(), "pid": pid,
            "usage": _usage_prompt(cfg["usage"])}
    out = []
    for name, cron, about, prompt in STANDARD_JOBS:
        ran = " Then run {relay} schedule ran --from {pid} --name %s --result ok." % name
        out.append({"name": name, "cron": cron.format(**fill), "about": about, "prompt": (prompt + ran).format(**fill)})
    return out


def standard_text(pid: str, harness: Optional[str], reported=(), peers: Sequence[str] = (),
                  cfg: Optional[dict] = None) -> str:
    """The exact setup for one project and harness, leaving out the jobs it already reports."""
    jobs = [j for j in standard_jobs(pid, peers, cfg) if j["name"] not in reported]
    if not jobs:
        return "%s reports all %d standard jobs." % (pid, len(STANDARD_JOBS))
    lines = ["# Standard jobs for %s: %d to set up. See docs/standard-jobs.md." % (pid, len(jobs))]
    if harness != "hermes":
        lines.append("# Claude Code crons end with the session and expire after 7 days. Repeat this at each session start.")
    for j in jobs:
        lines.append("\n# %s: %s" % (j["name"], j["about"]))
        if harness == "hermes":
            lines.append("hermes cron create --name %s --workdir \"$PWD\" %s %s" % (j["name"], shlex.quote(j["cron"]), shlex.quote(j["prompt"])))
        else:
            lines.append("# Use CronCreate. cron: %s\n# prompt: %s" % (j["cron"], j["prompt"]))
        lines.append("%s schedule set --from %s --name %s --cron %s --about %s" % (
            relay_command(), pid, j["name"], shlex.quote(j["cron"]), shlex.quote(j["about"])))
    return "\n".join(lines)


def job_id(from_: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not slug:
        raise ValueError("job name %r has no letters or digits" % (name,))
    return "%s-sched-%s" % (from_, slug)


def _current(root: Path, roster, doc_id: str) -> Optional[dict]:
    from .reports import load_docs  # reports imports more than this module should at load time
    docs, fatal = load_docs(root, roster)
    if fatal:
        raise ValueError("the log has a fatal problem: %s" % fatal[0].message)
    return docs.get(("schedules", doc_id))


def _next_after(doc: dict, now: datetime, tz, explicit: Optional[str]) -> Optional[str]:
    """The next run: `--next` if given, else from the cron, else `everyMin` after now, else None."""
    if explicit:
        return explicit
    try:
        nxt = next_run(doc.get("cron"), now, tz)
    except CronError:
        nxt = None
    if nxt is None and doc.get("everyMin"):
        nxt = now + timedelta(minutes=doc["everyMin"])
    return utc_text(nxt) if nxt else None


def _period(doc: dict, now: datetime, tz) -> Optional[int]:
    if doc.get("everyMin"):
        return doc["everyMin"]
    try:
        return period_minutes(doc.get("cron"), now, tz)
    except CronError:
        return None


def _now(now: Optional[datetime]) -> datetime:
    return (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)


def set_job(root: Path, from_: str, name: str, cron: str, next_: Optional[str] = None,
            every_min: Optional[int] = None, now: Optional[datetime] = None, tz=None, roster=None,
            about: Optional[str] = None):
    """Report a job (or change one, keeping its history and, unless `about` is given, its description)."""
    from .logs import append_line
    now = _now(now)
    if every_min is not None and every_min < 1:
        raise ValueError("--every-min must be at least 1")
    if next_ is not None and parse_utc(next_) is None:
        raise ValueError("--next %r is not UTC ISO 8601 (yyyy-mm-ddThh:mm:ssZ)" % (next_,))
    did = job_id(from_, name)
    prior = _current(root, roster, did) or {}
    doc = {"from": from_, "name": name, "cron": cron, "reported": utc_text(now),
           "runs": (prior.get("runs") or [])[-RUNS_KEPT:]}
    about = prior.get("about") if about is None else about.strip()
    if about:
        doc["about"] = about
    if every_min:
        doc["everyMin"] = every_min
    doc["next"] = _next_after(doc, now, tz, next_)
    if doc["next"] is None:
        raise ValueError("can't work out a next run from cron %r; pass --next" % (cron,))
    doc["periodMin"] = _period(doc, now, tz)
    return append_line(root, from_, "set", "schedules", did, doc, roster=roster)


def _live(root: Path, from_: str, name: str, roster) -> tuple:
    did = job_id(from_, name)
    doc = _current(root, roster, did)
    if doc is None or doc.get("removed"):
        raise ValueError("no job %r for %s; report it with schedule set first" % (name, from_))
    return did, doc


def mark_ran(root: Path, from_: str, name: str, result: str, note: Optional[str] = None,
             next_: Optional[str] = None, now: Optional[datetime] = None, tz=None, roster=None):
    """Record one run and move the next run on; returns the Line written."""
    from .logs import append_line
    if result not in RUN_RESULTS:
        raise ValueError("result must be one of %s" % ", ".join(RUN_RESULTS))
    if next_ is not None and parse_utc(next_) is None:
        raise ValueError("--next %r is not UTC ISO 8601 (yyyy-mm-ddThh:mm:ssZ)" % (next_,))
    now = _now(now)
    did, doc = _live(root, from_, name, roster)
    run = {"at": utc_text(now), "result": result}
    if note:
        run["note"] = note
    return append_line(root, from_, "update", "schedules", did, {
        "runs": (list(doc.get("runs") or []) + [run])[-RUNS_KEPT:], "reported": run["at"],
        "next": _next_after(doc, now, tz, next_), "periodMin": _period(doc, now, tz)}, roster=roster)


def remove_job(root: Path, from_: str, name: str, now: Optional[datetime] = None, roster=None):
    """Mark a job gone; returns the Line written."""
    from .logs import append_line
    did, _doc = _live(root, from_, name, roster)
    return append_line(root, from_, "update", "schedules", did,
                       {"removed": utc_text(_now(now))}, roster=roster)
