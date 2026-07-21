#!/usr/bin/env bash
# examples/hello-watchdog/precheck.sh — type=watchdog: THIS SCRIPT IS THE
# JOB (docs/INTERFACES.md §4.1). Probes the single URL configured in
# target.txt (first non-comment, non-blank line) with `curl --max-time 5`.
# Exit 0 = probe healthy (silent-green, no engine invocation). Non-zero
# exit or failure-shaped output = escalate to the diagnosis engine.
#
# The runner cd's into this loop dir before exec'ing this script (§4.1), so
# "target.txt" below is always relative to examples/hello-watchdog/.
set -uo pipefail   # deliberately NOT -e: we classify curl's exit ourselves

TARGET_FILE="target.txt"

if [ ! -f "$TARGET_FILE" ]; then
  echo "target: MISSING (no target.txt in loop dir)"
  exit 1
fi

url=""
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|'#'*) continue ;;
    *) url="$line"; break ;;
  esac
done < "$TARGET_FILE"

if [ -z "$url" ]; then
  echo "target: NO URL configured (target.txt has no non-comment line)"
  exit 1
fi

# --fail: HTTP >=400 becomes a curl failure (nonzero exit), so exit code
# alone is the pass/fail signal regardless of scheme (http(s):// vs
# file://). --max-time 5 bounds worst-case wall time.
curl_output="$(curl --max-time 5 --fail --silent --show-error \
  --output /dev/null --write-out 'http_code=%{http_code}' "$url" 2>&1)"
curl_exit=$?

echo "target: $url"
echo "curl_exit: $curl_exit"
echo "$curl_output"

if [ "$curl_exit" -eq 0 ]; then
  echo "result: OK"
  exit 0
else
  echo "result: FAIL"
  exit 1
fi
