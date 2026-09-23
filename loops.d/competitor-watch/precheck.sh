#!/usr/bin/env bash
# competitor-watch/precheck.sh — deterministic gathering step (script->agent
# pattern, docs/INTERFACES.md §4.1/§6.2). Fetches raw signal for all six
# watches from gc-actions/specs/B-27.md's design (watch 5, Ref Plans depth
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

echo "# competitor-watch precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# Strips <script>/<style> blocks and volatile Cloudflare anti-bot noise
# (challenge tokens, cdn-cgi beacon URLs — both rotate on every single fetch
# regardless of whether the page's real content changed) before caching, so
# the diff reflects actual page content, not CDN churn. Found by hand-testing
# this precheck against sourcegraph.com/pricing, which is Cloudflare-fronted.
normalize_html () {
  perl -0pe '
    s/<script\b[^>]*>.*?<\/script>//gis;
    s/<style\b[^>]*>.*?<\/style>//gis;
    s/[?&]?id=[A-Za-z0-9._-]{20,}//g;
    s/cdn-cgi\/[^"'\'' ]*//g;
    s/\br=.[a-f0-9]{16}.,t=.[A-Za-z0-9=]+./r=REDACTED,t=REDACTED/g;
  ' "$1" 2>/dev/null || cat "$1"
}

fetch_and_diff () {
  # $1 = cache key (filename-safe), $2 = url, $3 = human label
  local key="$1" url="$2" label="$3"
  local cache_file="$CACHE/${key}.txt"
  local fresh_file="$INPUTS/${key}.txt"
  local fresh_norm="$INPUTS/${key}.norm.txt"
  if $CURL "$UA" "$url" -o "$fresh_file" --fail; then
    if [ -s "$fresh_file" ]; then
      echo "## $label — fetched OK ($(wc -c <"$fresh_file" | tr -d ' ') bytes)"
      normalize_html "$fresh_file" > "$fresh_norm"
      if [ -f "$cache_file" ]; then
        if cmp -s "$cache_file" "$fresh_norm"; then
          echo "  no change vs cached copy (after stripping scripts/CDN noise)"
        else
          echo "  CHANGED vs cached copy — diff (first 60 lines, scripts/CDN noise stripped):"
          diff "$cache_file" "$fresh_norm" 2>/dev/null | head -60 | sed 's/^/  /'
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
fetch_and_diff "claude-code-changelog" "https://docs.claude.com/en/release-notes/claude-code" "Claude Code changelog"
fetch_and_diff "cursor-blog" "https://cursor.com/blog" "Cursor research blog"
echo

# Watch 3: OSS local-indexer commoditization
echo "### Watch 3: OSS local-indexer traction"
for repo in "DeusData/codebase-memory-mcp" "abhigyanpatwari/GitNexus" "Pharaoh-so/pharaoh-mcp" "MagneticAnomaly/SourcePrep-MCP"; do
  api_out="$INPUTS/gh-$(echo "$repo" | tr '/' '_').json"
  if $CURL "$UA" "https://api.github.com/repos/$repo" -o "$api_out" --fail 2>/dev/null && \
     jq -e '.stargazers_count' "$api_out" >/dev/null 2>&1; then
    stars=$(jq -r '.stargazers_count' "$api_out")
    pushed=$(jq -r '.pushed_at' "$api_out")
    echo "- $repo: stars=$stars, last_pushed=$pushed"
    cache_file="$CACHE/gh-$(echo "$repo" | tr '/' '_').txt"
    prev="none"
    [ -f "$cache_file" ] && prev="$(cat "$cache_file")"
    echo "  previous cached star count: $prev"
    echo "$stars" > "$cache_file"
  else
    echo "- $repo: LOOKUP FAILED (source-unavailable) — repo id may have changed since B-27's design; verify the slug"
  fi
done
echo "NOTE: the four repo slugs above are best-effort guesses at the org/repo path for the names in"
echo "B-27's design (code-graph-mcp, GitNexus, Pharaoh, SourcePrep) — confirm/correct them on first review."
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
    url_count=$(grep -c '<loc>' "$sm_out" 2>/dev/null); url_count=${url_count:-0}
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

# Watch 5: Ref Plans depth creep — ref.tools' "Ref Plans" product
# (plan.ref.tools) currently does shallow codebase reading to inform task
# generation (per its own docs: "researches your codebase, reads relevant
# files... auth flows, DB models, API routes") — real but shallow overlap,
# not the graph-based/dependency analysis Maguyva does. Watch for its own
# public docs starting to claim dependency-graph, call-graph, blast-radius,
# or structural/AST-based analysis — that would close the "depth gap" a lot
# of current Maguyva positioning leans on. "Reads more file types" or
# "supports more languages" is noise, not signal (see prompt.md).
echo "### Watch 5: Ref Plans depth creep"
fetch_and_diff "refplans-first-plan" "https://docs.ref.tools/plans/getting-started/your-first-plan" "docs.ref.tools/plans/getting-started/your-first-plan"
fetch_and_diff "refplans-intro" "https://docs.ref.tools/plans/getting-started/intro" "docs.ref.tools/plans/getting-started/intro (capability description; other easily-discoverable Ref Plans docs page via docs.ref.tools/sitemap.xml)"
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
