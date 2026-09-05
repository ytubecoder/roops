#!/usr/bin/env bash
# gc-actions/render.sh — post-promotion hook (INTERFACES §4.1 step 6.5).
# Used here as the harness-trusted APPLY step of the approved Q2 design
# (spec: maguyva-marketing/docs/superpowers/specs/2026-07-25-dmp-actions-pipeline-design.md): it runs only for
# runs that completed AND validated AND were promoted, and LATEST_JSON has the
# runner's suppression already applied — so dismissed findings can never create
# tickets. This loop deliberately produces NO report page in v1: the page
# promotion gate will report "no page" in page-render.log, which is expected
# and harmless (SPEC.md §12).
set -euo pipefail
exec python3 "$(dirname "${BASH_SOURCE[0]}")/bin/apply_tickets.py"
