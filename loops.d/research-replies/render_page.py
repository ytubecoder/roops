#!/usr/bin/env python3
"""research-replies render_page.py — snapshot page of the user-research email
programme: send progress, reply rate, every reply to date (verbatim, with the
hand-coded research columns), call bookings, unsubscribes. Input is the probe
capture `replies.json` written by precheck.sh. Deterministic; inlines
$PAGEKIT/kit.css + toggle.js per pagekit/README.md; one #report-data envelope.
"""

import argparse
import datetime
import html
import json
import os
import pathlib
import re
import sys
from string import Template

_ROOT = pathlib.Path(os.environ.get("LOOPS_ROOT") or pathlib.Path(__file__).resolve().parents[2])
_PAGEKIT = pathlib.Path(os.environ.get("PAGEKIT") or _ROOT / "pagekit")
_KIT_HEADER_RE = re.compile(r"\A/\*.*?\*/\s*", re.DOTALL)

COHORT = {"1": "abandoned — never linked a repo", "2": "synced — linked, free plan", "3": "paid — crew plan"}
CODED = [("acquisition_source", "acquisition source"), ("exact_phrase", "phrase that caused signup"),
         ("job", "job to be done"), ("urgency", "urgency"), ("barrier", "barrier"),
         ("alternative", "alternative"), ("first_value_moment", "first value moment"),
         ("return_reason", "reason to return / not"), ("verbatim_language", "language worth testing"),
         ("followup_permitted", "follow-up permitted"), ("notes", "notes")]


def esc(s) -> str:
    return html.escape(str(s or ""), quote=True)


def load_kit(name: str) -> str:
    path = _PAGEKIT / name
    if not path.is_file():
        raise SystemExit(f"render_page.py: missing {path}")
    return _KIT_HEADER_RE.sub("", path.read_text()).rstrip("\n")


def reply_rows(replies: list[dict]) -> str:
    out = []
    for i, r in enumerate(sorted(replies, key=lambda r: r.get("received_at") or "", reverse=True), 1):
        coded = "".join(
            f"<tr><th>{esc(label)}</th><td>{esc(r.get(k) or '—')}</td></tr>" for k, label in CODED
        )
        text = esc(r.get("reply_text"))
        out.append(Template("""<details class="frow" style="--i:$i">
  <summary>
    <span class="cell-mk">$mk</span>
    <span class="cell-src">$who</span>
    <span class="cell-path">$first</span>
    <span class="cell-sev">$date</span>
  </summary>
  <div class="fbody">
    <p class="explain"><b>cohort $cohort</b> · $cohort_label · $last_event</p>
    <blockquote class="quote">$text</blockquote>
    <h4>Coding</h4>
    <table class="coded">$coded</table>
  </div>
</details>""").substitute(
            i=i, mk="●", who=esc(r.get("email")), first=esc((r.get("reply_text") or "").strip().splitlines()[0][:90] if r.get("reply_text") else "(empty)"),
            date=esc((r.get("received_at") or "")[:10]), cohort=esc(r.get("cohort")), cohort_label=esc(COHORT.get(r.get("cohort"), "?")),
            last_event=esc(r.get("last_verified_event")), text=text.replace("\n", "<br>"), coded=coded))
    return "\n".join(out) or '<p class="sub">No replies yet.</p>'


def booking_rows(bookings: list[dict]) -> str:
    if not bookings:
        return '<p class="sub">No call bookings yet.</p>'
    rows = "".join(
        f"<tr><td>{esc((b.get('received_at') or '')[:16])}</td><td>{esc(b.get('kind'))}</td>"
        f"<td>{esc(b.get('attendee_email') or 'unknown')}</td><td>{esc(b.get('when') or '?')}</td><td>{esc(b.get('notes'))}</td></tr>"
        for b in sorted(bookings, key=lambda b: b.get("received_at") or "", reverse=True)
    )
    return f'<table class="coded"><tr><th>notified</th><th>kind</th><th>attendee</th><th>when</th><th>notes</th></tr>{rows}</table>'


PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>$title</title>
<script>
$toggle_js
</script>
<style>
$kit_css
.quote{white-space:normal;border-left:3px solid var(--ai);padding:.4rem .8rem;margin:.4rem 0}
.coded{border-collapse:collapse;font-size:.9em}.coded th{text-align:left;padding:.15rem .6rem .15rem 0;color:var(--nibi);font-weight:600;vertical-align:top}.coded td{padding:.15rem 0}
</style>
</head>
<body>
<div class="wrap">
  <header class="hd">
    <div>
      <p class="kicker">Maguyva · user-research emails</p>
      <p class="hero">$replies<span class="unit"><strong>replies</strong> from $sent sent — $rate</span></p>
    </div>
    <p class="meta">
      mailbox <b>$mailbox</b><br>
      sending <b>$first_sent → $last_sent</b><br>
      captured <b>$captured</b><br>
      rendered <b>$rendered</b><br>
      <button id="theme-toggle" type="button" aria-label="toggle theme">◐</button>
    </p>
  </header>

  <div class="stats">
    <div class="stat" style="--i:1"><p class="n">$sent</p><p class="l">emails sent · $c1 abandoned · $c2 synced · $c3 paid</p></div>
    <div class="stat" style="--i:2"><p class="n">$replies</p><p class="l">replies · $r1 / $r2 / $r3 by cohort</p></div>
    <div class="stat" style="--i:3"><p class="n">$bookings</p><p class="l">call bookings</p></div>
    <div class="stat" style="--i:4"><p class="n md">$unsubs</p><p class="l"><span class="dot" style="background:var(--ochre)"></span>unsubscribed · $errors send errors</p></div>
  </div>

  <section class="group">
    <header class="ghead"><h3>Replies</h3><span class="gmeta">$replies to date, newest first</span></header>
    <p class="gblurb">Verbatim text as received (quoted history stripped). The coding table is filled by hand or from the loop's proposed coding in each finding; this page never edits it. Do not read counts as market prevalence.</p>
$reply_rows
  </section>

  <section class="group">
    <header class="ghead"><h3>Call bookings</h3><span class="gmeta">$bookings via cal.com</span></header>
    <p class="gblurb">Second bite: 15-minute calls booked from the email link. Prepare with user-research-interview-guide.md.</p>
$booking_rows
  </section>

  <footer>
    data <span class="cmd">probe research-replies-read</span> (IMAP read-only on llm) · record
    <span class="cmd">user-research-replies.csv</span> / <span class="cmd">user-research-bookings.csv</span> ·
    render <span class="cmd">render_page.py</span> · envelope <span class="cmd">#report-data</span>
  </footer>
</div>
<script type="application/json" id="report-data">$envelope</script>
</body>
</html>
""")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--loop", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()
    d = json.loads(pathlib.Path(a.capture).read_text())
    tot = d.get("totals") or {}
    prog = d.get("send_progress") or {}
    replies = d.get("all_replies") or []
    bookings = d.get("all_bookings") or []
    by_c = {c: sum(1 for r in replies if r.get("cohort") == c) for c in ("1", "2", "3")}
    sent_c = prog.get("by_cohort_sent") or {}
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rate = f"{tot.get('reply_rate_pct')}% reply rate" if tot.get("reply_rate_pct") is not None else "reply rate n/a"
    envelope = {
        "meta": {"loop": a.loop, "run_id": a.run_id, "generated_at": now,
                 "title": "User-research emails — replies and bookings", "page_class": "snapshot",
                 "totals": {"sent": tot.get("sent", 0), "replies": tot.get("replies", 0),
                            "reply_rate_pct": tot.get("reply_rate_pct"), "bookings": tot.get("bookings", 0),
                            "unsubscribed": tot.get("unsubscribed_total", 0)}},
        "data": {"totals": tot, "send_progress": prog, "replies": replies, "bookings": bookings},
    }
    page = PAGE.substitute(
        title="User-research emails — replies and bookings", toggle_js=load_kit("toggle.js"), kit_css=load_kit("kit.css"),
        replies=tot.get("replies", 0), sent=tot.get("sent", 0), rate=esc(rate), mailbox=esc(d.get("mailbox")),
        first_sent=esc((prog.get("first_sent_at") or "—")[:16]), last_sent=esc((prog.get("last_sent_at") or "—")[:16]),
        captured=esc(d.get("generated_at")), rendered=now,
        c1=sent_c.get("1", 0), c2=sent_c.get("2", 0), c3=sent_c.get("3", 0),
        r1=by_c["1"], r2=by_c["2"], r3=by_c["3"], bookings=tot.get("bookings", 0),
        unsubs=tot.get("unsubscribed_total", 0), errors=prog.get("errors", 0),
        reply_rows=reply_rows(replies), booking_rows=booking_rows(bookings),
        envelope=json.dumps(envelope).replace("</", "<\\/"),
    )
    pathlib.Path(a.out).write_text(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())
