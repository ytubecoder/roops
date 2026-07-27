#!/usr/bin/env bash
# examples/hello-loop/precheck.sh — deterministic, zero-network gathering
# step (script->agent pattern, docs/INTERFACES.md §4.1/§6.2). Scans the
# fixture "world" (world/*.md, checked into this loop dir) and prints a
# compact per-file summary for the engine to interpret. Never touches
# anything outside this loop directory; never makes a network call.
#
# The runner cd's into this loop dir before exec'ing this script (§4.1), so
# "world" below is always relative to examples/hello-loop/.
set -euo pipefail

WORLD_DIR="world"

if [ ! -d "$WORLD_DIR" ]; then
  echo "world: MISSING (no $WORLD_DIR/ directory found)"
  exit 0
fi

shopt -s nullglob
files=("$WORLD_DIR"/*.md)
shopt -u nullglob

total=${#files[@]}
todo_count=0

echo "world files: $total"
for f in "${files[@]}"; do
  name="$(basename "$f")"
  if grep -q '^TODO:' "$f"; then
    todo_count=$((todo_count + 1))
    line="$(grep '^TODO:' "$f" | head -n1)"
    echo "- ${name}: TODO present — ${line}"
  else
    echo "- ${name}: clean"
  fi
done
echo "world.todo_files: $todo_count"
