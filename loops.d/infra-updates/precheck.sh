#!/usr/bin/env bash
# infra-updates/precheck.sh — deterministic gathering step (script->agent
# pattern). This is a plain, unsandboxed runner script: every ssh/curl of the
# loop lives here, all of it read-only (apt list, apt-cache policy, uname,
# docker ps, `ha … info`, GET to GitHub/plex.tv/CISA). The only writes are the
# optional `sudo -n apt-get update` list refresh on the guests whose admin has
# passwordless sudo, and the raw inputs saved under $OUT_DIR/inputs as the
# audit trail. Nothing is installed, ever.
#
# Output: a compact digest the engine grades against the hold policy
# (~/projects/sysadmin/update-policy.md). If NOTHING is pending anywhere —
# no upgradable package, no app/HA/OS version behind upstream, no reboot
# pending, no KEV match, every host reachable, no stale apt lists — the
# script prints nothing, so the runner records skipped-precheck and never
# invokes the engine (zero tokens).
#
# Hosts and how (from inventory.md; access per ACCESS-WARMSTART.md):
#   pve1          root ssh         Debian 13 + Proxmox kernel, HAOS via qm guest exec 105
#   firstparty    admin, sudo -n   Debian 13 guest (Docker deliberately off)
#   thirdparty    admin, sudo -n   Debian 13 guest, docker compose stacks
#   evilempire    admin via -J pve1, sudo -n   Debian 13 guest on vmbr9
#   ntp           root ssh         Debian 13 CT 104
#   media         user, NO sudo    Debian 13 bare metal; apt lists refreshed by apt-daily.timer
#   llm           local            macOS, brew, Tailscale.app
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export LC_ALL=C

HOLD_DAYS="${HOLD_DAYS:-7}"
STALE_LIST_DAYS=3
KEV_WINDOW_DAYS=30
INPUTS="${OUT_DIR:-${TMPDIR:-/tmp}}/inputs"
mkdir -p "$INPUTS"
SSH="ssh -o BatchMode=yes -o ConnectTimeout=12 -o ServerAliveInterval=10"

