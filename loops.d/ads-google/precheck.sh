#!/usr/bin/env bash
# ads-google/precheck.sh — the pre-engine FETCH + deterministic digest stage
# (script->agent pattern, docs INTERFACES.md §4.1/§6.2). This script is a plain
# unsandboxed runner-invoked script — it is NOT governed by the engine's
# perm_network axis (§7.3 note), so it is where all network I/O for this loop
# lives. It:
#   1. curls the LOCAL Growth Console read-only ads JSON surface into the run
#      dir ($OUT_DIR/inputs/*.json) — keeps every umami/ads read behind GC's
#      single rate limiter; no Google/Reddit credentials in this loop.
#   2. derives loop SCOPE from the experiments registry at RUN TIME (never
#      hardcodes campaign ids) — google cards EXCEPT intl/retired.
#   3. prints a compact, deterministic digest to stdout (impressions/CTR/spend/
#      verdict per in-scope variant, journal tail, program events, budget
#      headroom, verdict-due, prior action-set ids for stable-ID continuity).
# Its stdout is injected into the engine prompt as ground truth; the raw json
# files stay in the run dir as the audit trail. Never mutates anything remote,
# never touches git/CDP/record_and_apply.
set -euo pipefail

GC="${GC_BASE:-http://127.0.0.1:8787}"
INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"
mkdir -p "$INPUTS"

fetch() { # fetch <name> <path-with-query>
  local name="$1" path="$2"
  curl -s -m 15 "$GC$path" -o "$INPUTS/$name.json" 2>/dev/null || true
  # a non-JSON or empty body becomes an explicit null so the digest can flag it
  if ! python3 -c "import json,sys; json.load(open('$INPUTS/$name.json'))" 2>/dev/null; then
    printf 'null' > "$INPUTS/$name.json"
  fi
}

fetch scoreboard      "/api/ads/scoreboard"
fetch campaigns       "/api/ads/campaigns"
fetch journal         "/api/ads/journal?limit=60"
fetch program-events  "/api/ads/program-events"

FETCHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

INPUTS="$INPUTS" FETCHED_AT="$FETCHED_AT" \
LOOPS_ROOT="${LOOPS_ROOT:-}" RUN_ID="${RUN_ID:-}" \
python3 <<'PY'
import json, os, glob
from pathlib import Path

INPUTS = Path(os.environ["INPUTS"])
FETCHED_AT = os.environ["FETCHED_AT"]
LOOPS_ROOT = os.environ.get("LOOPS_ROOT") or ""
RUN_ID = os.environ.get("RUN_ID") or ""

def load(name):
    try:
        v = json.loads((INPUTS / f"{name}.json").read_text())
        return v if v is not None else None
    except Exception:
        return None

sb   = load("scoreboard")
camp = load("campaigns")
jrnl = load("journal")
prog = load("program-events")

print("# ads-google — precheck digest")
print(f"fetched_at: {FETCHED_AT}  (source: local Growth Console JSON surface)")
print(f"inputs: scoreboard={'ok' if sb else 'MISSING'} "
      f"campaigns={'ok' if camp else 'MISSING'} "
      f"journal={'ok' if jrnl else 'MISSING'} "
      f"program_events={'ok' if prog else 'MISSING'}")
print("x_cache_age: n/a (google network — no X CDP cache involved)")
print()

# ---- SCOPE from the experiments registry (google cards except intl/retired) ----
INTL_KEYS = {"g-intl"}
scope_variants, scope_campaigns, scope_cards = set(), {}, []
if isinstance(camp, dict):
    for c in camp.get("cards", []):
        key = c.get("key", "")
        if key in INTL_KEYS or c.get("status") == "retired":
            continue
        google_leg = None
        for leg in c.get("legs", []):
            if leg.get("network") == "google":
                google_leg = leg
                break
        if not google_leg:
            continue
        vids = [v for v in (google_leg.get("variant_ids") or [])]
        if not vids and not google_leg.get("campaigns"):
            continue
        scope_variants.update(vids)
        camps = [(cc.get("campaign_id"), cc.get("name"),
                  cc.get("configured_status") or cc.get("status"))
                 for cc in google_leg.get("campaigns", [])]
        for cid, nm, st in camps:
            if cid:
                scope_campaigns[cid] = nm
        scope_cards.append({"key": key, "title": c.get("title"),
                            "started": c.get("started"),
                            "variant_ids": sorted(vids), "campaigns": camps,
                            "cadence_days": c.get("cadence_days")})

print("## Scope (derived from registry at run time — do NOT hardcode)")
if scope_cards:
    for sc in scope_cards:
        cadence = sc["cadence_days"]
        cad = f", cadence_days={cadence}" if cadence else ""
        print(f"- {sc['key']} ({sc['title']}) started {sc['started']}{cad}")
        for cid, nm, st in sc["campaigns"]:
            print(f"    campaign {cid} {nm} [{st}]")
    print(f"- scope variant ids: {sorted(scope_variants)}")
