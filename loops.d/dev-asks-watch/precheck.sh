#!/usr/bin/env bash
# dev-asks-watch/precheck.sh — deterministic gathering step (script->agent
# pattern, docs/LOOP_AUTHORING.md §3/§6). This script reads, read-only, every
# issue and PR our work account (tomokagami = tom@utbox.net) has filed in the
# two Synacy repos the Maguyva developers answer in, diffs the result against
# this loop's own cache/, and prints ONLY what changed. Empty stdout means
# nothing moved -> the runner records skipped-precheck and the engine is never
# invoked (zero tokens). The engine's job starts from this output; it never
# re-discovers the world.
#
# Credentials: `gh auth token -u tomokagami` reads ~/.config/gh/hosts.yml of
# the launchd user (llm). The token is used inline per command and never
# printed. Every call here is a read (`gh issue list/view`, `gh pr list/view`,
# `gh api` GET) — the standing exception in memory
# feedback_never_use_utbox_github_unasked ("checking the status of OUR OWN
# outstanding asks, read-only, needs no permission").
#
# Cross-run diffing: a self-contained cache/snapshot.json next to this script
# (same convention as competitor-watch). Fetch fresh, diff, overwrite. If the
# engine run after a diff fails, that diff is not replayed next time — the
# dated inputs copy under $OUT_DIR/inputs/ is the audit trail (SPEC.md §3).
set -euo pipefail

INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"
mkdir -p "$INPUTS"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$HERE/cache"
mkdir -p "$CACHE"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
REPOS="Synacy/maguyva-frontend Synacy/rise-data-platform"
AUTHORS="tomokagami"          # our work account; add ytubecoder here if it ever files asks
OURS="tomokagami,ytubecoder"  # comments by these are "us", not "them"
STALE_DAYS=14                 # open + untouched this long -> stale nudge, re-nudged each further 14d

if ! command -v gh >/dev/null; then
  echo "INPUT GAP: gh CLI not found on PATH ($PATH). No asks were read." ; exit 1
fi
if ! GH_TOKEN="$(gh auth token -u tomokagami 2>/dev/null)" || [ -z "${GH_TOKEN:-}" ]; then
  echo "INPUT GAP: no gh credential for the tomokagami account on this host (gh auth token -u tomokagami failed)."
  echo "Nothing was read. This is a credential problem on the loop host, not a statement about the asks."
  exit 1
fi
export GH_TOKEN

FRESH="$INPUTS/snapshot.json"
: > "$INPUTS/raw.jsonl"
for repo in $REPOS; do
  for author in $AUTHORS; do
    gh issue list -R "$repo" --author "$author" --state all --limit 100 \
      --json number,title,state,url,createdAt,updatedAt,closedAt,comments \
      | jq -c --arg repo "$repo" '.[] | . + {repo:$repo, kind:"issue"}' >> "$INPUTS/raw.jsonl"
    gh pr list -R "$repo" --author "$author" --state all --limit 100 \
      --json number,title,state,url,createdAt,updatedAt,closedAt,mergedAt,comments,reviews \
      | jq -c --arg repo "$repo" '.[] | . + {repo:$repo, kind:"pr"}' >> "$INPUTS/raw.jsonl"
  done
done

python3 - "$INPUTS/raw.jsonl" "$FRESH" "$CACHE/snapshot.json" "$OURS" "$STALE_DAYS" <<'PY'
import json, sys, datetime as dt
raw, fresh_p, cache_p, ours, stale_days = sys.argv[1], sys.argv[2], sys.argv[3], set(sys.argv[4].split(",")), int(sys.argv[5])
now = dt.datetime.now(dt.timezone.utc)

def parse(ts):
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None

