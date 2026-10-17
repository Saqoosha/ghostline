#!/usr/bin/env bash
# Publish a race day's audience pages to https://ghostline.saqoo.sh/<event>/ (worker/: the relay and R2).
#   tools/publish-event.sh <event> <scene folder> [title]   the pages (event.html as index.html, watch.html, heat.html) and the
#        day's data from the folder: index.json (its "title" set when given), scene/<scene>.sog, and sky.jpg, track.json,
#        countries.json when there. heats.json is created empty only if the day has none (the relay writes it).
#   tools/publish-event.sh <event> --track <file>           the course line alone ({"track": [[x, y, z], ...]}, rt_trackline.py
#        or make_race.py), as track.json: the pages read it again every minute
#   tools/publish-event.sh <event> --night <folder>         the night rebuild (tools/dvr/night_heats.py): <folder>/<heat id>/
#        race.json and the pilots' clips, to heats/<heat id>/
# DRY_RUN=1 lists what would go up. The event name is the URL and the R2 prefix: staging-* days are not listed on /.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EV="${1:?event, e.g. jdl-2026-10-25}"; WHAT="${2:?scene folder, --track <file> or --night <folder>}"
[[ "$EV" =~ ^(staging-)?[a-z0-9][a-z0-9-]*$ ]] || { echo "not an event name: $EV" >&2; exit 2; }
BUCKET="vdgs"; REMOTE="${VDGS_R2_REMOTE:-r2}"; MOUNT="${VDGS_R2_ENV:-$HOME/.claude/1p-mounts/vdgs.env}"
DRY=(); [ -n "${DRY_RUN:-}" ] && DRY=(--dry-run)
DEST="$REMOTE:$BUCKET/dvr/$EV"
say() { printf '\n== %s ==\n' "$1"; }

say "credentials"
command -v rclone >/dev/null || { echo "rclone is not installed" >&2; exit 1; }
CAT=(cat); command -v timeout >/dev/null && CAT=(timeout 15 cat)
[ -e "$MOUNT" ] || { echo "no R2 credentials at $MOUNT" >&2; exit 1; }
CREDS="$("${CAT[@]}" "$MOUNT" 2>/dev/null || true)"
[ -n "$CREDS" ] || { echo "$MOUNT did not produce anything - is 1Password running and unlocked?" >&2; exit 1; }
AK="$(printf '%s\n' "$CREDS" | sed -n 's/^R2_ACCESS_KEY_ID=//p' | head -1)"
SK="$(printf '%s\n' "$CREDS" | sed -n 's/^R2_SECRET_ACCESS_KEY=//p' | head -1)"
unset CREDS
[ -n "$AK" ] && [ -n "$SK" ] || { echo "$MOUNT has no R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY" >&2; exit 1; }
UC="$(printf '%s' "$REMOTE" | tr '[:lower:]-' '[:upper:]_')"
export "RCLONE_CONFIG_${UC}_ACCESS_KEY_ID=$AK" "RCLONE_CONFIG_${UC}_SECRET_ACCESS_KEY=$SK"; unset AK SK
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT INT TERM
up() { rclone --s3-no-check-bucket copy --checksum "${DRY[@]}" "$@"; }

case "$WHAT" in
  --track)
    F="${3:?--track <file>}"
    python3 -c 'import json,sys; t=json.load(open(sys.argv[1]))["track"]; assert len(t) > 10 and all(len(p) == 3 for p in t); json.dump({"track": t}, open(sys.argv[2], "w"))' "$F" "$TMP/track.json"
    say "course line -> $DEST/track.json"; up "$TMP/track.json" "$DEST/" ;;
  --night)
    D="${3:?--night <folder>}"
    for h in "$D"/*/; do [ -f "$h/race.json" ] || { echo "no race.json in $h" >&2; exit 1; }; done
    say "night rebuild -> $DEST/heats/"; up --progress "$D" "$DEST/heats/" ;;
  *)
    SCENE="$WHAT"; TITLE="${3:-}"
    [ -f "$SCENE/index.json" ] || { echo "no index.json in $SCENE" >&2; exit 1; }
    say "build the pages"
    ( cd "$ROOT/viewer" && VITE_BASE="/$EV/" bun run build )
    PAGE="$TMP/page"; mkdir -p "$PAGE/scene"
    cp "$ROOT/viewer/dist/event.html" "$PAGE/index.html"; cp "$ROOT/viewer/dist/watch.html" "$ROOT/viewer/dist/heat.html" "$PAGE/"
    cp -R "$ROOT/viewer/dist/assets" "$PAGE/"
    python3 -c 'import json,sys; j=json.load(open(sys.argv[1])); j.update({"title": sys.argv[3]} if sys.argv[3] else {}); json.dump(j, open(sys.argv[2], "w"))' "$SCENE/index.json" "$PAGE/index.json" "$TITLE"
    SC="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["scene"])' "$SCENE/index.json")"
    cp -L "$SCENE/scene/$SC.sog" "$PAGE/scene/"
    for f in sky.jpg track.json countries.json; do [ -e "$SCENE/$f" ] && cp -L "$SCENE/$f" "$PAGE/$f"; done
    # the heat list belongs to the relay once the day starts: written here only when there is none
    if ! rclone lsf "$DEST/heats.json" 2>/dev/null | grep -q heats.json; then
      python3 -c 'import json,sys; t=json.load(open(sys.argv[1])).get("title") or sys.argv[2]; json.dump({"event": sys.argv[2], "title": t, "live": None, "heats": []}, open(sys.argv[3], "w"))' "$PAGE/index.json" "$EV" "$PAGE/heats.json"
    fi
    du -sh "$PAGE"; ls -R "$PAGE" | head -40
    say "pages and data -> $DEST/"; up --progress "$PAGE" "$DEST/" ;;
esac
echo "https://ghostline.saqoo.sh/$EV/"
