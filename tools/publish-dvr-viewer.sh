#!/usr/bin/env bash
# Publish the DVR viewer for one capture to https://vdgs.saqoo.sh/dvr/<name>/.
#   tools/publish-dvr-viewer.sh <name> <data dir>      e.g. jdl-2026-r6 build/dvr/hdz_0067
#   tools/publish-dvr-viewer.sh <name> <flights.json>  several flights, e.g. fdf-2026-r6 viewer/public/flights.json:
#     each flight's folder (viewer/public/<data>) goes to dvr/<name>/data/<data>/ - the files its index.json lists, the
#     video, scan_cameras.json, sky.jpg and its scene - and the page opens the first one when the URL names none.
#     Folders under "races" (race.html) go the same way: race.json, the pilots' clips, audio.m4a, sky.jpg, the scene.
# The page (viewer/, built with base /dvr/<name>/) goes into the site as a static asset and
# is kept in build/dvr-viewer/<name> so make-catalog.sh can put it back; the data (scene .sog,
# the pinhole video and its json, the pose sets, scan_cameras.json; marks.json for one flight) goes to R2 under
# dvr/<name>/data/ with rclone, the same way tools/publish.sh sends captures. The scan's proxy
# videos are not published on purpose. Then the Worker is deployed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:?name, e.g. jdl-2026-r6}"; DATA="${2:?data dir, e.g. build/dvr/hdz_0067}"
# A folder name and nothing else: it is joined into rm -rf paths below ("..", "a/b") and
# into the comma list that lets a viewer change (",").
[[ "$NAME" =~ ^[a-z0-9][a-z0-9._-]*$ ]] || { echo "not a viewer name: $NAME" >&2; exit 2; }
SITE="$ROOT/build/release/site"; KEEP="$ROOT/build/dvr-viewer/$NAME"
BUCKET="vdgs"; REMOTE="${VDGS_R2_REMOTE:-r2}"; MOUNT="${VDGS_R2_ENV:-$HOME/.claude/1p-mounts/vdgs.env}"
say() { printf '\n== %s ==\n' "$1"; }

[ -d "$SITE" ] || { echo "no $SITE - run tools/make-catalog.sh first" >&2; exit 1; }

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

# The deploy below replaces the whole site from this checkout, not just dvr/$NAME: the
# catalog, the front page and every other viewer go out as they are here. Each of those must
# already be what is live, or this publish reverts it (or, with no copy, removes it) with no
# error anywhere. Checked before the upload, and again before the deploy, because the scene
# files take minutes and the site can be published from another checkout meanwhile.
# Each step returns its own status rather than relying on set -e: called as `f || ...`
# below, bash ignores errexit inside f, and a refusal from the site check would then be
# overwritten by the viewer check's success.
# Only $NAME may change - not whatever VDGS_VIEWER_CHANGE a publish.sh run left exported.
check_the_rest() {
  rclone --s3-no-check-bucket lsjson --recursive --files-only --include "dvr/**" \
    "$REMOTE:$BUCKET" > "$TMP/dvr.json" || {
    echo "   could not list $REMOTE:$BUCKET/dvr - refusing to deploy blind" >&2; return 1; }
  python3 "$ROOT/tools/check_live_site.py" "$SITE" || return 1
  VDGS_VIEWER_CHANGE="$NAME" python3 "$ROOT/tools/check_live_viewers.py" "$TMP/dvr.json" "$SITE" || return 1
  # The deploy ships this checkout's Worker too - the routing to R2 - not only the site.
  bash "$ROOT/tools/check_worker_source.sh" || return 1
}
# Before the build, not after: a refusal must leave build/dvr-viewer/$NAME and the site as
# they were, or the next publish.sh from here finds an unpublished build in them. The check
# does not need the new page - $NAME is the one viewer allowed to change.
say "checking what else the deploy would change"
check_the_rest || exit 1

say "build the page"
MULTI=; [[ "$DATA" == *flights.json ]] && MULTI=1
DEFAULT=; [ -n "$MULTI" ] && DEFAULT="data/$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["flights"][0]["data"])' "$DATA")"
# race pages (race.html) listed under "races"; the page opens the first
RACES=; [ -n "$MULTI" ] && RACES="$(python3 -c 'import json,sys;print(" ".join(json.load(open(sys.argv[1])).get("races", [])))' "$DATA")"
RACE=; [ -n "$RACES" ] && RACE="data/${RACES%% *}"
( cd "$ROOT/viewer" && VITE_BASE="/dvr/$NAME/" VITE_DEFAULT_DATA="$DEFAULT" VITE_RACE_DATA="$RACE" bun run build )
rm -rf "$KEEP"; mkdir -p "$KEEP"; cp -R "$ROOT/viewer/dist/." "$KEEP/"
# the flight menu: the same list with every folder moved under data/, where the Worker serves R2 from
[ -n "$MULTI" ] && python3 - "$DATA" "$KEEP/flights.json" <<'PY'
import json, sys
j = json.load(open(sys.argv[1]))
for f in j["flights"]: f["data"] = "data/" + f["data"]
json.dump(j, open(sys.argv[2], "w"), indent=1)
PY
rm -rf "$SITE/dvr/$NAME"; mkdir -p "$SITE/dvr"; cp -R "$KEEP" "$SITE/dvr/$NAME"

say "data -> r2:$BUCKET/dvr/$NAME/data/"
# A folder of its own: the whole of it is uploaded, and $TMP also holds the check's bucket
# listing, which would otherwise be published as dvr/$NAME/data/dvr.json.
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

say "checking again, just before the deploy"
check_the_rest || {
  echo "   The data for dvr/$NAME is already in R2, so the live page is now reading it." >&2
  echo "   Resolve the above and run this again, or the page and its data stay mismatched." >&2
  exit 1; }

say "deploy"
( cd "$ROOT/worker" && npx wrangler deploy )
echo "https://vdgs.saqoo.sh/dvr/$NAME/"
