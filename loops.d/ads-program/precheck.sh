#!/usr/bin/env bash
# ads-program/precheck.sh — the pre-engine FETCH + deterministic digest stage
# (script->agent pattern, docs INTERFACES.md §4.1/§6.2). This script is a plain
# unsandboxed runner-invoked script — it is NOT governed by the engine's
# perm_network axis (§7.3 note), so it is where all network I/O for this loop
# lives. It:
#   1. curls the LOCAL Growth Console read-only ads JSON surface into the run
#      dir ($OUT_DIR/inputs/*.json) — keeps every umami/ads read behind GC's
#      single rate limiter; no network credentials in this loop.
#   2. enumerates the four NETWORK loops' newest action sets from $LOOPS_ROOT
#      run dirs (read-only) with same-day freshness checks — a missing/stale
#      upstream set becomes a REPORTED GAP in this loop's own set, never a run
#      failure (the stagger is best-effort; freshness logic is the guarantee).
#   3. prints a cross-network digest: per-experiment campaign inventory across
#      networks, spend totals vs caps (committed binds first), journal tail
#      (ALL networks), program events, sibling sets' open action ids, prior
#      ADP set for stable-ID continuity.
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

camp = load("campaigns")
jrnl = load("journal")
prog = load("program-events")

INPUT_STATE = {"campaigns": camp, "journal": jrnl, "program_events": prog}
INPUTS_MISSING = sum(1 for v in INPUT_STATE.values() if not v)

print("# ads-program — precheck digest")
print(f"fetched_at: {FETCHED_AT}  (source: local Growth Console JSON surface)")
print(f"inputs: campaigns={'ok' if camp else 'MISSING'} "
      f"journal={'ok' if jrnl else 'MISSING'} "
      f"program_events={'ok' if prog else 'MISSING'}")
print("x_cache_age: see the ads-x set below — X freshness is ads-x's check, referenced here.")
print()

# ---- Cross-network experiment inventory (ALL networks — program view) ----
scope_campaigns = {}
print("## Experiments x networks (from the registry — the program-level view)")
if isinstance(camp, dict):
    for c in camp.get("cards", []):
        status = c.get("status")
        marker = " [RETIRED]" if status == "retired" else ""
        print(f"- {c.get('key')} ({c.get('title')}){marker} networks={c.get('networks')} spend=${c.get('spend_usd')}")
        for leg in c.get("legs", []):
            for cc in leg.get("campaigns", []):
                cid = cc.get("campaign_id")
                if cid and status != "retired":
                    scope_campaigns[cid] = cc.get("name")
                print(f"    {leg.get('network')} campaign {cc.get('campaign_id')} {cc.get('name')} "
                      f"[{cc.get('configured_status') or cc.get('status')}] "
                      f"budget/day={cc.get('daily_budget_usd')} spend=${cc.get('spend_usd')}")
            for pend in (leg.get("pending") or []):
                print(f"    {leg.get('network')} PENDING bring-up: {pend.get('name')} — {str(pend.get('note') or '')[:120]}")
            for dr in (leg.get("drift") or []):
                print(f"    {leg.get('network')} DRIFT: {str(dr)[:140]}")
else:
    print("- campaigns MISSING — no inventory (input gap).")
print()

