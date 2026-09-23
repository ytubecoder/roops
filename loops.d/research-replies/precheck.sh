#!/usr/bin/env bash
# research-replies/precheck.sh — deterministic gathering (script->agent pattern,
# docs/INTERFACES.md §4.1/§6.2). Calls probe:research-replies-read, which does
# the IMAP read and the CSV bookkeeping on llm, then prints ONLY what is new
# since the probe's UID cursor. Nothing new -> empty stdout -> the runner
# records skipped-precheck and the engine is never invoked.
set -euo pipefail

INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"
mkdir -p "$INPUTS"

if ! "$LOOPS_ROOT/bin/probe" research-replies-read --out "$INPUTS/replies.json" 2>"$INPUTS/replies.err"; then
  echo "# research-replies precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "PROBE TRANSPORT FAILED — could not run research-replies-read on llm."
  echo "This is an input gap, not evidence of an empty inbox."
  sed -n '1,20p' "$INPUTS/replies.err" 2>/dev/null || true
  exit 1
fi

python3 - "$INPUTS/replies.json" <<'PY'
import json, sys

d = json.load(open(sys.argv[1]))
new = d.get("new") or {}
errors = d.get("errors") or []
tot = d.get("totals") or {}
n_new = sum(len(v) for v in new.values())
if not n_new and not errors:
    sys.exit(0)  # nothing new: empty stdout on purpose

print(f"# research-replies precheck — probe generated {d.get('generated_at')}")
print(f"mailbox {d.get('mailbox')} · window since {d.get('window_since')} · uid cursor {d.get('cursor_before')} -> {d.get('cursor_after')}")
print(f"totals: sent {tot.get('sent')} · replies {tot.get('replies')} (new {tot.get('replies_new')}) · reply rate {tot.get('reply_rate_pct')}% · unsubscribed {tot.get('unsubscribed_total')}")
print(f"by_code: {json.dumps(tot.get('by_code'))}")
print()
if errors:
    print("## probe errors")
    for e in errors:
        print(f"  ERROR: {e}")
    print()

def cap(s, n=1500):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + " …[truncated]"

reps = new.get("replies") or []
print(f"## new replies ({len(reps)})")
for r in reps:
    print(f"- uid {r['uid']} · {r['received_at']} · {r['email']} · name={r.get('name') or '-'} · cohort {r['cohort']} · signup {r.get('signup_date')} · repo_linked={r.get('repo_linked')} · plan={r.get('plan_tier')}")
    print(f"  subject: {r.get('subject')}")
    print(f"  reply_code (parsed): {r.get('reply_code') or 'none'}")
    print("  text:")
    for ln in cap(r.get("reply_text")).splitlines():
        print(f"    | {ln}")
print()
b = new.get("bounces") or []
print(f"## new bounces ({len(b)})")
for x in b:
    print(f"- uid {x['uid']} · {x['received_at']} · from {x['from']} · bounced_email={x.get('bounced_email') or 'unknown'} · subject: {x.get('subject')}")
    print(f"  snippet: {cap(x.get('snippet'), 300)}")
print()
u = new.get("unsubscribes") or []
print(f"## new unsubscribes ({len(u)}) — send-list rows already marked notes=unsubscribed by the probe")
for x in u:
    print(f"- uid {x['uid']} · {x['received_at']} · {x.get('matched_email') or x['from']} · subject: {x.get('subject')} · snippet: {cap(x.get('snippet'), 200)}")
print()
a = new.get("auto_replies") or []
print(f"## new auto-replies ({len(a)}) — informational, not answers")
for x in a:
    print(f"- uid {x['uid']} · {x.get('matched_email') or x['from']} · subject: {x.get('subject')}")
print()
o = new.get("unmatched") or []
print(f"## unmatched senders ({len(o)}) — not on the send list; sender domain + subject only")
for x in o:
    print(f"- uid {x['uid']} · {x['received_at']} · @{x['domain']} · subject: {x.get('subject')}")
PY
