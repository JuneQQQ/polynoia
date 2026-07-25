# Native Skill Delivery

场景:解释 Polynoia 如何把同一个完整 Skill 包按联系人隔离后投递给三个原生 Agent，同时保留未来 Adapter 的文本回退。

## GPT Image prompt

```text
A clean technical infographic in modern flat-design style on a soft off-white
background. Title at top in bold sans-serif: "Polynoia Native Skill Delivery".

Use a left-to-right flow with five columns:

1. A blue source box labeled exactly:
   "Polynoia Skill Registry"
   "SKILL.md"
   "scripts/"
   "references/"
   "assets/"

2. An orange binding box labeled:
   "Contact Binding"
   "agent_id + conv_id"
   "selected skill names only"

3. A dark-slate routing diamond labeled:
   "Adapter-native support?"

4. Three vertically stacked native targets:
   - Purple box: "Claude Code" and "~/.claude/skills" and
     "ClaudeAgentOptions.skills allowlist"
   - Blue box: "Codex" and "~/.agents/skills" and
     "contact-scoped HOME"
   - Green box: "OpenCode" and "~/.config/opencode/skills" and
     "skill permission: allow"
   Draw a pale green boundary around all three labeled
   "Per-contact runtime isolation".

5. A gray fallback box below the routing diamond labeled:
   "Future adapter fallback"
   "inline SKILL.md instructions"

Show a thin arrow from every native target to a final green box labeled:
"Progressive disclosure"
"metadata first → full package on demand".

Add a red crossed-out note under the flow:
"No shared all-agent Skill directory"
and a small security note:
"Native write/shell stays denied; side effects use Polynoia MCP".

Color palette: off-white background, soft blue #5B8FF9 for system and registry,
warm orange #F2994A for binding/routing, pale purple #8B7CF6 for Claude,
fresh green #27AE60 for isolation/success, gray #E5E7EB for fallback,
red #D64545 for rejected shared state, dark slate #1F2937 for text.
Thin 1-2px strokes, rounded rectangles, flat icons, no 3D, minimal shadow.
All labels must be English and spelled exactly as provided. Aspect ratio 16:9.
```
