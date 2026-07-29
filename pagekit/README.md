# pagekit — the shared report-page kit

Rules of the road: `docs/REPORT_PAGES_PLAN.md` §2 (contract, MUST) + §3 (kit, SHOULD).

## Using the kit

- Inline `kit.css` into your page's `<style>` at render time. Never `<link>` it —
  pages must be self-contained (zero network fetches; `<a href>` links are fine).
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
