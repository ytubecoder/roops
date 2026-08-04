#!/usr/bin/env bash
# ads-x/precheck.sh — the pre-engine FETCH + deterministic digest stage
# (script->agent pattern, docs INTERFACES.md §4.1/§6.2). This script is a plain
# unsandboxed runner-invoked script — it is NOT governed by the engine's
# perm_network axis (§7.3 note), so it is where all network I/O for this loop
# lives. It:
#   1. curls the LOCAL Growth Console read-only ads JSON surface into the run
#      dir ($OUT_DIR/inputs/*.json) — keeps every umami/ads read behind GC's
#      single rate limiter; no Google/Reddit credentials in this loop.
#   2. derives loop SCOPE from the experiments registry at RUN TIME (never
#      hardcodes campaign ids) — cards with an X leg (x-boost today, plus
#      g-theme's pending x-take3 bring-up), excluding retired.
#   NOTE: X has NO ads API. All X metrics are the LAST Ads Manager snapshot in
#   x_cache; this loop labels the snapshot age (read-only sqlite peek) and when
#   it exceeds ~3 days the FIRST action must be the manual scrape/CSV import.
#   This loop NEVER touches CDP, the OpenTwins Chrome, or the browser lease.
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

print("# ads-x — precheck digest")
print(f"fetched_at: {FETCHED_AT}  (source: local Growth Console JSON surface)")
print(f"inputs: scoreboard={'ok' if sb else 'MISSING'} "
      f"campaigns={'ok' if camp else 'MISSING'} "
      f"journal={'ok' if jrnl else 'MISSING'} "
      f"program_events={'ok' if prog else 'MISSING'}")
# ---- X snapshot age (read-only sqlite peek at GC's ads.db x_cache) ----
snapshot_age_days = None
snapshot_at = None
try:
    import sqlite3
    _db = Path.home() / ".growth-console" / "ads.db"
    if _db.is_file():
        _con = sqlite3.connect(f"file:{_db}?mode=ro", uri=True, timeout=5)
        _row = _con.execute("SELECT MAX(imported_at), source FROM x_cache").fetchone()
        _con.close()
        if _row and _row[0]:
            snapshot_at = _row[0]
            _dt = datetime.fromisoformat(str(_row[0]).replace("Z", "+00:00"))
            snapshot_age_days = round((datetime.now(timezone.utc) - _dt).total_seconds() / 86400, 1)
except Exception:
    pass
if snapshot_age_days is None:
    print("x_cache_age: UNKNOWN — could not read x_cache (treat as stale; first action = manual X scrape/CSV import)")
else:
    _stale = "STALE (>3d) — FIRST action of this set MUST be: run the manual X scrape/CSV import" if snapshot_age_days > 3 else "fresh enough"
    print(f"x_cache_age: {snapshot_age_days} days (last import {snapshot_at}; {_stale})")
print("ALL X metrics below are a SNAPSHOT as of that import — never live. Writes are human checklists only.")
print()

# ---- Monthly spend ledger (decoded from x_cache TOTAL REMAINING) ----
# The Ads Manager SPEND column is the UI date-picker window, NOT lifetime:
# groups that exhausted their total caps before the window report "—" there and
# vanish from the window total — this is the budget guard's documented
# undercount (~$121 on 2026-07-25: window $515.97 vs true $636.80). True
# lifetime per group = TOTAL BUDGET − TOTAL REMAINING, read positionally from
# raw_json cells (cells[-3] = TOTAL BUDGET, cells[-1] = TOTAL REMAINING;
# `header` is off-by-one vs `cells` — never zip them; store.py indexes
# positionally and is correct).
def _decode_batch(con, batch_id):
    life = window = headroom = 0.0
    rows_n = bad = at_cap = 0
    for spend, raw in con.execute(
            "SELECT spend_usd, raw_json FROM x_cache WHERE batch_id=?", (batch_id,)):
        rows_n += 1
        window += spend or 0.0
        try:
            cells = json.loads(raw)["cells"]
            rem = float(cells[-1].split("\n")[0].replace("$", "").replace(",", ""))
            bud = float(cells[-3].replace("$", "").replace(",", ""))
        except Exception:
            bad += 1
            continue
        life += bud - rem
        if rem <= 0.01:
            at_cap += 1
        elif "Active" in str(cells[3] or ""):
            headroom += rem
    return {"life": life, "window": window, "rows": rows_n, "bad": bad,
            "at_cap": at_cap, "headroom": headroom}

