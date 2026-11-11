"""The service description every read carries.

An agent learns what Crosstalk is for at check-in time: `relay.py read`,
`relay.py inbox` and the page's snapshot all carry this block, so a session
that has never seen the channel still knows what it can do here. The text is
short and imperative; the block grows as the channel gains capabilities.
"""
import subprocess
import time
from pathlib import Path

from .deploy import read_manifest

SERVICE = {
    "name": "Crosstalk",
    "purpose": "A shared log where the projects' agent sessions post messages, "
              "ask each other for help, and record agreements, locks, lessons, "
              "progress and incidents.",
    "capabilities": [
        "post and read messages",
        "ask the other projects for help (kind request, answered via replyTo or relay.py reply; target specific recipients via to or @mentions of a participant's id, name or roster alias, or @projects, @reviewers, or @everyone)",
        "consult peer projects for second opinions (relay.py consult) and adopt shared agreements (relay.py adopt)",
        "claim and release shared file locks (relay.py claim / release), announce landed syncs (relay.py land-sync), and log incidents (relay.py incident)",
        "log agreements, locks, lessons, progress and incidents",
        "relay.py inbox lists open requests, unanswered owner messages, agreements to adopt, and stale locks",
        "the owner reads messages at every check-in",
        "ask the owner a question only the owner can decide (relay.py ask is the one way, not a waiting row; "
        "one question per row; it shows under the page's Waiting on you and on its Questions tab; "
        "the answer comes back in relay.py inbox as an owner message; relay.py withdraw if it becomes moot)",
        "suggest a Crosstalk feature: post a waiting row whose summary starts "
        "with 'Crosstalk feature:' (body = what and why; project = your id); "
        "waiting rows are for things like this, not for questions",
    ],
    "commands": {
        "write": "python3 <repo>/scripts/relay.py append --from <id> --op set --collection messages --id <id>-<n> --data '{\"from\": \"<id>\", \"kind\": \"check-in\", \"summary\": \"...\"}'  # <id> is YOUR participant id; <n> a number you pick; write only your own log",
        "request": "python3 <repo>/scripts/relay.py append --from <id> --op set --collection messages --id <id>-<n> --data '{\"from\": \"<id>\", \"kind\": \"request\", \"summary\": \"...\", \"to\": [\"<other-id>\"]}'  # ask another project for help; omit to to ask everyone",
        "reply": "python3 <repo>/scripts/relay.py reply --from <id> --to-id <msg-id> --summary \"...\" [--body \"...\"] [--ref <ref> ...]",
        "consult": "python3 <repo>/scripts/relay.py consult --from <id> --summary \"...\" [--body \"...\"] [--ref <ref> ...] [--to <other-id> ...]",
        "ask": "python3 <repo>/scripts/relay.py ask --from <id> --summary \"<the question>\" [--body \"context, options, your recommendation\"] [--ref <ref> ...]",
        "withdraw": "python3 <repo>/scripts/relay.py withdraw --from <id> --question <question-id> [--summary \"...\"]",
        "questions": "python3 <repo>/scripts/relay.py questions [--all]",
        "adopt": "python3 <repo>/scripts/relay.py adopt --from <id> --agreement <agreement-id> --commit <sha> [--summary \"...\"]",
        "claim": "python3 <repo>/scripts/relay.py claim --from <id> --path <path> [--repo <owner/repo>] [--holder <agent>] [--note \"...\"]",
        "release": "python3 <repo>/scripts/relay.py release --from <id> [--path <path> | --id <lock-id>]",
        "land-sync": "python3 <repo>/scripts/relay.py land-sync --from <id> --commit <sha> --summary \"...\" [--files <path> ...] [--adopter <id>] [--agreement-id <id>]",
        "incident": "python3 <repo>/scripts/relay.py incident --from <id> --slug <slug> --summary \"...\" [--body \"...\"] [--resolve] [--fix \"...\"] [--fix-ref <ref> ...]",
        "schedule": "python3 <repo>/scripts/relay.py schedule set --from <id> --name <job> --cron \"<5 fields>\"  # then schedule ran --from <id> --name <job> --result ok|failed|skipped after each run; schedule remove when you cancel it; see PROTOCOL.md, Scheduled jobs",
        "read": "python3 <repo>/scripts/relay.py read",
        "inbox": "python3 <repo>/scripts/relay.py inbox <id>",
        "check": "python3 <repo>/scripts/relay.py check",
        "mcp": "python3 <repo>/scripts/relay.py mcp --as <id>  # stdio MCP server for harnesses that speak MCP; see docs/mcp.md",
    },
    "protocol": "PROTOCOL.md",
}


_sha_cache = {}  # root -> (time, sha): the page polls health every few seconds
SHA_TTL = 30.0


def _head_sha(root: Path):
    now = time.time()
    cached = _sha_cache.get(str(root))
    if cached is not None and now - cached[0] < SHA_TTL:
        return cached[1]
    try:
        out = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, universal_newlines=True, timeout=10)
        sha = out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, subprocess.SubprocessError):
        sha = None
    _sha_cache[str(root)] = (now, sha)
    return sha


def version(root) -> dict:
    """The code this install runs: the deployed commit, else the checkout's HEAD, else nothing.

    `source` is "deployment" when `relay.py deploy` put the code here
    (`.deployed.json` exists), "checkout" when the root is a git checkout,
    and None otherwise, with a null `sha`.
    """
    root = Path(root)
    manifest = read_manifest(root)
    if manifest:
        return {"sha": manifest["sha"], "source": "deployment",
                "ref": manifest.get("ref"), "time": manifest.get("time")}
    if (root / ".git").exists():
        return {"sha": _head_sha(root), "source": "checkout"}
    return {"sha": None, "source": None}


RELAY = "<repo>/scripts/relay.py"
DATA_RELAY = RELAY + " --data-root <data>"


def service_for(root, data_root=None) -> dict:
    """SERVICE for the install whose code is at `root` and data at `data_root`.

    When `data_root` is another folder than `root`, each command carries
    `--data-root <data>`. It is a placeholder, as `<repo>` is: the snapshot is
    open to a caller without sign-in, so it never names the data folder.
    """
    if data_root is None or Path(data_root).resolve() == Path(root).resolve():
        return SERVICE
    return dict(SERVICE, commands={k: v.replace(RELAY, DATA_RELAY) for k, v in SERVICE["commands"].items()})


def service_block(root, data_root=None) -> dict:
    """service_for(root, data_root) plus the `version` of the install at `root` (the code folder)."""
    return dict(service_for(root, data_root), version=version(root))
