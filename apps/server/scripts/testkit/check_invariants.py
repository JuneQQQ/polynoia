#!/usr/bin/env python3
"""Event-log invariant checker — Layer-1 + Layer-5 of the verification framework.

The premise (see docs/design + the verification reflection): green unit tests do
NOT prove the multi-agent system is sound. The bugs that bite live in the EVENT
STREAM — turn_id drift, running states with no terminal, tool-calls escaping their
turn/discussion/burst, raw ``<tool_call>`` leaking into chat body, discuss() that
no-ops. Those are invisible to per-unit tests but checkable as INVARIANTS over the
persisted message log.

This module is the repeatable regression baseline: feed it a conversation's
messages and it returns every invariant violation, located to a message id. It is
pure (``check_conversation`` takes data, returns violations) so it is unit-tested
in tests/testkit/test_invariants.py, and it has a ``main()`` that runs against a
live backend so it can be a release-gate / daily-cron check.

Usage:
    python3 scripts/testkit/check_invariants.py                 # all convs on :7780
    python3 scripts/testkit/check_invariants.py --base http://host:7780
    python3 scripts/testkit/check_invariants.py --conv <id>     # one conversation
    # exit code 0 = all green, 1 = violations found

Invariants checked (each maps to the framework's Layer-1/Layer-5 list):
    INV1  message_id unique within a conversation
    INV2  every non-user card kind (incl. tasks/discussion/files ANCHORS) has turn_id
    INV3  no tasks/burst card stuck in a non-terminal task state on a settled conv
    INV4  a discussion_id on a child message resolves to a real discussion anchor
    INV5  discuss() called  ⇒  a discussion anchor was produced (no-op detector)
    INV6  a done discussion has a conclusion_message_id and >= 2 participants
    INV7  no raw <tool_call>/<tool_response>/<tool_result> leaked into a text body
    INV8  every tasks-card task agent is a member of the conversation
    INV9  in_reply_to references resolve to a loaded message (reply-thread integrity)
    INV12 a conflict card (kind='conflict') reaches a terminal status on a settled conv
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass

# Card kinds that represent a model/agent contribution and therefore MUST carry a
# turn_id so any card can be correlated to the turn that produced it. Anchors
# (tasks=dispatch, discussion=discuss, files=present) are explicitly included —
# that is exactly where turn_id was historically dropped.
TURN_ID_REQUIRED_KINDS = {
    "text", "reasoning", "tool-call", "diff",
    "tasks", "files", "discussion", "terminal",
}
TERMINAL_TASK_STATES = {"done", "failed", "cancelled", "skipped", "merged"}
# A conflict card's real terminal vocabulary (ConflictPayload.status) is exactly
# {open, resolving, resolved, abandoned}; the first two are non-terminal.
CONFLICT_TERMINAL_STATES = {"resolved", "abandoned"}
RAW_TOOL_RE = re.compile(r"</?(?:tool_call|tool_response|tool_result)\b")
DISCUSS_TOOL_NAMES = {"discuss", "mcp__polynoia__discuss"}


@dataclass(frozen=True)
class Violation:
    code: str          # e.g. "INV2"
    rule: str          # human-readable
    conv: str          # conversation title or id
    where: str         # message id / discussion id / detail


def _payload(m: dict) -> dict:
    p = m.get("payload")
    return p if isinstance(p, dict) else {}


def _text_body(p: dict) -> str:
    out = []
    for b in p.get("body") or []:
        c = b.get("c") if isinstance(b, dict) else None
        if isinstance(c, str):
            out.append(c)
    return "".join(out)


def check_conversation(conv: dict, msgs: list[dict]) -> list[Violation]:
    """Pure invariant check over one conversation's ordered messages."""
    title = conv.get("title") or conv.get("id") or "?"
    members = set(conv.get("members") or [])
    settled = not conv.get("running")  # best-effort; absent → treat as settled
    V: list[Violation] = []

    # INV1 — id uniqueness
    ids = [m.get("id") for m in msgs]
    seen: set[str] = set()
    for mid in ids:
        if mid in seen:
            V.append(Violation("INV1", "duplicate message_id", title, str(mid)))
        seen.add(mid)

    discussion_anchor_ids: set[str] = set()      # discussion_id values with an anchor
    discuss_tool_calls = 0

    for m in msgs:
        p = _payload(m)
        kind = p.get("kind")
        mid = m.get("id", "?")
        sender = m.get("sender_id")

        # INV2 — turn_id present on agent card kinds (column OR payload fallback)
        if sender != "you" and kind in TURN_ID_REQUIRED_KINDS:
            tid = m.get("turn_id") or p.get("turn_id")
            if not (isinstance(tid, str) and tid):
                V.append(Violation("INV2", f"{kind} card missing turn_id", title, mid))

        # INV7 — raw tool protocol leaked as body text
        if kind == "text" and RAW_TOOL_RE.search(_text_body(p)):
            V.append(Violation("INV7", "raw <tool_*> leaked into text body", title, mid))

        if kind == "discussion":
            did = p.get("discussion_id")
            if isinstance(did, str):
                discussion_anchor_ids.add(did)
            # INV6 — a done discussion must conclude + have >= 2 participants
            parts = p.get("participants") or []
            if len(parts) < 2:
                V.append(Violation("INV6", "discussion has < 2 participants", title, str(did)))
            if p.get("status") == "done" and not p.get("conclusion_message_id"):
                V.append(Violation("INV6", "done discussion w/o conclusion_message_id", title, str(did)))

        if kind == "tool-call":
            name = str(p.get("name") or p.get("tool") or "")
            if name in DISCUSS_TOOL_NAMES or name.endswith("__discuss"):
                discuss_tool_calls += 1

        if kind == "tasks":
            for t in p.get("tasks") or []:
                a = t.get("agent")
                # INV8 — task agent is a real member
                if a and members and a not in members:
                    V.append(Violation("INV8", "task agent not a conv member", title, str(a)))
            # INV3 — no non-terminal task on a settled conversation
            if settled:
                stuck = [t.get("state") for t in (p.get("tasks") or [])
                         if t.get("state") not in TERMINAL_TASK_STATES]
                if stuck:
                    V.append(Violation("INV3", f"tasks stuck non-terminal: {stuck}", title, mid))

        # INV12 — a conflict card must reach a terminal status on a settled conv
        if kind == "conflict" and settled:
            st = p.get("status")
            if st not in CONFLICT_TERMINAL_STATES:
                V.append(Violation("INV12", f"conflict card not terminal (status={st!r})", title, mid))

    # INV4 — every child discussion_id resolves to an anchor
    for m in msgs:
        p = _payload(m)
        if p.get("kind") == "discussion":
            continue
        did = p.get("discussion_id")
        if isinstance(did, str) and did and did not in discussion_anchor_ids:
            V.append(Violation("INV4", "discussion_id without an anchor card", title, m.get("id", "?")))

    # INV9 — in_reply_to resolves to a loaded message (reply-thread integrity).
    # Skip when the page may be truncated (a target could be unloaded, not missing);
    # a rewound/deleted target is by-design and would false-positive, so this is a
    # best-effort check on fully-loaded conversations.
    if len(msgs) < 500:
        idset = {i for i in ids if i}
        for m in msgs:
            rt = m.get("in_reply_to")
            if isinstance(rt, str) and rt and rt not in idset:
                V.append(Violation("INV9", "in_reply_to references a missing message", title, m.get("id", "?")))

    # INV5 — discuss() called but produced no anchor (the no-op detector)
    if discuss_tool_calls > 0 and not discussion_anchor_ids:
        V.append(Violation("INV5", f"discuss() called x{discuss_tool_calls} but NO discussion anchor (no-op)",
                           title, "—"))

    return V


# ── live runner ────────────────────────────────────────────────────────────
def _get(base: str, path: str) -> object:
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.load(r)


def _messages(base: str, cid: str) -> list[dict]:
    d = _get(base, f"/api/conversations/{cid}/messages?limit=500")
    return d.get("messages", d) if isinstance(d, dict) else d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:7780")
    ap.add_argument("--conv", default=None, help="check only this conversation id")
    args = ap.parse_args(argv)

    convs = _get(args.base, "/api/conversations")
    if args.conv:
        convs = [c for c in convs if c.get("id") == args.conv]

    all_v: list[Violation] = []
    checked = 0
    for c in convs:
        msgs = _messages(args.base, c["id"])
        if not msgs:
            continue
        checked += 1
        all_v.extend(check_conversation(c, msgs))

    from collections import Counter
    by_code = Counter(v.code for v in all_v)
    print(f"checked {checked} non-empty conversation(s)")
    if not all_v:
        print("✅ all event-log invariants hold")
        return 0
    print(f"❌ {len(all_v)} violation(s):")
    for code in sorted(by_code):
        print(f"  [{by_code[code]}] {code}")
    for v in all_v[:40]:
        print(f"   {v.code} · {v.rule} · «{v.conv}» · {v.where}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
