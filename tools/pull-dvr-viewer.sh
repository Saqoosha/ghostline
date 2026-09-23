#!/usr/bin/env bash
# Make this checkout's copy of a DVR viewer page the one that is live.
#
#   bash tools/pull-dvr-viewer.sh <name>        e.g. jdl-2026-r6
#
# make-catalog.sh puts build/dvr-viewer/<name> back into the site on every catalog build,
# and publish.sh deploys that site whole - so a copy older than what is live reverts the
# viewer. check_live_viewers.py stops the deploy when that is about to happen; this is the
# fix it points at. The page is index.html plus the hashed files it names, and whatever
# those name in turn; each is fetched from the live site, never rebuilt, so what lands here
# is byte for byte what is being served.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="${1:?name, e.g. jdl-2026-r6}"
BASE="${VDGS_BASE_URL:-https://vdgs.saqoo.sh}"
DEST="$ROOT/build/dvr-viewer/$NAME"
STAGE="$(mktemp -d)"
# The swap copy sits beside build/dvr-viewer, not in it: make-catalog.sh publishes every
# folder in there, so one left by an interrupted run would go live as a viewer of its own.
# Same filesystem, so the final mv is still a rename.
NEW="$ROOT/build/.dvr-viewer-new.$NAME.$$"
trap 'rm -rf "$STAGE" "$NEW"' EXIT INT TERM

python3 - "$BASE" "$NAME" "$STAGE" <<'PY'
import os, random, re, sys, urllib.error, urllib.request
base, name, stage = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
prefix = "/dvr/%s/" % name

def get(rel):
    url = base + prefix + rel + ("?cb=%d" % random.randrange(1 << 30) if rel == "" else "")
    # Named: the zone's bot check answers Python's default User-Agent with a 403.
    req = urllib.request.Request(url, headers={"User-Agent": "vdgs-publish"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001 - any failure stops with nothing moved
        # The old copy is only replaced once every file is here. A 404 on an asset can be a
        # string in the bundle that only looks like one.
        sys.exit("could not fetch %s (%s) - build/dvr-viewer/%s left as it was"
                 % (url, getattr(e, "code", None) or e, name))

# Asset references as the build writes them: absolute under the viewer's base, or relative.
ref = re.compile(rb'(?:%s)?(assets/[A-Za-z0-9_.-]+\.(?:js|mjs|css|wasm|png|jpg|svg|woff2?))'
                 % re.escape(prefix.encode()))
todo, seen = [], set()
# Cloudflare Web Analytics adds its beacon before </body> in some responses; kept, it would
# be deployed as part of the page and injected again on top.
html = re.sub(rb'<script[^>]*static\.cloudflareinsights\.com[^>]*></script>\n?', b"", get(""))
open(os.path.join(stage, "index.html"), "wb").write(html)
todo += [m.decode() for m in ref.findall(html)]
while todo:
    rel = todo.pop()
    if rel in seen:
        continue
    seen.add(rel)
    body = get(rel)
    os.makedirs(os.path.join(stage, os.path.dirname(rel)), exist_ok=True)
    open(os.path.join(stage, rel), "wb").write(body)
    print("   %s  (%d bytes)" % (rel, len(body)))
    if rel.endswith((".js", ".mjs", ".css")):
        todo += [m.decode() for m in ref.findall(body)]
if not seen:
    sys.exit("the live page names no assets - not what a viewer build looks like, refusing")
PY

# Copied next to DEST first and swapped in by rename, so a copy that fails part way leaves
# the old one in place rather than a half page that the deploy check could pass on index.html
# alone. Into a fresh directory, not the mktemp one, which is mode 0700.
mkdir -p "$(dirname "$DEST")"
rm -rf "$NEW"; mkdir -p "$NEW"
cp -R "$STAGE/." "$NEW/"
# The old copy is set aside, not deleted: it may be the only copy of a build someone meant
# to publish from here.
if [ -d "$DEST" ]; then
  OLD="$ROOT/build/dvr-viewer-previous/$NAME-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$(dirname "$OLD")"
  mv "$DEST" "$OLD"
  echo "   previous copy -> ${OLD#$ROOT/}"
fi
mv "$NEW" "$DEST"
echo "-> build/dvr-viewer/$NAME is now the live page. Run make-catalog.sh again before publishing."
