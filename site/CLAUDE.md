# site/ — brand + UI concept pages for the "Roops" rebrand

Static two-page site, no build step. Live at https://ytubecoder.github.io/roops/ —
served from the public `ytubecoder/ytubecoder.github.io` repo (`roops/` folder). This
`site/` directory is the source of truth; deploy = copy the two pages there and push
(`workflows/publish.txt`). Merged here from the standalone `ytubecoder/roops` repo
2026-07-30.

**Status of the rebrand:** APPLIED at brand level 2026-07-30 — repo name, README, and
dashboard header say roops, and `bin/roopctl` exists as an alias. Mechanical names are
deliberately unchanged per the settled alias-not-a-rewrite rule: `loopctl`, `loops.d/`,
`state/loops.sqlite`, `com.loops.*` launchd labels, and `docs/INTERFACES.md` vocabulary
all remain "loops". Do not rename mechanical internals from here.

## Non-negotiables (design system — every addition must conform)

- **Tokens** (CSS vars in both files): sumi `#1C1A17` ink · washi `#F2EDE3` paper ·
  shu `#C73E2B` vermillion · ai `#2E4A5B` indigo · nibi `#8C8578` · koke `#6B7A5C` moss ·
  ochre `#A87A2A`. No new colors. Never pure black. No purple, no glows, no gradients.
- **Vermillion is the ONLY accent** and appears only where a hanko would: seals, alerts,
  human decisions (承/認/休/済 stamps, × failed, − removals, focus rings). Moss/ochre are
  strictly data/status colors.
- **Fonts:** `--serif` (Hiragino/Yu Mincho/Noto Serif JP) for text; `--seal` (Yuji Syuku)
  for STAMPS ONLY — small kanji (≤16px) collapse into blobs in the seal face, set them in
  `--serif` (learned the hard way, twice); `--mono` for ALL numbers, labels, timestamps.
  Google Fonts @import means first load needs network.
- **No emoji anywhere, including ✓** — ticks are marubatsu: 〇 achieved (moss),
  △ partial (ochre), × failed (shu).
- **Motion is slow and breathing:** `cubic-bezier(0.16,1,0.3,1)`, transitions ≥.8s,
  ambient cycles 3–6s. The ensō draw-on IS the spinner. Every animation needs a
  `prefers-reduced-motion` fallback; every layout must hold at 390px with zero horizontal
  overflow. Both files have existing blocks for each — extend them, don't fork.
- **Mock data is genericized on purpose** (tls-certs, dead-links, deps-drift…). NEVER put
  internal loop names (`ads-*`) or real business data on these public pages. Numbers must
  look organic, never round.

## Concept vocabulary (what the UI pieces mean)

- **庭 garden** — the loop matrix. Row anatomy: [status stamp | name | tokonoma | run meta
  incl. per-run tok/$ | 巡/休 off-switch]. No sparklines/stages in rows; severity lives in
  the stamp alone.
- **床の間 tokonoma** — per-loop output alcove (fixed 64px, scrolls within itself):
  OBJECTIVES (marubatsu + evidence, default) / DIFF (+/− text) / CAPTURES (clickable
  thumbs → lightbox) / LIVE (transcript tail, running loop only).
- **縁側 engawa** — slide-in read-only live view of a running round (simulated transcript).
  Copy rule: rounds are read-only BUT findings are actions in waiting — never write plain
  "report-only, never acts"; the model is propose → 承 approve → order executes on a
  separate audited path (this wording was an explicit correction, keep it).
- **Pancaking** — recurring finding renders as stacked plies, one stamp applies
  retroactively to all appearances. **掃 swept pile** — stale-outs raked away un-stamped.
- **帳 ledger** — timestamped metric cards with brush trend lines + 留 PIN.

## Verifying changes

MCP browser blocks `file://` — serve first: `python3 -m http.server <port> --bind
127.0.0.1` (throwaway port, kill after; 8664/8677/8681 used historically). Scroll-reveal
content is opacity-0 until observed — force with
`document.querySelectorAll('.reveal').forEach(el=>el.classList.add('in'))` before
element screenshots on index.html. After pushing, verify live with a cache-busted curl.
User's copies go to the desktop machine via `tailscale file cp <files> desktop-7dms6vl:`.
