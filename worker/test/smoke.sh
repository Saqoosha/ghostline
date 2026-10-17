#!/usr/bin/env bash
# The relay end to end on wrangler dev (local R2 and Durable Object): a viewer's WebSocket gets what the sender posts,
# the heat list follows the heat's start and end, a heat upload lands in R2, the key is required, staging- is unlisted.
#   worker/test/smoke.sh        (needs worker/.dev.vars: cp .dev.vars.example .dev.vars; bun)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${PORT:-8787}; B=http://127.0.0.1:$PORT; E=staging-smoke; KEY=$(sed -n 's/^RELAY_KEY=//p' .dev.vars)
TMP=$(mktemp -d); trap 'kill $(jobs -p) 2>/dev/null; rm -rf "$TMP"' EXIT
npx wrangler dev --local --port "$PORT" --persist-to "$TMP/state" > "$TMP/dev.log" 2>&1 &
until curl -s -o /dev/null "$B/"; do sleep 0.5; done
fail() { echo "smoke FAILED: $*" >&2; cat "$TMP/dev.log" >&2; exit 1; }
post() { curl -s -o /dev/null -w '%{http_code}' -X POST -H "authorization: Bearer $KEY" -H 'content-type: application/json' --data "$1" "$B/$E/api/push"; }

bun -e "const w = new WebSocket(process.argv[1]); w.onmessage = e => console.log(e.data)" "ws://127.0.0.1:$PORT/$E/api/live" > "$TMP/ws.txt" &
sleep 1
T=$(($(date +%s) * 1000))
[ "$(post "{\"msgs\":[{\"k\":\"h\",\"t\":$T,\"id\":\"151700-h7\",\"heat\":\"7\",\"cls\":\"Expert\",\"cells\":{\"R2\":\"A\"},\"order\":[\"R2\",\"F1\"]}]}")" = 204 ] || fail "push h"
[ "$(post "{\"msgs\":[{\"k\":\"a\",\"s\":\"R2\",\"t\":$((T+10)),\"p\":[1,2,3],\"q\":[0,0,0,1],\"n\":100},{\"k\":\"a\",\"s\":\"R2\",\"t\":$((T+43)),\"p\":[1,2,4],\"q\":[0,0,0,1],\"n\":90}]}")" = 204 ] || fail "push a"
curl -s "$B/$E/heats.json" | grep -q '"live":{"id":"151700-h7"' || fail "heats.json live"
[ "$(post "{\"msgs\":[{\"k\":\"e\",\"t\":$((T+5000)),\"id\":\"151700-h7\"}]}")" = 204 ] || fail "push e"
sleep 1
grep -q '"backlog"' "$TMP/ws.txt" || fail "no backlog line"
grep -q '"p":\[1,2,4\]' "$TMP/ws.txt" || { cat "$TMP/ws.txt"; fail "answer not relayed"; }
curl -s "$B/$E/heats.json" | grep -q "\"live\":null,\"heats\":\[{\"id\":\"151700-h7\",\"start\":$T,\"end\":$((T+5000))" || fail "heats.json end"
echo '{"id":"151700-h7"}' | gzip > "$TMP/heat.gz"
ENTRY="{\"id\":\"151700-h7\",\"start\":$T,\"end\":$((T+5000)),\"heat\":\"7\",\"cls\":\"Expert\",\"pilots\":[\"A\",\"\"]}"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "authorization: Bearer $KEY" -H "x-heat-entry: $ENTRY" --data-binary @"$TMP/heat.gz" "$B/$E/api/heats/151700-h7")" = 204 ] || fail "put heat"
curl -s -D "$TMP/h.txt" --compressed "$B/$E/heats/151700-h7/live.json" > "$TMP/live.json"; grep -q 151700-h7 "$TMP/live.json" || { cat "$TMP/h.txt"; xxd "$TMP/live.json" | head -3; fail "live.json"; }
# the end's push dropped, the heat's upload arrives: the room closes the heat for new viewers and tells those watching
[ "$(post "{\"msgs\":[{\"k\":\"h\",\"t\":$((T+9000)),\"at\":$((T+9000)),\"id\":\"151800-h8\",\"heat\":\"8\",\"cls\":null,\"cells\":{},\"order\":[\"R2\"]}]}")" = 204 ] || fail "push h8"
ENTRY8="{\"id\":\"151800-h8\",\"start\":$((T+9000)),\"end\":$((T+20000)),\"heat\":\"8\",\"cls\":null,\"pilots\":[\"\"]}"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "authorization: Bearer $KEY" -H "x-heat-entry: $ENTRY8" --data-binary @"$TMP/heat.gz" "$B/$E/api/heats/151800-h8")" = 204 ] || fail "put h8"
sleep 1; grep -q '"k":"e","t":'$((T+20000))',"id":"151800-h8"' "$TMP/ws.txt" || { cat "$TMP/ws.txt"; fail "no end sent to viewers after the upload"; }
bun -e "const w = new WebSocket(process.argv[1]); w.onmessage = e => { console.log(e.data); process.exit(0) }" "ws://127.0.0.1:$PORT/$E/api/live" > "$TMP/ws2.txt"
grep -q '"heat":null' "$TMP/ws2.txt" || { cat "$TMP/ws2.txt"; fail "a new viewer still gets the uploaded heat as live"; }
! grep -q '"k":"h","t":'$T',"id":"151700-h7"' "$TMP/ws2.txt" || { cat "$TMP/ws2.txt"; fail "an ended heat's start is still in the backlog"; }
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST --data '{"msgs":[]}' "$B/$E/api/push")" = 401 ] || fail "push without key"
[ "$(curl -s -o /dev/null -w '%{http_code}' -X PUT -H "authorization: Bearer $KEY" -H "x-heat-entry: $ENTRY" --data x "$B/$E/api/heats/..%2Fx")" = 400 ] || fail "bad heat id"
! curl -s "$B/" | grep -q "$E" || fail "staging listed"
echo "smoke ok"