print("## Monthly spend ledger (decoded from x_cache — snapshot-bounded, never live)")
try:
    import sqlite3
    _db = Path.home() / ".growth-console" / "ads.db"
    _con = sqlite3.connect(f"file:{_db}?mode=ro", uri=True, timeout=5)
    _batches = _con.execute(
        "SELECT batch_id, MAX(imported_at) AS ia FROM x_cache "
        "GROUP BY batch_id ORDER BY ia").fetchall()
    decoded = []
    for _bid, _ia in _batches:
        _d = _decode_batch(_con, _bid)
        if _d["rows"] - _d["bad"] > 0:
            decoded.append((_ia, _bid, _d))
    _con.close()
    if not decoded:
        print("- x_cache has no decodable batches — TRUE spend unreadable (input gap).")
    else:
        _ia, _bid, _d = decoded[-1]
        _under = _d["life"] - _d["window"]
        print(f"- TRUE lifetime spend ${_d['life']:.2f} as of the latest import {_ia} "
              f"(batch {_bid[:8]}, {_d['rows']} rows{', ' + str(_d['bad']) + ' undecodable' if _d['bad'] else ''}).")
        print(f"  Window column summed ${_d['window']:.2f} → guard/window undercount ${_under:.2f}. "
              f"NEVER quote the window number as spend.")
        print(f"- {_d['at_cap']} groups at their total caps; ${_d['headroom']:.2f} armed headroom "
              f"on still-active groups (hard ceiling for any future spend without a human raising caps).")
        if len(decoded) >= 2:
            _pia, _, _pd = decoded[-2]
            _t0 = datetime.fromisoformat(str(_pia).replace("Z", "+00:00"))
            _t1 = datetime.fromisoformat(str(_ia).replace("Z", "+00:00"))
            _dd = (_t1 - _t0).total_seconds() / 86400
            if _dd > 0.2 and _pd["rows"] >= _d["rows"] - 5:
                print(f"- serving rate between the last two imports ({_pia} → {_ia}): "
                      f"${max(_d['life'] - _pd['life'], 0.0) / _dd:.2f}/day over {_dd:.1f}d.")
        _now = datetime.now(timezone.utc)
        _month_start = _now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        _pre = [t for t in decoded
                if datetime.fromisoformat(str(t[0]).replace("Z", "+00:00")) < _month_start]
        _latest_dt = datetime.fromisoformat(str(_ia).replace("Z", "+00:00"))
        if _latest_dt >= _month_start:
            if _pre:
                _pre_ia, _, _pre_d = _pre[-1]
                print(f"- month attribution (UTC): lifetime was ${_pre_d['life']:.2f} at the newest "
                      f"pre-month import ({_pre_ia}) → this-month-to-date ≈ "
                      f"${max(_d['life'] - _pre_d['life'], 0.0):.2f} (snapshot-bounded: the pre-month "
                      f"import may predate the month boundary by days — say so).")
            else:
                print("- month attribution: no pre-month import exists — all decoded lifetime "
                      "falls in the current month as far as the cache can tell.")
        else:
            _tail = " (campaign may still have served its remaining armed headroom after that import)" \
                if _d["headroom"] > 0.5 else ""
            print(f"- month attribution (UTC): the latest import predates this month — "
                  f"month-to-date spend is UNKNOWN from cache; last-known lifetime "
                  f"${_d['life']:.2f} at {_ia}{_tail}. Prior-month total is snapshot-bounded "
                  f"between ${_d['life']:.2f} and ${_d['life'] + _d['headroom']:.2f}.")
        print("- CAVEATS: the ad-groups table virtualizes (~25 of 28 rows; absent rows understate "
              "lifetime); every figure is as-of an import date, not calendar-exact.")
except Exception as exc:
    print(f"- ledger unreadable ({type(exc).__name__}: {exc}) — treat as input gap.")
print()

