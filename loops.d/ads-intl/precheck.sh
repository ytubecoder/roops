#!/usr/bin/env bash
# ads-intl/precheck.sh — the pre-engine FETCH + deterministic digest stage
# (script->agent pattern, docs INTERFACES.md §4.1/§6.2). This script is a plain
# unsandboxed runner-invoked script — it is NOT governed by the engine's
# perm_network axis (§7.3 note), so it is where all network I/O for this loop
# lives. It:
#   1. curls the LOCAL Growth Console read-only ads JSON surface into the run
#      dir ($OUT_DIR/inputs/*.json) — keeps every umami/ads read behind GC's
#      single rate limiter; no Google/Reddit credentials in this loop.
#   2. derives loop SCOPE from the experiments registry at RUN TIME (never
#      hardcodes campaign ids) — the INTL google cards ONLY (g-intl today,
#      plus any future card whose utm_campaigns contain 'intl').
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

fetch() { # fetch <name> <path-with-query> — bounded retry: 3 attempts, 3s/6s
  # backoff. GC cold-starts after a machine wake can exceed one 15s curl (run
  # f3acea alerted spuriously on exactly this); worst case stays under the
  # runner's 300s precheck cap: 4 endpoints x (3x15s + 9s) = 216s.
  local name="$1" path="$2" attempt
  for attempt in 1 2 3; do
    curl -s -m 15 "$GC$path" -o "$INPUTS/$name.json" 2>/dev/null || true
    if python3 -c "import json,sys; json.load(open('$INPUTS/$name.json'))" 2>/dev/null; then
      return 0
    fi
    if [ "$attempt" -lt 3 ]; then sleep $((attempt * 3)); fi
  done
  # still not JSON after retries -> explicit null so the digest flags the gap
  printf 'null' > "$INPUTS/$name.json"
}

fetch scoreboard      "/api/ads/scoreboard"
fetch campaigns       "/api/ads/campaigns"
fetch journal         "/api/ads/journal?limit=60"
fetch program-events  "/api/ads/program-events"

FETCHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

INPUTS="$INPUTS" FETCHED_AT="$FETCHED_AT" OUT_DIR="${OUT_DIR}" \
LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" \
LOOPS_ROOT="${LOOPS_ROOT:-}" RUN_ID="${RUN_ID:-}" \
python3 <<'PY'
import json, os, glob, re, sys
from datetime import datetime, timezone
from pathlib import Path

INPUTS = Path(os.environ["INPUTS"])
FETCHED_AT = os.environ["FETCHED_AT"]
OUT_DIR = Path(os.environ.get("OUT_DIR") or ".")
LOOP_DIR = os.environ.get("LOOP_DIR") or ""
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

INPUT_STATE = {"scoreboard": sb, "campaigns": camp, "journal": jrnl, "program_events": prog}
INPUTS_MISSING = sum(1 for v in INPUT_STATE.values() if not v)

print("# ads-intl — precheck digest")
print(f"fetched_at: {FETCHED_AT}  (source: local Growth Console JSON surface)")
print(f"inputs: scoreboard={'ok' if sb else 'MISSING'} "
      f"campaigns={'ok' if camp else 'MISSING'} "
      f"journal={'ok' if jrnl else 'MISSING'} "
      f"program_events={'ok' if prog else 'MISSING'}")
print("x_cache_age: n/a (intl google (intl campaigns ride the google network account) — no X CDP cache involved)")
print()

# ---- SCOPE from the experiments registry (INTL google cards ONLY) ----
# Shared intl predicate — BYTE-IDENTICAL in ads-google/precheck.sh and
# ads-intl/precheck.sh (review 2026-07-28): ads-intl INCLUDES a card when true,
# ads-google EXCLUDES it — complementary by construction, so a future intl card
# with a new key (e.g. g-intl-jp) is claimed by exactly one loop. Edit BOTH
# copies together or the fleet double-claims/orphans a campaign.
INTL_KEYS = {"g-intl"}
def _is_intl(c):
    if c.get("key", "") in INTL_KEYS:
        return True
    return any("intl" in str(u or "") for u in (c.get("utm_campaigns") or []))
