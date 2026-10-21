#!/usr/bin/env bash
# Publish the landing page (site/index.html) to https://ghostline.saqoo.sh/ - R2 object dvr/index.html, served at /.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUCKET="vdgs"; REMOTE="${VDGS_R2_REMOTE:-r2}"; MOUNT="${VDGS_R2_ENV:-$HOME/.claude/1p-mounts/vdgs.env}"

# credentials: the same block as tools/publish-dvr-viewer.sh
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

rclone --s3-no-check-bucket copyto --checksum "$ROOT/site/index.html" "$REMOTE:$BUCKET/dvr/index.html"
echo "https://ghostline.saqoo.sh/"
