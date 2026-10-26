"""A stand-in agent for wake tests.

Appends one JSON line {argv, cwd, env: {CROSSTALK_*}} to $FAKE_AGENT_OUT, then
behaves per $FAKE_AGENT_MODE: ok (exit 0), fail (exit 3), sleep:<s> (sleep,
then exit 0), hang (ignore SIGTERM, sleep forever).
"""
import json
import os
import signal
import sys
import time


def main():
    record = {"argv": sys.argv[1:], "cwd": os.getcwd(),
              "env": {k: v for k, v in os.environ.items() if k.startswith("CROSSTALK_")}}
    out = os.environ.get("FAKE_AGENT_OUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    mode = os.environ.get("FAKE_AGENT_MODE", "ok")
    if mode == "fail":
        sys.exit(3)
    if mode.startswith("sleep:"):
        time.sleep(float(mode.split(":", 1)[1]))
        sys.exit(0)
    if mode == "hang":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(60)
    sys.exit(0)


if __name__ == "__main__":
    main()
