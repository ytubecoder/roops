# pagekit — the shared report-page kit

Rules of the road: `docs/REPORT_PAGES_PLAN.md` §2 (contract, MUST) + §3 (kit, SHOULD).

## Using the kit

- **Read** `$PAGEKIT/kit.css` at render time and inline the body into your page's
  `<style>`. Do not paste a copy into your renderer — a copy drifts, and the kit
  exists so a restyle reaches every page-enabled loop at once. Strip the leading
  `/* … */` header comment; it is for maintainers, not browsers.
- **Read** `$PAGEKIT/toggle.js` the same way and inline it into a `<script>` in
  `<head>` **before** the `<style>` block (so the saved theme stamps onto
  `<html data-theme>` before first paint — no flash). Same missing-file-is-fatal
  rule as `kit.css`. Never `<script src=…>`: pages must fetch nothing on load.
- Never `<link>` the stylesheet either. Navigation `<a href="https://…">` is fine,
  and so is a URL inside escaped report text — the rule is about references the
  browser dereferences, not about the string `http://`. `tests/html_selfcontained.py`
  is the check.
- Treat a missing/unreadable `kit.css` or `toggle.js` as fatal to the render. Both
  ship in this repo, so absence means a broken checkout; failing names the path in
  `page-render.log` instead of promoting a silently unstyled or untoggleable page.
- Layout vocabulary: `.wrap` page column · `.hd`/`.kicker`/`.hero` header · `.stats`/`.stat`
  stat strip · `.brow` label+bar rows · `.group`/`.ghead`/`.frow`/`.fbody` grouped
  detail rows (`<details>`) · `footer` provenance line · `#tip` tooltip.
- Palette (garden role tokens, both modes — values shared with
  `dashboard/generate.py`, enforced by `tests/test_token_drift.py`):
  - light: surface `--washi` `#F2EDE3`, ink `--sumi` `#1C1A17`, alert `--shu` `#C73E2B`,
    watch `--ochre` `#A87A2A`, accent `--ai` `#2E4A5B`, muted `--nibi` / deeper
    `--nibi-faint`
  - dark (OS default via `prefers-color-scheme`, or explicit `data-theme="dark"`):
    surface `#0E0F12`, ink `#E7E9EC`, alert `#D84F63`, watch `#B48C1A`, accent
    `#279A83`
  Severity always gets a marker/text label as well as color.

### Theme toggle button (renderer-owned markup)

`toggle.js` wires a click handler on `#theme-toggle` and persists under the same
localStorage key the garden uses (`loops-theme`). The button HTML is small enough
to own in the renderer (same tier as the envelope snippet below). Place it near
header meta:

```html
<button id="theme-toggle" type="button" aria-label="toggle theme">◐</button>
```

## The envelope (required on every page)

Emit EXACTLY ONE block, escaping `</` inside the JSON so the payload can never
terminate the script element:

    envelope = {"meta": {"loop": loop, "run_id": run_id,
                          "generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "title": title, "page_class": "snapshot",
                          "totals": {...flat numbers/short strings...}},
                "data": {...loop-specific payload...}}
    html_block = ('<script type="application/json" id="report-data">'
                  + json.dumps(envelope).replace("</", "<\\/") + "</script>")

Verify locally: `bin/page_envelope.py check --file page.html`.

## reference/

`fixture-scan.json` — a sanitized av-scan-shaped fixture (fake paths, fake homedir).
The rendered reference page (`reference/reference-page.html`, produced by the kagi-ban
renderer in its build task) is the quality benchmark; the original Generalissimo-approved page
lives outside the repo at `~/projects/av-audit/` and must not be vendored (it embeds
this machine's real exposure paths).
