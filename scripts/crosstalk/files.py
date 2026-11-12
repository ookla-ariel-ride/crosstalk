"""File helpers the other modules share."""
import json
import os
import tempfile
from pathlib import Path


def utf8_json(obj, **kwargs) -> str:
    """JSON text that encodes as UTF-8: the characters as they are, or, when a
    string holds a lone surrogate (the log check accepts "\\ud800"), the whole
    text with \\u escapes. Never `surrogatepass`, which writes invalid UTF-8."""
    text = json.dumps(obj, ensure_ascii=False, **kwargs)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return json.dumps(obj, ensure_ascii=True, **kwargs)
    return text


def atomic_write_text(path: Path, text: str) -> None:
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
