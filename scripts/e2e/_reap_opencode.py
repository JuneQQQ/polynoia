#!/usr/bin/env python3
"""Kill leaked `opencode acp` children older than THRESHOLD seconds (default 600).
The opencode adapter doesn't reap its child after a turn; over a long test run
these pile up. The active case's child is young, so an age threshold spares it.
"""
import re
import subprocess
import sys

THRESH = int(sys.argv[1]) if len(sys.argv) > 1 else 600


def secs(et):
    et = et.strip()
    d = 0
    if "-" in et:
        d, et = et.split("-")
        d = int(d)
    p = [int(x) for x in et.split(":")]
    while len(p) < 3:
        p = [0] + p
    return d * 86400 + p[0] * 3600 + p[1] * 60 + p[2]


out = subprocess.run(["ps", "-eo", "pid,etime,command"], capture_output=True, text=True).stdout
killed = 0
for line in out.splitlines():
    if "opencode acp" not in line:
        continue
    m = re.match(r"\s*(\d+)\s+(\S+)", line)
    if m and secs(m.group(2)) > THRESH:
        try:
            subprocess.run(["kill", "-9", m.group(1)])
            killed += 1
        except Exception:
            pass
print(f"reaped {killed}")