# ---------------------------------------------------------------- remote probe
# Same snippet on every Debian host. Prints machine lines:
#   LISTAGE <days since newest Release file in /var/lib/apt/lists>
#   KERNEL <running> <newest installed vmlinuz, empty on a CT>
#   UPG <pkg>|<installed>|<candidate>|<repo URL of the candidate>
read -r -d '' REMOTE <<'EOS'
set -u
if [ "$(id -u)" = 0 ]; then apt-get update -qq >/dev/null 2>&1 || true
elif sudo -n true 2>/dev/null; then sudo -n apt-get update -qq >/dev/null 2>&1 || true
fi
newest=$(stat -c %Y /var/lib/apt/lists/*Release 2>/dev/null | sort -n | tail -1)
if [ -n "${newest:-}" ]; then echo "LISTAGE $(( ( $(date +%s) - newest ) / 86400 ))"; else echo "LISTAGE unknown"; fi
nk=$(ls /boot/vmlinuz-* 2>/dev/null | sed 's#.*/vmlinuz-##' | sort -V | tail -1)
echo "KERNEL $(uname -r) ${nk:-}"
apt list --upgradable 2>/dev/null | tail -n +2 | while IFS= read -r line; do
  n=${line%%/*}
  cand=$(printf '%s' "$line" | awk '{print $2}')
  inst=$(printf '%s' "$line" | sed -n 's/.*upgradable from: \(.*\)\]/\1/p')
  src=$(apt-cache policy "$n" 2>/dev/null | awk -v v="$cand" '$1==v {getline; print $2; exit}')
  echo "UPG $n|$inst|$cand|${src:-?}"
done
EOS

probe_host() { # probe_host <label> <ssh target...>
  local label="$1"; shift
  if $SSH "$@" "bash -s" <<<"$REMOTE" >"$INPUTS/$label.txt" 2>"$INPUTS/$label.err"; then :; else
    echo "UNREACHABLE $(tr '\n' ' ' <"$INPUTS/$label.err" | cut -c1-160)" >>"$INPUTS/$label.txt"
  fi
}

probe_host pve1 pve1 &
probe_host firstparty firstparty &
probe_host thirdparty thirdparty &
probe_host ntp root@ntp.home.arpa &
probe_host media media &
probe_host evilempire -J pve1 admin@10.99.99.10 &
wait

# pve1 extras: HAOS versions through the guest agent (plain text; --raw-json
# intermittently returns nothing through the agent).
{
  for cmd in "ha os info" "ha core info" "ha supervisor info"; do
    echo "### $cmd"
    $SSH pve1 "qm guest exec 105 --timeout 40 -- docker exec hassio_cli $cmd 2>/dev/null" 2>/dev/null \
      | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin); print(d.get("out-data",""))
except Exception as e:
    print("PARSE-ERROR", e)'
  done
} >"$INPUTS/haos.txt" 2>&1

# thirdparty: running image tags (compose stacks pin tags; rolling tags are a policy finding)
$SSH thirdparty "docker ps --format '{{.Names}} {{.Image}}'" >"$INPUTS/thirdparty-images.txt" 2>/dev/null || true

# media: app versions over their unauthenticated local endpoints
$SSH media "curl -s -m 5 http://127.0.0.1:32400/identity | grep -o 'version=\"[^\"]*\"' | head -1; curl -s -m 5 http://127.0.0.1:2283/api/server/version" >"$INPUTS/media-apps.txt" 2>/dev/null || true

# llm (local): brew, macOS, Tailscale.app
brew outdated --json=v2 >"$INPUTS/brew.json" 2>/dev/null || echo '{}' >"$INPUTS/brew.json"
python3 - "$INPUTS/softwareupdate.txt" <<'EOF' 2>/dev/null || true
import subprocess, sys
try:
    r = subprocess.run(["softwareupdate", "-l"], capture_output=True, text=True, timeout=120)
    open(sys.argv[1], "w").write(r.stdout + r.stderr)
except Exception as e:
    open(sys.argv[1], "w").write("softwareupdate failed: %s\n" % e)
EOF
{
  echo "macos $(sw_vers -productVersion 2>/dev/null)"
  echo "tailscale-cli $(tailscale version 2>/dev/null | head -1)"
  echo "tailscale-app $(defaults read /Applications/Tailscale.app/Contents/Info.plist CFBundleShortVersionString 2>/dev/null)"
} >"$INPUTS/llm.txt"

# upstream: GitHub releases (unauthenticated, 60 req/h is plenty), plex.tv, CISA KEV
gh_releases() { # gh_releases <owner/repo> -> $INPUTS/gh-<repo>.json
  local repo="$1" f="$INPUTS/gh-${1##*/}.json"
  curl -s -m 20 -H 'Accept: application/vnd.github+json' -H 'User-Agent: infra-updates-loop' \
    "https://api.github.com/repos/$repo/releases?per_page=40" -o "$f" || echo '[]' >"$f"
}
for r in moby/moby docker/compose docker/buildx containerd/containerd tailscale/tailscale caddyserver/caddy \
         immich-app/immich home-assistant/core home-assistant/operating-system home-assistant/supervisor \
         open-webui/open-webui gitroomhq/postiz-app louislam/uptime-kuma; do gh_releases "$r"; done
curl -s -m 20 'https://plex.tv/api/downloads/5.json' -o "$INPUTS/plex.json" || echo '{}' >"$INPUTS/plex.json"
curl -s -m 60 'https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json' -o "$INPUTS/kev.json" || echo '{}' >"$INPUTS/kev.json"

# ---------------------------------------------------------------- digest
python3 - "$INPUTS" "$HOLD_DAYS" "$STALE_LIST_DAYS" "$KEV_WINDOW_DAYS" <<'EOF'
import json, os, re, sys, datetime as dt

inputs, HOLD, STALE, KEVW = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
now = dt.datetime.now(dt.timezone.utc)
today = now.date()

def read(name):
    p = os.path.join(inputs, name)
    return open(p, errors="replace").read() if os.path.exists(p) else ""

def jload(name, default):
    try:
        return json.load(open(os.path.join(inputs, name)))
    except Exception:
        return default

def age_days(iso):
    if not iso: return None
    try:
        d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (now - d).days
    except Exception:
        return None

