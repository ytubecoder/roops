# site/ — the roops brand, explainer, and UI concept

Brand, explainer, and UI-concept pages for **Roops** (ループス) — the Japanese rebrand of
this recurring-agent harness. "Loop" is already a Japanese loanword (ループ, *rūpu*);
Roops is that word borrowed back.

Formerly its own public repo (`ytubecoder/roops`) — merged into this repo 2026-07-30 with
history preserved, and the standalone repo retired. The pages stay live at
**https://ytubecoder.github.io/roops/**, served from the public
`ytubecoder/ytubecoder.github.io` repo (`roops/` folder). This directory is the source of
truth — edit here, then publish per `workflows/publish.txt`.

| Page | What it is |
|---|---|
| `index.html` | Landing — "welcome to the zen garden": what Roops is, the three pillars (karesansui / bushidō / ikebana), vocabulary, palette, marks, taglines |
| `ui.html` | UI concept "The Garden" — loop matrix with per-loop tokonoma output alcoves, per-run token costs, off switches, ensō-as-spinner, 縁側 live drop-in panel, pancaked findings, 掃 swept pile, 帳 metrics ledger, 承 approve → orders queue |

Single static pages, vanilla HTML/CSS/JS, no build step. Design system rules live in
`CLAUDE.md` (this directory); `garden-desktop.png` is a rendered reference of the ui.html
garden. The published pages are public — the mock-data-only rule in `CLAUDE.md` applies
to everything that ships there.
