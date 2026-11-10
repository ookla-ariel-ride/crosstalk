"""Participant tokens: mint them, keep only their hash, verify them.

`tokens.json` maps a participant id to the sha256 of its token. The token
itself is printed once, at minting, and never written down: nothing here
ever puts one in a log, a response or an error message.

Python 3.9, standard library only.
"""
import hashlib
import hmac
import json
import os
import secrets
import tempfile
from pathlib import Path
from typing import Dict, Optional

TOKEN_FILE = "tokens.json"


def token_hash(token: str) -> str:
    """The sha256 hex of a token: what is kept at rest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_token() -> str:
    """A fresh token: 24 random bytes, URL-safe."""
    return secrets.token_urlsafe(24)


class Result:
    """The outcome of a token check.

    `code` says why: "ok", "missing" (no header), "malformed" (not a bearer
    token), "unknown" (no such token) or "mismatch" (another participant's).
    """

    def __init__(self, ok: bool, pid: Optional[str] = None, code: str = "", error: str = ""):
        self.ok = ok
        self.pid = pid
        self.code = code
        self.error = error

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return "Result(ok=%r, pid=%r, code=%r)" % (self.ok, self.pid, self.code)


class Tokens:
    """The token store: participant id -> sha256 hex of its token."""

    def __init__(self, path, entries: Optional[Dict[str, str]] = None):
        self.path = Path(path)
        self.entries = dict(entries or {})

    @classmethod
    def load(cls, path) -> "Tokens":
        """The store at `path`; an absent or unreadable file is an empty store."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(path)
        if not isinstance(raw, dict):
            return cls(path)
        entries = {}  # type: Dict[str, str]
        for pid, digest in raw.items():
            if isinstance(pid, str) and isinstance(digest, str):
                entries[pid] = digest
        return cls(path, entries)

    def save(self) -> None:
        """Write the store atomically, readable by its owner only."""
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.entries, indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, str(path))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def mint(self, pid: str) -> str:
        """Mint a token for `pid`, keep only its hash, and return the token once."""
        token = mint_token()
        self.entries[pid] = token_hash(token)
        self.save()
        return token

    def verify(self, auth_header, pid: Optional[str] = None) -> Result:
        """Check an Authorization header.

        With `pid`, the token must be that participant's. The comparison is on
        hashes and constant-time; the token itself is never compared, logged
        or echoed.
        """
        if not auth_header or not isinstance(auth_header, str):
            return Result(False, code="missing", error="no authorization header")
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
            return Result(False, code="malformed", error="not a bearer token")
        digest = token_hash(parts[1])
        found = None  # type: Optional[str]
        for other, stored in self.entries.items():
            if hmac.compare_digest(digest, stored):
                found = other
        if found is None:
            return Result(False, code="unknown", error="unknown token")
        if pid is not None and found != pid:
            return Result(False, pid=found, code="mismatch",
                          error="the token belongs to another participant")
        return Result(True, pid=found, code="ok")


def load_tokens(path) -> Optional[Tokens]:
    """The store at `path`, or None when there is no file yet."""
    p = Path(path)
    if not p.exists():
        return None
    return Tokens.load(p)
