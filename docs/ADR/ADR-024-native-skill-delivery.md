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
| Claude Code | credential `~/.claude/skills/<name>` | `ClaudeAgentOptions.skills` is the per-session allowlist |
| Codex | contact runtime `~/.agents/skills/<name>` | HOME is private per `(adapter, agent, conversation)`; `CODEX_HOME` remains the credential/config snapshot |
| OpenCode | contact runtime `~/.config/opencode/skills/<name>` | HOME is private per `(adapter, agent, conversation)`; native `skill` permission denies `*` and allows only bound names |

Codex and OpenCode runtime homes live under Polynoia's `.polynoia/agent-homes`
state, outside the agent-managed Git tree. Their native write/shell tools remain
denied; Skill scripts can only cause side effects through the existing,
role-gated Polynoia MCP tools.

The identity layer keeps compact Skill name/description metadata. It does not
inline package instructions when the selected adapter supports native Skills.
Explicit per-contact instruction overrides remain inline. Unknown future
adapters and packages outside the portable Agent Skills name/metadata contract
keep the full-text fallback until they implement native delivery.

Packages are treated as untrusted input. Installation and delivery reject
symbolic links rather than dereferencing a link that could copy host files from
outside the package into an agent-visible runtime directory.

## Consequences

- Scripts, references, and assets are preserved instead of silently dropping
  everything except `SKILL.md`.
- Different contacts can bind different Skill sets without leaking packages
  through a workspace-shared HOME.
- OpenCode may discover repository-local Skills, but its generated permission
  map hides and rejects every name that was not bound to the contact.
- Context usage drops because complete instructions are loaded only when the
  underlying agent selects the Skill.
- Adding a native-capable adapter requires declaring its discovery path and
  subprocess HOME/config behavior; adapters without that work still function
  through the inline fallback.
- Native Skill names and frontmatter still need to follow the upstream Agent
  Skills conventions. Polynoia does not rewrite third-party package contents.
- Codex and Claude Code may still expose system-bundled or trusted
  repository-local Skills according to their native product behavior. This ADR
  isolates and selects Polynoia-managed packages; it does not claim to replace
  every upstream discovery scope.

## Alternatives rejected

1. **Keep injecting full `SKILL.md` into every turn.** Simple, but loses
   progressive disclosure and cannot expose packaged scripts/resources.
2. **Put generated Skills inside each project worktree.** Native discovery
   works, but generated folders appear in Git status and risk accidental
   commits.
3. **Use one shared global Skill directory for all contacts.** Smallest change,
   but violates contact-level binding and workspace isolation.

## Implementation basis and related designs

- [Claude Code Skills](https://code.claude.com/docs/en/skills) documents
  Agent Skills packages, supporting files, and progressive disclosure.
- [Codex Skills](https://learn.chatgpt.com/docs/build-skills) documents
  `~/.agents/skills`, repository discovery scopes, and package resources.
- [OpenCode Agent Skills](https://opencode.ai/docs/skills/) documents
  `~/.config/opencode/skills`, project-level discovery, and pattern-based
  per-Skill permissions. The deny-by-default name allowlist follows that
  permission model.
- [OpenHands Skills](https://docs.openhands.dev/overview/skills) is a related
  example of keeping an AgentSkills-style package intact and exposing compact
  metadata before on-demand loading.
- [OpenDesign agent runtime registry](https://github.com/nexu-io/open-design/blob/main/apps/daemon/src/agents.ts)
  is a related example of keeping common orchestration stable while isolating
  executable, environment, and stream differences at the agent boundary.

These projects informed the boundary, not the implementation: Polynoia keeps
its own contact binding, group-chat orchestration, PAP events, sandbox policy,
and memory model.
