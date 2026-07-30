# pagekit — the shared report-page kit

Rules of the road: `docs/REPORT_PAGES_PLAN.md` §2 (contract, MUST) + §3 (kit, SHOULD).

## Using the kit

- **Read** `$PAGEKIT/kit.css` at render time and inline the body into your page's
  `<style>`. Do not paste a copy into your renderer — a copy drifts, and the kit
  exists so a restyle reaches every page-enabled loop at once. Strip the leading
  `/* … */` header comment; it is for maintainers, not browsers.
- Never `<link>` it: pages must be self-contained and fetch NOTHING on load.
  Navigation `<a href="https://…">` is fine, and so is a URL inside escaped report
  text — the rule is about references the browser dereferences, not about the
  string `http://`. `tests/html_selfcontained.py` is the check.
- Treat a missing/unreadable `kit.css` as fatal to the render. It ships in this
  repo, so its absence means a broken checkout; failing names the path in
  `page-render.log` instead of promoting a silently unstyled page.
- Layout vocabulary: `.wrap` page column · `.hd`/`.kicker`/`.hero` header · `.stats`/`.stat`
  stat strip · `.brow` label+bar rows · `.group`/`.ghead`/`.frow`/`.fbody` grouped
  detail rows (`<details>`) · `footer` provenance line · `#tip` tooltip.
- Palette: high `#d84f63`, medium `#b48c1a`, accent `#279a83` on surface `#0e0f12`.
  Severity always gets a marker/text label as well as color.

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
