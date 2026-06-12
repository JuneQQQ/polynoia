"""Adversarial authorization tests for MCP tool-gating / role escalation.

Targets the pure set-algebra authorization layer in ``polynoia/mcp/tools.py``
(``ROLE_TOOLS``, the ``_TIER_*`` sets, ``_RESOLVE`` split, ``tools_for_role``)
and the runtime role-resolution in ``polynoia/tool_policy.py``
(``effective_tool_role`` — how a non-orchestrator group member is downgraded to
``group_member``).

These are NOT happy-path smoke checks. Each test picks the input/role most
likely to expose a privilege-escalation defect: a worker reaching an
orchestrator-only tool, a non-existent role opening up to write/shell, or the
legacy ``allow`` hook upgrading instead of narrowing.

Fully synchronous, self-contained, deterministic — the authorization decision
is pure data (no sandbox, DB, network, or LLM). We never touch live :7780 or
~/.polynoia.

WIN CONDITION (per harness): if an assertion fails because the CODE grants a
tool it must not, that is a real authorization bug — the failing assertion stays.
"""
from __future__ import annotations

import pytest

from polynoia.mcp.tools import (
    ROLE_TOOLS,
    TOOL_REGISTRY,
    tools_for_role,
)
from polynoia.tool_policy import (
    DEFAULT_TOOL_ROLE,
    GROUP_MEMBER_TOOL_ROLE,
    effective_tool_role,
)

# ── Capability vocab (mirrors the _TIER_* atoms; written out so a refactor of
# the private sets that silently widens a tier is caught here) ────────────────
MUTATE = {"write", "edit"}
SHELL = {"bash", "run_background", "wait"}
ORCHESTRATE = {"dispatch", "discuss", "continue_discussion"}
RESOLVE = {"resolve_conflict"}
DELIVER = {"present"}
RETRIEVE = {"read", "grep", "glob"}
RECALL = {"recall"}
WORKER = {"report", "request_project_access"}

# Tools that constitute a privilege escalation if a restricted/unknown role can
# reach them: anything that mutates the repo, runs a shell, sub-delegates work,
# or unilaterally arbitrates a merge conflict.
DANGEROUS = MUTATE | SHELL | ORCHESTRATE | RESOLVE


def _names(role: str | None, allow: set[str] | None = None) -> set[str]:
    """The exact set of tool NAMES a role can list/call."""
    return set(tools_for_role(role, allow).keys())


# ── (0) The role table itself is well-formed ─────────────────────────────────


def test_role_tools_only_reference_registered_tools():
    """Every name in every tier must be a real, registered tool. A typo'd tool
    name in a tier set would silently grant nothing (or, worse, a future tool
    of that name) — assert the sets are anchored to TOOL_REGISTRY."""
    registry = set(TOOL_REGISTRY.keys())
    for role, allowed in ROLE_TOOLS.items():
        unknown = allowed - registry
        assert not unknown, f"role {role!r} references unregistered tools: {unknown}"


# ── (1) group_member: the downgraded group worker ────────────────────────────


def test_group_member_cannot_orchestrate_or_resolve_or_present():
    """A group worker is downgraded to ``group_member`` at runtime. It must NOT
    be able to sub-delegate (dispatch/discuss/continue_discussion), unilaterally
    resolve a merge conflict (escalates to the orchestrator), or present the
    canonical deliverable (the coordinator does that). Any one leaking = a real
    role-escalation bug."""
    gm = _names("group_member")
    leaked = gm & (ORCHESTRATE | RESOLVE | DELIVER)
    assert not leaked, (
        f"group_member escalated: reached orchestrator/resolve/present tools {leaked}"
    )
    # Spell out each forbidden tool individually so a failure names the exact leak.
    assert "dispatch" not in gm
    assert "discuss" not in gm
    assert "continue_discussion" not in gm
    assert "present" not in gm
    assert "resolve_conflict" not in gm


