#!/usr/bin/env bash
# competitor-watch/precheck.sh — deterministic gathering step (script->agent
# pattern, docs/INTERFACES.md §4.1/§6.2). Fetches raw signal for all six
# watches from gc-actions/specs/B-27.md's design (watch 5, ref.tools — Ref Plans depth
# creep, added after that design's original build — see SPEC.md); the engine
# only interprets the digest below, never fetches anything itself
# (perm_network=none for the engine — this script is unsandboxed and does
# ALL the fetching).
#
# Cross-run diffing: this loop keeps its OWN persistent cache under
# cache/ (relative to this loop's own directory, which the runner cd's into
# before exec — see hello-loop/precheck.sh's convention). There is no
# documented "find the previous run's OUT_DIR" mechanism in this harness, so
# a self-contained cache is the simplest correct approach: fetch fresh, diff
# against the cached copy, then overwrite the cache with the fresh copy.
#
# DEVIATION from gc-actions/deliverables/B-27/loop-design.md's literal wording
# (documented here per that ticket's own § "Backlink dependency... declared,
# and inert" precedent of naming deviations explicitly):
#   Watch 4 was designed around "SERP spot-checks via the tavily API," but
#   `credential_env` is RESERVED and HARD-DISABLED in this harness's v1
#   (loopctl validate hard-fails any non-empty value — docs/INTERFACES.md
#   §5, rule 8: "real passthrough needs a launchd-env design; do not fake
#   it"). A launchd-installed precheck.sh has no supported way to receive
#   TAVILY_API_KEY or any other credential. Watch 4 below instead checks
#   augmentcode.com's and maguyva.ai's public sitemaps for URL slugs that
#   target the three contestable query phrases — a content-targeting proxy,
#   not verified live SERP position. This is weaker signal than real rank
#   data but requires zero credentials, matches "no paid API," and is
#   genuinely buildable under this harness today.
set -uo pipefail

INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"
mkdir -p "$INPUTS"
CACHE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cache"
mkdir -p "$CACHE"

UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
CURL="curl -sL -m 20 -A"
# GitHub API reads (watch 3 + 5b) go through gh_get: read-only public-metadata
# GETs under Generalissimo's own ytubecoder account when its gh login exists
# on this host (5,000/h), anonymous otherwise (60/h per IP — exhausted on
# 2026-10-15 when three competitor prechecks shared the address). The token
# is used inline and never printed.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
GH_TOKEN="$(gh auth token -u ytubecoder 2>/dev/null || true)"
# Hosts without that gh login (firstparty) carry the same read-only token in
# the harness .env as COMPETITOR_GH_TOKEN (exported into prechecks only).
[ -n "$GH_TOKEN" ] || GH_TOKEN="${COMPETITOR_GH_TOKEN:-}"
gh_get () {
  # $1 = url, $2 = output file
  if [ -n "$GH_TOKEN" ]; then
    curl -sL -m 20 -A "$UA" -H "Authorization: Bearer $GH_TOKEN" "$1" -o "$2" --fail
  else
    curl -sL -m 20 -A "$UA" "$1" -o "$2" --fail
  fi
}

# gh_field <file> <field>: one top-level field of a GitHub repo JSON; exit 1
# when the file is not a repo document. python3 only — jq is not on every loop
# host (firstparty has none; the 2026-10-27 move found that out).
gh_field () {
  python3 - "$1" "$2" <<'PYF'
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
    value = doc[sys.argv[2]]
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
print(value)
PYF
}

