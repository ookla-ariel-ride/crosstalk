"""The owner's permanent login PIN, kept hashed in owner-pin.json (git-ignored).

Unlike the startup PIN it does not expire and is not used up; login throttling
still applies. The page sends six digits as a PIN, so the PIN is six digits.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from pathlib import Path

PIN_FILE = "owner-pin.json"
PIN_RE = re.compile(r"^\d{6}$")
ITERATIONS = 200000


def _digest(pin: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)


def set_pin(root, pin: str) -> Path:
    """Store `pin` hashed in <root>/owner-pin.json (0600, written atomically)."""
    if not isinstance(pin, str) or not PIN_RE.fullmatch(pin):
        raise ValueError("the PIN must be exactly six digits")
    salt = secrets.token_bytes(16)
    doc = {"scheme": "pbkdf2_sha256", "iterations": ITERATIONS,
           "salt": salt.hex(), "hash": _digest(pin, salt, ITERATIONS).hex()}
    path = Path(root) / PIN_FILE
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))
    return path


def clear_pin(root) -> bool:
    """Remove the permanent PIN; True when there was one."""
    try:
        (Path(root) / PIN_FILE).unlink()
        return True
    except FileNotFoundError:
        return False


def has_pin(root) -> bool:
    return (Path(root) / PIN_FILE).is_file()


def check_pin(root, pin) -> bool:
    """True when `pin` matches the stored permanent PIN. A missing or malformed
    file matches nothing. The file is read on every call, so a change applies
    without a restart."""
    if not isinstance(pin, str) or not PIN_RE.fullmatch(pin):
        return False
    try:
        doc = json.loads((Path(root) / PIN_FILE).read_text(encoding="utf-8"))
        if doc.get("scheme") != "pbkdf2_sha256":
            return False
        iterations = doc["iterations"]
        if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 1:
            return False
        salt = bytes.fromhex(doc["salt"])
        want = bytes.fromhex(doc["hash"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(_digest(pin, salt, iterations), want)
