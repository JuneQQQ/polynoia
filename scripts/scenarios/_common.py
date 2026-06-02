#!/usr/bin/env python3
"""Shared plumbing for the scenario seed scripts in this directory.

Every `NN_*.py` scenario script is a thin wrapper: it declares a SCENARIO dict
(workspace + group conv + the prompt you should send) and calls `run()`. This
module does the actual work, reusing `scripts/seed_demo.py`'s 5 personas
(林知夏 orch · 顾屿 backend · 沈昭 frontend · 苏念 docs · 周野 fullstack) and its
HTTP helpers, so the cast is identical to a normal clean-init.

How seeding works (same model as seed_demo.py):
  - The live server must be running (`make dev` / `make server`, :7780) — seeding
    talks to its HTTP API.
  - `--fresh` first WIPES the WHOLE DB (drop_all + bootstrap) for a clean slate.
  - Without it the script ACCUMULATES (reuse-or-create), and re-running a scenario
    CLEARS that scenario's conversation timeline (fresh test, members/roles kept)
    while leaving the other scenarios' conversations untouched. So you usually do
    NOT need --fresh to "reset the chat" — just re-run the one scenario.
  - Contacts/workspace/conv are created; ZERO messages are seeded. The scenario's
    header docstring tells you which conversation to open and what to send.

Run a single scenario:
    python3 scripts/scenarios/02_web_game.py            # accumulate
    python3 scripts/scenarios/02_web_game.py --fresh     # wipe first
Run ALL scenarios at once (one wipe, every scenario as its own workspace):
    python3 scripts/scenarios/seed_all.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# scripts/ is the parent dir — import seed_demo from there to reuse personas +
# HTTP helpers + the wipe routine (no duplication of the giant persona prompts).
_SCRIPTS = Path(__file__).resolve().parent.parent
_SERVER = _SCRIPTS.parent / "apps" / "server"
sys.path.insert(0, str(_SCRIPTS))

import seed_demo as sd  # noqa: E402 — relies on the path insert above

# Re-export the standard cast so scenarios reference members by name.
ALL_MEMBERS = ["林知夏", "顾屿", "沈昭", "苏念", "周野"]


def ensure_server_env() -> None:
    """The `--fresh` wipe needs the server's deps (sqlalchemy/aiosqlite). If we're
    under a bare interpreter that lacks them, re-exec under apps/server's uv env so
    `python3 scripts/scenarios/foo.py --fresh` just works. Pure-HTTP seeding (no
    wipe) needs only stdlib, so this is only called on the --fresh path."""
    try:
        import aiosqlite  # noqa: F401
        import sqlalchemy  # noqa: F401
        return
    except ModuleNotFoundError:
        if os.environ.get("_POLYNOIA_SCENARIO_REEXEC"):
            raise
        os.environ["_POLYNOIA_SCENARIO_REEXEC"] = "1"
        # Resolve the ENTRY script to an absolute path BEFORE chdir, else the
        # re-exec'd `uv run python <relative>` resolves against apps/server.
        entry = str(Path(sys.argv[0]).resolve())
        os.chdir(_SERVER)
        os.execvp("uv", ["uv", "run", "python", entry, *sys.argv[1:]])


def _wipe() -> None:
    import asyncio
    ensure_server_env()
    asyncio.run(sd._wipe_and_bootstrap())
    print("✓ DB wiped + schema/base data re-bootstrapped\n")


def ensure_contacts() -> dict[str, str]:
    """Onboard the 3 adapters + reuse-or-create the 5 personas. Returns name→id."""
    for aid in ("claudeCode", "codex", "opencoder"):
        try:
            sd.post(f"/api/agents/{aid}/enable", {})
        except Exception:
            pass  # already onboarded / adapter not installed — non-fatal
    existing = {a["name"]: a for a in sd.get("/api/agents") if a.get("custom")}
    ids: dict[str, str] = {}
    for spec in sd.CONTACTS_SPEC:
        name = spec["name"]
        if name in existing:
            ids[name] = existing[name]["id"]
        else:
            ids[name] = sd.post("/api/contacts", spec)["contact"]["id"]
    return ids


def ensure_workspace(name: str, desc: str, members: list[str], color: str) -> str:
    existing = next(
        (w for w in sd.get("/api/workspaces") if w.get("name") == name), None
    )
    if existing:
        return existing["id"]
    return sd.post("/api/workspaces", {
        "name": name, "desc": desc, "members": members, "color": color,
    })["workspace"]["id"]


def ensure_group_conv(
    ws_id: str,
    title: str,
    members: list[str],
    member_roles: dict[str, str],
    orch: str,
    merge_mode: str,
) -> str:
    convs = sd.get(f"/api/conversations?workspace_id={ws_id}")
    existing = next((c for c in convs if c.get("title") == title), None)
    if existing:
        cid = existing["id"]
        # Reuse → reset THIS conv to an empty timeline so each scenario run is a
        # clean test (keeps members/roles; other scenarios' convs untouched).
        # /clear broadcasts data-conv-cleared so an open frontend drops its
        # message list live. This is why re-running a scenario "resets the chat".
        try:
            sd.post(f"/api/conversations/{cid}/clear", {})
        except Exception:
            pass
    else:
        cid = sd.post("/api/conversations", {
            "workspace_id": ws_id,
            "title": title,
            "members": ["you", *members],
            "group": True,
            "direct": False,
            "member_roles": member_roles,
            "orchestrator_member_id": orch,
        })["id"]
    if merge_mode != "auto":
        sd.patch(f"/api/conversations/{cid}/merge_mode", {"mode": merge_mode})
    return cid


def run(scenario: dict, *, wipe: bool = False) -> int:
    """Seed one scenario. `scenario` keys:
        key, ws_name, ws_desc, color, conv_title, members[name], roles{name:desc},
        orch(name), merge_mode, prompt(str), send_to(name), expect(str).
    """
    if wipe:
        _wipe()
    ids = ensure_contacts()
    members = [ids[n] for n in scenario["members"]]
    roles = {ids[n]: r for n, r in scenario["roles"].items()}
    orch = ids[scenario["orch"]]
    ws_id = ensure_workspace(
        scenario["ws_name"], scenario["ws_desc"], members,
        scenario.get("color", "#7A5AE0"),
    )
    conv_id = ensure_group_conv(
        ws_id, scenario["conv_title"], members, roles, orch,
        scenario.get("merge_mode", "auto"),
    )
    _print_howto(scenario, conv_id)
    return 0


def _print_howto(scenario: dict, conv_id: str) -> None:
    bar = "─" * 64
    print(f"\n{bar}")
    print(f"✓ 场景就绪:【{scenario['conv_title']}】  (merge={scenario.get('merge_mode','auto')})")
    print(f"  workspace:{scenario['ws_name']}   conv_id={conv_id}")
    print(f"{bar}")
    print("怎么测:")
    print(f"  1. 打开前端,进入工作区「{scenario['ws_name']}」→ 会话「{scenario['conv_title']}」")
    print(f"  2. @{scenario['send_to']} 发这条:")
    print()
    for line in scenario["prompt"].strip().splitlines():
        print(f"       {line}")
    print()
    print(f"  3. 预期:{scenario['expect']}")
    print(f"{bar}\n")


def cli(scenario: dict) -> int:
    """Entry point for a single-scenario script: parse --fresh and run."""
    return run(scenario, wipe="--fresh" in sys.argv)