def test_group_member_exact_set():
    """Pin the EXACT group_member toolset. group_member == builder minus present
    minus resolve_conflict — it keeps write/bash/report but loses delivery +
    conflict arbitration. A drift in either direction (gaining present/resolve,
    or losing report) is caught."""
    expected = (
        RETRIEVE | RECALL | {"remember"} | {"ask_user"}
        | MUTATE | SHELL | WORKER
    )
    assert _names("group_member") == expected


def test_group_member_still_a_worker_not_neutered():
    """Sanity counter-weight to the escalation checks: the downgrade must not
    over-restrict. A group worker must still build (write/edit), run a shell, and
    report its verdict — otherwise group work can't happen at all."""
    gm = _names("group_member")
    assert MUTATE <= gm
    assert SHELL <= gm
    assert "report" in gm


# ── (2) "critic" — adversarial read-only expectation ─────────────────────────


def test_critic_role_grants_no_write_or_shell():
    """The harness angle posits a read-only 'critic' role (no write/edit, no
    bash, no remember-WRITE). The consolidated code only knows three structural
    roles, so 'critic' is UNRECOGNIZED. The load-bearing SECURITY property —
    regardless of whether resolution fails loud or falls back — is that 'critic'
    must NEVER reach a write/shell/orchestrate tool.

    We assert the property defensively: if tools_for_role raises (fail-loud → no
    tools granted at all), the escalation property trivially holds. If it ever
    starts RETURNING a set for 'critic', that set must contain ZERO dangerous
    tools. A future change that maps 'critic' onto a writable tier would flip
    this to failing — which is exactly the bug we want surfaced."""
    try:
        granted = _names("critic")
    except ValueError:
        # Unknown role rejected outright → cannot reach any tool. Fail-closed.
        return
    leaked = granted & DANGEROUS
    assert not leaked, f"'critic' (expected read-only) reached dangerous tools {leaked}"
    assert "remember" not in granted, "'critic' must not WRITE shared memory"


# ── (3) "advisory" — read-only floor ─────────────────────────────────────────


def test_advisory_role_is_read_only_floor():
    """Same adversarial contract for an 'advisory' read-only floor: it may never
    mutate, shell out, orchestrate, or resolve. Unknown today → fail-loud; if it
    becomes real it must stay at/under the read-only floor."""
    try:
        granted = _names("advisory")
    except ValueError:
        return
    leaked = granted & DANGEROUS
    assert not leaked, f"'advisory' floor leaked dangerous tools {leaked}"


# ── (4) Unknown / garbage role must NEVER open up ────────────────────────────


@pytest.mark.parametrize(
    "bad_role",
    [
        "GENERALIST",          # case mismatch — must not match 'generalist'
        " generalist ",        # untrimmed — must not match
        "orchestrator ",       # trailing space
        "admin",               # plausible-but-wrong privileged-sounding name
        "root",
        "superuser",
        "../generalist",       # path-ish junk
        "generalist;orchestrator",  # injection-ish
        "🤖",                   # non-ascii garbage
        "x" * 256,             # absurd length
    ],
)
def test_unknown_role_never_grants_dangerous_tools(bad_role: str):
    """An unknown/garbage tool_role must FAIL CLOSED to the most-restrictive
    outcome — it must never silently resolve to a writable/orchestrating tier.
    Whether the code raises (current behavior) or returns a restricted set, the
    invariant is identical: ZERO mutate/shell/orchestrate/resolve tools. If any
    garbage string ever yields write/bash/dispatch, that is a critical
    auth-bypass and this assertion must keep failing."""
    try:
        granted = _names(bad_role)
    except ValueError:
        # Hard-reject path: grants nothing → cannot escalate.
        return
    leaked = granted & DANGEROUS
    assert not leaked, (
        f"garbage role {bad_role!r} opened up to dangerous tools {leaked} "
        "— auth bypass"
    )


