# README Real Product Media Design

## Goal

Keep the new Shared Studio hero as Polynoia's single conceptual illustration,
then restore the earlier real product demo and UI screenshots as the README's
proof of the product. The English and Chinese READMEs remain structurally
equivalent.

## Approved media policy

- Keep `assets/readme/community/hero-shared-studio.webp` as the only ImageGen
  illustration shown in either README.
- Remove the three unused chapter illustrations from the repository:
  `identity-has-a-seat.webp`, `chats-end-work-stays.webp`, and
  `reviewable-outcomes.webp`.
- Prune `assets/readme/community/PROMPTS.md` to the retained hero's provenance,
  generation mode, source paths, final dimensions, and optimization command.
- Do not restore the previous `yw.png`, `zw.png`, or
  `产品定位概念图.png`. They are competing concept art rather than real product
  evidence.
- Restore `assets/readme/demo.mp4` and the six real UI captures in their earlier
  row-major order:
  `群聊与编排.png`, `预览.png`, `diff.png`, `联系人.png`, `质量面板.jpg`, and
  `角色库.jpg`.

## README composition

The opening remains the current logo, language switch, Shared Studio hero,
product belief, calls to action, and factual badges. Immediately after that
opening, embed the existing product demo with a direct fallback link. Call it a
product demo without claiming a 90-second duration: the committed MP4 is about
100 seconds long.

The three product-principle sections keep their current prose and headings but
lose their chapter illustration blocks. After those principles, add one
`See Polynoia at work` / `看看 Polynoia 如何工作` section containing the six
real captures in a two-column GitHub Markdown table:

1. Group chat and orchestration / 群聊与编排
2. Inline artifact preview / 内联产物预览
3. Reviewable diffs and commit history / 可审查的 diff 与提交历史
4. Persistent agent identities / 持久的 Agent 身份
5. Agent quality panel / Agent 质量面板
6. Specialty library / 角色专长库

Every image receives accurate English and Chinese alt text. In particular,
`联系人.png` must describe the contacts/agent-detail view rather than the stale
old Chinese conflict-resolution label.

The remaining memory-scope, quick-start, capability, trust, limitation, and
community sections stay unchanged except for heading-number parity caused by
the new product-media section.

## Video behavior

Both READMEs use the existing raw GitHub URL for `assets/readme/demo.mp4`, an
inline `<video>` element with controls, muted inline playback, and a normal
anchor fallback. The caption makes no duration promise. The binary is reused
unchanged; no 80 MB duplicate or transcoded copy is added.

## Scope and exclusions

This change does not rewrite product claims, change application code, recapture
the UI, regenerate images, modify the released DMG, or add new media. Historical
completed implementation plans remain as records; this design supersedes only
their four-illustration presentation decision.

## Verification

- Confirm both READMEs reference exactly one community ImageGen asset, the same
  MP4 URL, and the same six UI captures in the same order.
- Confirm the three removed WebPs have no live README or provenance reference.
- Check every local media path, the raw video fallback, all local anchors, and
  English/Chinese heading and media parity.
- Render the feature branch on GitHub at desktop and narrow widths. Verify the
  hero, inline video/fallback, screenshot grid, captions, and absence of broken
  media or raw HTML.
- Run the existing README validator, TypeScript check, production build, diff
  hygiene, and an independent adversarial review before a non-force push.
