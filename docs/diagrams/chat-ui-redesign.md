# Chat UI Redesign — image prompts

> 给 GPT-IMAGE-2 / Nano-Banana / Midjourney 出一套完整 chat 界面 mockup 的 prompts。
> 一共 6 张图,覆盖主要 flow + state。每张 16:9 高清。所有 label 用英文(图像模型对英文渲染最稳),技术词原样保留。
>
> 用法:把下方 `## Image N` 节的 prompt 整段贴进图像模型。生成后回贴截图给团队 review。

---

## Design system 总规(每张图都遵守这套)

```
Color palette:
- Background:                #1d1916  (deep warm dark, never pure black)
- Surface (cards/panels):    #25201c  (slightly lighter, warm)
- Surface 2 (sidebar etc):   #2a241f
- Hair-line / divider:       #3a342d  (1px lines, never thicker)
- Foreground (body text):    #ecdfcf  (warm cream)
- Foreground muted (meta):   #9a9080  (khaki)
- Foreground subtle:         #5d574f
- Accent (single):           #d97757  (terracotta orange — ONLY for CTA / active / focus)
- Semantic green (success):  #5fb16e
- Semantic red (error):      #d96868
- Semantic amber (pending):  #d9a55f

Typography (use real font names visible on rendered text):
- Display headings:          Noto Serif SC / Songti / serif — used for app title + chat title
- Body text:                 IBM Plex Sans / system-ui sans-serif
- Code & meta numbers:       JetBrains Mono / ui-monospace
- All-caps eyebrow:          mono, 9.5px, tracking 0.22em

Visual rules:
- Heavy editorial whitespace. No cramped UI. Generous 24px / 32px gutters.
- Rounded corners 8-14px. NEVER pill-shaped (50% radius) for cards.
- Hair-line 1px dividers, never 2-3px borders.
- No drop shadows on regular cards. Only on floating overlays.
- No emoji. No illustration. No gradient backgrounds. No glassmorphism.
- Avatars: CIRCLES (32-40px for chat, 28-32px for sidebar). Each agent has
  one solid color: orchestrator purple #7A5AE0, backend orange #D2691E,
  frontend blue #3D7FD1, docs green #2E9F73.

Layout grid (3 columns):
- Left sidebar:    260-280px
- Center chat:     flex-1
- Right preview:   480-540px (collapsible)

DON'T do:
- Don't use Material Design ripples / FAB / cards with elevation 1-24
- Don't use Bootstrap pills / badges with bg colors
- Don't use Apple human-interface bright vibrancy
- Don't use Discord / Slack purple gradient nav
- This product wants the feel of a typographic editorial magazine
  (think Are.na, Linear circa 2020, Visual Studio Code dark+ but warmer)
```

---

## Image 1 · Main chat window (idle / receiving messages)

