"""The game projects' Codex branch reviews, brought into Crosstalk.

Each project's `.githooks/codex-review` keeps its reports outside its repo, in
the folder its roster entry names as `reviewDir` (participants.json). Per
branch tip it writes `branch-<tip>.meta` (lines `tip`, `base`, `diff`, ...),
then `branch-<tip>.md` (the report: `# Codex Adversarial Review`, an optional
`Model:` line, an optional `Tier:` line, `Verdict:`, then `Findings:` with one
`- [severity] headline` line per finding), and `branch-<tip>.resolved` (the
agent's reason) when `codex-review resolve` overrides the findings. Everything
else in the folder (per-commit `review-*` files, `.checked` lists, the cache
subfolders) is ignored. This module only reads that folder.

The Scanner runs on the server's notifier pass (every SCAN_SEC) and from
`relay.py reviews scan`. For each project with a reviewDir it posts, as the
participant `crosstalk` and with `crosstalk-` ids only:

- a new or rewritten report: a `review` message (verdict, tier, finding count,
  never the report's text). Its id comes from project, tip and diff, so a rerun
  of the same range replaces the message instead of adding one;
- a new `.resolved`: a `disagreement` message (the overridden findings'
  headlines and the agent's reason), plus a `request` to one other project
  with a reviewDir, taken in turn, asking it to agree or object in the thread.

Objections are advisory: nothing blocks the branch. On each pass the scanner
reads every open override's thread (one without the owner's `resolution`): a
reply from an approved participant other than crosstalk, the owner and the
branch's project counts by its `position` field ("agree" or "object") or else
by its summary's first word (Agree/Agreed, Object/Objection). Each
participant's latest such reply counts. The override's `status` becomes
`objected` (any objection), `agreed`, or stays `awaiting`; each objection is
forwarded once to the branch's project as a request (so it lands in its inbox).

The first pass for a project records every report present without posting;
a folder that is missing is skipped silently (and baselined when it appears).
At most MAX_ITEMS_PER_PASS items per project per pass are posted; the rest
carry over to the next pass, oldest first.

State (default ~/.crosstalk-reviews-state.json, 0600, `.lock` beside it;
CROSSTALK_REVIEWS_STATE moves it, empty turns the scanner off):
    {"projects": {"<pid>": {"seen": {"<stem>": {"md": [mtime_ns, size, sha],
                                                "resolved": [...]}}}}}
A file whose stat changed but whose content hash did not (a moved folder)
posts nothing.
"""
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Tuple

from .courier import atomic_write_text
from .logs import append_line
from .participants import Roster
from .requests import _Threads, _is_approved, _messages

POSTER = "crosstalk"
REVIEWER = "codex"
SOURCE = "codex-review"
SCAN_SEC = 60.0
MAX_ITEMS_PER_PASS = 3
HEADLINE_MAX = 120
HEADLINES_MAX = 5
REASON_MAX = 600
OBJECTION_MAX = 160
READ_MAX = 1 << 20
LOCK_WAIT_SEC = 5.0
DEFAULT_STATE = Path.home() / ".crosstalk-reviews-state.json"

NAME_RE = re.compile(r"^branch-([0-9a-f]{7,40})\.(md|meta|resolved)$")
FINDING_RE = re.compile(r"^- \[([A-Za-z-]+)\] (.*)$")
POSITION_RE = re.compile(r"^\W*(agree[sd]?|object(?:s|ed|ion|ing)?)\b", re.IGNORECASE)


def state_path_from_env() -> Optional[Path]:
    """CROSSTALK_REVIEWS_STATE, else the default; None when it is set to empty."""
    env = os.environ.get("CROSSTALK_REVIEWS_STATE")
    if env is None:
        return DEFAULT_STATE
    return Path(env) if env else None


def _warn(msg: str) -> None:
    print("crosstalk reviews: " + msg, file=sys.stderr)


