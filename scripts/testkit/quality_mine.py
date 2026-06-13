#!/usr/bin/env python3
"""Read-only quality / UI-bug miner over the ACCUMULATED conversation DB.

Runs against `~/.polynoia/polynoia.db` opened read-only (WAL → safe to read while
the stress wave keeps writing). Surfaces the signals that matter for the
"质量 / UI BUG 检测" half of the campaign — without touching the event loop or the DB.

Signals:
  1. Reach: convs with ≥1 agent reply vs. started-but-silent (only the user msg).
  2. Empty agent text bubbles (kind=text, sender≠you, body blank) — a render bug.
  3. Tool-call error rate (state=='error' / is_error) + top failing tools.
  4. Terminal command failures (exit_code∉{0,None}) + still-running terminal cards.
  5. Payload-kind sanity (unknown kind = would break the render registry).
  6. 承重 git invariants across every workspace (single HEAD / no MERGE_HEAD / no markers).

Usage:  python3 quality_mine.py [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

DB = f"file:{Path.home()}/.polynoia/polynoia.db?mode=ro"
SANDBOX_WS = Path.home() / "sandbox" / "polynoia" / "workspaces"
# Mirror the frontend PARTS_REGISTRY (apps/web/src/components/parts/index.tsx) —
# a kind absent here = would fall through the render registry. `error` (persisted
# turn-failure card) and `conflict` are legit kinds, not bugs.
KNOWN_KINDS = {
    "text", "tasks", "diff", "web", "swatches", "copy", "metrics", "sql",
    "schema", "logs", "api", "typing", "ask-form", "tool-call", "reasoning",
    "terminal", "files", "file", "image", "error", "conflict",
}


def text_blank(d: dict) -> bool:
    """Mirror the frontend (MessageView.isEmptyStreamingTextPayload / payloadText):
    a text payload is blank iff every body block's `c` (str OR inline-segment list)
    carries no non-space text and no mention. NB the frontend does NOT render a
    finalized (non-streaming) empty text payload at all (isRenderableMessagePayload),
    so a blank persisted text body is NOT a visible bubble — only meaningful as a
    streaming-interruption signal, never a render bug."""
    if d.get("kind") != "text":
        return False
    body = d.get("body")
    if isinstance(body, str):
        return not body.strip()
    if not isinstance(body, list):
        return not body
    for block in body:
        if not isinstance(block, dict):
            if str(block).strip():
                return False
            continue
        c = block.get("c")
        if isinstance(c, str):
            if c.strip():
                return False
        elif isinstance(c, list):
            for seg in c:
                if isinstance(seg, dict):
                    if seg.get("type") == "mention":
                        return False
                    if (seg.get("text") or "").strip():
                        return False
                elif str(seg).strip():
                    return False
        elif c:
            return False
    return True


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(DB, uri=True)


def _tables(c) -> set[str]:
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _git(ws: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(ws), *args],
                       capture_output=True, text=True, timeout=30)
    return p.returncode, (p.stdout or p.stderr).strip()


def git_invariants(ws_id: str) -> list[str]:
    ws = SANDBOX_WS / ws_id
    if not (ws / ".git").exists():
        return []
    bad: list[str] = []
    if (ws / ".git" / "MERGE_HEAD").exists():
        bad.append("MERGE_HEAD present")
    rc, out = _git(ws, "grep", "-l", "-E", r"^<<<<<<< |^>>>>>>> |^=======$", "HEAD")
    if rc == 0 and out:
        bad.append(f"conflict markers in {out.splitlines()[:3]}")
    rc, head = _git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    if rc == 0 and head not in ("main", "HEAD"):
        bad.append(f"root not on main: {head}")
    return bad


def mine() -> dict:
    c = _conn()
    tables = _tables(c)

    # conv titles
    titles: dict[str, str] = {}
    if "conversations" in tables:
        cols = {r[1] for r in c.execute("PRAGMA table_info(conversations)")}
        tcol = "title" if "title" in cols else None
        if tcol:
            for cid, t in c.execute(f"SELECT id, {tcol} FROM conversations"):
                titles[cid] = t or cid

    rows = list(c.execute(
        "SELECT conv_id, sender_id, payload FROM messages ORDER BY conv_id, created_at"
    ))

    per_conv_agent_msgs: dict[str, int] = defaultdict(int)
    per_conv_user_msgs: dict[str, int] = defaultdict(int)
    empty_text: list[tuple[str, str]] = []          # (conv, sender)
    unknown_kind: list[tuple[str, str]] = []         # (conv, kind)
    tool_total = 0
    tool_err = 0
    tool_err_by_name: Counter = Counter()
    term_total = 0
    term_fail: list[tuple[str, int, str]] = []       # (conv, exit, cmd)
    term_running = 0

    for conv, sender, payload in rows:
        try:
            d = json.loads(payload)
        except Exception:
            unknown_kind.append((conv, "<unparseable>"))
            continue
        kind = d.get("kind", "?")
        is_user = sender == "you"
        if kind not in KNOWN_KINDS:
            unknown_kind.append((conv, kind))
        if is_user:
            per_conv_user_msgs[conv] += 1
        else:
            per_conv_agent_msgs[conv] += 1

        if kind == "text" and not is_user and text_blank(d):
            empty_text.append((conv, sender))
        elif kind == "tool-call":
            tool_total += 1
            if d.get("state") == "error" or d.get("is_error"):
                tool_err += 1
                tool_err_by_name[d.get("name", "?")] += 1
        elif kind == "terminal":
            term_total += 1
            ec = d.get("exit_code")
            if ec not in (0, None):
                term_fail.append((conv, ec, (d.get("command") or "")[:50]))
            if d.get("running"):
                term_running += 1

    # Classify empty bubbles: trailing (conv ends here → interrupted turn) vs
    # stray (real agent content follows → a finalized empty bubble = render bug).
    # rows are ordered by (conv_id, created_at), so group preserves order.
    by_conv: dict[str, list] = defaultdict(list)
    for conv, sender, payload, *_ in (r + (None,) for r in rows):
        by_conv[conv].append((sender, payload))

    _is_empty_text = text_blank

    empty_trailing = 0
    empty_stray: list[str] = []
    for conv, msgs in by_conv.items():
        for i, (sender, payload) in enumerate(msgs):
            if sender == "you":
                continue
            try:
                d = json.loads(payload)
            except Exception:
                continue
            if not _is_empty_text(d):
                continue
            follows = False
            for s2, p2 in msgs[i + 1:]:
                if s2 == "you":
                    continue
                try:
                    d2 = json.loads(p2)
                except Exception:
                    continue
                if (d2.get("kind") == "text" and not _is_empty_text(d2)) or \
                        d2.get("kind") in ("tool-call", "terminal", "diff", "files", "tasks"):
                    follows = True
                    break
            if follows:
                empty_stray.append(conv)
            else:
                empty_trailing += 1

    # convs that have any message
    active = set(per_conv_agent_msgs) | set(per_conv_user_msgs)
    silent = [cv for cv in active if per_conv_agent_msgs.get(cv, 0) == 0]

    # 承重 git sweep over every workspace dir on disk
    git_violations: dict[str, list[str]] = {}
    if SANDBOX_WS.exists():
        for ws in SANDBOX_WS.iterdir():
            if ws.is_dir():
                bad = git_invariants(ws.name)
                if bad:
                    git_violations[ws.name] = bad

    return {
        "convs_active": len(active),
        "convs_with_agent_reply": len([1 for v in per_conv_agent_msgs.values() if v]),
        "convs_silent": [(cv, titles.get(cv, cv)) for cv in silent],
        "empty_agent_text_bubbles": [(titles.get(cv, cv), s) for cv, s in empty_text],
        "empty_trailing": empty_trailing,
        "empty_stray": [titles.get(cv, cv) for cv in empty_stray],
        "tool_calls_total": tool_total,
        "tool_calls_error": tool_err,
        "tool_error_rate": round(tool_err / tool_total, 3) if tool_total else 0.0,
        "tool_error_by_name": tool_err_by_name.most_common(10),
        "terminals_total": term_total,
        "terminal_failures": [(titles.get(cv, cv), ec, cmd) for cv, ec, cmd in term_fail],
        "terminals_still_running": term_running,
        "unknown_payload_kinds": unknown_kind,
        "git_violations": git_violations,
        "workspaces_on_disk": len(list(SANDBOX_WS.iterdir())) if SANDBOX_WS.exists() else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = mine()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return
    print("== 质量挖掘(累积 DB,只读)==")
    print(f"活跃会话: {r['convs_active']}  有 agent 回复: {r['convs_with_agent_reply']}"
          f"  静默(只有用户消息): {len(r['convs_silent'])}")
    if r["convs_silent"]:
        for cv, t in r["convs_silent"][:10]:
            print(f"   · 静默 {t}")
    print(f"\n空 agent 文本气泡: {len(r['empty_agent_text_bubbles'])} "
          f"= trailing(中断,末尾) {r['empty_trailing']} + stray(轮中,疑真 bug) {len(r['empty_stray'])}")
    for t in r["empty_stray"][:10]:
        print(f"   !! STRAY {t}")
    print(f"\n工具调用: {r['tool_calls_total']}  错误: {r['tool_calls_error']}"
          f"  错误率: {r['tool_error_rate']}")
    for name, n in r["tool_error_by_name"]:
        print(f"   · {name}: {n} 次错误")
    print(f"\n终端卡: {r['terminals_total']}  失败(exit≠0): {len(r['terminal_failures'])}"
          f"  仍在运行: {r['terminals_still_running']}(进行中会话属正常)")
    for t, ec, cmd in r["terminal_failures"][:10]:
        print(f"   · exit={ec} [{t}] {cmd}")
    print(f"\n未知 payload kind(会崩渲染): {len(r['unknown_payload_kinds'])}")
    for cv, k in r["unknown_payload_kinds"][:10]:
        print(f"   !! {k} in {cv}")
    print(f"\n承重 git 不变量 —— 工作区 {r['workspaces_on_disk']} 个,"
          f"违反 {len(r['git_violations'])} 个")
    for ws, bad in list(r["git_violations"].items())[:10]:
        print(f"   !! {ws}: {bad}")


if __name__ == "__main__":
    main()