scope_variants, scope_campaigns, scope_cards = set(), {}, []
if isinstance(camp, dict):
    for c in camp.get("cards", []):
        key = c.get("key", "")
        if not _is_intl(c) or c.get("status") == "retired":
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
print("- EXCLUDED by design: every non-intl google card (g-msg, g-theme — owned by ads-google), all other networks, retired.")
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

# ---- Prior action set + ID continuity (ids are NEVER reused) ----
# Two separate questions, historically conflated into one bug:
#   (a) which set do I carry forward?  -> newest VALID set (chronological, by
#       context.json `generated`, never by dir name — run ids are harness-named)
#   (b) which ids are burned?          -> the HIGH-WATER MARK across every set
#       AND every prior contract's finding ids. A run that emitted ADI-01..04
#       into its contract but failed to persist a set (real: run 58e835) still
#       burned those ids — the next run must NOT hand them to new actions.
# Conflating them is what let run aba304 restart at ADI-01 and silently reuse
# four live ids.
ID_RE = re.compile(r"^ADI-(?:[A-Z]+-)?(\d{2,})$")
HEAD_RE = re.compile(r"^##\s+(?:~~)?\s*(ADI-(?:[A-Z]+-)?\d+)\b")
LOOP_PREFIX = "ads-intl:"

# Defense in depth: screen candidate sets with the shipped validator, so a
# malformed set is never carried forward as if it were truth.
_validator = None
if LOOP_DIR:
    sys.path.insert(0, str(Path(LOOP_DIR) / "bin"))
    try:
        import validate_action_set as _validator
    except Exception:
        _validator = None


