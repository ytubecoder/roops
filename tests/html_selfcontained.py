"""Self-containment check for generated pages (dashboard + report pages).

The rule these pages actually have to satisfy is **no subresource the browser
fetches on load** — they are opened as `file://` or served offline, and a single
webfont or CDN script would leak a request (and break the page with no network).

The obvious approximation, "the page must not contain the substring `http://`",
is both too strict and too loose:

  * too strict — real finding text legitimately contains URLs (a probe reporting
    that `http://127.0.0.1:9/dead` refused a connection), and an SVG's
    `xmlns="http://www.w3.org/2000/svg"` is an identifier, not an address. Both
    are inert once escaped, and banning them means mangling report content.
  * too loose — it says nothing about `src="//cdn.example/x.js"`, which fetches
    from the network without containing `http://` at all.

So assert the real thing: collect every attribute the browser dereferences
without user action, plus CSS `@import`/`url()`, and require them all to be
local (relative) or `data:`. Navigation `<a href>` is deliberately NOT collected
— following a link is user-initiated and fetches nothing on load.
"""

import re
from html.parser import HTMLParser

# Attributes the browser dereferences on load, regardless of tag.
_FETCHING_ATTRS = ("src", "srcset", "poster", "data", "background")

# <link rel=...> values that fetch. `icon` fetches too (favicon), so a data: URI
# is the only acceptable form for it. Navigation-ish rels (author, license,
# canonical) do not fetch and are excluded.
_FETCHING_LINK_RELS = {
    "stylesheet",
    "icon",
    "shortcut icon",
    "apple-touch-icon",
    "apple-touch-icon-precomposed",
    "mask-icon",
    "manifest",
    "preload",
    "prefetch",
    "preconnect",
    "dns-prefetch",
    "modulepreload",
    "prerender",
}

_CSS_IMPORT_RE = re.compile(
    r"@import\s+(?:url\(\s*)?['\"]?([^'\")\s;]+)", re.IGNORECASE
)
_CSS_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+)", re.IGNORECASE)

# http:, https:, any other scheme, or protocol-relative //host.
_EXTERNAL_RE = re.compile(r"\A\s*(?:[a-z][a-z0-9+.-]*:|//)", re.IGNORECASE)


def _is_external(url):
    """True if fetching this would leave the page. `data:` is self-contained."""
    if url is None:
        return False
    candidate = url.strip()
    if not candidate or candidate.startswith("#"):
        return False
    if candidate.lower().startswith("data:"):
        return False
    return bool(_EXTERNAL_RE.match(candidate))


def _srcset_urls(value):
    """`srcset` is a comma-separated list of "<url> <descriptor>" pairs."""
    for part in value.split(","):
        candidate = part.strip().split()
        if candidate:
            yield candidate[0]


class _SubresourceScanner(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        for attr in _FETCHING_ATTRS:
            if attr not in d:
                continue
            if attr == "srcset":
                for url in _srcset_urls(d[attr]):
                    self.refs.append((tag, attr, url))
            else:
                self.refs.append((tag, attr, d[attr]))
        if tag == "link":
            rels = {r.strip().lower() for r in d.get("rel", "").split()}
            if (
                rels & _FETCHING_LINK_RELS
                or "shortcut icon" in d.get("rel", "").lower()
            ):
                self.refs.append((tag, "href", d.get("href", "")))
        # Inline style="" can carry url(...) just as a <style> block can.
        if "style" in d:
            for url in _CSS_URL_RE.findall(d["style"]):
                self.refs.append((tag, "style-url", url))
        if tag == "style":
            self._in_style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if not self._in_style:
            return
        for url in _CSS_IMPORT_RE.findall(data):
            self.refs.append(("style", "@import", url))
        for url in _CSS_URL_RE.findall(data):
            self.refs.append(("style", "url()", url))


def external_subresources(html_text):
    """Every fetched-on-load reference that would leave the page.

    Returns a list of (tag, attribute, url). Empty means self-contained."""
    scanner = _SubresourceScanner()
    scanner.feed(html_text)
    scanner.close()
    return [ref for ref in scanner.refs if _is_external(ref[2])]


def assert_self_contained(testcase, html_text, label="page"):
    found = external_subresources(html_text)
    testcase.assertEqual(
        found,
        [],
        f"{label} fetches external subresources on load: {found}",
    )
