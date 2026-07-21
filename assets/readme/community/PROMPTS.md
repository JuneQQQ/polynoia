# Shared Studio README artwork

Generation mode: built-in ImageGen

## Shared Studio hero

Prompt:

```text
Create a panoramic editorial illustration for an open-source software project's GitHub README, aspect ratio about 2.35:1. Concept: “The Shared Studio — work remembers.” A thoughtful human collaborator and three distinct non-robotic AI colleagues gather around one long, deeply used physical workbench in a cinematic future creative studio. Represent each AI colleague as a different elegant translucent material presence with personality — one made of folded luminous paper, one of prismatic glass and soft woven fibers, one of ink-like light and ceramic forms — never as a humanoid robot. At the center, a tangible project artifact is being assembled from code-like structure, written research, design material, and physical prototypes without showing any readable code or screen. Behind them, accumulated work memory remains present as layered annotations, bookmarks, archived decisions, worn paper edges, version textures, and aurora-like geological strata. The mood says yesterday's work is still here and today's team can continue it. Vibrant midnight indigo, electric violet, cyan, emerald, coral orange, and amber gold; dramatic volumetric light, glass refraction, tactile paper, screen-print grain, deep shadows, premium editorial art direction, high detail, generous central safe area, coherent depth, optimistic and human. No user interface, no dashboard, no monitor content, no screen, no chat bubbles, no flowchart, no arrows, no boxes, no pipeline, no database cylinder, no glowing brain, no generic humanoid robot, no code rain, no logos, no readable text, no watermark.
```

- Approved returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-74c320a7-1ae5-4baf-bc8d-789655eea2a5.png` (`1921×819`)
- Staged source: `/tmp/polynoia-readme-imagegen/hero.png`
- Final asset: `assets/readme/community/hero-shared-studio.webp` (`1880×800`, 330200 bytes)
- Rejected returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-767570b2-6b23-4733-acef-160455dc5725.png` (ceramic figure read as a generic humanoid robot)

## Optimization command

The following Sharp CLI command was run from the repository root after staging the approved source:

```bash
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/hero.png -o assets/readme/community/hero-shared-studio.webp -f webp -q 86 --effort 6 resize 1880 800 --fit cover --position centre
```