```
A flat, editorial dark-mode chat interface mockup for a multi-agent
collaboration platform called "Polynoia". 16:9 aspect ratio, 1440×810px.

Three-column layout against a deep warm-brown background #1d1916:

LEFT SIDEBAR (260px wide, surface #25201c, vertically separated by a
hair-line #3a342d on its right edge):
  - Top: a 40×40 rounded-square orange #d97757 monogram with white serif
    letter "P". Below it: serif title "Polynoia" in cream #ecdfcf at 20px,
    eyebrow "AGENT HUB" in mono caps 9.5px khaki #9a9080 underneath.
    A subtle 1px orange gradient hair-line beneath the title.
  - Search input (no border, just bottom hair-line on focus). Placeholder
    "Search".
  - Primary CTA "+ New Agent" (text + accent orange icon, no chunky button).
  - Section heading "CONTACTS · 4" in mono caps tracking-[0.22em] 10px.
    Four contact rows, each 48px tall, generous gap:
      • Purple circle 32px "Lx" — "林知夏" + tagline "Claude Code · opus-4-7"
        in mono 11px khaki. This row has a 2px orange accent stripe on left
        edge (it's the active selection).
      • Orange circle "Gy" — "顾屿" + "Claude Code · sonnet-4-6"
      • Blue circle "Sz" — "沈昭" + "OpenCode · sonnet-4-6"
      • Green circle "Sn" — "苏念" + "OpenCode · mimo-v2.5"
  - Section "PROJECTS · 1" — one row with a tiny 10px solid orange square
    (not circle, distinguishing it from contacts), label "Polynoia 工作室".
  - Footer: tiny pill showing "2 adapters connected" in mono 10px.

CENTER CHAT (flex-1, surface #25201c):
  - Header bar 56px tall, no chunky border, just a hair-line bottom:
      • Serif title "v1.0 发布筹备" 18px cream
      • Mono caps eyebrow "· 5 MEMBERS" 9.5px khaki
      • Sub-line "主 Agent · Orchestrator 林知夏" 11px khaki
      • Right side: tight stack of 4 overlapping 28px circular avatars
        (purple/orange/blue/green) with -8px negative margin
      • A prominent SEGMENTED TOGGLE 28px tall: two pill halves labeled
        "AUTO" (selected, orange bg #d97757 with white text) and "MANUAL"
        (deselected, transparent with khaki text). Mono caps font. This
        toggle must be CLEARLY visible — not subtle.
      • Tiny icon-buttons: search, panel-right, settings (12px lucide-style
        line icons, khaki, hover-accent)

  - Message stream (28px gutter left/right, 24px between distinct senders):
      Message 1 — User "我" (warm sienna avatar):
        "@林知夏 给一个虚构的开源 SDK 「@polynoia/agentmesh」 准备
         v1.0 发布的 3 份产物 — 并行做。"
        Reply-button visible on hover (subtle khaki icon row at end of line)

      Message 2 — Orchestrator 林知夏 (purple avatar, name in serif 14px,
        small mono CAPS purple pill "ORCHESTRATOR"):
        "拆 3 个并行任务:
         1. README.md (@苏念)
         2. index.html (@沈昭)
         3. CHANGELOG.md (@苏念)
         约束:产物英文,workspace 根目录"
        A small "tasks" card embedded below with 3 rows, each row showing
        a colored dot (matching agent color), task label, and a state pill
        "● running" in amber #d9a55f mono caps.

      Message 3 — 顾屿 (orange avatar, mono CAPS orange pill "BACKEND"):
        Tool-call card 480×80px:
          Header row: file-edit icon, mono "agentmesh/setup.py", mono caps
          "WRITE" badge khaki, right-aligned timestamp "12:43:08".
          Body row: green "+47" badge, "Created module skeleton (Python
          3.12, asyncio, type-annotated)".
        Below it a streaming code-block 480×120px, syntax-highlighted dark
        theme matching the warm palette:
          ```python
          from dataclasses import dataclass
          from collections.abc import Iterable

          @dataclass(frozen=True)
          class Mesh:
              ...
          ```

      Message 4 — 沈昭 (blue avatar, BOT pill blue):
        A streaming text part, cursor blinking after "正在写 hero 区..."

  - Below messages, ABOVE composer, a thin attention bar (only when
    agents active): "RUNNING · 沈昭 · 顾屿" with 2 spinning loader icons
    in mono caps amber, centered, soft amber background.

  - Composer at bottom, 80px tall:
      • Top row: a quote-chip showing a reply target ("Reply to 林知夏:
        拆 3 个并行任务...") with × dismiss
      • Big textarea, no border, just bottom hair-line that turns orange
        on focus. Placeholder "Message v1.0 发布筹备..."
      • Bottom row: At-sign icon (mention picker), paperclip (attach),
        ml-auto: primary orange button "发送 →" rounded 8px.

RIGHT PREVIEW PANE collapsed (just shows a vertical 40px-wide rail with
4 tab icons stacked: web globe, code </>, diff arrows, tasks checklist —
all khaki, the "code" one slightly brighter because it's been visited).

Outside the panel layout: at very top of the canvas, a 24px-tall window
chrome bar with three colored traffic-light dots on the left (macOS style)
in muted reds/yellows/greens, the URL/title bar centered showing
"polynoia.app · v1.0 发布筹备".

Style: precise pixel-perfect mockup, no painterly brushstrokes, sharp
1px lines, even spacing, font rendering must be crisp. Avoid generic
"chat app" look — this is closer to Linear/Are.na/iA Writer than to
WhatsApp/Slack.
```

---

## Image 2 · Manual mode · PendingEdit approval card

