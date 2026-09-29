#!/usr/bin/env bash
# Publish a DVR viewer to https://ghostline.saqoo.sh/<name>/.
#   tools/publish-dvr-viewer.sh <name> <data dir>      e.g. jdl-2026-r6 build/dvr/hdz_0067
#   tools/publish-dvr-viewer.sh <name> <flights.json>  several flights, e.g. fdf-2026-r6 viewer/public/flights.json:
#     each flight's folder (viewer/public/<data>) goes to <name>/data/<data>/ - the files its index.json lists, the
#     video, scan_cameras.json, sky.jpg and its scene - and the page opens the first one when the URL names none.
#     Folders under "races" (race.html) go the same way: race.json, the pilots' clips, audio.m4a, sky.jpg, the scene.
# The page (viewer/, built with base /<name>/) and its data go to R2 (bucket vdgs) under dvr/<name>/ with rclone;
# the Worker (worker/, deployed once) serves that prefix, so a publish is an upload and touches no other viewer.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:?name, e.g. jdl-2026-r6}"; DATA="${2:?data dir, e.g. build/dvr/hdz_0067}"
# A folder name and nothing else: it becomes the URL and the R2 prefix
[[ "$NAME" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || { echo "not a viewer name: $NAME" >&2; exit 2; }
BUCKET="vdgs"; REMOTE="${VDGS_R2_REMOTE:-r2}"; MOUNT="${VDGS_R2_ENV:-$HOME/.claude/1p-mounts/vdgs.env}"
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

say "build the page"
MULTI=; [[ "$DATA" == *flights.json ]] && MULTI=1
DEFAULT=; [ -n "$MULTI" ] && DEFAULT="data/$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["flights"][0]["data"])' "$DATA")"
# race pages (race.html) listed under "races"; the page opens the first
RACES=; [ -n "$MULTI" ] && RACES="$(python3 -c 'import json,sys;print(" ".join(json.load(open(sys.argv[1])).get("races", [])))' "$DATA")"
RACE=; [ -n "$RACES" ] && RACE="data/${RACES%% *}"
( cd "$ROOT/viewer" && VITE_BASE="/$NAME/" VITE_DEFAULT_DATA="$DEFAULT" VITE_RACE_DATA="$RACE" bun run build )
PAGE="$TMP/page"; rm -rf "$PAGE"; mkdir -p "$PAGE"; cp -R "$ROOT/viewer/dist/." "$PAGE/"
# the flight menu: the same list with every folder moved under data/, where the data goes
[ -n "$MULTI" ] && python3 - "$DATA" "$PAGE/flights.json" <<'PY'
import json, sys
j = json.load(open(sys.argv[1]))
for f in j["flights"]: f["data"] = "data/" + f["data"]
json.dump(j, open(sys.argv[2], "w"), indent=1)
PY

say "data -> r2:$BUCKET/dvr/$NAME/data/"
UP="$TMP/data"
if [ -n "$MULTI" ]; then
  PUB="$(dirname "$DATA")"
  # lists taken into variables first: a failing $(...) in a for list does not stop set -e, and the upload would go on without it
  FLS="$(python3 -c 'import json,sys;print(" ".join(f["data"] for f in json.load(open(sys.argv[1]))["flights"]))' "$DATA")"
  for FL in $FLS; do
    SRC="$PUB/$FL"; DST="$UP/$FL"; mkdir -p "$DST/scene"
    # the pose sets its index.json offers and the scene it opens; -L copies through the dev symlinks
    PS="$(python3 -c 'import json,sys;print(" ".join(p["file"] for p in json.load(open(sys.argv[1]))["poses"]))' "$SRC/index.json")"
    for f in index.json dvr_pinhole.mp4 dvr_pinhole.mp4.json scan_cameras.json $PS; do
      cp -L "$SRC/$f" "$DST/$f"; done
    [ -e "$SRC/sky.jpg" ] && cp -L "$SRC/sky.jpg" "$DST/sky.jpg"
    SCN="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["scene"])' "$SRC/index.json")"
    cp -L "$SRC/scene/$SCN.sog" "$DST/scene/"
  done
  # a race: race.json, each pilot's DVR clip, the race audio, the sky and the scene it names
  for RC in $RACES; do
    SRC="$PUB/$RC"; DST="$UP/$RC"; mkdir -p "$DST/scene"
    VS="$(python3 -c 'import json,sys;print(" ".join(p["video"] for p in json.load(open(sys.argv[1]))["pilots"]))' "$SRC/race.json")"
    for f in race.json audio.m4a sky.jpg $VS; do
      cp -L "$SRC/$f" "$DST/$f"; done
    SCN="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["scene"])' "$SRC/race.json")"
    cp -L "$SRC/scene/$SCN.sog" "$DST/scene/"
  done
else
  mkdir -p "$UP/scene"
  for f in dvr_pinhole.mp4 dvr_pinhole.mp4.json poses60_refined15.json poses60_init15.json poses60_refined10.json poses60_cpr2.json poses60_cpr2_raw.json poses60_cpr_match.json scan_cameras.json marks.json sky.jpg; do
    cp "$DATA/$f" "$UP/$f"; done
  cp "$DATA/scene/JDL-2026-R6-fix-web.sog" "$DATA/scene/JDL-2026-R6-fix-web-edit.sog" "$DATA/scene/JDL-2026-R6-spirula-web-edit.sog" "$UP/scene/"
fi
du -sh "$UP"
rclone --s3-no-check-bucket copy --checksum --progress "$UP" "$REMOTE:$BUCKET/dvr/$NAME/data/"

say "page -> r2:$BUCKET/dvr/$NAME/"
# copy, not sync: data/ sits beside the page, and old hashed assets/ are harmless
rclone --s3-no-check-bucket copy --checksum "$PAGE" "$REMOTE:$BUCKET/dvr/$NAME/"
echo "https://ghostline.saqoo.sh/$NAME/"