# --- upstream release tables: repo -> {normalised version: published date}, plus latest
def gh_table(repo):
    rel = jload(f"gh-{repo}.json", [])
    if not isinstance(rel, list): rel = []
    table, latest = {}, None
    for r in rel:
        if r.get("draft"): continue
        tag = (r.get("tag_name") or "").lstrip("v")
        pub = (r.get("published_at") or "")[:10]
        table[tag] = pub
        if latest is None and not r.get("prerelease"):
            latest = (tag, pub)
    return table, latest

GH = {k: gh_table(k) for k in ["moby", "compose", "buildx", "containerd", "tailscale", "caddy", "immich",
                               "core", "operating-system", "supervisor", "open-webui", "postiz-app", "uptime-kuma"]}
# apt package name -> upstream repo key, where the repo URL alone is ambiguous (Docker's repo ships four projects)
PKG_UPSTREAM = {"docker-compose-plugin": "compose", "docker-buildx-plugin": "buildx", "containerd.io": "containerd",
                "docker-ce": "moby", "docker-ce-cli": "moby", "docker-ce-rootless-extras": "moby"}

plex = jload("plex.json", {})
plex_latest = None
try:
    lin = plex["computer"]["Linux"]
    plex_latest = (lin["version"], dt.datetime.fromtimestamp(int(lin["release_date"]), dt.timezone.utc).date().isoformat())
except Exception:
    pass

# --- classify an apt candidate by its repo URL
def classify(url):
    u = url or ""
    if "security.debian.org" in u or "-security" in u: return "debian-security", None
    if "deb.debian.org" in u or "debian.org" in u: return "debian", None
    if "download.docker.com" in u: return "third-party", "moby"
    if "pkgs.tailscale.com" in u: return "third-party", "tailscale"
    if "cloudsmith.io/public/caddy" in u or "caddy" in u: return "third-party", "caddy"
    if "downloads.plex.tv" in u: return "third-party", "plex"
    if "proxmox.com" in u: return "third-party", "proxmox"
    if u in ("?", ""): return "unknown-origin", None
    return "third-party", None

def short_ver(v):
    # 5:29.9.3-1~debian.13~trixie -> 29.9.3 ; 1.43.5.11079-b3b13e506 -> 1.43.5.11079-b3b13e506 ; 1.104.5 -> 1.104.5
    v = v.split(":", 1)[-1]
    v = re.split(r"[-~+]", v)[0] if not v.startswith("1.43") else v
    return v

def verdict_for(cls, src, ver):
    """-> (verdict, released, age) ; verdict in apply-now | ready | wait | unknown-age"""
    if cls in ("debian-security", "debian"):
        return "apply-now", None, None
    released = None
    if src == "plex" and plex_latest and ver.startswith(plex_latest[0]):
        released = plex_latest[1]
    elif src in GH:
        released = GH[src][0].get(short_ver(ver))
    if released is None:
        return "unknown-age", None, None
    age = (today - dt.date.fromisoformat(released)).days
    return ("ready" if age >= HOLD else f"wait({HOLD - age}d)"), released, age

pending, reboot, unreachable, stale, notes = [], [], [], [], []

def add(host, comp, inst, cand, cls, verdict, released=None, age=None):
    pending.append(dict(host=host, comp=comp, inst=inst, cand=cand, cls=cls, verdict=verdict,
                        released=released or "-", age="-" if age is None else age))

# --- Debian hosts
for host in ["pve1", "firstparty", "thirdparty", "ntp", "media", "evilempire"]:
    txt = read(f"{host}.txt")
    if "UNREACHABLE" in txt or not txt.strip():
        unreachable.append(f"{host}: {txt.strip()[:160] or 'no output'}")
        continue
    for line in txt.splitlines():
        if line.startswith("LISTAGE "):
            a = line.split()[1]
            if a == "unknown" or int(a) > STALE: stale.append(f"{host}: apt lists {a} days old")
        elif line.startswith("KERNEL "):
            parts = line.split()
            run = parts[1] if len(parts) > 1 else "?"
            newest = parts[2] if len(parts) > 2 else ""
            if newest and newest != run:
                reboot.append(f"{host}: running {run}, newest installed {newest}")
        elif line.startswith("UPG "):
            n, inst, cand, src = (line[4:].split("|") + ["?"] * 4)[:4]
            cls, upstream = classify(src)
            upstream = PKG_UPSTREAM.get(n, upstream)
            v, rel, age = verdict_for(cls, upstream, cand)
            add(host, n, inst, cand, cls, v, rel, age)

