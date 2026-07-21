#!/usr/bin/env bash
# tests/run-tests.sh — hermetic test runner for the loops harness core
# Python layer (§11). Runs the unittest suite plus any tests/test_*.sh
# shell fixtures. Non-zero exit on any failure. macOS-safe (no GNU-only
# flags).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

status=0

echo "== python3 -m unittest discover -s tests -p 'test_*.py' =="
if ! (cd "$REPO_ROOT" && python3 -m unittest discover -s tests -p 'test_*.py'); then
  status=1
fi

shopt -s nullglob
sh_tests=("$HERE"/test_*.sh)
shopt -u nullglob

if [ "${#sh_tests[@]}" -gt 0 ]; then
  for t in "${sh_tests[@]}"; do
    echo "== $t =="
    if ! bash "$t"; then
      echo "FAIL: $t"
      status=1
    fi
  done
fi

exit "$status"
