#!/usr/bin/env bash
# tailnet-zones precheck — trusted deterministic gathering (script→agent pattern).
# Resolves the policy source (live API read when a read-only credential exists,
# tailnet-setup repo snapshot otherwise), builds the full page model, computes
# every count and finding_id HERE (model-emitted metrics get believed — house
# gotcha), and stages the normalized-policy baseline for the post-promotion
# commit. The engine only interprets the digest this script prints.
set -euo pipefail

TAILNET_SETUP="${TAILNET_SETUP_DIR:-$HOME/projects/tailnet-setup}"
SNAPSHOT="$TAILNET_SETUP/docs/policy-live.hujson"
META="$TAILNET_SETUP/site/zones-meta.json"
TOKEN_FILE="${TS_POLICY_READ_TOKEN_FILE:-$HOME/.config/tailscale-policy-read.token}"
POLICY_FILE="$OUT_DIR/policy.hujson"

if [ ! -r "$META" ]; then
  echo "ERROR: zones-meta.json not readable at $META" >&2
  exit 1
fi
if [ ! -r "$SNAPSHOT" ]; then
  echo "ERROR: policy snapshot not readable at $SNAPSHOT" >&2
  exit 1
fi

# Source resolution. The credential is optional and read-scoped by policy
# (see SPEC §7); its VALUE must never reach stdout — only which source won.
SOURCE=snapshot
FETCH_ERROR=""
if [ -r "$TOKEN_FILE" ]; then
  TOK="$(cat "$TOKEN_FILE")"
  HTTP="$(curl -s --connect-timeout 10 --max-time 30 -w '%{http_code}' \
    -o "$POLICY_FILE" -u "$TOK:" -H 'Accept: application/hujson' \
    https://api.tailscale.com/api/v2/tailnet/-/acl || echo curl-failed)"
  if [ "$HTTP" = "200" ] && [ -s "$POLICY_FILE" ]; then
    SOURCE=live
  else
    FETCH_ERROR="credential present but GET /acl gave: $HTTP"
    cp "$SNAPSHOT" "$POLICY_FILE"
  fi
else
  cp "$SNAPSHOT" "$POLICY_FILE"
fi

mkdir -p "$OUT_DIR/loop-data.commit"

exec python3 "$LOOPS_ROOT/loops.d/tailnet-zones/build_model.py" \
  --policy "$POLICY_FILE" \
  --snapshot "$SNAPSHOT" \
  --meta "$META" \
  --source "$SOURCE" \
  --fetch-error "$FETCH_ERROR" \
  --prev "$LOOPS_ROOT/state/loop-data/tailnet-zones/policy-prev.json" \
  --out-model "$OUT_DIR/zones-model.json" \
  --commit-prev "$OUT_DIR/loop-data.commit/policy-prev.json"
