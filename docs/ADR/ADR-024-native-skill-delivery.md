# ADR-024: Adapter-native Skill delivery with contact isolation

- **Status**: accepted
- **Date**: 2026-07-25

## Context

Polynoia stores a Skill as a complete directory (`SKILL.md` plus optional
scripts, references, and assets) and binds packages to contacts by name. Before
this decision, only Claude Code received a copied directory, while Codex and
OpenCode accepted the `skills` argument but ignored it. The identity layer also
inlined every complete `SKILL.md`, defeating progressive disclosure.

The existing Claude destination was workspace-shared. Different contacts in the
same workspace could therefore leave packages visible in the same HOME. In
addition, OpenCode explicitly denied its native `skill` tool, so copying a
folder alone would not make a Skill usable.

## Decision

Each adapter receives the complete bound package in a native discovery path:

| Adapter | Native path | Selection/isolation |
|---|---|---|
| Claude Code | isolated `~/.claude/skills/<name>` | `ClaudeAgentOptions.skills` is the per-session allowlist |
| Codex | contact runtime `~/.agents/skills/<name>` | HOME is private per `(adapter, agent, conversation)`; `CODEX_HOME` remains the credential/config snapshot |
| OpenCode | contact runtime `~/.config/opencode/skills/<name>` | HOME is private per `(adapter, agent, conversation)` and the native `skill` permission is allowed |

Codex and OpenCode runtime homes live under Polynoia's `.polynoia/agent-homes`
state, outside the agent-managed Git tree. Their native write/shell tools remain
denied; Skill scripts can only cause side effects through the existing,
role-gated Polynoia MCP tools.

The identity layer keeps compact Skill name/description metadata. It does not
inline package instructions when the selected adapter supports native Skills.
Explicit per-contact instruction overrides remain inline. Unknown future
adapters keep the full-text fallback until they implement native delivery.

## Consequences

- Scripts, references, and assets are preserved instead of silently dropping
  everything except `SKILL.md`.
- Different contacts can bind different Skill sets without leaking packages
  through a workspace-shared HOME.
- Context usage drops because complete instructions are loaded only when the
  underlying agent selects the Skill.
- Adding a native-capable adapter requires declaring its discovery path and
  subprocess HOME/config behavior; adapters without that work still function
  through the inline fallback.
- Native Skill names and frontmatter still need to follow the upstream Agent
  Skills conventions. Polynoia does not rewrite third-party package contents.

## Alternatives rejected

1. **Keep injecting full `SKILL.md` into every turn.** Simple, but loses
   progressive disclosure and cannot expose packaged scripts/resources.
2. **Put generated Skills inside each project worktree.** Native discovery
   works, but generated folders appear in Git status and risk accidental
   commits.
3. **Use one shared global Skill directory for all contacts.** Smallest change,
   but violates contact-level binding and workspace isolation.
