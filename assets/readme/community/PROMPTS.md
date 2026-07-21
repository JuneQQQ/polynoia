# Shared Studio README artwork

Generation mode: built-in ImageGen

These illustrations were generated rather than deterministically rendered. The approved hero source was supplied as the style reference for all three chapter illustrations. Source files remain in ImageGen's default generated-images directory; copies used for optimization were staged under `/tmp/polynoia-readme-imagegen/`.

## Shared Studio hero

Prompt:

```text
Create a panoramic editorial illustration for an open-source software project's GitHub README, aspect ratio about 2.35:1. Concept: “The Shared Studio — work remembers.” A thoughtful human collaborator and three distinct non-robotic AI colleagues gather around one long, deeply used physical workbench in a cinematic future creative studio. Represent each AI colleague as a different elegant translucent material presence with personality — one made of folded luminous paper, one of prismatic glass and soft woven fibers, one of ink-like light and ceramic forms — never as a humanoid robot. At the center, a tangible project artifact is being assembled from code-like structure, written research, design material, and physical prototypes without showing any readable code or screen. Behind them, accumulated work memory remains present as layered annotations, bookmarks, archived decisions, worn paper edges, version textures, and aurora-like geological strata. The mood says yesterday's work is still here and today's team can continue it. Vibrant midnight indigo, electric violet, cyan, emerald, coral orange, and amber gold; dramatic volumetric light, glass refraction, tactile paper, screen-print grain, deep shadows, premium editorial art direction, high detail, generous central safe area, coherent depth, optimistic and human. No user interface, no dashboard, no monitor content, no screen, no chat bubbles, no flowchart, no arrows, no boxes, no pipeline, no database cylinder, no glowing brain, no generic humanoid robot, no code rain, no logos, no readable text, no watermark.
```

- Approved returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-74c320a7-1ae5-4baf-bc8d-789655eea2a5.png` (`1921×819`)
- Staged source: `/tmp/polynoia-readme-imagegen/hero.png`
- Final asset: `assets/readme/community/hero-shared-studio.webp` (`1880×800`, 330200 bytes)
- Rejected returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-767570b2-6b23-4733-acef-160455dc5725.png` (ceramic figure read as a generic humanoid robot)

## Persistent identity

Prompt:

```text
Create a 16:9 editorial illustration in the exact same visual world and palette as a premium open-source GitHub README hero: a warm cinematic shared creative studio at night, midnight indigo with electric violet, cyan, emerald, coral, and amber accents, volumetric light, translucent glass, tactile paper, screen-print grain. Concept: “A teammate has a seat.” Show three distinctive workstations along the same physical workbench, each expressing a persistent AI colleague through its own material personality, tools, annotated notebooks, accumulated marks, and unfinished craft — folded luminous paper, prismatic glass with woven fibers, and ink-like light with ceramic objects. A human colleague moves naturally among them. Communicate stable identity, role, and working style without labels, screens, portraits, or interfaces. The studio feels inhabited and continuous, not staged. No user interface, no dashboard, no screen, no chat bubbles, no flowchart, no arrows, no nodes, no labels, no readable text, no logo, no humanoid robot, no glowing brain, no code rain, no watermark.
```

- Style reference: approved Shared Studio hero source above
- Approved returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-0384481a-dc99-4ba2-b7fb-7977817e2be3.png` (`1672×941`)
- Staged source: `/tmp/polynoia-readme-imagegen/identity.png`
- Final asset: `assets/readme/community/identity-has-a-seat.webp` (`1440×810`, 238616 bytes)

## Durable memory

Prompt:

```text
Create a 16:9 cinematic editorial illustration in the same Shared Studio visual system: midnight indigo, electric violet, cyan, emerald, coral orange, amber gold, translucent glass, tactile paper, volumetric light, screen-print grain. Concept: “Chats end. The work stays.” Show one continuous creative studio at the moment a team returns to work: yesterday's half-built physical artifact, pinned annotations, folded notes, bookmarks, revision scars, and layered decision traces remain exactly where they matter, while a human and an abstract non-robotic AI colleague resume the work with calm familiarity. Suggest two moments through changing light, patina, and layered memory, but do not use a literal split screen, timeline, sequence, arrows, panels, or labels. Make the persistence of context emotionally obvious and the collaboration human. No UI, no screens, no chat bubbles, no flowchart, no diagrams, no readable text, no logo, no generic robot, no glowing brain, no code rain, no watermark.
```

- Style reference: approved Shared Studio hero source above
- Approved returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-e39edc7d-5df0-4474-ae5e-8e60710aa9e4.png` (`1672×941`)
- Staged source: `/tmp/polynoia-readme-imagegen/memory.png`
- Final asset: `assets/readme/community/chats-end-work-stays.webp` (`1440×810`, 242320 bytes)

## Reviewable outcomes

Prompt:

```text
Create a 16:9 premium editorial illustration in the same Shared Studio world and palette. Concept: “Teammates leave reviewable work.” A human and three distinct abstract AI colleagues bring separate tangible contributions — carefully marked paper structures, a precise glass mechanism, woven research fragments, ceramic components, and revision notes — into one coherent inspectable artifact on a long shared workbench. Their contributions remain distinguishable and accountable inside the finished whole. The scene communicates craft, review, responsibility, and convergence rather than magical automation. Vibrant midnight indigo, electric violet, cyan, emerald, coral orange, amber gold; cinematic volumetric light, translucent materials, tactile paper, screen-print grain, deep shadow, original editorial composition. No interface, no screens, no dashboard, no chat bubbles, no flowchart, no arrows, no boxes, no labels, no readable text, no logos, no humanoid robots, no glowing brain, no code rain, no watermark.
```

- Style reference: approved Shared Studio hero source above
- Approved returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-d7f9512e-cd15-4d6c-a017-381f0bdab317.png` (`1672×941`)
- Staged source: `/tmp/polynoia-readme-imagegen/outcomes.png`
- Final asset: `assets/readme/community/reviewable-outcomes.webp` (`1440×810`, 281544 bytes)
- Rejected returned source: `/Users/lishaobo/.codex/generated_images/019f82d8-ce9e-7da1-84c4-bcba519d91b3/exec-4eafba45-c0c0-4dc5-9f64-270505dd45df.png` (readable-looking all-caps text appeared on a foreground note)

## Optimization commands

The following Sharp CLI commands were run from the repository root after staging the approved sources:

```bash
mkdir -p assets/readme/community
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/hero.png -o assets/readme/community/hero-shared-studio.webp -f webp -q 86 --effort 6 resize 1880 800 --fit cover --position centre
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/identity.png -o assets/readme/community/identity-has-a-seat.webp -f webp -q 84 --effort 6 resize 1440 810 --fit cover --position centre
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/memory.png -o assets/readme/community/chats-end-work-stays.webp -f webp -q 84 --effort 6 resize 1440 810 --fit cover --position centre
pnpm dlx sharp-cli -i /tmp/polynoia-readme-imagegen/outcomes.png -o assets/readme/community/reviewable-outcomes.webp -f webp -q 84 --effort 6 resize 1440 810 --fit cover --position centre
```