# ---- X account signal (read-only peek at the OT twitter agent's memory) ----
# The engagement agent records login walls / account locks in its daily memory
# files. An account lock halts ad serving, makes the manual scrape impossible,
# and downs engagement — so it is a delivery-critical signal for this loop.
# This is a plain substring scan of local files; it never touches the browser.
print("## X account signal (from OpenTwins twitter agent memory — read-only file peek)")
try:
    _memdir = Path.home() / ".opentwins" / "workspaces" / "agent-twitter" / "memory"
    _files = sorted(_memdir.glob("20??-??-??.md"))[-3:]
    _hits = []
    for _f in _files:
        _text = _f.read_text(errors="replace").lower()
        if "account has been locked" in _text or "account/access" in _text:
            _hits.append(_f.name)
    if _hits:
        print(f"- 🚨 lock/access-wall markers present in: {', '.join(_hits)} (newest file matters most).")
        print("  If the NEWEST file still shows the lock: the @maguyvaai account is locked — ads do")
        print("  not serve, the manual scrape/CSV import is impossible, engagement is down. Unlock")
        print("  is HUMAN-ONLY (email verification in a real browser). Raise ONE alert-severity")
        print("  action for this; a stale-snapshot action is subordinate to it while locked")
        print("  (the import cannot be run until the account is unlocked).")
    elif _files:
        print(f"- no lock/access-wall markers in the last {len(_files)} OT twitter memory files.")
    else:
        print("- OT twitter memory dir empty/absent — no account signal this run.")
except Exception as exc:
    print(f"- OT memory unreadable ({type(exc).__name__}) — no account signal this run.")
print()

# ---- SCOPE from the experiments registry (cards with an X leg) ----
scope_variants, scope_campaigns, scope_cards = set(), {}, []
pending_bringups = []
if isinstance(camp, dict):
    for c in camp.get("cards", []):
        key = c.get("key", "")
        if c.get("status") == "retired":
            continue
        google_leg = None
        for leg in c.get("legs", []):
            if leg.get("network") == "x":
                google_leg = leg
                break
        if not google_leg:
            continue
        for pend in (google_leg.get("pending") or []):
            pending_bringups.append((key, pend.get("name"), pend.get("note", "")))
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
for _k, _n, _note in pending_bringups:
    print(f"- PENDING bring-up on card {_k}: {_n} — {_note[:140]} (no ids yet; observe-only)")
print("- EXCLUDED by design: google (ads-google/ads-intl), reddit (ads-reddit), retired.")
print()

# ---- Per-variant metrics (in-scope google rows) ----
print("## In-scope variant metrics (scoreboard)")
EVAL_IMPR_GATE = 2000
if isinstance(sb, dict):
    grows = ((sb.get("networks") or {}).get("x") or {}).get("rows") or []
    shown = 0
    for r in grows:
        vid = r.get("variant_id")
        if vid not in scope_variants:
            continue
        shown += 1
        ev = r.get("evaluator") or {}
        verdict = ev.get("action") or ev.get("verdict") or ev.get("status") or "-"
        impr = r.get("impressions") or 0
        gate = "EVAL" if impr >= EVAL_IMPR_GATE else "sub-gate"
        pls = r.get("placements") or []
        ext = ";".join(str(p.get("external_id") or "") for p in pls) or r.get("external_id") or ""
        # compact one-liner: 28+ x variants would bloat the digest at 3 lines each
        print(f"- {vid} [{r.get('angle')}] st={r.get('status')} v={verdict} impr={impr} "
              f"clk={r.get('clicks')} ctr={r.get('ctr')} spend=${r.get('spend_usd')} "
              f"cpc={r.get('cpc_usd')} lv={r.get('landing_views')} ({gate}) grp={ext}")
    if shown == 0:
        print("- no in-scope x variant rows found in scoreboard.")
    print(f"- scoreboard window label: last {sb.get('days')} days — but X values are the SNAPSHOT, not the window")
else:
    print("- scoreboard MISSING — cannot read per-variant metrics (input gap).")
print()

# ---- Budget headroom ----
print("## Budget headroom / spend basis")
_b = (sb.get("budget") or {}) if isinstance(sb, dict) else {}
_caps = _b.get("network_caps_usd") or {}
if _caps:
    _com = _b.get("committed_this_month_usd") or {}
    _act = _b.get("actual_spend_usd") or {}
    print(f"- LIVE budget (scoreboard): monthly cap ${(_b.get('monthly_cap_usd') or 0):.0f}; per-network "
          "committed / actual-MTD / cap: "
          + " · ".join(f"{n} ${(_com.get(n) or 0):.0f} / ${(_act.get(n) or 0):.2f} / ${(_caps.get(n) or 0):.0f}"
                       for n in sorted(_caps)))