# ---- Sibling network loops' newest action sets (read-only; REFERENCE, never duplicate) ----
print("## Network loops' newest sets (reference their ids — e.g. ads-google:ADG-03 — never duplicate orders)")
SIBLINGS = ["ads-google", "ads-intl", "ads-reddit", "ads-x"]
STALE_HOURS = 36
sets_stale = 0
sets_missing = 0
now = datetime.now(timezone.utc)
if LOOPS_ROOT:
    runs_root = Path(LOOPS_ROOT) / "state" / "runs"
    for sib in SIBLINGS:
        best = None
        for ctxp in glob.glob(str(runs_root / "*" / "action-set" / "context.json")):
            try:
                ctx = json.loads(Path(ctxp).read_text())
            except Exception:
                continue
            if not isinstance(ctx, dict) or ctx.get("loop") != sib:
                continue
            ts = str(ctx.get("generated") or "")
            if best is None or ts > best[0]:
                best = (ts, Path(ctxp).parent, ctx)
        if best is None:
            sets_missing += 1
            print(f"- {sib}: NO SET FOUND — report this as a gap in your own set (never a run failure).")
            continue
        ts, sdir, ctx = best
        try:
            age_h = round((now - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 3600, 1)
        except Exception:
            age_h = None
        stale = age_h is None or age_h > STALE_HOURS
        if stale:
            sets_stale += 1
        tag = f"age={age_h}h" + (" STALE — flag as a gap; do not treat its numbers as current" if stale else "")
        print(f"- {sib}: run {ctx.get('run_id')} generated {ts} ({ctx.get('open_count')} open / {ctx.get('struck_count')} struck) {tag}")
        reg = sdir / "ACTIONS.md"
        if reg.is_file():
            for line in reg.read_text().splitlines():
                s = line.strip()
                if s.startswith("## ") and not s.startswith("## ~~"):
                    print(f"    OPEN {s[3:]}")
else:
    sets_missing = len(SIBLINGS)
    print("- LOOPS_ROOT unset — cannot read sibling sets (report as gap).")
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
    print("- CONFIGURED caps (scheduler/config.json ads block, not readable via GC — "
          "cited as configured intent): monthly $1,600 total, google $500. Generalissimo's "
          "standing SOFT target: keep REAL spend under ~$1,000/mo.")
    print("- NOTE: the budget GUARD binds on COMMITTED basis FIRST (un-multiplied "
          "monthly cap) before any actual gate — X's paper overcommit + reddit fill "
          "the backstop, so positive-amount orders can be refused with real headroom "
          "(intl 2026-07-17 lesson). Zero/negative orders (kills, pauses) always pass. "
          "Any positive-spend recommendation MUST state committed-vs-actual basis "
          "and whether the guard will refuse it.")
else:
    print("- campaigns MISSING — no headroom read.")
print()

# ---- Journal tail (google only) ----
print("## Journal tail (ALL networks, newest first)")
if isinstance(jrnl, dict):
    rows = [r for r in (jrnl.get("rows") or [])]
    for r in rows[:16]:
        print(f"- #{r.get('id')} {r.get('created_at')} [{r.get('network')}] {r.get('action')} "
              f"ext={r.get('external_id')} amt=${r.get('amount_usd')} "
              f"[{r.get('status')}] {(r.get('detail') or '')[:90]}")
    if not rows:
        print("- no journal rows in the last 60.")
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
#       AND every prior contract's finding ids. A run that emitted ADP-01..04
#       into its contract but failed to persist a set (real: run 58e835) still
#       burned those ids — the next run must NOT hand them to new actions.
# Conflating them is what let run aba304 restart at ADP-01 and silently reuse
# four live ids.
ID_RE = re.compile(r"^ADP-(?:[A-Z]+-)?(\d{2,})$")
HEAD_RE = re.compile(r"^##\s+(?:~~)?\s*(ADP-(?:[A-Z]+-)?\d+)\b")
LOOP_PREFIX = "ads-program:"

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
        if not isinstance(ctx, dict) or ctx.get("loop") != "ads-program":
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
        if "ads-program" not in run_name:
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
next_id = f"ADP-<SRC>-{high_water + 1:02d}"

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
    print("- Do NOT restart at ADP-01 — those ids are live/spent. Carry nothing")
    print("  forward (no set to read), and number every new action from the")
    print("  high-water mark below. Raise the gap itself as an action.")
elif skipped:
    print("- NO usable prior set. Prior run dirs exist but none could be read (see")
    print("  SKIPPED below), so the high-water mark may UNDERSTATE what is spent.")
    print("  Treat the unreadable sets as an input gap and raise it as an action.")
else:
    print("- NO prior set and no prior ids — this is genuinely the first ads-program run.")
for run_name, why in skipped:
    print(f"- SKIPPED {run_name}: {why}")
if ghost_ids:
    print(f"- BURNED (emitted as findings, set never persisted): {sorted(ghost_ids)}")
print(f"- id high-water mark: {high_water}  →  next NEW action id = {next_id}")
print("- replace <SRC> with the source designator that raised the exception:")
print("  PRG program policy · BUD budget/caps · INP input gap.")
print("- prior legacy single-part ids (ADP-NN) are carried VERBATIM — never renamed.")
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
print(f"- inputs.missing: {INPUTS_MISSING}   (of 3 GC endpoints; 0 = all fetched)")
print(f"- sets.stale: {sets_stale}   (sibling sets older than {STALE_HOURS}h)")
print(f"- sets.missing: {sets_missing}   (sibling loops with no set at all)")
print(f"- scope.campaigns: {len(scope_campaigns)}   (non-retired campaigns across all networks)")
print("- actions.open / actions.struck: count them from the set YOU emit this run.")
print("- All metric values are NUMBERS, not strings.")
print()

# ---- What to do when the action set cannot be written ----
print("## If the action set cannot be written or fails validation")
print("- Emit the analysis anyway: full `report_markdown` + a `headline` saying so.")
print("- Set `status: alert` with a precise `status_reason`.")
print("- Emit **ZERO findings** — `findings: []`. No ADP- id may enter the findings")
print("  list without a durable set behind it, or it becomes an un-openable brief.")
print("- This is deliberate (settled 2026-07-28) and it is also what makes the")
print("  alert surface: per INTERFACES.md §4.5, a non-empty findings array overrides")
print("  the declared status with the findings' max severity, so an `alert` run with")
print("  only `warn` findings displays amber. Empty findings ⇒ declared status stands.")
print()
print("## END digest")
PY
