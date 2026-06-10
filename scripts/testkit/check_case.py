#!/usr/bin/env python3
"""Per-case health check for testkit E2E runs.

Usage:  python3 scripts/testkit/check_case.py "<workspace name prefix>"

Checks, for the conversation of the named workspace:
  1. message census (kinds, senders)
  2. INVARIANT: no blocking terminal card left running=true
  3. INVARIANT: no tool-call left pending/running
  4. INVARIANT: no empty-body reasoning/text rows persisted
  5. terminal cards with exit!=0 (surfaced, not a failure by itself)
  6. present/files card present?
  7. workspace main checkout: deliverable file listing (top 2 levels)
  8. process_runs leftover running rows

Exit code 0 = invariants hold; 1 = at least one invariant violated.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

DB = os.path.expanduser("~/.polynoia/polynoia.db")
SANDBOX = os.path.expanduser("~/sandbox/polynoia/workspaces")


def text_of(payload: dict) -> str:
    body = payload.get("body") or []
    s = ""
    for b in body:
        c = b.get("c") if isinstance(b, dict) else None
        if isinstance(c, str):
            s += c
        elif isinstance(c, list):
            s += "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return s


def main() -> int:
    name = sys.argv[1]
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    ws = c.execute(
        "select * from workspaces where name like ? || '%' limit 1", (name,)
    ).fetchone()
    if not ws:
        print(f"!! workspace '{name}' not found")
        return 1
    conv = c.execute(
        "select * from conversations where workspace_id=? limit 1", (ws["id"],)
    ).fetchone()
    if not conv:
        print(f"!! no conversation for workspace {ws['id']}")
        return 1
    rows = c.execute(
        "select id,sender_id,payload,created_at from messages where conv_id=? order by created_at",
        (conv["id"],),
    ).fetchall()

    kinds: dict[str, int] = {}
    bad: list[str] = []
    warn: list[str] = []
    has_present = False
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except Exception:
            bad.append(f"unparseable payload msg={r['id']}")
            continue
        k = p.get("kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
        if k == "terminal":
            if p.get("running") is True and p.get("mode", "blocking") == "blocking":
                bad.append(f"BLOCKING terminal still running: {r['id']} cmd={p.get('command','')[:50]!r}")
            ec = p.get("exit_code")
            if isinstance(ec, int) and ec != 0 and p.get("running") is not True:
                warn.append(f"terminal exit={ec}: {r['id']} cmd={p.get('command','')[:50]!r}")
        elif k == "tool-call":
            if p.get("state") in ("pending", "running"):
                bad.append(f"tool-call stuck {p.get('state')}: {r['id']} name={p.get('name')}")
        elif k in ("reasoning", "text"):
            if not text_of(p).strip():
                bad.append(f"EMPTY {k} persisted: {r['id']} sender={r['sender_id'][:10]}")
        elif k == "files":
            has_present = True

    runs = c.execute(
        "select id,command,mode,status,pid,pgid from process_runs where conv_id=? and status in ('starting','running')",
        (conv["id"],),
    ).fetchall()

    print(f"== {ws['name']}  conv={conv['id']}")
    print(f"   msgs={len(rows)}  kinds={dict(sorted(kinds.items()))}")
    print(f"   present_card={'YES' if has_present else 'no'}")
    for w in warn:
        print(f"   [warn] {w}")
    for b in bad:
        print(f"   [BAD]  {b}")
    if runs:
        print("   process_runs still running:")
        for r in runs:
            alive = "?"
            if r["pgid"]:
                try:
                    os.kill(int(r["pgid"]), 0)
                    alive = "alive"
                except OSError:
                    alive = "DEAD(zombie row)"
            print(f"     {r['id'][:14]} {r['mode']} {r['status']} pgid={r['pgid']}({alive}) cmd={r['command'][:46]!r}")

    root = os.path.join(SANDBOX, ws["id"])
    print(f"   workspace root: {root}")
    if os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", ".venv", ".polynoia", "__pycache__")]
            if depth > 1:
                dirnames[:] = []
                continue
            for f in sorted(filenames):
                fp = os.path.join(dirpath, f)
                rel = os.path.relpath(fp, root)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    sz = -1
                print(f"     {rel}  ({sz}B)")
    else:
        print("     (no main checkout dir)")

    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