else:
    print("- scoreboard budget block missing — committed/actual MTD not readable this run (input gap).")
if isinstance(camp, dict):
    tot = camp.get("totals") or {}
    print(f"- program total spend (all networks): ${tot.get('spend_usd')} "
          f"across {tot.get('campaigns')} campaigns / {tot.get('experiments')} experiments")
    cpa = camp.get("cpa") or {}
    print(f"- CPA readable={cpa.get('readable')} conversions_sitewide={cpa.get('conversions_sitewide')} "
          f"intent_sitewide={cpa.get('intent_sitewide')} (event={cpa.get('conversion_event')})")
    print("- NOTE: the budget GUARD binds on COMMITTED basis first (monthly_cap); "
          "X committed is a PAPER overcommit by design (group caps x 28). "
          "x-boost is coasting to auto-stop under its $30/group caps — Generalissimo's "
          "standing call: do NOT raise those caps. X writes are human "
          "checklists (journaled manual_pending), never API calls.")
else:
    print("- campaigns MISSING — no headroom read.")
print()

# ---- Journal tail (google only) ----
print("## Journal tail (x rows, newest first — incl. manual_pending checklists)")
if isinstance(jrnl, dict):
    rows = [r for r in (jrnl.get("rows") or []) if r.get("network") == "x"]
    for r in rows[:14]:
        print(f"- #{r.get('id')} {r.get('created_at')} {r.get('action')} "
              f"ext={r.get('external_id')} amt=${r.get('amount_usd')} "
              f"[{r.get('status')}] {(r.get('detail') or '')[:90]}")
    if not rows:
        print("- no x journal rows in the last 60.")
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
#       AND every prior contract's finding ids. A run that emitted ADX-01..04
#       into its contract but failed to persist a set (real: run 58e835) still
#       burned those ids — the next run must NOT hand them to new actions.
# Conflating them is what let run aba304 restart at ADX-01 and silently reuse
# four live ids.
ID_RE = re.compile(r"^ADX-(?:[A-Z]+-)?(\d{2,})$")
HEAD_RE = re.compile(r"^##\s+(?:~~)?\s*(ADX-(?:[A-Z]+-)?\d+)\b")
LOOP_PREFIX = "ads-x:"

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
        if not isinstance(ctx, dict) or ctx.get("loop") != "ads-x":
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
        if "ads-x" not in run_name:
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
next_id = f"ADX-<SRC>-{high_water + 1:02d}"

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
    print("- Do NOT restart at ADX-01 — those ids are live/spent. Carry nothing")
    print("  forward (no set to read), and number every new action from the")
    print("  high-water mark below. Raise the gap itself as an action.")
elif skipped:
    print("- NO usable prior set. Prior run dirs exist but none could be read (see")
    print("  SKIPPED below), so the high-water mark may UNDERSTATE what is spent.")
    print("  Treat the unreadable sets as an input gap and raise it as an action.")
else:
    print("- NO prior set and no prior ids — this is genuinely the first ads-x run.")
for run_name, why in skipped:
    print(f"- SKIPPED {run_name}: {why}")
if ghost_ids:
    print(f"- BURNED (emitted as findings, set never persisted): {sorted(ghost_ids)}")
print(f"- id high-water mark: {high_water}  →  next NEW action id = {next_id}")
print("- replace <SRC> with the source designator that raised the exception:")
print("  EV evaluator · CMP campaign/delivery · JRN journal/guard · BUD budget/caps · INP input gap.")
print("- prior legacy single-part ids (ADX-NN) are carried VERBATIM — never renamed.")
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
print("- Emit **ZERO findings** — `findings: []`. No ADX- id may enter the findings")
print("  list without a durable set behind it, or it becomes an un-openable brief.")
print("- This is deliberate (settled 2026-07-28) and it is also what makes the")
print("  alert surface: per INTERFACES.md §4.5, a non-empty findings array overrides")
print("  the declared status with the findings' max severity, so an `alert` run with")
print("  only `warn` findings displays amber. Empty findings ⇒ declared status stands.")
print()
print("## END digest")
PY