def _parse_ts(value):
    """ISO-8601 -> aware datetime; unparseable/absent sorts oldest, never crashes."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _burn(aid, state):
    m = ID_RE.match(str(aid))
    if m:
        state["reserved"].add(str(aid))
        state["high_water"] = max(state["high_water"], int(m.group(1)))


state = {"high_water": 0, "reserved": set()}
candidates, skipped, ghost_ids = [], [], set()

if LOOPS_ROOT:
    runs_root = Path(LOOPS_ROOT) / "state" / "runs"

    # (b) burn ids from every persisted set — valid or not.
    for ctxp in sorted(glob.glob(str(runs_root / "*" / "action-set" / "context.json"))):
        set_dir = Path(ctxp).parent
        run_name = set_dir.parent.name
        try:
            ctx = json.loads(Path(ctxp).read_text())
        except Exception as exc:
            skipped.append((run_name, f"context.json unparseable ({type(exc).__name__})"))
            continue
        if not isinstance(ctx, dict) or ctx.get("loop") != "ads-intl":
            continue
        for aid in (ctx.get("action_ids") or []):
            _burn(aid, state)
        reg = set_dir / "ACTIONS.md"
        if reg.is_file():
            for line in reg.read_text().splitlines():
                m = HEAD_RE.match(line)
                if m:
                    _burn(m.group(1), state)
        if RUN_ID and ctx.get("run_id") == RUN_ID:
            continue  # never carry forward this very run
        errors = _validator.validate(set_dir) if _validator else []
        if errors:
            skipped.append((run_name, f"set fails validation ({errors[0]})"))
            continue
        candidates.append((_parse_ts(ctx.get("generated")), run_name, set_dir, ctx))

    # (b cont.) burn "ghost" ids — emitted as findings by a run whose set never
    # landed on disk. These have no set to carry forward but are still spent.
    for cpath in sorted(glob.glob(str(runs_root / "*" / "contract.json"))):
        run_name = Path(cpath).parent.name
        if "ads-intl" not in run_name:
            continue
        try:
            contract = json.loads(Path(cpath).read_text())
        except Exception:
            continue
        for f in (contract.get("findings") or []):
            fid = str(f.get("finding_id") or "")
            if fid.startswith(LOOP_PREFIX):
                bare = fid[len(LOOP_PREFIX):]
                if bare not in state["reserved"]:
                    ghost_ids.add(bare)
                _burn(bare, state)

# Deterministic: newest timestamp wins; run-dir name breaks exact ties.
candidates.sort(key=lambda t: (t[0], t[1]))
prior = candidates[-1] if candidates else None
high_water = state["high_water"]
next_id = f"ADI-<SRC>-{high_water + 1:02d}"

print("## Prior action set (keep ids of still-open actions; strike resolved)")
if prior:
    _, _, pdir, pctx = prior
    print(f"- prior run: {pctx.get('run_id')} generated {pctx.get('generated')} "
          f"({pctx.get('open_count')} open / {pctx.get('struck_count')} struck)")
    reg = pdir / "ACTIONS.md"
    if reg.is_file():
        for line in reg.read_text().splitlines():
            s = line.strip()
            if s.startswith("## "):
                print(f"    {s[3:]}")
elif high_water:
    print("- ⚠️ NO usable prior set, but PRIOR RUNS EXIST. This is NOT a first run.")
    print("- Do NOT restart at ADI-01 — those ids are live/spent. Carry nothing")
    print("  forward (no set to read), and number every new action from the")
    print("  high-water mark below. Raise the gap itself as an action.")
elif skipped:
    print("- NO usable prior set. Prior run dirs exist but none could be read (see")
    print("  SKIPPED below), so the high-water mark may UNDERSTATE what is spent.")
    print("  Treat the unreadable sets as an input gap and raise it as an action.")
else:
    print("- NO prior set and no prior ids — this is genuinely the first ads-intl run.")
for run_name, why in skipped:
    print(f"- SKIPPED {run_name}: {why}")
if ghost_ids:
    print(f"- BURNED (emitted as findings, set never persisted): {sorted(ghost_ids)}")
print(f"- id high-water mark: {high_water}  →  next NEW action id = {next_id}")
print("- replace <SRC> with the source designator that raised the exception:")
print("  EV evaluator · CMP campaign/delivery · JRN journal/guard · BUD budget/caps · INP input gap.")
print("- prior legacy single-part ids (ADI-NN) are carried VERBATIM — never renamed.")
print("- ids are NEVER reused, not even after a strike.")
print()

# Machine-readable continuity record for validate_action_set.py --continuity.
# prior_open_ids = prior register headings NOT struck (~~...~~) — the ids the
# next set MUST carry forward (open or newly struck); dropping one is the
# "silent restart / silent drop" failure the completeness check exists for.
prior_open_ids = []
if prior:
    _reg = prior[2] / "ACTIONS.md"
    if _reg.is_file():
        for _line in _reg.read_text().splitlines():
            _s = _line.strip()
            _m = HEAD_RE.match(_s)
            if _m and not _s.startswith("## ~~"):
                prior_open_ids.append(_m.group(1))
try:
    (OUT_DIR / "continuity.json").write_text(json.dumps({
        "high_water": high_water,
        "next_id": next_id,
        "prior_ids": sorted((prior[3].get("action_ids") or []) if prior else []),
        "prior_open_ids": sorted(prior_open_ids),
        "prior_run_id": (prior[3].get("run_id") if prior else None),
        "reserved_ids": sorted(state["reserved"]),
        "skipped": [{"run": r, "why": w} for r, w in skipped],
    }, indent=2) + "\n")
except Exception as exc:
    print(f"- (continuity.json not written: {exc})")
    print()

# ---- Authoritative metrics (copy verbatim — do not recount) ----
# These are computed here, not inferred by the model. Run aba304 emitted
# `inputs.missing: 4` on a run where this digest said all four inputs were ok,
# which lit the dashboard's alert threshold on healthy inputs.
print("## METRICS (authoritative — copy these values verbatim into contract.metrics)")
print(f"- inputs.missing: {INPUTS_MISSING}   (of 4 GC endpoints; 0 = all fetched)")
print(f"- scope.variants: {len(scope_variants)}")
print(f"- scope.campaigns: {len(scope_campaigns)}")
print("- actions.open / actions.struck: count them from the set YOU emit this run.")
print("- All metric values are NUMBERS, not strings.")
print()

# ---- What to do when the action set cannot be written ----
print("## If the action set cannot be written or fails validation")
print("- Emit the analysis anyway: full `report_markdown` + a `headline` saying so.")
print("- Set `status: alert` with a precise `status_reason`.")
print("- Emit **ZERO findings** — `findings: []`. No ADI- id may enter the findings")
print("  list without a durable set behind it, or it becomes an un-openable brief.")
print("- This is deliberate (settled 2026-07-28) and it is also what makes the")
print("  alert surface: per INTERFACES.md §4.5, a non-empty findings array overrides")
print("  the declared status with the findings' max severity, so an `alert` run with")
print("  only `warn` findings displays amber. Empty findings ⇒ declared status stands.")
print()
print("## END digest")
PY