else:
    print("- NO scope cards resolved (campaigns payload missing/empty) — treat as input gap.")
print("- EXCLUDED by design: g-intl (owned by ads-intl loop), retired.")
print()

# ---- Per-variant metrics (in-scope google rows) ----
print("## In-scope variant metrics (scoreboard)")
EVAL_IMPR_GATE = 2000
if isinstance(sb, dict):
    grows = ((sb.get("networks") or {}).get("google") or {}).get("rows") or []
    shown = 0
    for r in grows:
        vid = r.get("variant_id")
        if vid not in scope_variants:
            continue
        shown += 1
        ev = r.get("evaluator") or {}
        verdict = ev.get("verdict") or ev.get("status") or "—"
        impr = r.get("impressions") or 0
        gate = "EVAL-ELIGIBLE" if impr >= EVAL_IMPR_GATE else f"below {EVAL_IMPR_GATE}-impr gate"
        pls = r.get("placements") or []
        legs = "; ".join(f"{p.get('leg')}=camp {p.get('campaign_external_id')}"
                         + (f"/grp {p.get('ad_group_external_id')}" if p.get('ad_group_external_id') else "")
                         for p in pls) or "(no placements)"
        print(f"- {vid} [{r.get('angle')}] status={r.get('status')} verdict={verdict}")
        print(f"    impr={impr} clicks={r.get('clicks')} ctr={r.get('ctr')} "
              f"spend=${r.get('spend_usd')} cpc={r.get('cpc_usd')} "
              f"landing_views={r.get('landing_views')} ({gate})")
        print(f"    placements: {legs}")
    if shown == 0:
        print("- no in-scope google variant rows found in scoreboard.")
    print(f"- scoreboard window: last {sb.get('days')} days")
else:
    print("- scoreboard MISSING — cannot read per-variant metrics (input gap).")
print()

# ---- Budget headroom ----
print("## Budget headroom / spend basis")
if isinstance(camp, dict):
    tot = camp.get("totals") or {}
    print(f"- program total spend (all networks): ${tot.get('spend_usd')} "
          f"across {tot.get('campaigns')} campaigns / {tot.get('experiments')} experiments")
    cpa = camp.get("cpa") or {}
    print(f"- CPA readable={cpa.get('readable')} conversions_sitewide={cpa.get('conversions_sitewide')} "
          f"intent_sitewide={cpa.get('intent_sitewide')} (event={cpa.get('conversion_event')})")
    print("- NOTE: the budget GUARD binds on COMMITTED basis first (monthly_cap), "
          "then the google-network ACTUAL MTD-spend gate; a positive-spend order "
          "may be refused even with paper headroom. Any positive-spend action "
          "brief MUST state committed-vs-actual basis.")
else:
    print("- campaigns MISSING — no headroom read.")
print()

# ---- Journal tail (google only) ----
print("## Journal tail (google rows, newest first)")
if isinstance(jrnl, dict):
    rows = [r for r in (jrnl.get("rows") or []) if r.get("network") == "google"]
    for r in rows[:14]:
        print(f"- #{r.get('id')} {r.get('created_at')} {r.get('action')} "
              f"ext={r.get('external_id')} amt=${r.get('amount_usd')} "
              f"[{r.get('status')}] {(r.get('detail') or '')[:90]}")
    if not rows:
        print("- no google journal rows in the last 60.")
else:
    print("- journal MISSING.")
print()

# ---- Program events ----
print("## Program events (non-journalable changes / incidents)")
if isinstance(prog, dict):
    for e in prog.get("events", []):
        print(f"- {e.get('date')}: {(e.get('text') or '')[:220]}")
else:
    print("- program-events MISSING.")
print()

# ---- Prior action set (stable-ID continuity) ----
print("## Prior action set (keep ids of still-open actions; strike resolved; new = max+1)")
prior = None
if LOOPS_ROOT:
    best_ts = None
    for ctxp in glob.glob(str(Path(LOOPS_ROOT) / "state" / "runs" / "*" / "action-set" / "context.json")):
        try:
            ctx = json.loads(Path(ctxp).read_text())
        except Exception:
            continue
        if ctx.get("loop") != "ads-google":
            continue
        if RUN_ID and ctx.get("run_id") == RUN_ID:
            continue
        ts = ctx.get("generated") or ""
        if best_ts is None or ts > best_ts:
            best_ts = ts
            prior = (Path(ctxp).parent, ctx)
if prior:
    pdir, pctx = prior
    print(f"- prior run: {pctx.get('run_id')} generated {pctx.get('generated')} "
          f"({pctx.get('open_count')} open / {pctx.get('struck_count')} struck)")
    reg = pdir / "ACTIONS.md"
    if reg.is_file():
        for line in reg.read_text().splitlines():
            s = line.strip()
            if s.startswith("## "):
                print(f"    {s[3:]}")
else:
    print("- NO prior set found — this is the first ads-google run. Start at ADG-01.")
print()
print("## END digest")
PY
