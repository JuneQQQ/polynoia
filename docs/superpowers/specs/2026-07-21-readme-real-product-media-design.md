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
- Extract `assets/readme/demo-poster.png` from the existing MP4 with macOS Quick
  Look at 1600 px. It is an uncropped real video frame, not regenerated or
  redrawn UI.

## README composition

The opening remains the current logo, language switch, Shared Studio hero,
product belief, calls to action, and factual badges. Immediately after that
opening, show the real video poster linked directly to the existing product
demo, followed by the localized direct-link fallback. Call it a product demo
without claiming a 90-second duration: the committed MP4 is about 100 seconds
long.

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

GitHub sanitizes `<video>` elements from rendered README DOM, so an inline
player cannot be the primary presentation. Both READMEs instead use a centered
`<a>` pointing at the existing raw GitHub URL for `assets/readme/demo.mp4` and
wrap the real `assets/readme/demo-poster.png` thumbnail inside it. The existing
localized direct-link fallback remains below the poster, and the caption makes
no duration promise. The MP4 is reused unchanged; no 80 MB duplicate or
transcoded video is added.

## Scope and exclusions

This change does not rewrite product claims, change application code, recapture
or redraw the UI, regenerate images, or modify the released DMG. The only new
media is the deterministic Quick Look poster extracted from the existing demo.
Historical completed implementation plans remain as records; this design
supersedes only their four-illustration presentation decision.

## Verification

- Confirm both READMEs reference exactly one community ImageGen asset, the same
  real-video poster, the same MP4 URL, and the same six UI captures in the same
  order.
- Confirm the three removed WebPs have no live README or provenance reference.
- Check every local media path, the raw video fallback, all local anchors, and
  English/Chinese heading and media parity.
- Render the feature branch on GitHub at desktop and narrow widths. Verify the
  hero, linked video poster/direct fallback, screenshot grid, captions, and
  absence of broken media or raw HTML. Confirm no `<video>` element is expected
  in GitHub's sanitized DOM.
- Run the existing README validator, TypeScript check, production build, diff
  hygiene, and an independent adversarial review before a non-force push.
