#!/usr/bin/env bash
# vecomap-unmapped/precheck.sh — type=agent: THIS SCRIPT IS THE JOB
# (docs/INTERFACES.md §4.1). Exit 0 = silent-green (nothing traced, nothing
# superseded/aging). Non-zero = escalate to the diagnosis engine.
#
# All git / API / tracer work lives here (script→agent pattern, kagami
# precedent). The engine that follows is at the report-only floor and only
# interprets $OUT_DIR/summary.json as printed on stdout.
#
# Deliberate remote mutation: git push of tracer/<YYYYMMDD-HHMM> to
# ytubecoder/vecomap, justified in SPEC.md §7. Never opens/merges PRs,
# never touches main, never changes remotes.
set -euo pipefail

: "${LOOPS_ROOT:?LOOPS_ROOT required}"
: "${OUT_DIR:?OUT_DIR required}"

HERE="$(cd "$(dirname "$0")" && pwd)"
export VECOMAP_ROOT="${VECOMAP_ROOT:-$HOME/projects/vecomap}"
export TRACER_PY="${TRACER_PY:-$HOME/.cache/vecomap-tracer/venv/bin/python}"
export TRACER_CACHE="${TRACER_CACHE:-$HOME/.cache/vecomap-tracer}"
export TRACER_DIR="${VECOMAP_ROOT}/tools/area-tracer"
export GIT_TERMINAL_PROMPT=0

exec python3 "$HERE/run.py"
