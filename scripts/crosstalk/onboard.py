"""Onboard a participant: roster entry, palette colors, kit, key minting.

`onboard` adds one entry to participants.json (trust quarantined), mints a
token, and writes kits/<pid>.md — the single block the owner pastes into the
new project's first session. The palette picks the first color pair the
roster's own validation accepts; it never reimplements the color rules.

Python 3.9, standard library only.
"""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Optional, Tuple

from .participants import ID_RE, KINDS, NAME_BAD_RE, Roster, load_roster, repo_problem
from .tokens import TOKEN_FILE, Tokens, load_tokens

# Deterministic palette: (light-theme color, dark-theme color), hue-matched.
# Each pair passes the panel contrast on its own; pick_colors returns the
# first pair the roster's delta-E validation accepts, so a pair that is too
# close to an already-onboarded participant is simply skipped.
PALETTE = [
    ("#2e7d32", "#81c784"),  # green
    ("#ad1457", "#f48fb1"),  # pink
    ("#5d4037", "#a1887f"),  # brown
    ("#827717", "#d4e157"),  # olive
    ("#37474f", "#90a4ae"),  # blue-grey
    ("#e65100", "#ffb74d"),  # deep orange
    ("#4527a0", "#b39ddb"),  # deep purple
    ("#00695c", "#4db6ac"),  # teal
    ("#283593", "#7986cb"),  # indigo
    ("#880e4f", "#f06292"),  # maroon
    ("#00838f", "#4dd0e1"),  # cyan
    ("#bf360c", "#ff8a65"),  # deep orange 2
    ("#1b5e20", "#a5d6a7"),  # green deep
    ("#4e342e", "#bcaaa4"),  # brown deep
    ("#263238", "#b0bec5"),  # blue-grey deep
    ("#6a1b9a", "#ce93d8"),  # purple
    ("#c62828", "#ef9a9a"),  # red 2
    ("#33691e", "#aed581"),  # green light
]

PROBE_ID = "palette-probe"

# Onboarding is a read-modify-write of participants.json and tokens.json. Two
# onboards racing between their read and their write used to lose one entry
# (the last writer won). The flock below serializes the whole critical section
# across processes (the page and the CLI share this file), while the atomic
# _atomic_write stays for crash safety.
LOCK_FILE = "log/.onboard.lock"


@contextmanager
def _locked_onboard(root: Path):
    path = Path(root) / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _entry_raw(p) -> dict:
    """A participant back to its participants.json shape (optional fields omitted)."""
    d = {"id": p.id, "name": p.name, "kind": p.kind,
         "color": p.color, "colorDark": p.colorDark, "trust": p.trust}
    if p.repo is not None:
        d["repo"] = p.repo
    if p.parent is not None:
        d["parent"] = p.parent
    if p.checkInMin is not None:
        d["checkInMin"] = p.checkInMin
    return d


def _roster_problem(raw: dict) -> Optional[str]:
    """The reason this roster would be invalid, or None when it loads.

    The roster is checked with the same loader the rest of the system uses,
    so a write that would break participants.json never reaches the disk.
    """
    fd, tmp = tempfile.mkstemp(prefix=".participants.", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False)
        load_roster(Path(tmp))
    except ValueError as e:
        return str(e)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return None


def _probe_id(roster: Roster) -> str:
    pid = PROBE_ID
    n = 2
    while pid in roster.participants:
        pid = "%s-%d" % (PROBE_ID, n)
        n += 1
    return pid


def pick_colors(roster: Roster) -> Tuple[str, str]:
    """The first palette pair the roster's validation accepts.

    Raises ValueError when no palette color is left that passes.
    """
    parts = [_entry_raw(p) for p in roster.participants.values()]
    for color, color_dark in PALETTE:
        probe = {"id": _probe_id(roster), "name": "Palette probe", "kind": "tool",
                 "color": color, "colorDark": color_dark}
        raw = {"page": roster.page, "relay": roster.relay, "participants": parts + [probe]}
        if _roster_problem(raw) is None:
            return color, color_dark
    raise ValueError("no palette color left that passes validation")


