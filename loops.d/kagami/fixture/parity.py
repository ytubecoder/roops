"""kagami parity — the mirror must exercise the real garden's feature surface.

The drift check (byte-compare vs live) can only see what the fixture renders:
any data-conditional UI feature the mock fleet never exhibits is invisible to
it, and the public mockup silently stops showcasing the current interface —
"4/4 checks pass" while the real garden visibly outgrows the mirror. This
module extracts the rendered feature surface (CSS class tokens + tag names) so
precheck's `parity` gate and the hermetic fixture test can both require
real-render ⊆ mirror-render.

TRANSIENT tokens are excluded from the comparison: kagami's own firing is in
flight while precheck renders the real garden, so `running`/`overdue` badges —
and the `grey` light/stamp that accompanies an in-flight row — are
guaranteed-present there and impossible for the pinned-clock mirror to
reproduce honestly. (A genuinely never-run loop's grey still crosses: its
empty run history mirrors as grey on the mock side too.)
"""

import re

TRANSIENT = frozenset({"running", "overdue", "grey"})


def feature_tokens(html):
    """(class_tokens, tag_names) actually rendered in `html` markup."""
    classes = set()
    for m in re.finditer(r'class="([^"]+)"', html):
        classes.update(m.group(1).split())
    tags = set(re.findall(r"<([a-zA-Z][a-zA-Z0-9-]*)", html))
    return classes, tags


def missing_from_mirror(real_html, mirror_html):
    """Feature tokens the real garden renders that the mirror lacks (sorted).

    Only token NAMES are returned — safe for check notes and PR bodies; no
    fleet data crosses.
    """
    real_classes, real_tags = feature_tokens(real_html)
    mirror_classes, mirror_tags = feature_tokens(mirror_html)
    missing = sorted(real_classes - mirror_classes - TRANSIENT)
    missing += sorted(f"<{t}>" for t in real_tags - mirror_tags)
    return missing
