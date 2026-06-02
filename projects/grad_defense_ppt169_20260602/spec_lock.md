## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## colors
- bg: #F7FAFC
- secondary_bg: #FFFFFF
- primary: #1A365D
- accent: #2B6CB0
- secondary_accent: #3182CE
- text: #2D3748
- text_secondary: #718096
- text_tertiary: #A0AEC0
- border: #E2E8F0
- success: #38A169
- warning: #E53E3E

## typography
- title_family: Georgia, "Microsoft YaHei", "PingFang SC", serif
- body_family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif
- emphasis_family: Georgia, SimSun, serif
- code_family: Consolas, "Courier New", monospace
- body: 20
- title: 32
- subtitle: 24
- annotation: 15
- footnote: 11
- cover_title: 60

## icons
- library: tabler-outline
- stroke_width: 2
- inventory: bulb, stack-2, window, chart-bar, terminal-2, shield, users, check, bolt, link, target, brand-github

## page_rhythm
- P01: anchor
- P02: anchor
- P03: dense
- P04: dense
- P05: breathing
- P06: dense
- P07: dense
- P08: dense
- P09: breathing
- P10: anchor

## page_charts
- P05: layered_architecture
- P08: grouped_bar_chart

## forbidden
- Mixing icon libraries
- rgba()
- `<style>`, class, `<foreignObject>`, `textPath`, `@font-face`, `<animate*>`, `<script>`, `<iframe>`, `<symbol>`+`<use>`
- `<g opacity>` (set opacity on each child element individually)
- HTML named entities in text (`&nbsp;`, `&mdash;`, `&copy;`, `&ndash;`, `&reg;`, `&hellip;`, `&bull;`) — write as raw Unicode (`—`, `©`, `→`, NBSP, etc.); XML reserved chars `& < > " '` must be escaped as `&amp; &lt; &gt; &quot; &apos;`