```
Same Polynoia editorial dark UI as Image 1, same 3-column layout.
Focus: the chat center column, showing what manual merge mode looks like
when an agent is mid-edit and waiting for user approval.

Header bar: segmented toggle now shows "MANUAL" selected (orange bg,
white text), "AUTO" deselected. A small amber "⏸" mono caps eyebrow next
to the title reads "AWAITING APPROVAL · 1".

Message stream:
  - User message at top: "@顾屿 把 setup.py 里的 dataclass 加个 timestamp 字段"
  - 顾屿's reply (orange avatar): "好,我加 created_at: datetime,默认 now()"

  - Then a FLOATING APPROVAL CARD takes over the chat space, 560px wide,
    surface #25201c, 14px rounded, hair-line border, NO drop shadow but
    a subtle 2px orange #d97757 left-edge accent stripe.

    Card structure (vertical):
      Top eyebrow row (mono caps 9.5px tracking-[0.22em]):
        FILE-EDIT icon + "agentmesh/setup.py · EDIT · @顾屿 · 12:45:18"
      Body row 1 (mono red bg #d96868/15%, 4px rounded):
        "−  created_at: datetime = field(default_factory=datetime.now)"
        (label "− removed" mono red caps top-right)
      Body row 2 (mono green bg #5fb16e/15%, 4px rounded):
        "+  created_at: datetime = field(default_factory=lambda: datetime.now(UTC))"
        (label "+ added" mono green caps top-right)
      Bottom row: two prominent buttons + meta:
        • Green pill button "✓ ACCEPT (Y)" — semantic green bg, white text,
          mono caps 11px, 8px rounded
        • Red pill button "✗ REJECT (N)" — semantic red bg, white text
        • Spacer
        • Right-aligned countdown mono caps amber: "TIMES OUT IN 4:38"

  - Below the card, faded out: two more pending approval cards stacked,
    50% opacity, showing "agentmesh/__init__.py" and "tests/test_mesh.py"
    queued. Mono caps eyebrow "QUEUED · 2".

Right side floating: a small toast slide-in 240×64px, surface, hair-line,
showing a tiny orange dot + mono "WAITING ON YOU" with a sub-line
"3 edits pending review".

Composer at bottom: disabled state (slightly faded), placeholder
"Approve or reject before sending more →" in mono khaki italic.

Mood: clear that something demands user attention. The amber + orange
draws the eye. No anxiety colors — this is a calm "please review"
state, not a "broken" alert.
```

---

## Image 3 · Web preview tab (right pane open)

```
Same Polynoia editorial dark UI. Sidebar collapsed/minimized to a 56px
rail showing just monogram + avatar dots. Chat center column narrowed.
Right pane FULLY OPEN at 540px width.

Right pane structure:
  Tab strip 40px tall:
    [WEB ● selected, orange underline] [CODE] [DIFF] [TASKS]
    All mono caps 11px tracking-[0.18em]

  Below tab strip, a sub-header 48px:
    • Device-size segmented control: [DESKTOP selected] [TABLET] [MOBILE]
      mono caps khaki, selected one has subtle accent underline
    • Mono khaki text "1440 × 900"
    • A file dropdown selector showing mono "index.html ▾"
    • Refresh icon (cycling arrows)
    • Right-aligned tiny green dot + mono "SYNCED" mono caps 9.5px

  Main iframe area (the actual web preview):
    A rendered editorial landing page for "@polynoia/agentmesh", same
    warm-dark aesthetic as the parent app (the agent designed it
    consistently — meta touch). Shows:
      • Hero: orange monogram "M", serif title "agentmesh",
        sub "Compose AI agents like UNIX pipes.",
        a single orange CTA "pip install polynoia-agentmesh"
      • Below: 3-column features row with hair-line dividers
      • Below: a code-example block in mono on a slightly darker bg
      • Footer: thin row of mono links: GITHUB · DOCS · DISCORD

Outside the iframe but within the right pane: a hairline bottom showing
"127.0.0.1 · /api/workspaces/.../preview?file=index.html" in mono khaki
9.5px (giving devs context where the iframe is loading from).

Center chat column shows the conversation that led to this preview:
  - 沈昭 (blue): "写了 index.html — hero + 3-col features + code 区 + footer.
                 桌面/手机都过。"
  - tiny system message in serif italic khaki: "auto-merged to main · 7e2af3a"
```

