#!/usr/bin/env python3
"""emit_action_set.py — write the ads-intl action set into the run dir.

Reads ONE action-set payload from stdin and materializes it as
    <out-dir>/action-set/ACTIONS.md          (register, DMP shape)
    <out-dir>/action-set/actions/<ADI-NN>.md (one framing brief per action)
    <out-dir>/action-set/context.json        (loop / run / windows / freshness)
then runs validate_action_set.py over the result and exits non-zero if the set
is malformed.

WHY THIS SCRIPT EXISTS (documented deviation, see SPEC.md §7/§11): the harness
report-only floor gives the *model* no filesystem-write access under either
adapter — codex maps report_only to a read-only sandbox, and the claude adapter
floor exposes no Write tool (docs INTERFACES.md §7.2/§7.3). The only harness-
sanctioned way for the engine session to write the action-set files is an
allowlisted local command (perm_local_exec=allowlist). This script is that
command; it does no network and no remote mutation. The plan's "validator runs
inside the engine session via the allowlist" is honored — this script validates
as its final act, and the standalone validator is a second allowlist entry.

PAYLOAD FORMATS (auto-detected on the first non-space character):
  1. FLAT sectioned format (the ENGINE's format — REQUIRED in the engine
     session): Claude Code's Bash permission matcher classifies any command
     whose text combines a brace character with a quote as "too-complex" and
     hard-denies it BEFORE this script runs, with "Contains brace with quote
     character (expansion obfuscation)".

     Re-verified 2026-07-28 against claude 2.1.220 with `--permission-mode
     default` (probe pair, allowlist `Bash(python3 <script>:*)`):
       DENIED : python3 s.py <<'EOF' / {"id": "ADI-01", "t": "x"} / EOF
       ALLOWED: python3 s.py <<'ACTIONSET' / id: ADI-01 / title: … / ACTIONSET
     Quoted heredocs, double quotes, `[section]` brackets and `key: value`
     lines all pass; a brace anywhere in the command text does not. NOTE the
     denial is permission-layer, so it is invisible to this script — the check
     below only catches the case where the command was allowed anyway (e.g. an
     ambient permissive `defaultMode`). So the engine delivers a brace-free
     line format:

         loop: ads-intl
         run_id: <RUN_ID>
         engine: claude
         generated: 2026-07-27T03:00:00Z
         window.scoreboard: last 7 days
         freshness.fetched_at: 2026-07-27T02:58:00Z
         freshness.x_cache_age: n/a
         scope: g-msg campaigns 24017560784 24013344207
         scope: g-theme campaigns 24043161296 24043160774 24038115258

         [action]
         id: ADI-01
         title: ...one line...
         status: open
         outcome: ...one line...
         exception: ...observation with numbers + source...
         order.network: google
         order.verb: set_budget
         order.amount_usd: 1.5
         order.basis: committed vs actual note
         order.guard_note: will the guard refuse it
         placement: search campaign=24043161296 name=google-build-jul26
         placement: dg campaign=24038115258 ad_group=987 name=google-dg2-jul26
         resolution: ...what future evidence strikes this...
         source: scoreboard
         source: program_events 2026-07-21

     Repeatable keys: scope, placement, source. `struck_reason:` only when
     `status: struck`. Omit order.* entirely for a pure observation. Any
     brace character in the payload is REJECTED with a clear error.
  2. A JSON object (kept for tests/manual use; first non-space char is `{`).

Run dir resolution order: --out DIR | $OUT_DIR | $LOOPS_ROOT/state/runs/$RUN_ID.

Usage:
    python3 emit_action_set.py [--out <run-dir>] < action-set.payload
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_action_set  # noqa: E402


def _resolve_out_dir(argv: list[str]) -> Path:
    out = None
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    if not out:
        out = os.environ.get("OUT_DIR")
    if not out:
        root = os.environ.get("LOOPS_ROOT")
        run_id = os.environ.get("RUN_ID")
        if root and run_id:
            out = str(Path(root) / "state" / "runs" / run_id)
    if not out:
        sys.stderr.write(
            "cannot resolve run dir: pass --out, or set OUT_DIR, or LOOPS_ROOT+RUN_ID\n"
        )
        raise SystemExit(2)
    return Path(out)


def _stamp(generated: str) -> str:
    return f"> generated: {generated}\n"


def _order_block(order: dict) -> str:
    if not isinstance(order, dict) or not order:
        return "_No mutating order — observation / recommendation only._\n"
    lines = []
    network = order.get("network", "google")
    verb = order.get("verb", "(verb)")
    amount = order.get("amount_usd")
    amt = f"${amount}" if amount is not None else "(n/a)"
    lines.append(
        f"- **Suggested order (`record_and_apply` vocabulary — NOT applied):** "
        f"`{network}` / `{verb}` / {amt}"
    )
    basis = order.get("basis")
    if basis:
        lines.append(f"- **Spend basis:** {basis}")
    guard = order.get("guard_note")
    if guard:
        lines.append(f"- **Budget guard:** {guard}")
    placements = order.get("placements") or []
    if placements:
        lines.append("- **Placements (every leg's external ids):**")
        for pl in placements:
            leg = pl.get("leg", "?")
            camp = pl.get("campaign_external_id", "?")
            grp = pl.get("ad_group_external_id")
            grp_s = f", ad_group `{grp}`" if grp else ""
            name = pl.get("name")
            name_s = f" ({name})" if name else ""
            lines.append(f"    - {leg}: campaign `{camp}`{grp_s}{name_s}")
    else:
        lines.append(
            "    - _(no placement ids supplied — a real apply must resolve them "
            "before acting)_"
        )
    return "\n".join(lines) + "\n"


def _write_brief(briefs_dir: Path, action: dict, generated: str) -> None:
    aid = action["id"]
    title = action.get("title", "")
    status = "struck" if action.get("status") == "struck" else "open"
    body = []
    body.append(_stamp(generated))
    body.append("")
    body.append(f"# {aid} — {title}")
    body.append("")
    body.append(f"- **Status:** {status}")
    if action.get("outcome"):
        body.append(f"- **Outcome:** {action['outcome']}")
    srcs = action.get("sources") or []
    if srcs:
        body.append(f"- **Sources:** {', '.join(srcs)}")
    body.append("")
    body.append("## Exception observed")
    body.append(action.get("exception", "_(none recorded)_"))
    body.append("")
    body.append("## Suggested order")
    body.append(_order_block(action.get("suggested_order")))
    body.append("## Resolution evidence")
    body.append(
        action.get("resolution_evidence", "_(what future evidence strikes this)_")
    )
    body.append("")
    (briefs_dir / f"{aid}.md").write_text("\n".join(body))


def _write_register(set_dir: Path, data: dict, generated: str) -> list[str]:
    actions = data.get("actions") or []
    ids = [a["id"] for a in actions]
    open_n = sum(1 for a in actions if a.get("status") != "struck")
    struck_n = len(actions) - open_n
    lines = []
    lines.append(_stamp(generated).rstrip("\n"))
    lines.append("")
    lines.append(f"# Action register — ads-intl · run {data.get('run_id', '')}")
    lines.append("")
    lines.append(
        f"Generated {generated} by the ads-intl loop (report-only). "
        f"{len(actions)} actions ({open_n} open · {struck_n} struck). "
        "IDs are stable and NEVER reused after a strike. Read the per-action "
        "brief under `actions/`, not this index. These are suggested orders in "
        "`record_and_apply` vocabulary — nothing here has been applied."
    )
    lines.append("")
    for a in actions:
        aid = a["id"]
        title = a.get("title", "")
        if a.get("status") == "struck":
            lines.append(f"## ~~{aid} — {title}~~")
            if a.get("struck_reason"):
                lines.append(f"- **Struck:** {a['struck_reason']}")
        else:
            lines.append(f"## {aid} — {title}")
            if a.get("outcome"):
                lines.append(f"- **Outcome:** {a['outcome']}")
        lines.append(f"- [full brief](actions/{aid}.md)")
        lines.append("")
    (set_dir / "ACTIONS.md").write_text("\n".join(lines))
    return ids


def _write_context(set_dir: Path, data: dict, generated: str, ids: list[str]) -> None:
    actions = data.get("actions") or []
    ctx = {
        "loop": data.get("loop", "ads-intl"),
        "run_id": data.get("run_id", os.environ.get("RUN_ID", "")),
        "generated": generated,
        "engine": data.get("engine", "claude"),
        "data_windows": data.get("data_windows", {}),
        "input_freshness": data.get("input_freshness", {}),
        "scope_campaigns": data.get("scope_campaigns", []),
        "action_ids": ids,
        "open_count": sum(1 for a in actions if a.get("status") != "struck"),
        "struck_count": sum(1 for a in actions if a.get("status") == "struck"),
    }
    (set_dir / "context.json").write_text(json.dumps(ctx, indent=2) + "\n")


_HEADER_LIST_KEYS = {"scope"}
_ACTION_LIST_KEYS = {"placement", "source"}


def _parse_placement(rest: str) -> dict:
    """`<leg> campaign=<id> [ad_group=<id>] [name=<text...>]` — name may
    contain spaces, so it is parsed last and greedily."""
    pl: dict = {}
    parts = rest.split()
    if parts and "=" not in parts[0]:
        pl["leg"] = parts[0]
        parts = parts[1:]
    rest2 = " ".join(parts)
    # name= is greedy to end of line
    if " name=" in f" {rest2}":
        idx = f" {rest2}".index(" name=")
        pl["name"] = f" {rest2}"[idx + len(" name="):].strip()
        rest2 = f" {rest2}"[:idx].strip()
    for tok in rest2.split():
        if tok.startswith("campaign="):
            pl["campaign_external_id"] = tok[len("campaign="):]
        elif tok.startswith("ad_group="):
            pl["ad_group_external_id"] = tok[len("ad_group="):]
    return pl


def parse_flat(text: str) -> dict:
    """Parse the brace-free sectioned format into the internal dict shape."""
    data: dict = {"data_windows": {}, "input_freshness": {}, "scope_campaigns": [],
                  "actions": []}
    current: dict | None = None  # None = header section
    for rawline in text.splitlines():
        line = rawline.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[action]":
            current = {}
            data["actions"].append(current)
            continue
        if ":" not in line:
            raise ValueError(f"unparseable line (no colon): {line!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if current is None:
            if key.startswith("window."):
                data["data_windows"][key[len("window."):]] = val
            elif key.startswith("freshness."):
                data["input_freshness"][key[len("freshness."):]] = val
            elif key in _HEADER_LIST_KEYS:
                data["scope_campaigns"].append(val)
            else:
                data[key] = val
        else:
            if key.startswith("order."):
                order = current.setdefault("suggested_order", {})
                okey = key[len("order."):]
                if okey == "amount_usd":
                    try:
                        order[okey] = float(val)
                    except ValueError:
                        order[okey] = val
                else:
                    order[okey] = val
            elif key == "placement":
                order = current.setdefault("suggested_order", {})
                order.setdefault("placements", []).append(_parse_placement(val))
            elif key == "source":
                current.setdefault("sources", []).append(val)
            elif key == "resolution":
                current["resolution_evidence"] = val
            elif key in _ACTION_LIST_KEYS:
                current.setdefault(key + "s", []).append(val)
            else:
                current[key] = val
    return data


def main(argv: list[str]) -> int:
    out_dir = _resolve_out_dir(argv)
    raw = sys.stdin.read()
    stripped = raw.lstrip()
    if stripped.startswith("{"):
        # JSON path (tests / manual use only — the engine session can never
        # deliver braces past the Bash permission matcher).
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            sys.stderr.write(f"stdin is not valid JSON: {exc}\n")
            return 2
        if not isinstance(data, dict):
            sys.stderr.write("stdin JSON must be an object\n")
            return 2
    else:
        if "{" in raw or "}" in raw:
            sys.stderr.write(
                "flat payload must not contain brace characters — the Bash "
                "permission matcher denies any command combining a brace with "
                "a quote. Remove the brace and re-send the SAME plain command "
                "(for 'no order', omit the order.* and placement: lines "
                "entirely). Do NOT work around this with tr/sed/base64, hex or "
                "octal escapes, $'...' strings, pipes, process substitution or "
                "output redirection.\n"
            )
            return 2
        try:
            data = parse_flat(raw)
        except ValueError as exc:
            sys.stderr.write(f"flat payload parse error: {exc}\n")
            return 2
    if not data.get("actions"):
        if str(data.get("empty_set", "")).lower() not in ("yes", "true", "1"):
            sys.stderr.write(
                "payload has no [action] sections — an empty set is only "
                "valid with an explicit `empty_set: yes` header line\n"
            )
            return 2
        data["actions"] = []

    # The stamp is WRITE-TIME, computed here — a model-supplied `generated:` is
    # accepted in the payload but deliberately ignored (review 2026-07-28: model
    # clocks drift/round; the stamp must be when the set actually hit disk).
    # GENERATED_TS env override exists for tests only.
    generated = os.environ.get("GENERATED_TS")
    if not generated:
        from datetime import datetime, timezone

        generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for a in data.get("actions") or []:
        if "id" not in a:
            sys.stderr.write("every action needs an id\n")
            return 2
        if a.get("status") == "struck" and not str(a.get("struck_reason") or "").strip():
            sys.stderr.write(
                f"{a['id']}: status is struck but struck_reason is missing — "
                "every strike must say why (add a struck_reason: line)\n"
            )
            return 2

    set_dir = out_dir / "action-set"
    briefs_dir = set_dir / "actions"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    ids = _write_register(set_dir, data, generated)
    for a in data.get("actions") or []:
        _write_brief(briefs_dir, a, generated)
    _write_context(set_dir, data, generated, ids)

    # Auto-discover precheck.sh's continuity.json (run-dir root) so the ID
    # continuity guard runs in the AUTOMATIC flow, not only when a human
    # remembers --continuity. A missing/unparseable file degrades to the
    # non-continuity checks (defence in depth — same policy as the validator
    # CLI), but it is warned about loudly.
    continuity = None
    cont_path = out_dir / "continuity.json"
    if cont_path.is_file():
        try:
            continuity = json.loads(cont_path.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            sys.stderr.write(
                f"warning: {cont_path} not parseable ({exc}) — continuity "
                "check skipped\n"
            )
    else:
        sys.stderr.write(
            f"warning: no continuity.json at {cont_path} — ID continuity not "
            "verified this run\n"
        )

    errors = validate_action_set.validate(set_dir, continuity)
    if errors:
        for e in errors:
            sys.stderr.write(e + "\n")
        sys.stderr.write(
            "action set FAILED validation — fix the payload and re-emit; if it "
            "still cannot be written, declare contract status=alert with "
            "status_reason=action_set_invalid and ZERO findings (see the "
            "validator REMEDY: no ADI- id enters findings without a durable "
            "set behind it)\n"
        )
        return 1

    sys.stdout.write(
        f"OK wrote + validated action set: {set_dir} "
        f"({len(ids)} actions"
        f"{'; continuity verified' if continuity else '; continuity NOT verified'}). "
        "Include action_set.written: 1 in your contract metrics.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