snap = {}
for line in open(raw):
    it = json.loads(line)
    short = it["repo"].split("/")[-1]
    key = f"{short}#{it['number']}"
    events = []
    for c in it.get("comments") or []:
        events.append({"kind": "comment", "author": (c.get("author") or {}).get("login") or "?",
                       "at": c.get("createdAt"), "body": (c.get("body") or "")[:400].strip()})
    for r in it.get("reviews") or []:
        events.append({"kind": f"review:{(r.get('state') or '').lower()}", "author": (r.get("author") or {}).get("login") or "?",
                       "at": r.get("submittedAt"), "body": (r.get("body") or "")[:400].strip()})
    events.sort(key=lambda e: e["at"] or "")
    state = it["state"]
    if it.get("mergedAt"):
        state = "MERGED"
    last_touch = parse(it.get("updatedAt")) or now
    days_quiet = (now - last_touch).days
    theirs = [e for e in events if e["author"] not in ours]
    snap[key] = {
        "key": key, "repo": it["repo"], "kind": it["kind"], "number": it["number"], "title": it["title"],
        "url": it["url"], "state": state, "created": it.get("createdAt"), "updated": it.get("updatedAt"),
        "closed": it.get("closedAt"), "merged": it.get("mergedAt"),
        "n_events": len(events), "events": events,
        "last_their_event": theirs[-1] if theirs else None,
        "awaiting_us": bool(events) and events[-1]["author"] not in ours and state == "OPEN",
        "stale_bucket": (days_quiet // stale_days) if state == "OPEN" else 0,
        "days_quiet": days_quiet,
    }

json.dump(snap, open(fresh_p, "w"), indent=1)
try:
    cache = json.load(open(cache_p))
except (FileNotFoundError, json.JSONDecodeError):
    cache = None

out = []
def item_line(s):
    return f"- {s['key']} [{s['kind']} · {s['state']}] {s['title']}  <{s['url']}>  created {s['created'][:10]}, last update {s['updated'][:10]} ({s['days_quiet']}d ago)"

if cache is None:
    out.append("## BASELINE — first run, no cache. Every ask listed; nothing here is a change.")
    for s in sorted(snap.values(), key=lambda s: (s["state"] != "OPEN", s["key"])):
        out.append(item_line(s))
        if s["last_their_event"]:
            e = s["last_their_event"]
            out.append(f"    last reply from {e['author']} {e['at'][:10]} ({e['kind']}): {e['body'][:200]!r}")
else:
    changes = []
    for key, s in snap.items():
        c = cache.get(key)
        if c is None:
            changes.append(("NEW ASK FILED", s, None)); continue
        if s["state"] != c["state"]:
            changes.append((f"STATE {c['state']} -> {s['state']}", s, None))
        new_events = s["events"][c["n_events"]:] if s["n_events"] > c["n_events"] else []
        for e in new_events:
            who = "us" if e["author"] in ours else "THEM"
            changes.append((f"NEW {e['kind'].upper()} from {e['author']} ({who}) {e['at'][:16]}", s, e))
        if s["stale_bucket"] > c.get("stale_bucket", 0) and s["state"] == "OPEN" and not new_events:
            changes.append((f"STALE — open with no activity for {s['days_quiet']} days", s, None))
    for key in cache:
        if key not in snap:
            changes.append(("VANISHED from listing (deleted/transferred?)", cache[key], None))
    if changes:
        out.append(f"## CHANGES since last run ({len(changes)})")
        for label, s, e in changes:
            out.append(f"- {label}: {s['key']} [{s['kind']}] {s['title']}  <{s['url']}>  state now {s['state']}")
            if e and e.get("body"):
                out.append(f"    > {e['body'][:400]}")
        out.append("")
        out.append("## CURRENT OPEN ASKS (context)")
        for s in sorted(snap.values(), key=lambda s: s["key"]):
            if s["state"] == "OPEN":
                flag = " · AWAITING US (their comment is the latest)" if s["awaiting_us"] else ""
                out.append(item_line(s) + flag)

if out:
    n_open = sum(1 for s in snap.values() if s["state"] == "OPEN")
    n_wait = sum(1 for s in snap.values() if s["awaiting_us"])
    out.append("")
    out.append(f"## TOTALS asks.open={n_open} asks.awaiting_us={n_wait} asks.total={len(snap)}")
    print("\n".join(out))
PY

# Overwrite the cache only after a successful fetch+diff.
cp "$FRESH" "$CACHE/snapshot.json"
