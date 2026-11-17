#!/usr/bin/env bash
# Publish a race day's audience pages to https://ghostline.saqoo.sh/<event>/ (worker/: the relay and R2).
#   tools/publish-event.sh <event> <scene folder> [title]   the pages (event.html as index.html, watch.html, heat.html) and the
#        day's data from the folder: index.json (its "title" set when given), scene/<scene>.sog, and sky.jpg, track.json,
#        countries.json when there. heats.json is created empty only if the day has none (the relay writes it).
#        Also scene/<scene>-lite.sog for phones: the folder's own, else made from the .sog (env LITE: splats, 800000)
#        and og.jpg, the link preview, when the folder has one (tools/og_unlabel.py, then tools/og_image.py)
#   tools/publish-event.sh <event> --track <file>           the course line alone ({"track": [[x, y, z], ...]}, rt_trackline.py
#        or make_race.py), as track.json: the live page reads it again every minute
#   tools/publish-event.sh <event> --night <folder>         the night rebuild (tools/dvr/night_heats.py): <folder>/<heat id>/
#        race.json and the pilots' clips, to heats/<heat id>/
# DRY_RUN=1 lists what would go up. The event name is the URL and the R2 prefix: staging-* days are not listed on /.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EV="${1:?event, e.g. jdl-2026-10-25}"; WHAT="${2:?scene folder, --track <file> or --night <folder>}"
[[ "$EV" =~ ^(staging-)?[a-z0-9][a-z0-9-]*$ ]] || { echo "not an event name: $EV" >&2; exit 2; }
# one thing per run: anything after the third argument (a second --night / --track) was ignored without a word, and the
# heats did not go up (UDGP 2026 R3, 2026-11-17); a title that is an option is the same mistake
[ $# -le 3 ] || { echo "one thing per run; run again for: ${*:4}" >&2; exit 2; }
[[ "$WHAT" == --* || "${3:-}" != --* ]] || { echo "the title looks like an option: $3 (one thing per run)" >&2; exit 2; }
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
    cp "$ROOT/viewer/dist/event.html" "$PAGE/index.html"; cp "$ROOT/viewer/dist/watch.html" "$ROOT/viewer/dist/heat.html" "$ROOT/viewer/dist/compare.html" "$PAGE/"
    cp -R "$ROOT/viewer/dist/assets" "$PAGE/"
    python3 -c 'import json,sys; j=json.load(open(sys.argv[1])); j.update({"title": sys.argv[3]} if sys.argv[3] else {}); json.dump(j, open(sys.argv[2], "w"))' "$SCENE/index.json" "$PAGE/index.json" "$TITLE"
    SC="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["scene"])' "$SCENE/index.json")"
    cp -L "$SCENE/scene/$SC.sog" "$PAGE/scene/"
    # the phones' scan (stage.ts reads it on a phone): LITE splats (800000), SH 1; reused when the folder has one
    if [ -e "$SCENE/scene/$SC-lite.sog" ]; then cp -L "$SCENE/scene/$SC-lite.sog" "$PAGE/scene/"
    else
      ST=(npx -y @playcanvas/splat-transform@3.10.2 --no-tty -w)
      "${ST[@]}" "$SCENE/scene/$SC.sog" -H 1 --decimate-adaptive "${LITE:-800000}" "$TMP/lite.ply"   # decimate writes only .ply
      "${ST[@]}" "$TMP/lite.ply" "$PAGE/scene/$SC-lite.sog"; rm -f "$TMP/lite.ply"
    fi
    [ -s "$PAGE/scene/$SC-lite.sog" ] || { echo "no $SC-lite.sog: phones would get no scan" >&2; exit 1; }
    python3 -c 'import json,sys; j=json.load(open(sys.argv[1])); j["lite"]=sys.argv[2]; json.dump(j, open(sys.argv[1], "w"))' "$PAGE/index.json" "$SC-lite"
    for f in sky.jpg track.json countries.json; do [ -e "$SCENE/$f" ] && cp -L "$SCENE/$f" "$PAGE/$f"; done
    # the sky (a scan trained without it, tools/x6/jdl-r7-sky.sh): stage.ts draws it as the skybox when index.json names it
    [ -e "$PAGE/sky.jpg" ] && python3 -c 'import json,sys; j=json.load(open(sys.argv[1])); j["sky"]="sky.jpg"; json.dump(j, open(sys.argv[1], "w"))' "$PAGE/index.json"
    # the link preview: og.jpg (1200x630) in the folder goes up, and every page names it with the day's title (absolute URLs)
    if [ -e "$SCENE/og.jpg" ]; then
      cp -L "$SCENE/og.jpg" "$PAGE/og.jpg"
      python3 - "$PAGE" "$EV" <<'PY'
import hashlib, html, json, struct, sys
page, ev = sys.argv[1:3]; url = f"https://ghostline.saqoo.sh/{ev}/"
t = html.escape(json.load(open(f"{page}/index.json")).get("title") or ev)
d = "FPV race flights replayed over a 3D Gaussian-splat scan of the venue."
jpg = open(f"{page}/og.jpg", "rb").read(); i = 2
try:
    while jpg[i + 1] not in (0xC0, 0xC1, 0xC2): i += 2 + struct.unpack(">H", jpg[i + 2:i + 4])[0]   # the frame header: the size
    h, w = struct.unpack(">HH", jpg[i + 5:i + 9])
except (IndexError, struct.error): sys.exit("og.jpg is not a baseline / progressive JPEG (tools/og_image.py makes one)")
assert (w, h) == (1200, 630), f"og.jpg is {w}x{h}, not 1200x630 (tools/og_image.py)"
img = f"{url}og.jpg?v={hashlib.sha1(jpg).hexdigest()[:8]}"   # a new image is a new URL (og.jpg is cached a day)
for f in ("index.html", "heat.html", "watch.html", "compare.html"):   # og:url on the entry only: a heat's link keeps its ?h=
    s = open(f"{page}/{f}").read()
    meta = (f'<meta property="og:type" content="website"><meta property="og:site_name" content="ghostline">'
            f'<meta property="og:title" content="{t} · ghostline"><meta property="og:description" content="{html.escape(d)}">'
            + (f'<meta property="og:url" content="{url}">' if f == "index.html" else "") + f'<meta property="og:image" content="{img}">'
            f'<meta property="og:image:width" content="1200"><meta property="og:image:height" content="630">'
            f'<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{img}">')
    assert s.count("</head>") == 1 and s.count("<title>ghostline") == 1, f
    s = s.replace("<title>ghostline", f"<title>{t} · ghostline")   # the tab and the shared link name the day
    open(f"{page}/{f}", "w").write(s.replace("</head>", meta + "</head>"))
PY
    fi
    # the heat list belongs to the relay once the day starts: an empty one goes up only where there is none
    # (--ignore-existing: a failed listing must not read as "none" and overwrite the day's heats)
    mkdir -p "$TMP/list"
    python3 -c 'import json,sys; t=json.load(open(sys.argv[1])).get("title") or sys.argv[2]; json.dump({"event": sys.argv[2], "title": t, "live": None, "heats": []}, open(sys.argv[3], "w"))' "$PAGE/index.json" "$EV" "$TMP/list/heats.json"
    du -sh "$PAGE"; ls -R "$PAGE" | head -40
    # index.json last: it names the scans, and a phone reading it before its lite scan is up gets none
    say "pages and data -> $DEST/"; up --progress --exclude index.json "$PAGE" "$DEST/"; up "$PAGE/index.json" "$DEST/"; up --ignore-existing "$TMP/list/heats.json" "$DEST/" ;;
esac
echo "https://ghostline.saqoo.sh/$EV/"