---

## Image 4 · Code editor tab (right pane, file open + dirty)

```
Same Polynoia editorial dark UI. Right pane CODE tab selected.

Right pane:
  Tab strip: [WEB] [CODE ● selected, orange underline] [DIFF] [TASKS]

  Below tab strip, a 2-column layout INSIDE the right pane:

  Left sub-column 200px — file tree:
    Mono caps eyebrow "WORKSPACE" with a tiny refresh icon next to it.
    Tree rendered with hair-line indents:
      ▾ agentmesh/                     (folder icon, expanded)
          setup.py                     (file icon, khaki text)
          __init__.py
          ● core.py                    (active, orange-tinted bg, accent text)
          tests/                       (folder, collapsed)
      ▾ docs/
          README.md                    (with a tiny amber dot — unsaved
                                        marker)
          CHANGELOG.md
      index.html
      .gitignore                       (slightly dimmer — meta files)

  Right sub-column flex — editor:
    Tab strip across the top showing 3 open files as tabs:
      "core.py" [active, brighter bg]
      "README.md ●" [dirty marker amber dot, hover-x shown]
      "setup.py" [×]
    On the FAR RIGHT of the tab strip: a prominent orange button
    "SAVE  Ctrl+S" in mono caps 11px, accent bg, white text, 6px rounded.
    When file is dirty this is highlighted; when clean it would be subtle.

    Below tab strip: a CodeMirror editor showing the active file content:
    ```python
    """Polynoia mesh — compose AI agents like UNIX pipes."""
    from __future__ import annotations

    from collections.abc import AsyncIterator
    from dataclasses import dataclass

    @dataclass(frozen=True)
    class Mesh:
        """A directed acyclic graph of agents."""
        nodes: tuple[Node, ...]
        edges: tuple[Edge, ...]

        async def run(self, input: str) -> AsyncIterator[Output]:
            for node in self.topological_order():
                async for chunk in node(input):
                    yield chunk
    ```
    Use crisp syntax highlighting in subdued warm-dark theme:
    keywords in dusty rose, strings in soft yellow, comments in khaki
    italic, types in soft blue. Line numbers in mono khaki gutter.
    Cursor blinking on line 14.

    Status bar at very bottom of editor:
      Mono khaki text: "agentmesh/core.py  ·  PY  ·  UTF-8  ·  LF  ·  29 行"

Center chat column shows: 顾屿 (orange) saying "刚改了 core.py 第 14 行,
你看看",and the user has clicked through to the file in CodeTab —
demonstrating the chat-to-editor workflow.

Subtle visual cue: a thin orange hair-line connects the chat message
file reference to the editor tab (indicating "you came from here"),
visible only on hover.
```

---

## Image 5 · @-mention picker (composer focus state)

```
Close-up of the bottom of the chat center column showing the composer
in focus state with the @-mention picker dropdown open above it.

Composer (80px tall, normally,but expanded here because picker is open):
  - Textarea showing user typed: "请 @"  (the @ just typed, cursor right after)

  - PICKER FLOATS ABOVE the textarea, anchored to the @ caret position.
    Picker is 280px wide, surface #25201c, hair-line border, 8px rounded,
    overlay drop-shadow (this is the ONE place shadows are allowed because
    it's a floating overlay).

    Picker header: mono caps khaki 9.5px tracking-[0.22em] "MENTION  ↑↓  ENTER"
    Hair-line divider.

    Picker rows (5 visible, 44px each, generous padding):
      • [Active row, subtle accent bg] Purple circle "Lx" — name "林知夏"
        in serif 13.5px cream, sub-line "Orchestrator · Claude Code · opus-4-7"
        in mono khaki 10.5px. Mono caps tag "ORCH" in purple on the right.
      • Orange "Gy" — "顾屿" / "Backend · Claude Code · sonnet-4-6" /
        tag "BACKEND" orange
      • Blue "Sz" — "沈昭" / "Frontend · OpenCode" / tag "FRONTEND" blue
      • Green "Sn" — "苏念" / "Docs · OpenCode · mimo-v2.5" / tag "DOCS" green
      • (5th row, dimmer) Orchestrator template — "Orchestrator" /
        "Built-in coordinator" — tag "SYSTEM"

    Footer hint: mono khaki 9.5px italic "Type to filter · Esc to close"

  - In the textarea, two previously inserted mentions are visible as
    inline chips, NOT raw text:
      "@林知夏" rendered as a tiny pill — purple circle 12px + name in
      sans 11.5px, slight purple-soft background pill rounded 4px.
      followed by " 给一个虚构的 SDK 准备 v1.0 发布的产物。请你拆给 "
      then a typing cursor right after the trailing "@".

  - Below textarea, normal composer footer row: at-sign icon (highlighted
    because picker is open), paperclip, ml-auto orange Send button.

Above composer in the background (dimmed): the message stream continues
faintly. The picker is the visual hero.

Style note: the picker rows are NOT compact list items. They're
generously padded (12px vertical) so each row reads as a person, not a
dropdown entry. Avatars are real circles, not squares.
```

