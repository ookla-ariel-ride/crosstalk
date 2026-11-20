"""relay.py checkin: one `kind: check-in` message, its facts read from state.

With `--auto` the body opens with a Facts section that this module builds, so a
session writes only what changed in its own words (`--note` or `--body-file`,
under the heading Notes). The facts:

- the window: since the sender's previous check-in (scorecard.is_check_in,
  retracted posts left out; watchdog.last_check_in), or `--since ISO`; with no
  previous check-in, the last DEFAULT_HOURS hours;
- git: the branch read (`main` when the repo has one, else the current branch),
  its short hash, whether it is pushed (against its upstream) and clean, and its
  commits in the window, newest first, at most MAX_COMMITS listed;
- reviews: the codex-review `review` messages on the sender's project in the
  window, and the open overrides (reviews.open_overrides) of the sender's
  project, those it is asked to review, and every objected one;
- the land queue (landqueue.land_queue): the sender's runs (land or lane) and
  its waiting ask with its place and grant;
- open requests: the sender's requests that still wait for an answer, and its
  open questions for the owner (the owner items, "none" when empty).

The body ends with "No answer needed" (requests.no_answer) unless the prose
holds a question mark. The post goes through logs.append_line, so the duplicate
guard applies.
"""
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config as config_mod
from .landqueue import land_queue
from .requests import (_is_approved, _messages, _parse_iso, _recipients, is_retracted, no_answer,
                       open_owner_questions, open_requests, pending_recipients)
from .reviews import SOURCE as REVIEW_SOURCE, open_overrides
from .watchdog import last_check_in