echo "# competitor-watch precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# Normalization v2 (2026-10-15). v1 only stripped <script>/<style> and CDN
# noise and cached the remaining HTML — on minified sites (cursor.com, the
# Mintlify docs at docs.ref.tools) that is ONE line of 60-230 KB, so a single
# changed line blew the 64 KiB precheck cap and the 2026-10-01 run lost
# watches 3-6 to the truncation marker ("report truncated before watches 3-6").
# v2 caches visible TEXT: tags become line breaks, entities are decoded, runs
# of whitespace collapse, blank lines drop. Diffs are then a few short lines
# of prose per real change, and each diff is additionally capped in bytes
# (DIFF_CAP) with an explicit truncation note. The full fresh copies still
# land in inputs/ for anyone who needs the raw page.
NORM_VERSION=2
DIFF_CAP=5000
normalize_html () {
  perl -0pe '
    s/<script\b[^>]*>.*?<\/script>//gis;
    s/<style\b[^>]*>.*?<\/style>//gis;
    s/<noscript\b[^>]*>.*?<\/noscript>//gis;
    s/<!--.*?-->//gs;
    s/<(title|meta)\b([^>]*)>/\n[$1$2]\n/gi;   # keep <title>/<meta> attrs: the only copy on JS-shell pages
    s/<[^>]+>/\n/g;
    s/&nbsp;/ /g; s/&amp;/&/g; s/&lt;/</g; s/&gt;/>/g; s/&quot;/"/g; s/&#39;/'"'"'/g; s/&#x27;/'"'"'/g;
    s/[?&]?id=[A-Za-z0-9._-]{20,}//g;
    s/cdn-cgi\/[^"'\'' \n]*//g;
    s/\br=.[a-f0-9]{16}.,t=.[A-Za-z0-9=]+./r=REDACTED,t=REDACTED/g;
    s/[ \t\r\f\x{00a0}]+/ /g;
    s/^ +| +$//mg;
    s/\n{2,}/\n/g;
  ' "$1" 2>/dev/null || cat "$1"
}

# One-time baseline reset when the normalization version changes: old-format
# cache files would diff against new-format text as "everything changed".
if [ ! -f "$CACHE/.norm-v$NORM_VERSION" ]; then
  echo "NOTE: cache normalization is now v$NORM_VERSION (text, not HTML) — all page baselines are"
  echo "re-established THIS run; no page diff below is a real change. Star counts are unaffected."
  rm -f "$CACHE"/*.txt
  touch "$CACHE/.norm-v$NORM_VERSION"
  echo
fi

fetch_and_diff () {
  # $1 = cache key (filename-safe), $2 = url, $3 = human label
  local key="$1" url="$2" label="$3"
  local cache_file="$CACHE/${key}.txt"
  local fresh_file="$INPUTS/${key}.html"
  local fresh_norm="$INPUTS/${key}.txt"
  if $CURL "$UA" "$url" -o "$fresh_file" --fail; then
    if [ -s "$fresh_file" ]; then
      echo "## $label — fetched OK ($(wc -c <"$fresh_file" | tr -d ' ') bytes raw, $(normalize_html "$fresh_file" | wc -c | tr -d ' ') bytes as text)"
      normalize_html "$fresh_file" > "$fresh_norm"
      if [ -f "$cache_file" ]; then
        if cmp -s "$cache_file" "$fresh_norm"; then
          echo "  no change vs cached copy (visible text, scripts/CDN noise stripped)"
        else
          local d; d="$(diff "$cache_file" "$fresh_norm" 2>/dev/null | grep -E '^[<>]' )"
          local dbytes=${#d}
          echo "  CHANGED vs cached copy — $(printf '%s\n' "$d" | grep -c '^<') line(s) removed, $(printf '%s\n' "$d" | grep -c '^>') added (< old, > new; first $DIFF_CAP of $dbytes bytes):"
          printf '%s\n' "$d" | head -c "$DIFF_CAP" | sed 's/^/  /'
          [ "$dbytes" -gt "$DIFF_CAP" ] && echo "  [diff truncated at $DIFF_CAP bytes — full text copies: inputs/${key}.txt vs previous cache]"
        fi
      else
        echo "  no cached copy from a prior run — establishing baseline this run, not a change"
      fi
      cp "$fresh_norm" "$cache_file"
      return 0
    else
      echo "## $label — fetch returned empty body (source-unavailable)"
      return 1
    fi
  else
    echo "## $label — fetch FAILED (source-unavailable): $url"
    return 1
  fi
}

# Watch 1: Sourcegraph/Amp self-serve re-entry
echo "### Watch 1: Sourcegraph self-serve re-entry"
fetch_and_diff "sourcegraph-pricing" "https://sourcegraph.com/pricing" "sourcegraph.com/pricing"
echo

# Watch 2: editor/CLI-native context becoming good-enough
echo "### Watch 2: editor/CLI-native persistent repo indexing"
# docs.claude.com/en/release-notes/claude-code now 301s to the GitHub blob view of
# anthropics/claude-code CHANGELOG.md (386 KB of HTML, intermittently refused —
# failed 1 of 3 supervised runs on 2026-10-15). The raw file is the same content
# as plain markdown, a tenth the size, and not rate-gated the same way.
fetch_and_diff "claude-code-changelog-md" "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md" "Claude Code changelog (raw CHANGELOG.md; docs.claude.com redirects here)"
fetch_and_diff "cursor-blog" "https://cursor.com/blog" "Cursor research blog"
echo

# Watch 3: OSS local-indexer commoditization
echo "### Watch 3: OSS local-indexer traction"
# The repo list comes from the competitor registry (every tracked/watch row
# with repos, minus ref-tools which watch 5b tracks under its own key) since
# 2026-10-15; the literal list below is only the fallback when the registry
# cannot be read, so this watch never goes silent on a config problem.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
REGISTRY="${MAGUYVA_REPO:-$HOME/projects/maguyva-marketing}/gc-actions/specs/inputs/competitor-registry.yaml"
OSS_REPOS="$(python3 - "$REGISTRY" 2>/dev/null <<'PY'
import sys, yaml
rows = yaml.safe_load(open(sys.argv[1], encoding="utf-8")).get("competitors") or []
out = []
for r in rows:
    if r.get("status") not in ("tracked", "watch") or str(r.get("slug", "")).startswith("ref-"):
        continue
    for repo in r.get("repos") or []:
        if isinstance(repo, str) and "/" in repo and repo not in out:
            out.append(repo)
print(" ".join(out))
PY
)"
if [ -z "$OSS_REPOS" ]; then
  echo "NOTE: competitor registry unreadable at $REGISTRY — using the built-in fallback repo list (registry:source-unavailable)."
  OSS_REPOS="DeusData/codebase-memory-mcp abhigyanpatwari/GitNexus colbymchenry/codegraph Pharaoh-so/pharaoh-mcp MagneticAnomaly/SourcePrep-MCP"
else
  echo "repo list from registry ($REGISTRY): $(echo "$OSS_REPOS" | wc -w | tr -d ' ') repos"
fi
for repo in $OSS_REPOS; do
  api_out="$INPUTS/gh-$(echo "$repo" | tr '/' '_').json"
  if gh_get "https://api.github.com/repos/$repo" "$api_out" 2>/dev/null && \
     stars=$(gh_field "$api_out" stargazers_count); then
    pushed=$(gh_field "$api_out" pushed_at || echo unknown)
    echo "- $repo: stars=$stars, last_pushed=$pushed"
    cache_file="$CACHE/gh-$(echo "$repo" | tr '/' '_').txt"
    prev="none"
    [ -f "$cache_file" ] && prev="$(cat "$cache_file")"
    echo "  previous cached star count: $prev"
    echo "$stars" > "$cache_file"
  else
    echo "- $repo: LOOKUP FAILED (source-unavailable) — GitHub API refused or rate-limited, or the slug changed; a single failure is not evidence about the project"
  fi
done
echo "NOTE: a repo with 0-2 stars (Pharaoh, SourcePrep) is a best-effort slug guess from B-27's design, not traction data."
echo

# Watch 4: Augment SEO encroachment (DEVIATION — see header: sitemap proxy, not live SERP, no credential available)
echo "### Watch 4: Augment SEO encroachment (proxy method — see precheck.sh header deviation note)"
QUERIES=("codebase-context-mcp" "give-claude-code-repo-context" "mcp-server-codebase-map")
for site in "augmentcode.com" "maguyva.ai"; do
  sm_out="$INPUTS/sitemap-${site}.xml"
  # -L: both sites 301-redirect a plain /sitemap.xml to their real location
  # (e.g. maguyva.ai -> /sitemap-index.xml) — without -L, --fail treats the
  # redirect body itself as "success" and we diff garbage.
  if $CURL "$UA" -L "https://$site/sitemap.xml" -o "$sm_out" --fail 2>/dev/null && [ -s "$sm_out" ]; then
    # A sitemap INDEX (lists <sitemap><loc> children, not page <loc>s) has no
    # page URLs to grep yet — fetch its first few child sitemaps and append.
    if grep -qi "<sitemapindex" "$sm_out"; then
      child_urls=$(grep -oE "<loc>[^<]*</loc>" "$sm_out" | sed -E 's#</?loc>##g' | head -3)
      while IFS= read -r child; do
        [ -n "$child" ] || continue
        $CURL "$UA" -L "$child" --fail 2>/dev/null >> "$sm_out" || true
      done <<< "$child_urls"
    fi
    url_count=$(grep -o '<loc>' "$sm_out" 2>/dev/null | wc -l | tr -d ' '); url_count=${url_count:-0}  # -o|wc: single-line sitemaps counted 1 with grep -c
    echo "- $site sitemap fetched ($url_count urls, incl. any expanded child sitemaps)"
    for q in "${QUERIES[@]}"; do
      hits=$(grep -ioE "<loc>[^<]*</loc>" "$sm_out" | grep -iE "$(echo "$q" | tr '-' '.')" || true)
      if [ -n "$hits" ]; then
        echo "  MATCH for '$q': $hits"
      else
        echo "  no sitemap URL matching '$q'"
      fi
    done
  else
    echo "- $site sitemap: FETCH FAILED (source-unavailable)"
  fi
done
echo

# Watch 5: ref.tools — both products under the one brand (2026-10-15: widened
# from "Ref Plans depth creep" only, after Generalissimo asked to monitor
# ref.tools as a whole).
#   5a Ref Plans (plan.ref.tools) currently does shallow codebase reading to
#      inform task generation (per its own docs: "researches your codebase,
#      reads relevant files... auth flows, DB models, API routes") — real but
#      shallow overlap, not the graph-based/dependency analysis Maguyva does.
#      Signal = its docs start claiming dependency-graph, call-graph,
#      blast-radius, or structural/AST-based analysis. "Reads more file types"
#      or "supports more languages" is noise, not signal (see prompt.md).
#   5b Ref Context (docs.ref.tools/context, OSS ref-tools/ref-tools-mcp) is an
#      MCP server for EXTERNAL library/API docs — adjacent to Maguyva, not
#      substitutable. Signal = its docs/changelog start offering indexing of
#      the user's OWN repository/codebase (private repo context), which would
#      be a direct category entry; or a star jump on the OSS repo.
#   The docs site is Mintlify and server-renders, so fetch_and_diff works.
#   ref.tools/pricing and the ref.tools homepage are a JS shell (2 KB, no
#   pricing text server-side) — only the homepage <title>/<meta description>
#   carry positioning copy, so the homepage is diffed for that alone and the
#   pricing page is deliberately NOT watched (it would never diff).
#   docs.ref.tools/llms.txt is the docs' own page index: a new page appearing
#   there is the cheapest "new capability shipped" signal across both products.
echo "### Watch 5: ref.tools (5a Ref Plans depth creep · 5b Ref Context codebase-indexing entry)"
echo "#### 5a Ref Plans"
fetch_and_diff "refplans-first-plan" "https://docs.ref.tools/plans/getting-started/your-first-plan" "docs.ref.tools/plans/getting-started/your-first-plan"
fetch_and_diff "refplans-intro" "https://docs.ref.tools/plans/getting-started/intro" "docs.ref.tools/plans/getting-started/intro (capability description; other easily-discoverable Ref Plans docs page via docs.ref.tools/sitemap.xml)"
echo "#### 5b Ref Context"
fetch_and_diff "refcontext-intro" "https://docs.ref.tools/context/getting-started/intro" "docs.ref.tools/context/getting-started/intro (what Ref Context says it does)"
fetch_and_diff "ref-changelog" "https://docs.ref.tools/changelog" "docs.ref.tools/changelog (monthly updates, both products)"
fetch_and_diff "ref-docs-index" "https://docs.ref.tools/llms.txt" "docs.ref.tools/llms.txt (docs page index — a new page = new capability)"
fetch_and_diff "ref-home" "https://ref.tools/" "ref.tools homepage (JS shell; only <title>/<meta description> positioning copy is diffable)"
repo="ref-tools/ref-tools-mcp"
api_out="$INPUTS/gh-$(echo "$repo" | tr '/' '_').json"
if gh_get "https://api.github.com/repos/$repo" "$api_out" 2>/dev/null && \
   stars=$(gh_field "$api_out" stargazers_count); then
  pushed=$(gh_field "$api_out" pushed_at || echo unknown)
  echo "- $repo (Ref Context OSS MCP server): stars=$stars, last_pushed=$pushed"
  cache_file="$CACHE/gh-$(echo "$repo" | tr '/' '_').txt"
  prev="none"
  [ -f "$cache_file" ] && prev="$(cat "$cache_file")"
  echo "  previous cached star count: $prev"
  echo "$stars" > "$cache_file"
else
  echo "- $repo: LOOKUP FAILED (source-unavailable)"
fi
echo

# Watch 6: backlink-gap — dependent on B-23 (GSC Links export)
echo "### Watch 6: backlink-gap (dependent watch)"
BACKLOG="/Users/llm/projects/maguyva-marketing/gc-actions/PRODUCT_BACKLOG.md"
if [ -f "$BACKLOG" ]; then
  b23_line=$(grep -A2 "^### B-23:" "$BACKLOG" 2>/dev/null | head -3)
  echo "B-23 (GSC + Bing verification + measurement baseline) current status line(s):"
  echo "$b23_line" | sed 's/^/  /'
  if echo "$b23_line" | grep -qiE "status: done"; then
    echo "B-23 appears DONE — a GSC Links export may now exist. This precheck cannot itself read"
    echo "Search Console data; the engine should flag this as 'dependency may now be satisfiable,"
    echo "verify manually' rather than assume the watch is live."
  else
    echo "B-23 not done — backlink-gap watch has no data source yet."
  fi
else
  echo "Could not read $BACKLOG (source-unavailable) — cannot check B-23's status this run."
fi
echo

echo "### End of precheck digest"
exit 0