def _one_line(value, limit: int) -> str:
    text = " ".join(str(value).split()) if value is not None else ""
    text = "".join(c if c.isprintable() else " " for c in text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


# parsing

def parse_report(text: str) -> Optional[dict]:
    """The facts in a report: verdict, tier, model, finding count and headlines.

    None when the report has no Verdict line yet. The tier is `full` or
    `light` from the Tier line (no Tier line, as in AvoidMissingBall's and older
    reports, reads as `full`), or `empty` for a "Not reviewed: empty diff"
    record. A finding is a `- [severity] headline` line at the start of a line
    inside the Findings section; its headline is cut to one line of at most
    HEADLINE_MAX characters. The report's other text is never returned.
    """
    lines = text.splitlines()
    verdict = tier = model = None
    findings, headlines, section = 0, [], None
    for raw in lines:
        if verdict is None and raw.startswith("Verdict:"):
            verdict = raw[len("Verdict:"):].strip()
        elif tier is None and raw.startswith("Tier:"):
            word = raw[len("Tier:"):].strip().split(" ", 1)[0].lower()
            tier = word if word in ("full", "light") else "full"
        elif model is None and raw.startswith("Model:"):
            model = _one_line(raw[len("Model:"):].strip().split(" ", 1)[0], 60) or None
        elif raw.endswith(":") and raw[:1].isupper() and not raw.startswith(" "):
            section = raw[:-1]
        elif section == "Findings":
            m = FINDING_RE.match(raw)
            if m:
                findings += 1
                headlines.append(_one_line("[%s] %s" % (m.group(1).lower(), m.group(2)), HEADLINE_MAX))
    if not verdict:
        return None
    if lines and lines[0].startswith("# Not reviewed"):
        tier = "empty"
    return {"verdict": _one_line(verdict, 40), "tier": tier or "full", "model": model,
            "findings": findings, "headlines": headlines}


def parse_meta(text: str) -> Dict[str, str]:
    """A .meta file's first value per key (`tip`, `base`, `diff`, `review`, ...)."""
    out = {}  # type: Dict[str, str]
    for raw in text.splitlines():
        key, _, value = raw.partition(" ")
        if key and value and key not in out:
            out[key] = value.strip()
    return out


def position_of(doc: dict) -> Optional[str]:
    """`agree`, `object` or None: the reply's `position` field, else its summary's first word."""
    pos = doc.get("position")
    if isinstance(pos, str) and pos.strip().lower() in ("agree", "object"):
        return pos.strip().lower()
    s = doc.get("summary")
    m = POSITION_RE.match(s) if isinstance(s, str) else None
    if not m:
        return None
    return "agree" if m.group(1).lower().startswith("agree") else "object"


def review_id(project: str, tip: str, diff: str) -> str:
    return "crosstalk-review-%s-%s-%s" % (project, tip[:12], diff[:8])


def override_id(project: str, tip: str, diff: str) -> str:
    return "crosstalk-override-%s-%s-%s" % (project, tip[:12], diff[:8])


def ask_id(project: str, tip: str, diff: str) -> str:
    return "crosstalk-ask-%s-%s-%s" % (project, tip[:12], diff[:8])


def forward_id(reply_id: str) -> str:
    return "crosstalk-objection-" + hashlib.sha1(reply_id.encode("utf-8")).hexdigest()[:12]


def display_path(path: Path) -> str:
    """path with the home folder written as ~, so a posted path names no user."""
    home = str(Path.home())
    s = str(path)
    if s == home or s.startswith(home + os.sep):
        return "~" + s[len(home):]
    return s


def label(roster: Roster, pid: str) -> str:
    """A project's short label: its first alias in capitals (BIL), else its name."""
    p = roster.participants.get(pid)
    if p is None:
        return pid
    return p.aliases[0].upper() if p.aliases else p.name


def _plural(n: int) -> str:
    return "no findings" if n == 0 else "1 finding" if n == 1 else "%d findings" % n


def is_override(did: str, doc: dict) -> bool:
    return (doc.get("from") == POSTER and doc.get("kind") == "disagreement"
            and doc.get("source") == SOURCE and isinstance(doc.get("project"), str))


# overrides and objections

def override_status(messages: Dict[str, dict], threads: _Threads, oid: str,
                    roster: Roster) -> Tuple[str, List[dict], List[str]]:
    """(status, objections, agreed) for the override oid from the replies in its thread."""
    project = messages[oid].get("project")
    latest = {}  # type: Dict[str, Tuple[Tuple[str, str], str, str]]
    for did, m in messages.items():
        if did == oid or oid not in threads.ancestors(did):
            continue
        frm = m.get("from")
        if frm in (POSTER, "owner", project) or not _is_approved(frm, roster):
            continue
        pos = position_of(m)
        if pos is None:
            continue
        key = (m.get("t") if isinstance(m.get("t"), str) else "", did)
        if frm not in latest or key > latest[frm][0]:
            latest[frm] = (key, did, pos)
    objections = [{"id": did, "from": frm, "summary": _one_line(messages[did].get("summary"), OBJECTION_MAX)}
                  for frm, (_k, did, pos) in sorted(latest.items()) if pos == "object"]
    agreed = sorted(frm for frm, (_k, _d, pos) in latest.items() if pos == "agree")
    status = "objected" if objections else "agreed" if agreed else "awaiting"
    return status, objections, agreed


def open_overrides(docs: Dict, roster: Roster) -> List[dict]:
    """Overrides the owner has not settled and no reviewer has agreed to, newest first.

    Each is {id, project, tip, status, reviewer, objections, summary, t}; the
    crosstalk session lists the objected ones in its check-in.
    """
    out = []
    for (coll, did), doc in docs.items():
        if coll != "messages" or not is_override(did, doc) or doc.get("resolution"):
            continue
        if doc.get("status") == "agreed":
            continue
        out.append({"id": did, "project": doc.get("project"), "tip": doc.get("tip"),
                    "status": doc.get("status") or "awaiting", "reviewer": doc.get("reviewer"),
                    "objections": doc.get("objections") or [], "summary": doc.get("summary"),
                    "t": doc.get("t")})
    out.sort(key=lambda r: (r["status"] != "objected", -_ts(r["t"])))
    return out


def _ts(t) -> float:
    try:
        return time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0.0


# the folder

def _fingerprint(path: Path, st: os.stat_result, seen) -> Tuple[list, Optional[str]]:
    """([mtime_ns, size, sha], text) for path; text is None when the stat matches `seen`."""
    if isinstance(seen, list) and len(seen) == 3 and seen[0] == st.st_mtime_ns and seen[1] == st.st_size:
        return seen, None
    with open(str(path), "rb") as f:
        data = f.read(READ_MAX)
    return [st.st_mtime_ns, st.st_size, hashlib.sha1(data).hexdigest()[:16]], data.decode("utf-8", "replace")


def _list(folder: Path) -> Dict[str, Dict[str, Tuple[Path, os.stat_result]]]:
    """{stem: {"md"|"meta"|"resolved": (path, stat)}} for the branch records in folder."""
    out = {}  # type: Dict[str, Dict[str, Tuple[Path, os.stat_result]]]
    with os.scandir(str(folder)) as it:
        for entry in it:
            m = NAME_RE.match(entry.name)
            if not m or not entry.is_file(follow_symlinks=False):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            out.setdefault(m.group(1), {})[m.group(2)] = (Path(entry.path), st)
    return out


def _read(path: Path) -> str:
    try:
        with open(str(path), "rb") as f:
            return f.read(READ_MAX).decode("utf-8", "replace")
    except OSError:
        return ""


# state

def _clean(raw) -> Dict[str, dict]:
    projects = {}  # type: Dict[str, dict]
    src = raw.get("projects") if isinstance(raw, dict) else None
    for pid, st in (src.items() if isinstance(src, dict) else ()):
        if not isinstance(pid, str) or not isinstance(st, dict) or not isinstance(st.get("seen"), dict):
            continue
        seen = {}
        for stem, rec in st["seen"].items():
            if isinstance(stem, str) and isinstance(rec, dict):
                seen[stem] = {k: v for k, v in rec.items()
                              if k in ("md", "resolved") and isinstance(v, list) and len(v) == 3}
        projects[pid] = {"seen": seen}
    return {"projects": projects}


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.monotonic() + LOCK_WAIT_SEC
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


def _load(path: Optional[Path]) -> Dict[str, dict]:
    if path is None:
        return _clean(None)
    try:
        return _clean(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return _clean(None)


class Scanner:
    """Posts each project's new Codex reviews and overrides; see the module docstring.

    poll() runs scan() at most once every `interval` seconds of `clock`.
    `append` writes one line (logs.append_line's signature).
    """

    def __init__(self, root, state_path, clock: Callable[[], float] = time.time,
                 interval: float = SCAN_SEC, cap: int = MAX_ITEMS_PER_PASS,
                 append: Callable[..., object] = append_line):
        self.root = Path(root)
        self.state_path = Path(state_path) if state_path is not None else None
        self.clock = clock
        self.interval = interval
        self.cap = cap
        self.append = append
        self._last = None  # type: Optional[float]

    def poll(self, docs: Dict, roster: Roster) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.interval:
            return
        self._last = now
        try:
            self.scan(docs, roster)
        except OSError as e:
            _warn("skipping scan: %s" % e)

    def scan(self, docs: Dict, roster: Roster, dry_run: bool = False) -> dict:
        """One pass. Returns {dry_run, projects: {pid: counts}, writes: [lines]}.

        With dry_run nothing is written: no log line, no state.
        """
        if dry_run or self.state_path is None:
            return self._scan(docs, roster, _load(self.state_path), dry_run=True)
        with _locked(self.state_path):
            state = _load(self.state_path)
            before = json.dumps(state, sort_keys=True)
            plan = self._scan(docs, roster, state, dry_run=False)
            if json.dumps(state, sort_keys=True) != before or not self.state_path.exists():
                atomic_write_text(self.state_path, json.dumps(state, sort_keys=True) + "\n")
                os.chmod(str(self.state_path), 0o600)
            return plan

    # the pass

    def _scan(self, docs: Dict, roster: Roster, state: Dict[str, dict], dry_run: bool) -> dict:
        plan = {"dry_run": dry_run, "projects": {}, "writes": []}  # type: dict
        poster = roster.participants.get(POSTER)
        messages = _messages(docs)
        overlay = {}  # type: Dict[str, dict]  # docs written this pass
        budget = {}  # type: Dict[str, int]

        def write(project: str, op: str, did: str, data: dict) -> bool:
            line = {"op": op, "collection": "messages", "id": did, "data": data}
            if not dry_run:
                try:
                    self.append(self.root, POSTER, op, "messages", did, data, roster=roster)
                except (OSError, ValueError) as e:
                    _warn("%s: cannot post %s: %s" % (project, did, e))
                    return False
            plan["writes"].append(line)
            overlay[did] = dict(overlay.get(did) or messages.get(did) or {}, **data) \
                if op == "update" else dict(data)
            return True

        def known(did: str) -> Optional[dict]:
            return overlay.get(did) or messages.get(did)

        projects = sorted(pid for pid, p in roster.participants.items() if p.reviewDir)
        for pid in projects:
            folder = Path(os.path.expanduser(roster.participants[pid].reviewDir))
            info = {"dir": display_path(folder), "missing": False, "first_run": False, "reports": 0,
                    "overrides": 0, "recorded": 0, "posted": 0, "carried": 0}
            plan["projects"][pid] = info
            budget[pid] = self.cap
            try:
                records = _list(folder)
            except OSError:
                info["missing"] = True
                continue
            info["reports"] = sum(1 for r in records.values() if "md" in r)
            info["overrides"] = sum(1 for r in records.values() if "resolved" in r)
            pst = state["projects"].get(pid)
            if pst is None:
                info["first_run"] = True
                seen = {}  # type: Dict[str, dict]
                for stem, rec in records.items():
                    for kind in ("md", "resolved"):
                        if kind in rec:
                            try:
                                fp, _text = _fingerprint(rec[kind][0], rec[kind][1], None)
                            except OSError:
                                continue
                            seen.setdefault(stem, {})[kind] = fp
                            info["recorded"] += 1
                state["projects"][pid] = {"seen": seen}
                continue
            seen = pst["seen"]
            pending = []  # type: List[Tuple[int, int, str, str]]
            for stem, rec in records.items():
                for order, kind in ((0, "md"), (1, "resolved")):
                    if kind not in rec:
                        continue
                    path, st = rec[kind]
                    old = seen.get(stem, {}).get(kind)
                    if isinstance(old, list) and len(old) == 3 and old[:2] == [st.st_mtime_ns, st.st_size]:
                        continue
                    pending.append((st.st_mtime_ns, order, stem, kind))
            pending.sort()
            for _mt, _order, stem, kind in pending:
                rec = records[stem]
                path, st = rec[kind]
                try:
                    fp, text = _fingerprint(path, st, None)
                except OSError:
                    continue
                old = seen.get(stem, {}).get(kind)
                if isinstance(old, list) and len(old) == 3 and old[2] == fp[2]:
                    seen.setdefault(stem, {})[kind] = fp  # moved or touched: same content
                    continue
                if budget[pid] <= 0 or poster is None:
                    info["carried"] += 1
                    continue
                meta = parse_meta(_read(rec["meta"][0])) if "meta" in rec else {}
                if kind == "md":
                    done = self._review(pid, stem, path, text or "", meta, roster, known, write)
                else:
                    md_text = _read(rec["md"][0]) if "md" in rec else ""
                    done = self._override(pid, stem, path, rec, text or "", md_text, meta,
                                          roster, docs, overlay, known, write)
                if done is None:
                    continue  # not ready (an unfinished report): look again next pass
                if done is False:
                    info["carried"] += 1
                    budget[pid] = 0
                    continue
                if done == "posted":
                    budget[pid] -= 1
                    info["posted"] += 1
                else:
                    info["recorded"] += 1
                seen.setdefault(stem, {})[kind] = fp
        if poster is not None:
            self._objections(docs, roster, messages, overlay, budget, plan, write)
        return plan

    def _review(self, pid, stem, path, text, meta, roster, known, write):
        """Post a review for one report: "posted", "recorded" (nothing to post), None or False."""
        facts = parse_report(text)
        if facts is None:
            return None
        tip = meta.get("tip") or stem
        diff = meta.get("diff") or "none"
        if facts["tier"] == "empty" or diff == "empty":
            return "recorded"
        did = review_id(pid, tip, diff)
        data = {"from": POSTER, "kind": "review", "source": SOURCE, "project": pid,
                "tip": tip, "base": meta.get("base"), "diff": diff, "tier": facts["tier"],
                "verdict": facts["verdict"], "findings": facts["findings"],
                "report": display_path(path),
                "summary": "%s review of %s (%s): %s, %s" % (label(roster, pid), tip[:7], facts["tier"],
                                                          facts["verdict"], _plural(facts["findings"])),
                "refs": ["%s@%s" % (pid, tip[:7])]}
        if facts["model"]:
            data["model"] = facts["model"]
        if data["base"] is None:
            del data["base"]
        old = known(did)
        if old and all(old.get(k) == data.get(k) for k in ("verdict", "tier", "findings", "model")):
            return "recorded"
        return "posted" if write(pid, "set", did, data) else False

    def _override(self, pid, stem, path, rec, reason_text, md_text, meta, roster, docs, overlay,
                  known, write):
        reason = _one_line(reason_text, REASON_MAX)
        if not reason:
            return "recorded"
        facts = parse_report(md_text) or {"verdict": None, "tier": "full", "findings": 0,
                                          "headlines": [], "model": None}
        tip = meta.get("tip") or stem
        diff = meta.get("diff") or "none"
        did = override_id(pid, tip, diff)
        name = label(roster, pid)
        heads = facts["headlines"][:HEADLINES_MAX]
        more = facts["findings"] - len(heads)
        codex_pos = "%s, %s%s" % (facts["verdict"] or "no report", _plural(facts["findings"]),
                                  (": " + "; ".join(heads)) if heads else "")
        if more > 0:
            codex_pos += " (and %d more)" % more
        report = display_path(rec["md"][0]) if "md" in rec else None
        old = known(did)
        if old:
            if old.get("reason") == reason:
                return "recorded"
            body = self._override_body(roster, pid, tip, facts, heads, reason, report, old.get("reviewer"))
            data = {"reason": reason, "body": body,
                    "positions": dict(old.get("positions") or {}, **{pid: reason})}
            return "posted" if write(pid, "update", did, data) else False
        reviewer = self._next_reviewer(pid, roster, docs, overlay)
        body = self._override_body(roster, pid, tip, facts, heads, reason, report, reviewer)
        data = {"from": POSTER, "kind": "disagreement", "source": SOURCE, "project": pid,
                "tip": tip, "diff": diff, "review": review_id(pid, tip, diff),
                "verdict": facts["verdict"], "findings": facts["findings"], "headlines": heads,
                "reason": reason, "resolvedFile": display_path(path), "status": "awaiting",
                "positions": {REVIEWER: codex_pos, pid: reason},
                "summary": "%s overrode the Codex review of %s (%s)%s" % (
                    name, tip[:7], _plural(facts["findings"]),
                    "; %s asked to agree or object" % label(roster, reviewer) if reviewer else ""),
                "body": body, "refs": ["%s@%s" % (pid, tip[:7])]}
        if report:
            data["report"] = report
        if reviewer:
            data["reviewer"] = reviewer
            data["request"] = ask_id(pid, tip, diff)
        if not write(pid, "set", did, data):
            return False
        if reviewer:
            ask = {"from": POSTER, "kind": "request", "source": SOURCE, "to": [reviewer], "replyTo": did,
                   "project": pid, "tip": tip,
                   "summary": "%s: agree or object to %s's override of the Codex review of %s"
                              % (label(roster, reviewer), name, tip[:7]),
                   "body": ("%s set aside %s on %s with `codex-review resolve`. Read the report%s and "
                            "the reason in the thread above, then answer in this thread with "
                            "`relay.py reply --from %s --to-id %s` and a summary that starts with "
                            "\"Agree:\" or \"Object:\" (or pass a `position` field of \"agree\" or "
                            "\"object\"), saying why. Objections are advisory: nothing blocks the "
                            "branch; %s and the owner see them."
                            % (name, _plural(facts["findings"]), tip[:7],
                               " at " + report if report else "", reviewer, ask_id(pid, tip, diff),
                               name))}
            write(pid, "set", ask_id(pid, tip, diff), ask)
        return "posted"

    @staticmethod
    def _override_body(roster, pid, tip, facts, heads, reason, report, reviewer) -> str:
        lines = ["%s overrode the Codex review of %s (%s, %s)." % (
            roster.participants[pid].name if pid in roster.participants else pid, tip[:7],
            facts["tier"], facts["verdict"] or "no report")]
        if heads:
            lines.append("")
            lines.append("Findings set aside:")
            lines.extend("- " + h for h in heads)
            if facts["findings"] > len(heads):
                lines.append("- and %d more" % (facts["findings"] - len(heads)))
        lines += ["", "Reason: " + reason]
        if report:
            lines += ["", "Report: " + report]
        if reviewer:
            lines += ["", "%s is asked to agree or object in this thread. Objections are advisory: "
                          "nothing blocks the branch." % label(roster, reviewer)]
        return "\n".join(lines)

    @staticmethod
    def _next_reviewer(pid, roster, docs, overlay) -> Optional[str]:
        """The other project to ask, in turn: the approved projects with a reviewDir, by id,
        indexed by how many overrides this project already has."""
        others = sorted(q for q, p in roster.participants.items()
                        if q != pid and p.reviewDir and p.kind == "project" and p.trust == "approved")
        if not others:
            return None
        n = sum(1 for (c, did), d in docs.items() if c == "messages" and is_override(did, d)
                and d.get("project") == pid)
        n += sum(1 for did, d in overlay.items() if is_override(did, d) and d.get("project") == pid
                 and ("messages", did) not in docs)
        return others[n % len(others)]

    def _objections(self, docs, roster, messages, overlay, budget, plan, write) -> None:
        threads = _Threads(messages)
        for oid in sorted(messages):
            doc = messages[oid]
            if not is_override(oid, doc) or doc.get("resolution"):
                continue
            pid = doc["project"]
            if budget.get(pid, self.cap) <= 0:
                continue
            status, objections, agreed = override_status(messages, threads, oid, roster)
            if (status != (doc.get("status") or "awaiting") or objections != (doc.get("objections") or [])
                    or agreed != (doc.get("agreed") or [])):
                if not write(pid, "update", oid, {"status": status, "objections": objections,
                                                  "agreed": agreed}):
                    continue
                budget[pid] = budget.get(pid, self.cap) - 1
            for ob in objections:
                fid = forward_id(ob["id"])
                if overlay.get(fid) or messages.get(fid) or budget.get(pid, self.cap) <= 0:
                    continue
                who = label(roster, ob["from"])
                tip = doc.get("tip") or ""
                data = {"from": POSTER, "kind": "request", "source": SOURCE, "to": [pid],
                        "replyTo": ob["id"], "project": pid, "tip": tip,
                        "summary": "%s: %s objects to your override of the Codex review of %s"
                                   % (label(roster, pid), who, tip[:7]),
                        "body": ("%s objects: %s\n\nThis is advisory: nothing blocks the branch. "
                                 "Answer in this thread (`relay.py reply --to-id %s`): fix it, or "
                                 "say why the override stands. The owner can settle it on the "
                                 "page (Go with ...)." % (who, ob["summary"], fid))}
                if write(pid, "set", fid, data):
                    budget[pid] = budget.get(pid, self.cap) - 1