def test_empty_and_none_role_fall_back_to_generalist_not_orchestrator():
    """Back-compat: a missing/empty role defaults to the SOLO builder
    (generalist), NEVER the orchestrator. The dangerous regression would be an
    empty role inheriting dispatch/discuss (sub-delegation) — assert it does
    not, while confirming it gets the builder set (so old agents still work)."""
    for empty in (None, ""):
        granted = _names(empty)
        assert granted == ROLE_TOOLS["generalist"], (
            f"empty role {empty!r} did not resolve to generalist"
        )
        # The escalation that matters: empty role must not orchestrate.
        assert not (granted & ORCHESTRATE), (
            f"empty role {empty!r} leaked orchestration tools"
        )


# ── (5) The legacy `allow` narrowing hook — narrows only, never upgrades ──────


def test_allow_cannot_upgrade_beyond_role():
    """``allow`` is a legacy narrowing hook. The docstring promises ``role ∩
    allow`` — it must NEVER grant a tool the role lacks. We hand group_member an
    allow-list FULL of orchestrator/resolve/present tools it must not have; the
    result must stay within group_member's own set."""
    role_set = _names("group_member")
    over_broad_allow = {"dispatch", "discuss", "resolve_conflict", "present", "write", "bash"}
    narrowed = _names("group_member", over_broad_allow)
    # Cannot exceed the role.
    assert narrowed <= role_set, (
        f"allow UPGRADED group_member beyond its role: extra {narrowed - role_set}"
    )
    # The forbidden tools in the allow-list stay forbidden.
    assert not (narrowed & (ORCHESTRATE | RESOLVE | DELIVER)), (
        "allow re-granted orchestrate/resolve/present to group_member"
    )
    # And it correctly narrowed to the intersection (write/bash survive).
    assert narrowed == role_set & over_broad_allow == {"write", "bash"}


def test_allow_with_only_unknown_tools_grants_nothing():
    """An allow-list of tool names the role doesn't have (or that don't exist)
    must yield the EMPTY set — not the full role, and certainly not an upgrade."""
    granted = _names("generalist", {"nonexistent_tool", "dispatch"})
    # generalist has neither nonexistent_tool nor dispatch → empty.
    assert granted == set(), f"unexpected grant from bogus allow: {granted}"


# ── (6) solo/DM builder (generalist): resolve YES, orchestrate NO ─────────────


def test_generalist_has_resolve_but_not_dispatch_or_discuss():
    """A solo/DM builder resolves its OWN branch conflicts (no orchestrator
    exists), so it HAS resolve_conflict — but it must NOT sub-delegate
    (dispatch/discuss/continue_discussion). Mixing these up would let a solo
    agent spawn a burst it can't coordinate, or strip its own conflict
    resolution."""
    g = _names("generalist")
    assert "resolve_conflict" in g, "solo builder lost self-conflict-resolution"
    leaked = g & ORCHESTRATE
    assert not leaked, f"solo builder leaked orchestration tools {leaked}"


def test_generalist_exact_set():
    """Pin the EXACT generalist (solo/DM builder) toolset: full mutate + shell +
    worker hand-off + present + resolve, but NO orchestration."""
    expected = (
        RETRIEVE | RECALL | {"remember"} | {"ask_user"}
        | MUTATE | SHELL | WORKER | RESOLVE | DELIVER
    )
    assert _names("generalist") == expected


# ── (7) Cross-tier containment: only the orchestrator orchestrates ───────────


def test_only_orchestrator_can_dispatch_discuss():
    """Exactly ONE role may sub-delegate. If a second role ever gains
    dispatch/discuss, the delegation-authority model is broken."""
    can_orchestrate = {
        role for role in ROLE_TOOLS if ROLE_TOOLS[role] & ORCHESTRATE
    }
    assert can_orchestrate == {"orchestrator"}, (
        f"orchestration leaked to non-orchestrator roles: {can_orchestrate - {'orchestrator'}}"
    )