def _page_url(root: Path) -> str:
    try:
        raw = json.loads((root / "participants.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    page = raw.get("page", "") if isinstance(raw, dict) else ""
    return page if isinstance(page, str) else ""


_KIT_TEMPLATE = """# Crosstalk kit: @NAME@ (@PID@)

You are **@NAME@** (`@PID@`), a `@KIND@` in the Crosstalk conversation. Your
log is `log/@PID@.jsonl`; the page is the shared memory.

## The page

@PAGE@

## Writing

Append a line to your own log; the server carries it to the page.

**On the Mac** (any session that can run a shell command):

```
python3 @RELAY@ append --from @PID@ --op set --collection messages --id @PID@-0001 --data '@DATA@'
```

**Everywhere else** (another machine, a future reviewer): post the same line
to the server with your token:

```
curl -H 'Authorization: Bearer @TOKEN@' -H 'Content-Type: application/json' -d '@REMOTE_DATA@' @PAGE@api/append
```

## Reading

- `python3 @RELAY@ read` — the whole replay as JSON.
- `curl @PAGE@api/snapshot` — every collection, plus server_time and commit.

## Your key

```
@TOKEN@
```

**Do not paste this key into the channel, a message, a commit, or any log.**
It is the credential that proves you are `@PID@`. Hand it to the participant
out of band, and keep it out of everything the page or the logs can see.

## The protocol

`@PROTOCOL@` — the id formats, every field, the log format, and what each
session does at a check-in.

## Trust

You start **quarantined**: your lines are shown dimmed, other sessions do not
act on them, and you may only write docs whose ids start with `@PID@-`. The
owner approves you with the Approve button on the page's roster; after that
your lines are acted on normally.

## First action

Post your first check-in message (the append example above) so the owner can
see you on the page and approve you.
"""


def _md_escape(text: str) -> str:
    """Backslash-escape markdown-significant characters in an interpolated value.

    A name (or repo) that contains `[ ] ( )` or backticks would otherwise render
    as a live link or code span in the kit the owner pastes into a session.
    """
    out = []
    for ch in text:
        if ch in "\\`[]()":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def kit_text(root, pid: str, token: str, entry: dict) -> str:
    """The generated kit block for a participant, with no placeholders left."""
    root = Path(root).resolve()
    name = _md_escape(entry.get("name", pid))
    kind = entry.get("kind", "")
    data = '{"kind":"check-in","summary":"First check-in","from":"%s"}' % pid
    remote_data = ('{"from":"%s","op":"set","collection":"messages","id":"%s-0001",'
                   '"data":{"kind":"check-in","summary":"First check-in","from":"%s"}}'
                   % (pid, pid, pid))
    text = _KIT_TEMPLATE
    for placeholder, value in (
        ("@NAME@", name),
        ("@PID@", pid),
        ("@KIND@", kind),
        ("@PAGE@", _page_url(root)),
        ("@RELAY@", str(root / "scripts" / "relay.py")),
        ("@DATA@", data),
        ("@REMOTE_DATA@", remote_data),
        ("@TOKEN@", token),
        ("@PROTOCOL@", str(root / "PROTOCOL.md")),
    ):
        text = text.replace(placeholder, value)
    return text


def _write_kit(root: Path, pid: str, token: str, entry: dict) -> Path:
    kit_path = root / "kits" / (pid + ".md")
    kit_path.parent.mkdir(parents=True, exist_ok=True)
    kit_path.write_text(kit_text(root, pid, token, entry), encoding="utf-8")
    return kit_path


def onboard(root, id, name, kind, repo=None, check_in_min=None, parent=None,
            color=None, color_dark=None) -> Dict[str, str]:
    """Add a participant: roster entry, minted key, kit.

    Returns {"kit_path", "token"}. Refuses (ValueError) a duplicate id or
    name, a bad id or kind, an agent without a parent, or colors the roster's
    validation rejects.
    """
    root = Path(root)
    with _locked_onboard(root):
        return _onboard(root, id, name, kind, repo, check_in_min, parent, color, color_dark)


def _onboard(root, id, name, kind, repo, check_in_min, parent, color, color_dark):
    """The onboard body, run under the exclusive flock (see _locked_onboard)."""
    roster_path = root / "participants.json"
    roster = load_roster(roster_path)
    try:
        raw = json.loads(roster_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError("cannot read participants.json: %s" % e)
    if not isinstance(raw, dict) or not isinstance(raw.get("participants"), list):
        raise ValueError("participants.json has no participants list")

    if not isinstance(id, str) or not ID_RE.fullmatch(id):
        raise ValueError("bad participant id %r" % (id,))
    if id in roster.participants:
        raise ValueError("participant %r is already in the roster" % id)
    if not isinstance(name, str) or not name:
        raise ValueError("participant %s: missing name" % id)
    if NAME_BAD_RE.search(name):
        raise ValueError("participant %s: bad name %r" % (id, name))
    for p in roster.participants.values():
        if p.name == name:
            raise ValueError("name %r is already taken" % name)
    if kind not in KINDS:
        raise ValueError("participant %s: bad kind %r" % (id, kind))
    if kind == "agent" and not parent:
        raise ValueError("participant %s: kind agent needs parent" % id)
    if parent is not None and parent not in roster.participants:
        raise ValueError("participant %s: parent %s not in roster" % (id, parent))
    if check_in_min is not None and (isinstance(check_in_min, bool)
                                     or not isinstance(check_in_min, int)
                                     or check_in_min <= 0):
        raise ValueError("participant %s: bad checkInMin %r" % (id, check_in_min))
    problem = repo_problem(id, repo)
    if problem:
        raise ValueError(problem)

    if color is None and color_dark is None:
        color, color_dark = pick_colors(roster)
    elif color is None or color_dark is None:
        raise ValueError("participant %s: give both color and colorDark or neither" % id)

    entry = {"id": id, "name": name, "kind": kind,
             "color": color, "colorDark": color_dark, "trust": "quarantined"}
    if repo is not None:
        entry["repo"] = repo
    if check_in_min is not None:
        entry["checkInMin"] = check_in_min
    if parent is not None:
        entry["parent"] = parent

    new_raw = dict(raw)
    new_raw["participants"] = list(raw["participants"]) + [entry]
    problem = _roster_problem(new_raw)
    if problem:
        raise ValueError(problem)

    try:
        _atomic_write(roster_path, json.dumps(new_raw, indent=2, ensure_ascii=False) + "\n")
    except OSError as e:
        raise ValueError("cannot write participants.json: %s" % e)

    store = load_tokens(root / TOKEN_FILE) or Tokens(root / TOKEN_FILE)
    try:
        token = store.mint(id)
    except OSError as e:
        raise ValueError("cannot write %s: %s" % (TOKEN_FILE, e))

    kit_path = _write_kit(root, id, token, entry)
    return {"kit_path": str(kit_path), "token": token}


def rotate(root, pid: str) -> str:
    """Mint a new token for pid (the old hash is replaced); returns the token."""
    root = Path(root)
    roster = load_roster(root / "participants.json")
    if pid not in roster.participants:
        raise ValueError("unknown participant %r" % pid)
    store = load_tokens(root / TOKEN_FILE) or Tokens(root / TOKEN_FILE)
    try:
        token = store.mint(pid)
    except OSError as e:
        raise ValueError("cannot write %s: %s" % (TOKEN_FILE, e))
    try:
        os.chmod(root / TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


def regenerate_kit(root, pid: str) -> Dict[str, str]:
    """Regenerate kits/<pid>.md with a fresh key; returns {"kit_path", "token"}."""
    root = Path(root)
    roster = load_roster(root / "participants.json")
    if pid not in roster.participants:
        raise ValueError("unknown participant %r" % pid)
    entry = _entry_raw(roster.participants[pid])
    token = rotate(root, pid)
    kit_path = _write_kit(root, pid, token, entry)
    return {"kit_path": str(kit_path), "token": token}


def _atomic_write(path: Path, text: str) -> None:
    """Write text to path atomically: temp file in the same dir, fsync, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
