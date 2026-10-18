#!/usr/bin/env bash
# The audience pages on wrangler dev, as ghostline.saqoo.sh would serve them: the viewer's build and a day's scene folder put
# into the local R2 under dvr/<event>/, then the Worker (and its relay) on http://127.0.0.1:<port>/<event>/.
#   worker/test/local-page.sh <event> <scene folder: index.json, scene/*.sog, track.json ...> <state dir> [port]
# PAGES_ONLY=1: put the build again and stop (the running dev server serves it at once). Build first: (cd viewer && VITE_BASE=/<event>/ bun run build). Point rt_relay.py at --base http://127.0.0.1:<port> --event <event>.
set -euo pipefail
cd "$(dirname "$0")/.."
EV=$1 SCENE=$2 STATE=$3 PORT=${4:-8788}; DIST=../viewer/dist
[[ "$EV" =~ ^(staging-)?[a-z0-9][a-z0-9-]*$ ]] || { echo "not an event name: $EV" >&2; exit 2; }
# the content type from the extension, as rclone sets it on the real bucket (without one a module script is refused)
ctype() { case "$1" in *.html) echo text/html;; *.js) echo text/javascript;; *.css) echo text/css;; *.json) echo application/json;; *.jpg) echo image/jpeg;; *.mp4) echo video/mp4;; *) echo application/octet-stream;; esac; }
put() { npx wrangler r2 object put "vdgs/dvr/$EV/$1" --file "$2" --content-type "$(ctype "$1")" --local --persist-to "$STATE" > /dev/null; }
put index.html "$DIST/event.html"
for f in watch.html heat.html; do [ -f "$DIST/$f" ] && put "$f" "$DIST/$f"; done
for f in "$DIST"/assets/*; do put "assets/$(basename "$f")" "$f"; done
[ -n "${PAGES_ONLY:-}" ] && exit 0
(cd "$SCENE" && find . -type f | sed 's|^\./||') | while read -r f; do put "$f" "$SCENE/$f"; done
echo "http://127.0.0.1:$PORT/$EV/"
exec npx wrangler dev --local --ip "${IP:-127.0.0.1}" --port "$PORT" --persist-to "$STATE"   # IP=0.0.0.0: reachable from the venue box over Tailscale