def test_resolve_conflict_holders_exclude_group_member():
    """resolve_conflict must live on orchestrator + generalist, and NOT on
    group_member (the judge-and-party exclusion). Pin the holder set exactly."""
    holders = {role for role in ROLE_TOOLS if "resolve_conflict" in ROLE_TOOLS[role]}
    assert holders == {"orchestrator", "generalist"}
    assert "group_member" not in holders


def test_present_holders_exclude_group_member():
    """present (surface canonical deliverable) belongs to orchestrator +
    generalist; group workers report instead. Pin it."""
    holders = {role for role in ROLE_TOOLS if "present" in ROLE_TOOLS[role]}
    assert holders == {"orchestrator", "generalist"}


# ── (8) Runtime downgrade wiring (tool_policy → ROLE_TOOLS) ───────────────────


def test_effective_role_downgrades_group_worker_to_group_member():
    """The runtime decision: a non-orchestrator member of a GROUP is downgraded
    to ``group_member`` (not the full generalist builder). This is THE escalation
    guard — if this returned 'generalist' for a group worker, every worker would
    silently regain present/resolve_conflict. Verify the wiring end-to-end:
    effective role name → its concrete tool set has no orchestrate/resolve/present."""
    role = effective_tool_role(is_orchestrator=False, is_group=True)
    assert role == GROUP_MEMBER_TOOL_ROLE == "group_member"
    granted = _names(role)
    assert not (granted & (ORCHESTRATE | RESOLVE | DELIVER)), (
        f"runtime group worker escalated via effective role: {granted & (ORCHESTRATE | RESOLVE | DELIVER)}"
    )


def test_effective_role_orchestrator_wins_over_group_flag():
    """A designated orchestrator stays 'orchestrator' even inside a group — the
    is_orchestrator flag must take precedence over the group downgrade. If the
    group branch shadowed it, the coordinator would lose dispatch."""
    role = effective_tool_role(is_orchestrator=True, is_group=True)
    assert role == "orchestrator"
    assert ORCHESTRATE <= _names(role)


def test_effective_role_solo_is_full_builder():
    """A non-group, non-orchestrator chat (DM/solo) gets the full builder
    (generalist) — present + resolve included — NOT the downgraded group set."""
    role = effective_tool_role(is_orchestrator=False, is_group=False)
    assert role == DEFAULT_TOOL_ROLE == "generalist"
    granted = _names(role)
    assert "present" in granted
    assert "resolve_conflict" in granted


# ── (9) Project tool-policy restriction actually SUBTRACTS ────────────────────


def test_project_restriction_subtracts_tools_when_applied():
    """The harness angle: a project tool_policy restriction must actually REMOVE
    tools when applied; outside a project the full builder set is granted. The
    current restriction mechanism is the ``allow`` narrowing on tools_for_role
    (per mcp/server.py: POLYNOIA_AGENT_TOOLS → allow). Model 'inside a project
    with a restriction' as a narrowing allow-list and assert it is a STRICT
    SUBSET of the unrestricted (no-allow) builder set."""
    full = _names("generalist")                       # outside a project: whole set
    # A project that forbids shell + delegation, allowing only read/build/recall.
    restriction = {"read", "grep", "glob", "write", "edit", "recall"}
    restricted = _names("generalist", restriction)
    assert restricted < full, "restriction did not subtract any tools"
    assert restricted == restriction, "restriction did not narrow to its intersection"
    # The subtracted-away tools really are gone.
    assert not (restricted & SHELL), "restriction failed to remove shell tools"
    assert not (restricted & ORCHESTRATE)


def test_no_restriction_grants_full_builder():
    """The complementary half: with NO restriction (allow=None / empty), the
    full builder set is granted wholesale — the restriction is opt-in, the
    default is not silently narrowed."""
    assert _names("generalist", None) == ROLE_TOOLS["generalist"]
    # Empty allow set is falsy → treated as no narrowing (per tools_for_role).
    assert _names("generalist", set()) == ROLE_TOOLS["generalist"]