DEFAULT_HOURS = 24
MAX_COMMITS = 10
MAX_LOG = 500
SUBJECT_MAX = 100
SUMMARY_MAX = 200
GIT_TIMEOUT_SEC = 20


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cut(text, limit: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[:limit - 3].rstrip() + "..."


def window(docs: Dict, pid: str, since: Optional[str], now: float) -> dict:
    """{start, previous}: the window start (epoch) and the previous check-in id, or None.
    `since` is None or "auto" for the previous check-in, else an ISO time (ValueError when bad)."""
    if since not in (None, "", "auto"):
        ts = _parse_iso(since)
        if ts is None:
            raise ValueError("--since %r is not an ISO time" % since)
        return {"start": ts, "previous": None}
    last = last_check_in(docs, pid)
    if last is None:
        return {"start": now - DEFAULT_HOURS * 3600.0, "previous": None}
    return {"start": last[1], "previous": last[0]}


def _git(repo: Path, *args: str) -> Optional[str]:
    try:
        p = subprocess.run(["git", "-C", str(repo)] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, universal_newlines=True, timeout=GIT_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout if p.returncode == 0 else None


def git_facts(repo: Path, start: float) -> dict:
    """The branch read and its commits after `start` (committer time), newest first.
    {error} when repo is no git work tree."""
    if (_git(repo, "rev-parse", "--is-inside-work-tree") or "").strip() != "true":
        return {"error": "no git repository"}
    ref = "main" if _git(repo, "rev-parse", "--verify", "-q", "refs/heads/main") is not None else None
    if ref is None:
        ref = (_git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "").strip() or "HEAD"
    head = (_git(repo, "rev-parse", "--short", ref) or "").strip()
    if not head:
        return {"error": "no commit on %s" % ref}
    commits = []  # type: List[Tuple[str, str]]
    out = _git(repo, "log", "--format=%h%x09%ct%x09%s", "-n", str(MAX_LOG), ref) or ""
    for row in out.splitlines():
        parts = row.split("\t", 2)
        if len(parts) == 3 and parts[1].isdigit() and float(parts[1]) > start:
            commits.append((parts[0], parts[2]))
    pushed = None  # type: Optional[str]
    ahead = _git(repo, "rev-list", "--count", "%s@{upstream}..%s" % (ref, ref))
    if ahead is not None and ahead.strip().isdigit():
        n = int(ahead.strip())
        pushed = "pushed" if n == 0 else "%d not pushed" % n
    clean = (_git(repo, "status", "--porcelain") or "").strip() == ""
    return {"ref": ref, "head": head, "pushed": pushed or "no upstream", "clean": clean, "commits": commits}


def _reviews(docs: Dict, roster, pid: str, start: float) -> Tuple[List[str], int]:
    """(lines, objected count) for the review facts."""
    out = []  # type: List[str]
    for did, doc in sorted(_messages(docs).items(), key=lambda kv: (str(kv[1].get("t") or ""), kv[0])):
        if doc.get("kind") != "review" or doc.get("source") != REVIEW_SOURCE or doc.get("project") != pid:
            continue
        ts = _parse_iso(doc.get("t"))
        if ts is not None and ts > start and not is_retracted(doc):
            out.append("%s (%s)" % (_cut(doc.get("summary"), 160), did))
    objected = 0
    for o in open_overrides(docs, roster):
        mine, asked = o.get("project") == pid, o.get("reviewer") == pid
        if o["status"] == "objected":
            objected += 1
        if not (mine or asked or o["status"] == "objected"):
            continue
        who = "your override" if mine else "asks your review" if asked else "override of %s" % o.get("project")
        line = "%s %s, tip %s: %s" % (who, o["id"], str(o.get("tip") or "?")[:7], o["status"])
        names = sorted({str(x.get("from")) for x in o.get("objections") or [] if isinstance(x, dict)})
        if names:
            line += " (objection from %s)" % ", ".join(names)
        out.append(line)
    return out, objected


def _land(docs: Dict, roster, pid: str, now: float) -> Tuple[List[str], str]:
    """(lines, short state for the summary) for the sender in the land queue."""
    q = land_queue(docs, projects=getattr(roster, "participants", {}), now=now,
                   coordinator=config_mod.coordinator(roster))
    out, short = [], []  # type: List[str], List[str]
    for h in q["holders"]:
        if h["project"] != pid:
            continue
        kind = "lane" if h["word"] == "LANE START" else "land run"
        out.append("holding a %s of %s since %s (%s)"
                   % (kind, ", ".join(h["branches"]) or "no named branch", h["since"], h["id"]))
        short.append("holding " + (", ".join(h["branches"]) or kind))
    for n, w in enumerate(q["waiting"], 1):
        if w["project"] != pid:
            continue
        grant = ("grant void (%s)" % w["grant_void"] if "grant_void" in w
                 else "grant lapsed" if w.get("grant_lapsed")
                 else "granted (%s)" % w["granted"] if "granted" in w else "not granted")
        out.append("TURN ASK %s for %s: place %d of %d, %s"
                   % (w["id"], ", ".join(w["branches"]) or "no named branch", n, len(q["waiting"]), grant))
        short.append("asked, place %d%s" % (n, ", granted" if grant.startswith("granted") else ""))
    if not out:
        out.append("nothing held or asked")
    out.append("queue: %d holding, %d waiting, %d slot%s free"
               % (len(q["holders"]), len(q["waiting"]), q["slots_free"], "" if q["slots_free"] == 1 else "s"))
    return out, "; ".join(short) or "idle"


def _requests(docs: Dict, roster, pid: str) -> Tuple[List[str], List[str]]:
    """(the sender's open requests, its open questions for the owner) as lines."""
    reqs = []  # type: List[str]
    for did, doc, replies in reversed(open_requests(docs, roster=roster)):
        if doc.get("from") != pid:
            continue
        rec = _recipients(doc, roster=roster)
        waits = pending_recipients(doc, replies, roster=roster) if len(rec) >= 2 \
            else [r for r in rec if _is_approved(r, roster)]
        reqs.append("%s to %s: %s" % (did, ", ".join(waits) or "anyone", _cut(doc.get("summary"), 120)))
    owner = ["%s: %s" % (did, _cut(doc.get("summary"), 120))
             for did, doc in open_owner_questions(docs, roster=roster) if doc.get("from") == pid]
    return reqs, owner


def facts(docs: Dict, roster, pid: str, repo: Optional[Path], since: Optional[str], now: float) -> dict:
    """Every fact the check-in states, as data."""
    w = window(docs, pid, since, now)
    reviews, objected = _reviews(docs, roster, pid, w["start"])
    land, land_short = _land(docs, roster, pid, now)
    reqs, owner = _requests(docs, roster, pid)
    return {"since": _iso(w["start"]), "previous": w["previous"],
            "git": git_facts(repo, w["start"]) if repo is not None else {"error": "no repo given"},
            "reviews": reviews, "objected": objected, "land": land, "land_short": land_short,
            "requests": reqs, "owner_items": owner}


def _bullets(rows: List[str]) -> List[str]:
    return ["- " + r for r in rows] if rows else ["- none"]


def facts_text(f: dict) -> str:
    head = "Facts since %s" % f["since"]
    head += " (previous check-in %s):" % f["previous"] if f["previous"] else " (no previous check-in):"
    out = [head, ""]
    g = f["git"]
    if "error" in g:
        out += ["Git: not read (%s)." % g["error"], ""]
    else:
        out.append("Git: %s at %s, %s, %s." % (g["ref"], g["head"], g["pushed"],
                                               "clean" if g["clean"] else "uncommitted changes"))
        out.append("Commits on %s since then: %d" % (g["ref"], len(g["commits"])))
        out += ["- %s %s" % (h, _cut(s, SUBJECT_MAX)) for h, s in g["commits"][:MAX_COMMITS]]
        if len(g["commits"]) > MAX_COMMITS:
            out.append("- and %d more" % (len(g["commits"]) - MAX_COMMITS))
        out.append("")
    out += ["Reviews:"] + _bullets(f["reviews"]) + [""]
    out += ["Land queue:"] + _bullets(f["land"]) + [""]
    out += ["Open requests:"] + _bullets(f["requests"]) + [""]
    out += ["Owner items:"] + _bullets(f["owner_items"])
    return "\n".join(out)


def summary(f: dict) -> str:
    g = f["git"]
    if "error" in g:
        parts = ["git not read"]
    else:
        n = len(g["commits"])
        parts = ["%s %s: %d commit%s since last check-in" % (g["ref"], g["head"], n, "" if n == 1 else "s")]
    parts.append("land: " + f["land_short"])
    parts.append("%d open request%s" % (len(f["requests"]), "" if len(f["requests"]) == 1 else "s"))
    if f["objected"]:
        parts.append("%d objected override%s" % (f["objected"], "" if f["objected"] == 1 else "s"))
    return _cut("Check-in: " + "; ".join(parts), SUMMARY_MAX)


def asks(prose: str) -> bool:
    """True when the sender's prose asks something: it holds a question mark."""
    return "?" in prose


def message(pid: str, f: Optional[dict], prose: str) -> dict:
    """The check-in message. With no facts (no --auto) the body is the prose alone."""
    prose = prose.strip()
    parts = []  # type: List[str]
    if f is not None:
        parts.append(facts_text(f))
        if prose:
            parts.append("Notes:\n\n" + prose)
        head = summary(f)
    else:
        parts.append(prose)
        first = prose.splitlines()[0] if prose else ""
        head = _cut("Check-in: " + first, SUMMARY_MAX)
    data = {"from": pid, "kind": "check-in", "summary": head, "body": "\n\n".join(parts)}
    if not asks(prose):
        no_answer(data)
    return data