---

## Image 6 · Multi-agent parallel work (workspace overview)

```
A "system status" view of the chat platform during a multi-agent parallel
work session. Same 3-col layout but with more information density.

CENTER chat column shows a "tasks dashboard" card pinned at the top of
the message stream (~ 560×280px, surface #25201c, hair-line border,
no shadow). The card layout:

  Card eyebrow: mono caps "TASKS · v1.0 发布筹备" + an amber dot meaning
  "in progress". Right side: mono "ETA 3:24" countdown amber.

  4 rows (one per agent), each 48px tall:

    Row 1: Purple "Lx" 林知夏 (orchestrator)
      "✓ Decomposed into 3 tasks" — small green checkmark, mono khaki
      duration "00:04"
    Row 2: Orange "Gy" 顾屿 (backend)
      "Writing agentmesh/core.py · 47 LOC so far..." — amber spinner,
      a tiny inline progress bar (8px tall, 200px wide, 60% orange fill)
    Row 3: Blue "Sz" 沈昭 (frontend)
      "Streaming index.html hero section..." — amber spinner,
      progress bar 35% blue fill
    Row 4: Green "Sn" 苏念 (docs)
      "✓ README.md done (4 sections, 187 lines)" — green checkmark,
      mono duration "01:12", tiny "VIEW" link

  Bottom row of card: mono caps "AUTO-MERGE ENABLED · MAIN @ 7e2af3a" in
  khaki, right side: a "PAUSE ALL" link in subtle khaki underline.

Below the dashboard card, the message stream:
  - 顾屿 (orange): a streaming text part, cursor still blinking, partial
    Python code visible in a code-block embedded inline
  - 沈昭 (blue): another streaming part, HTML preview embedded
  - Tiny system message hair-line divider showing "↑ 1 hour ago" in mono
    italic khaki, signaling history scroll

RIGHT PANE shows the WEB tab active with the in-progress index.html
rendering (so the user sees both: the agents writing code in chat, AND
the live result on the right).

LEFT SIDEBAR shows the same 4 contacts, but each row now has a small
status indicator dot on its avatar's bottom-right:
  - 林知夏: green dot (idle)
  - 顾屿: amber pulsing (working)
  - 沈昭: amber pulsing (working)
  - 苏念: green dot (idle)

Mood: a "newsroom-in-action" feeling. Calm but clearly active. The
amber + orange palette signals movement without being alarming. Nothing
red or anxiety-inducing. The reader should think "this thing is humming
along, multiple specialists doing what they're best at, the user is in
charge but not micromanaging".

Style: similar to Linear's command-bar polish, or a 2010s magazine
infographic. Editorial restraint over flashy.
```

---

## 使用说明

1. 每张 prompt 单独跑一次模型。每张可以多生成 2-3 个候选取最好的
2. 看下来如果某块(例如 toggle 按钮)在所有图里都不显眼,说明设计本身需要再加重
3. 截图回贴 → 我会按图调整真实代码(`ChatPane.tsx` / `Composer.tsx` / `PendingEditsPanel.tsx` / `WebTab.tsx` / `CodeTab.tsx`)
4. 重点验收:**Auto/Manual segmented toggle 在 Image 1/2/6 都明确可见**(当前实装太小看不见,需要重做)
