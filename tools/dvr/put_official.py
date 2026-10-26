"""official laps (official_laps.py's JSON) into each heat's published replay: heats/<id>/race.json and live.json get
"official": {session, cells: {cell: {name, laps, ends, holeshot, laps_driven, best}}}. Several sessions' JSON merged into one
may carry "sessions": {heat id: session}. Runs on the Mac (rclone and the R2 key, ~/.claude/1p-mounts/vdgs.env).
python put_official.py <json> <event>"""
import gzip, json, subprocess, sys
src, ev = json.load(open(sys.argv[1])), sys.argv[2]; R = f"r2:vdgs/dvr/{ev}/heats"
files = set(subprocess.run(["rclone", "lsf", "-R", R + "/"], capture_output=True, text=True, check=True).stdout.split())
def put(p, data, enc=None):
    h = ["--header-upload", "Content-Type: application/json"] + (["--header-upload", f"Content-Encoding: {enc}"] if enc else [])
    subprocess.run(["rclone", "rcat", "--s3-no-check-bucket", *h, f"{R}/{p}"], input=data, check=True)
for hid, cells in src["heats"].items():
    o = dict(session=src.get("sessions", {}).get(hid, src["session"]), cells=cells)
    if f"{hid}/race.json" in files:
        r = json.loads(subprocess.run(["rclone", "cat", f"{R}/{hid}/race.json"], capture_output=True, check=True).stdout); r["official"] = o
        put(f"{hid}/race.json", json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode())
    if f"{hid}/live.json" in files:
        r = json.loads(subprocess.run(["rclone", "cat", "--s3-decompress", f"{R}/{hid}/live.json"], capture_output=True, check=True).stdout); r["official"] = o
        put(f"{hid}/live.json", gzip.compress(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode()), "gzip")
    print(hid, "race" if f"{hid}/race.json" in files else "-", "live" if f"{hid}/live.json" in files else "-", list(cells))
