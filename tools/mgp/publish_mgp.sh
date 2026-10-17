#!/usr/bin/env bash
# Publish the MGP page (viewer/mgp.html) with some rounds to https://ghostline.saqoo.sh/<name>/mgp.html
#   tools/mgp/publish_mgp.sh <name> <data dir> [round ...]     e.g. mgp-champs-2026 viewer/public/mgp-dev 44
# <data dir> holds rounds.json, one folder per round (race.json, the pilots' clips, audio.m4a), scene/<scene>.sog and
# sky.jpg; only the listed rounds go up (all when none are listed). Same R2 layout and credentials as
# tools/publish-dvr-viewer.sh: the page under dvr/<name>/, the data under dvr/<name>/data/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
NAME="${1:?name, e.g. mgp-champs-2026}"; DATA="${2:?data dir with rounds.json}"; shift 2; ROUNDS="$*"
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
( cd "$ROOT/viewer" && VITE_BASE="/$NAME/" VITE_MGP_DATA="data" bun run build )
PAGE="$TMP/page"; mkdir -p "$PAGE"; cp -R "$ROOT/viewer/dist/." "$PAGE/"
cp "$PAGE/mgp.html" "$PAGE/index.html"      # /<name>/ opens the MGP page itself, not the single-flight viewer

say "data"
UP="$TMP/data"; mkdir -p "$UP/scene"
# rounds.json with only the listed rounds; the folders and scenes it names
python3 - "$DATA/rounds.json" "$UP/rounds.json" $ROUNDS <<'PY'
import json, sys
j = json.load(open(sys.argv[1])); want = set(map(int, sys.argv[3:]))
if want: j["rounds"] = [r for r in j["rounds"] if r["round"] in want]
json.dump(j, open(sys.argv[2], "w"), indent=1)
print(" ".join(r["data"] for r in j["rounds"]))
PY
FOLDERS="$(python3 -c 'import json,sys;print(" ".join(r["data"] for r in json.load(open(sys.argv[1]))["rounds"]))' "$UP/rounds.json")"
[ -n "$FOLDERS" ] || { echo "no rounds selected" >&2; exit 1; }
for RD in $FOLDERS; do
  SRC="$DATA/$RD"; DST="$UP/$RD"; mkdir -p "$DST"
  VS="$(python3 -c 'import json,sys;print(" ".join(p["video"] for p in json.load(open(sys.argv[1]))["pilots"] if p.get("video")))' "$SRC/race.json")"
  for f in race.json $VS; do cp -L "$SRC/$f" "$DST/$f"; done
  [ -e "$SRC/audio.m4a" ] && cp -L "$SRC/audio.m4a" "$DST/audio.m4a"
  SCN="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["scene"])' "$SRC/race.json")"
  [ -e "$DATA/scene/$SCN.sog" ] && cp -L "$DATA/scene/$SCN.sog" "$UP/scene/"
done
# EXTRA_SCENES="<name> ...": scans no round names yet, uploaded too so the page's ?scene=<name> can show them for comparison
for SCN in ${EXTRA_SCENES:-}; do
  [[ "$SCN" =~ ^[A-Za-z0-9._-]+$ ]] && [ -e "$DATA/scene/$SCN.sog" ] && cp -L "$DATA/scene/$SCN.sog" "$UP/scene/"
done
[ -e "$DATA/sky.jpg" ] && cp -L "$DATA/sky.jpg" "$UP/sky.jpg"
du -sh "$UP"
rclone --s3-no-check-bucket copy --checksum --progress "$UP" "$REMOTE:$BUCKET/dvr/$NAME/data/"

say "page -> r2:$BUCKET/dvr/$NAME/"
rclone --s3-no-check-bucket copy --checksum "$PAGE" "$REMOTE:$BUCKET/dvr/$NAME/"
echo "https://ghostline.saqoo.sh/$NAME/mgp.html"