# --- HAOS (os / core / supervisor / add-ons)
ha = read("haos.txt")
def ha_block(name):
    m = re.search(rf"### {re.escape(name)}\n(.*?)(?=\n### |\Z)", ha, re.S)
    return m.group(1) if m else ""
ha_src = {"ha os info": ("haos", "operating-system"), "ha core info": ("core", "core"), "ha supervisor info": ("supervisor", "supervisor")}
for cmd, (comp, repo) in ha_src.items():
    b = ha_block(cmd)
    cur = re.search(r"^version: (\S+)", b, re.M)
    lat = re.search(r"^version_latest: (\S+)", b, re.M)
    if cur and lat and cur.group(1) != lat.group(1):
        v, rel, age = verdict_for("third-party", repo, lat.group(1))
        add("haos", comp, cur.group(1), lat.group(1), "app", v, rel, age)
if not ha.strip() or "PARSE-ERROR" in ha:
    unreachable.append("haos: guest-agent query failed")
# add-ons: blocks under "addons:" with slug/version/version_latest/update_available
sup = ha_block("ha supervisor info")
cur_slug = None; vers = {}
for line in sup.splitlines():
    m = re.match(r"\s*-?\s*(slug|version|version_latest|update_available|name): (.*)$", line)
    if not m: continue
    k, v = m.group(1), m.group(2).strip()
    if k == "name": cur_slug = v; vers[cur_slug] = {}
    elif cur_slug is not None: vers[cur_slug][k] = v
for slug, d in vers.items():
    if d.get("update_available") == "true":
        add("haos", f"addon:{d.get('slug', slug)}", d.get("version", "?"), d.get("version_latest", "?"), "app", "unknown-age")

# --- thirdparty images vs upstream
img_repo = {"open-webui": "open-webui", "postiz": "postiz-app", "kuma": "uptime-kuma"}
for line in read("thirdparty-images.txt").splitlines():
    try: name, image = line.split(None, 1)
    except ValueError: continue
    tag = image.rsplit(":", 1)[-1] if ":" in image.split("/")[-1] else "latest"
    if tag in ("latest",) or re.fullmatch(r"\d+", tag):
        notes.append(f"thirdparty: {name} runs rolling tag `{image}` (no version pin; every `docker compose pull` takes the newest build, against the hold policy)")
        continue
    repo = img_repo.get(name)
    if not repo: continue
    table, latest = GH[repo]
    if latest and latest[0] != tag.lstrip("v"):
        v, rel, age = verdict_for("third-party", repo, latest[0])
        add("thirdparty", name, tag, latest[0], "app", v, rel, age)

# --- media apps
ma = read("media-apps.txt")
pv = re.search(r'version="(\d+\.\d+\.\d+\.\d+[^"]*)"', ma)  # skip the XML declaration's version="1.0"
if pv and plex_latest and pv.group(1) != plex_latest[0]:
    v, rel, age = verdict_for("third-party", "plex", plex_latest[0])
    add("media", "plex (running)", pv.group(1), plex_latest[0], "app", v, rel, age)
iv = re.search(r'"major":(\d+),"minor":(\d+),"patch":(\d+)', ma)
if iv:
    cur = ".".join(iv.groups()); table, latest = GH["immich"]
    if latest and latest[0] != cur:
        v, rel, age = verdict_for("third-party", "immich", latest[0])
        add("media", "immich", cur, latest[0], "app", v, rel, age)

# --- llm
brew = jload("brew.json", {})
sec_re = re.compile(r"^(openssl|libssh2?|curl|gnutls|openssh|sqlite|libxml2|expat|zlib|nghttp2|python|node|go|git|git-lfs|tailscale|caddy|ca-certificates|libgcrypt|libsodium|wget|rsync)(@[\w.]+)?$")
outdated = (brew.get("formulae") or []) + (brew.get("casks") or [])
sec = [f"{o.get('name')} {','.join(o.get('installed_versions', []))}->{o.get('current_version')}" for o in outdated if sec_re.match(o.get("name", ""))]
if sec:
    add("llm", "brew (security-relevant)", f"{len(outdated)} outdated", "; ".join(sec), "third-party", "unknown-age")
