#!/usr/bin/env bash
# Publish the DVR viewer for one capture to https://vdgs.saqoo.sh/dvr/<name>/.
#   tools/publish-dvr-viewer.sh <name> <data dir>      e.g. jdl-2026-r6 build/dvr/hdz_0067
# The page (viewer/, built with base /dvr/<name>/) goes into the site as a static asset and
# is kept in build/dvr-viewer/<name> so make-catalog.sh can put it back; the data (scene .sog,
# the pinhole video and its json, the pose sets, scan_cameras.json, marks.json) goes to R2 under
# dvr/<name>/data/ with rclone, the same way tools/publish.sh sends captures. The scan's proxy
# videos are not published on purpose. Then the Worker is deployed.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${1:?name, e.g. jdl-2026-r6}"; DATA="${2:?data dir, e.g. build/dvr/hdz_0067}"
SITE="$ROOT/build/release/site"; KEEP="$ROOT/build/dvr-viewer/$NAME"
BUCKET="vdgs"; REMOTE="${VDGS_R2_REMOTE:-r2}"; MOUNT="${VDGS_R2_ENV:-$HOME/.claude/1p-mounts/vdgs.env}"
say() { printf '\n== %s ==\n' "$1"; }

say "build the page"
( cd "$ROOT/viewer" && VITE_BASE="/dvr/$NAME/" bun run build )
rm -rf "$KEEP"; mkdir -p "$KEEP"; cp -R "$ROOT/viewer/dist/." "$KEEP/"
[ -d "$SITE" ] || { echo "no $SITE - run tools/make-catalog.sh first" >&2; exit 1; }
rm -rf "$SITE/dvr/$NAME"; mkdir -p "$SITE/dvr"; cp -R "$KEEP" "$SITE/dvr/$NAME"

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

say "data -> r2:$BUCKET/dvr/$NAME/data/"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT INT TERM
mkdir -p "$TMP/scene"
for f in dvr_pinhole.mp4 dvr_pinhole.mp4.json poses60_refined15.json poses60_init15.json poses60_refined10.json scan_cameras.json marks.json sky.jpg; do
  cp "$DATA/$f" "$TMP/$f"; done
cp "$DATA/scene/JDL-2026-R6-fix-web.sog" "$DATA/scene/JDL-2026-R6-fix-web-edit.sog" "$DATA/scene/JDL-2026-R6-spirula-web-edit.sog" "$TMP/scene/"
du -sh "$TMP"
rclone --s3-no-check-bucket copy --checksum --progress "$TMP" "$REMOTE:$BUCKET/dvr/$NAME/data/"

say "deploy"
( cd "$ROOT/worker" && npx wrangler deploy )
echo "https://vdgs.saqoo.sh/dvr/$NAME/"
