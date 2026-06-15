#!/usr/bin/env python3
"""Restore draft_text for convs that FAILED (auth/rate-limit) in a stress run, so
they can be re-run. Leaves OK convs empty (skipped) and pending convs untouched.

Source of truth for which convs failed = the stress500 log's per-conv lines
(`[ N] ERR ... | <title>`). Draft text comes from seed_cases.CASES (title→task).

  python3 restore_failed_drafts.py /tmp/stress-full-500.log
"""
from __future__ import annotations
import ast, json, sys, urllib.request
from pathlib import Path

API = "http://localhost:7780"
LOG = sys.argv[1] if len(sys.argv) > 1 else "/tmp/stress-full-500.log"


def req(path: str, body=None, method="GET"):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data,
                               headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read() or "null")


def title_to_task() -> dict[str, str]:
    src = Path(__file__).with_name("seed_cases.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "CASES" for t in node.targets):
            cases = ast.literal_eval(node.value)
            return {c[1]: c[5] for c in cases}  # title -> task(draft)
    raise SystemExit("CASES not found")


def main() -> None:
    t2t = title_to_task()
    err_titles, ok_titles = set(), set()
    for ln in Path(LOG).read_text(errors="ignore").splitlines():
        if "] ERR" in ln and "|" in ln:
            err_titles.add(ln.split("|", 1)[1].strip())
        elif "] OK" in ln and "|" in ln:
            ok_titles.add(ln.split("|", 1)[1].strip())
    print(f"log: {len(ok_titles)} OK · {len(err_titles)} ERR titles")

    convs = req("/api/conversations")
    restored = skipped_ok = missing = already = 0
    for c in convs:
        title = c.get("title", "")
        if title in err_titles:
            task = t2t.get(title)
            if not task:
                missing += 1; continue
            req(f"/api/conversations/{c['id']}/draft", {"draft_text": task}, "PATCH")
            restored += 1
        elif title in ok_titles:
            skipped_ok += 1
    print(f"restored drafts on {restored} failed convs · left {skipped_ok} OK convs empty · "
          f"{missing} title-unmatched")
    draftful = [c for c in req("/api/conversations") if (c.get("draft_text") or "").strip()]
    print(f"→ draftful now: {len(draftful)} (should ≈ 155 failed + 139 pending)")


if __name__ == "__main__":
    main()