su = read("softwareupdate.txt")
llm = read("llm.txt")
mac_cur = re.search(r"^macos (\S+)", llm, re.M)
for m in re.finditer(r"Title: (macOS [^,]+), Version: ([^,]+),", su):
    major = m.group(2).split(".")[0]
    # one stable component per release line, so 26.7.3 -> 26.7.4 stays the same finding
    add("llm", f"macos-{major}.x", mac_cur.group(1) if mac_cur else "?", m.group(2), "os", "unknown-age")
tapp = re.search(r"tailscale-app (\S+)", llm); tcli = re.search(r"tailscale-cli (\S+)", llm)
ts_latest = GH["tailscale"][1]
if ts_latest:
    for label, m in (("Tailscale.app", tapp), ("tailscale CLI (brew)", tcli)):
        if m and m.group(1) and m.group(1) != ts_latest[0]:
            v, rel, age = verdict_for("third-party", "tailscale", ts_latest[0])
            add("llm", label, m.group(1), ts_latest[0], "third-party", v, rel, age)

# --- CISA KEV, last KEVW days, against the stack
kev = jload("kev.json", {})
stack = re.compile(r"plex|docker|moby|tailscale|immich|home assistant|proxmox|caddy|debian|linux kernel|synology|mikrotik|routeros|apple macos|openssh|openssl|homebrew|open webui|postiz|uptime kuma|searxng|temporal|elasticsearch|postgres|redis|valkey|chrony|wireguard|zigbee", re.I)
kev_hits = []
for v in kev.get("vulnerabilities", []):
    added = v.get("dateAdded", "")
    try:
        if (today - dt.date.fromisoformat(added)).days > KEVW: continue
    except Exception:
        continue
    blob = f"{v.get('vendorProject','')} {v.get('product','')} {v.get('vulnerabilityName','')}"
    if stack.search(blob):
        kev_hits.append(f"{v.get('cveID')} | {v.get('vendorProject')} {v.get('product')} | added {added} | {v.get('shortDescription','')[:140]}")

# --- emit (nothing at all when nothing is pending)
if not (pending or reboot or unreachable or stale or kev_hits or notes):
    sys.exit(0)

print(f"# infra-updates digest — {now.strftime('%Y-%m-%d %H:%M UTC')} — hold policy {HOLD} days")
print("policy: debian / debian-security = apply-now (vetted; unattended-upgrades should already have taken them on the hosts that have it; pve1 has none). "
      f"third-party / app / os = wait until the upstream release is >= {HOLD} days old, unless a CISA KEV entry below names it. "
      "unknown-age = no upstream date found; use the PRIOR FINDINGS 'since' date as the earliest-known date.")
print()
if pending:
    print("## pending")
    print("host | component | installed | candidate | class | verdict | released | age_days")
    for p in pending:
        print(f"{p['host']} | {p['comp']} | {p['inst']} | {p['cand']} | {p['cls']} | {p['verdict']} | {p['released']} | {p['age']}")
    print()
if reboot:
    print("## reboot pending (newer kernel installed than running)")
    for r in reboot: print(f"- {r}")
    print()
if kev_hits:
    print(f"## CISA KEV entries added in the last {KEVW} days that name something in this stack")
    for k in kev_hits: print(f"- {k}")
    print()
if notes:
    print("## policy notes")
    for n in notes: print(f"- {n}")
    print()
if stale:
    print(f"## stale apt lists (> {STALE} days, the host's refresh timer may be broken)")
    for s in stale: print(f"- {s}")
    print()
if unreachable:
    print("## unreachable / failed probes")
    for u in unreachable: print(f"- {u}")
    print()
print(f"## counts\npending={len(pending)} ready={sum(1 for p in pending if p['verdict']=='ready')} apply_now={sum(1 for p in pending if p['verdict']=='apply-now')} "
      f"waiting={sum(1 for p in pending if p['verdict'].startswith('wait'))} unknown_age={sum(1 for p in pending if p['verdict']=='unknown-age')} "
      f"reboot_pending={len(reboot)} kev={len(kev_hits)} unreachable={len(unreachable)} stale_lists={len(stale)}")
EOF
