# 観客向けビューア 実装計画

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 会場の tracker の答えを Cloudflare の中継で LAN の外へ出し、ghostline.saqoo.sh で大会の 1 日の入口・ライブ（遅れ付き）・ヒート直後のリプレイ・夜の DVR 付きリプレイを見られるようにする。

**Architecture:** rt4090 の新しい送り手 `rt_relay.py` が tracker の PUSH（SSE）を読み、ヒートを区切り、答えを 100 ms ごとに Worker へ POST する。Worker の Durable Object（大会の 1 日に 1 つ）が直近 60 秒を持って見る人の WebSocket に配り、ヒートの終わりに答えを R2 に置き、ヒートの一覧 `heats.json` を書く。ページは viewer（Vite＋PlayCanvas）に 3 枚足す：入口 `event.html`、ライブ `watch.html`、リプレイ `heat.html`。

**Tech Stack:** Python 3（mastenv、標準ライブラリだけ）、Cloudflare Workers＋Durable Objects（SQLite 版）＋R2、TypeScript、Vite 7、PlayCanvas 2.12、テストは `python -m unittest` と `bun test`。

**Spec:** [docs/superpowers/specs/2026-10-17-audience-viewer-design.md](../specs/2026-10-17-audience-viewer-design.md)

## Global Constraints

- 見る人が 1 人でも 1,000 人でも、会場が出すのは 1 本の流れだけ（rt4090 → Worker の POST のみ。見る人は Cloudflare にだけつなぐ）
- 送り手は tracker を止めも遅くもしない：tracker と別スレッド、送れない分は捨てる（ためない）。ただしヒートの答えはディスクに残す（tracker が落ちると `live/<マス>.jsonl` は書かれない）
- 遅れを付けた人には、答え・名前・ヒートの切り替え・状態（FLYING／LOST／NO PICTURE）・入口の一覧・「前のヒート」を全部同じだけ遅らせて出す
- DELAY は 0〜60 秒、0.5 秒刻み、目安ボタン「低遅延 約 7 秒」「通常 約 20 秒」、ブラウザに覚えさせる（`localStorage`、失敗しても動く）
- ページに「位置は DVR の映像から推定したもの」と書く。運営の公式に見える言い方をしない
- 見た目は `tools/dvr/rt_control.html` の作法：`:root` の `--paper --ink --dim --faint --hair --spirit --alarm --tl --tr --bl --br --sans --mono`、`.mark`（ghost —— line）、端末の字体だけ。**ライトだけ、ダークモードは作らない**
- マスの色はマスの位置（tl tr bl br）で決まる。FDF の構成では R2=tl、F1=tr、R4=bl、R5=br
- 大会の 1 日の名前は `^(staging-)?[a-z0-9][a-z0-9-]*$`（例 `jdl-2026-10-25`）。`staging-` は入口の一覧（Worker の `/`）に出さない
- 置き場所は R2 バケット `vdgs` の `dvr/<大会>/`。ヒートは `heats/<HHMMSS>-h<番号>/`（番号が無い日は `<HHMMSS>`、JST）
- Worker の今の GET の振る舞い（R2 の読み出し、Range、`/` の一覧）は変えない
- 書き込み口（`/<大会>/api/*`）は `Authorization: Bearer <RELAY_KEY>` で守る。鍵は Worker の secret `RELAY_KEY`、rt4090 は `~/.config/ghostline/key`（600）。値を画面・ログ・コミットに出さない
- コミット・push・`wrangler deploy`・`systemctl restart ghostline-control` は Saqoosha の OK のあと。rt4090 で GPU を使う試験は `systemctl is-active kf1017` が inactive になってから
- 最初はパソコンの画面。スマホは崩れなければいい

## 流れの形（全タスク共通）

送り手 → Worker：`POST /<大会>/api/push`、本文 `{"sent": <壁の ms>, "msgs": [Msg, ...]}`。Msg は 4 種類、`t` は rt4090 の壁の時計の ms（chrony で合っている前提。Task 9 で確かめる）：

| `k` | 形 | 意味 |
|---|---|---|
| `a` | `{k, s, t, p:[x,y,z], q:[qx,qy,qz,qw], n}` | 答え。`s` はマスの名前（R2 など）、`t` はフレームが tracker に届いた時刻（受けた時刻 − `lat`）、位置は小数 3 桁、向きは 4 桁、`n` は inlier 数。BA の改訂を待って 0.5 秒遅らせて送る |
| `x` | `{k, s, t}` | 絵はあるが解けなかったフレーム（LOST）。マスごとに 0.5 秒に 1 回まで |
| `h` | `{k, t, id, heat, cls, cells:{R2:"名前",...}, order:["R2","F1","R4","R5"]}` | ヒートの始まり。名前が途中で変わったら同じ `id` でもう一度 |
| `e` | `{k, t, id}` | ヒートの終わり |

Worker → 見る人（WebSocket `GET /<大会>/api/live`）：つないだ直後に `{"now": <ms>, "backlog": [Msg...], "heat": <最後の h か null>}`、以後 `{"now": <ms>, "msgs": [Msg...]}`。`now` はページが時計のずれを見積もるのに使う。

ヒートの答え（`PUT /<大会>/api/heats/<id>`、gzip の JSON、R2 の `heats/<id>/live.json`）：

```ts
type HeatRecord = { id: string; start: number; end: number; heat: string | null; cls: string | null
  cells: Record<string, string>; order: string[]; answers: Record<string, number[][]> }   // 行は [t, x, y, z, qx, qy, qz, qw, n]
```

一覧（R2 の `heats.json`、Durable Object だけが書く）：

```ts
type HeatIndex = { event: string; title: string; live: HeatEntry | null; heats: HeatEntry[] }   // heats は新しい順
type HeatEntry = { id: string; start: number; end: number | null; heat: string | null; cls: string | null; pilots: string[] }
```

## Review Focus

1. **ヒート管理に届かない日**（FDF の午後）：`heat`／`cls` が null、名前が空。入口・ライブ・リプレイは時刻（「15:17」）とマス名（R2 など）で表示し、空の名前で崩れない。→ Task 2 と Task 5 にテスト
2. **ヒートの途中で tracker が落ちる・再起動する**（2026-10-17 に 2 回）：PUSH が切れても送り手はヒートを閉じない（30 秒の区切りは「行が来ない」で数える）、再接続後の行は同じヒートに続く。送り手が持つディスクの記録は残る。→ Task 2 にテスト
3. **会場の回線が切れる・遅い**：POST の失敗で送り手が止まらず、ためずに捨てる。ヒートの答えの PUT だけはディスクから後で送り直す。→ Task 2 にテスト
4. **遅れを付けた人がヒートの切り替えの前後に開く**：backlog の 60 秒の中に `h`／`e` があるとき、遅れた時刻で正しいヒートと名前が出る（新しいヒートの名前が先に出ない）。→ Task 4 にテスト
5. **遅れが 60 秒のときの入口の一覧**：終わって 40 秒のヒートは、まだ LIVE として出し、リプレイのリンクを出さない。→ Task 5 にテスト

---

### Task 1: Worker に中継と書き込み口を足す

**Files:**
- Create: `worker/src/room.ts`（Durable Object `LiveRoom`）
- Create: `worker/src/api.ts`（純粋な部品：`parseEventPath`、`authorized`、`applyToIndex`）
- Modify: `worker/src/index.ts`（`/<大会>/api/*` を room へ、`/` の一覧から `staging-` を外す。ほかの GET は今のまま）
- Modify: `worker/wrangler.jsonc`（`durable_objects.bindings` に `ROOM`／`LiveRoom`、`migrations` に `new_sqlite_classes: ["LiveRoom"]`）
- Create: `worker/package.json`（devDependencies：`wrangler`、`@cloudflare/workers-types`、`typescript`）、`worker/tsconfig.json`
- Create: `worker/.dev.vars.example`（`RELAY_KEY=dev-only-key`）、`.gitignore` に `worker/.dev.vars`
- Test: `worker/test/api.test.ts`、`worker/test/smoke.sh`

**Interfaces:**
- Produces（HTTP）：`POST /<大会>/api/push`（204）、`GET /<大会>/api/live`（WebSocket の Upgrade、101）、`PUT /<大会>/api/heats/<id>`（204）。鍵が違えば 401、大会名が違えば 404、`id` が `^\d{6}(-h[\w-]{1,16})?$` でなければ 400
- Produces（TS）：`parseEventPath(path: string): { event: string; rest: string } | null`、`authorized(req: Request, key: string): boolean`（定数時間の比較）、`applyToIndex(ix: HeatIndex, m: Msg): HeatIndex`（`h` で `live` を作るか名前を更新、`e` で `live` を `heats` の先頭へ移して `end` を入れる）
- `LiveRoom` は WebSocket Hibernation（`state.acceptWebSocket`）で見る人を持つ。直近 60 秒の Msg をメモリの配列で持ち（`t` が `now - 60000` より古いものを捨てる）、push を受けるたびに全員へ 1 回送る。`h` と `e` を受けたら `applyToIndex` して `heats.json` を R2 に書く（書くのはこの 2 つの時だけ）。最後の `h` は storage にも置く（DO が寝ても新しい人に渡す）
- PUT は本文をそのまま `heats/<id>/live.json`（`content-encoding: gzip`、`content-type: application/json`）に書く

- [ ] **Step 1: 失敗するテストを書く**（`worker/test/api.test.ts`）

```ts
test('parseEventPath', () => {
  expect(parseEventPath('/jdl-2026-10-25/api/push')).toEqual({ event: 'jdl-2026-10-25', rest: 'push' })
  expect(parseEventPath('/staging-fdf-2026-10-17/api/live')).toEqual({ event: 'staging-fdf-2026-10-17', rest: 'live' })
  expect(parseEventPath('/Bad_Name/api/push')).toBeNull()
  expect(parseEventPath('/jdl-2026-10-25/race.json')).toBeNull()
})
test('authorized', () => {
  const r = (h?: string) => new Request('https://x/', { headers: h ? { authorization: h } : {} })
  expect(authorized(r('Bearer k1'), 'k1')).toBe(true)
  expect(authorized(r('Bearer k2'), 'k1')).toBe(false)
  expect(authorized(r(), 'k1')).toBe(false)
  expect(authorized(r('Bearer '), '')).toBe(false)          // secret が未設定なら誰も通さない
})
test('applyToIndex: start, rename, end', () => {
  let ix: HeatIndex = { event: 'e', title: 'e', live: null, heats: [] }
  ix = applyToIndex(ix, { k: 'h', t: 1000, id: '151700-h7', heat: '7', cls: 'Expert', cells: { R2: 'A', F1: '' }, order: ['R2', 'F1'] })
  expect(ix.live).toEqual({ id: '151700-h7', start: 1000, end: null, heat: '7', cls: 'Expert', pilots: ['A', ''] })
  ix = applyToIndex(ix, { k: 'h', t: 2000, id: '151700-h7', heat: '7', cls: 'Expert', cells: { R2: 'A', F1: 'B' }, order: ['R2', 'F1'] })
  expect(ix.live!.start).toBe(1000); expect(ix.live!.pilots).toEqual(['A', 'B'])
  ix = applyToIndex(ix, { k: 'e', t: 9000, id: '151700-h7' })
  expect(ix.live).toBeNull(); expect(ix.heats[0]).toMatchObject({ id: '151700-h7', end: 9000 })
  expect(applyToIndex(ix, { k: 'e', t: 9500, id: 'other' })).toEqual(ix)   // 知らない id の終わりは無視
})
```

- [ ] **Step 2: 失敗を確かめる** — `cd worker && bun test` → `parseEventPath` が無くて FAIL
- [ ] **Step 3: `api.ts`、`room.ts`、`index.ts` の分岐、`wrangler.jsonc` を書く**（大会名を `env.ROOM.idFromName(event)` に渡す。DO から R2 へは同じ `BUCKET` の binding）
- [ ] **Step 4: テストを通す** — `cd worker && bun test` → 全部 PASS、`npx tsc --noEmit` がエラー無し
- [ ] **Step 5: 通しの smoke を書いて回す**（`worker/test/smoke.sh`：`.dev.vars` の鍵で `npx wrangler dev --local --port 8787` を裏で起動 → `websocat ws://127.0.0.1:8787/staging-smoke/api/live` を裏で開く → `h`、`a` 2 つ、`e` を POST → WebSocket に 2 行目以降で届く、`curl /staging-smoke/heats.json` の `heats[0].end` が入っている、鍵なしの POST が 401、`curl /` に `staging-smoke` が無い）。Expected: `smoke ok`
- [ ] **Step 6: Commit**（Saqoosha の OK のあと）

### Task 2: 送り手 `rt_relay.py`（ヒートの区切り・送信・ディスクの記録）

**Files:**
- Create: `tools/dvr/rt_relay.py`
- Test: `tools/dvr/test_rt_relay.py`

**Interfaces:**
- Produces：`class Heats`（純粋、時計を引数で受ける）
  - `Heats(gap=30.0, order=("R2","F1","R4","R5"))`
  - `.row(now: float, stream: str) -> list[dict]`：PUSH の 1 行ごと。ヒートが無ければ `h` を返して始める
  - `.names(now: float, p: dict) -> list[dict]`：`/api/pilots` と同じ形（`{cells, heat, cls, stale}`）。開いているヒートの名前・番号・クラスが変わったら同じ `id` の `h` を返す。**番号やクラスが変わってもヒートは区切らない**（FDF では「7 Expert」が 30 分以上続いた。区切りは絵の有無が主）。ただし変わった時に開いているヒートの行が 30 秒以上来ていなければ終わりにする
  - `.tick(now: float) -> list[dict]`：最後の行から `gap` 秒過ぎたら `e` を返して閉じる
  - `id` は開始時刻の JST の `HHMMSS`、番号があれば `-h<番号>`（番号の文字は `[\w-]` だけ残す）
- Produces：`class Relay(push_url, base_url, event, key, pilots=callable|None, out_dir="live/relay")`、`.start()` でスレッドを 3 本：PUSH を読む（切れたら 1 秒後につなぎ直す）、100 ms ごとの送信、1 秒ごとの `tick`。送信は `urllib` の POST、timeout 2 秒、失敗した分は捨てる。答えは `p` が来てから 0.5 秒持ち、その間に届いた行の `win`（`[frame, x,y,z, qx,qy,qz,qw]`）で同じフレームの位置を差し替えて `a` にする
- ディスク：ヒートごとに `live/relay/<日付>/<id>.json`（`HeatRecord`、`a` の行を全部、10 秒ごとに書き直す）、`live/heats-<日付>.jsonl`（`h`／`e` を 1 行ずつ追記。夜の作り直しが読む）。`e` のあと `PUT /<大会>/api/heats/<id>` を gzip で送り、失敗したら 30 秒ごとに 10 分まで送り直す。送れていないファイルは `.pending` を付けて残し、起動時に送り直す
- CLI：`python rt_relay.py --push http://127.0.0.1:8765 --base https://ghostline.saqoo.sh --event staging-fdf-2026-10-17 [--pilots http://127.0.0.1:8080/api/pilots]`。鍵は `~/.config/ghostline/key` から読む

- [ ] **Step 1: 失敗するテストを書く**（`test_rt_relay.py`、`unittest`）
  - `test_first_row_starts_heat`：`Heats().row(t0, "R2")` が `k=="h"`、`id` が `t0` の JST の `HHMMSS`、`heat is None`、`cells=={}`
  - `test_gap_ends_heat`：行が t0〜t0+60 秒に 1 秒ごと → `tick(t0+89)` は `[]`、`tick(t0+90.1)` は `[{"k":"e", "id":...}]`
  - `test_tracker_restart_keeps_heat`：t0 の行のあと 20 秒何も来ず、t0+20 の行 → `h` は増えない（同じヒート）
  - `test_names_rename_same_id`：`names(.., {"cells":{"R2":"A"},"heat":"7","cls":"Expert"})` が同じ `id` の `h`（`heat=="7"`）。同じ値で 2 回目は `[]`
  - `test_heat_number_change_alone_does_not_split`：飛行中に番号が 7→8 → `h` が 1 つ（`id` は変わらない）、`e` は出ない
  - `test_heat_manager_down`：`names(.., {})` と `stale=True` で何も変えない
  - `test_post_failure_drops`：`Relay` の送信を例外を投げる関数に差し替え、200 行入れて 1 回送る → 例外が外に出ず、送り待ちが 0 件
  - `test_revision_applied`：`p` のある行（i=10）のあと 0.3 秒後に `win=[[10, 1,2,3, 0,0,0,1]]` の行 → 0.5 秒後に出る `a` の `p==[1,2,3]`
- [ ] **Step 2: 失敗を確かめる** — `cd tools/dvr && python -m unittest test_rt_relay -v` → ImportError で FAIL
- [ ] **Step 3: `rt_relay.py` を書く**（標準ライブラリだけ。`time.time()` は `Relay` だけが呼び、`Heats` には渡す）
- [ ] **Step 4: テストを通す** — 同じコマンドで 8 件 PASS
- [ ] **Step 5: Mac で通す**：Task 1 の `wrangler dev` に、手元の偽 PUSH（`python -c` の小さな SSE サーバーで `data/dvr` の `live` の答えを 30 Hz で流す。台本は `tools/dvr/test_rt_relay.py` の `fake_push()` に置く）をつなぎ、WebSocket に `a` が 10 Hz の塊で届くこと、流れを止めて 30 秒後に `heats.json` にヒートが入り `heats/<id>/live.json` が gzip で読めることを見る
- [ ] **Step 6: Commit**

### Task 3: `rt_control.py` から送り手を起動する

**Files:**
- Modify: `tools/dvr/rt_control.py`（docstring の env に `RELAY_BASE`・`RELAY_EVENT` を足し、両方あって鍵のファイルがあれば起動時に `Relay(f"http://127.0.0.1:{PUSH}/", RELAY_BASE, RELAY_EVENT, key, pilots=pilots).start()`。tracker の開始・停止とは独立に、ずっと PUSH をつなぎ直し続ける）
- Modify: `tools/dvr/rt_control.html`（上の帯に送り手の状態を 1 語：`RELAY` の横に「送信中／未送信 N 秒／切」）
- Modify: `rt_control.py` の `state()` に `relay=dict(ok, last_sent_age, event)`

**Interfaces:**
- Consumes：Task 2 の `Relay`（`.status() -> dict(ok: bool, age: float|None, event: str)` をこのタスクで足す）
- 鍵が無い・`RELAY_*` が無いときは何もしない（今の会場の動きと同じ）

- [ ] **Step 1:** `Relay.status()` のテスト（`test_status_before_any_send`：`ok False`、`age None`）を `test_rt_relay.py` に足して FAIL を見る
- [ ] **Step 2:** `status()` と `rt_control.py`・`rt_control.html` の変更を書く
- [ ] **Step 3:** `python -m unittest test_rt_relay -v` が PASS、`python -c "import ast; ast.parse(open('rt_control.py').read())"` が通る
- [ ] **Step 4:** rt4090 に置く手順を `docs/realtime-tracking.ja.md` の「会場の操作ページ」に 3 行で足す：`scp tools/dvr/rt_relay.py tools/dvr/rt_control.* rt4090:~/rt/`、`sudo systemctl edit ghostline-control` で `Environment=RELAY_BASE=https://ghostline.saqoo.sh RELAY_EVENT=<大会> PILOTS_URL=<ヒート管理>`、再起動は tracker が止まっているときだけ
- [ ] **Step 5: Commit**

### Task 4: ページの共通部品（遅れの時計・3D の舞台・見た目）

**Files:**
- Create: `viewer/src/aud/delay.ts`（純粋）
- Create: `viewer/src/aud/stage.ts`（`livepage.ts` から写す：PlayCanvas の app、シーンの読み込み、コースの線、Orbit／Top／Follow のカメラ、球・軌跡・名前タグ。Free と BA・スライダーは持たない）
- Create: `viewer/src/aud/flags.ts`（`livepage.ts` の `KR`・`country`・`flagOf` を写す）
- Create: `viewer/src/aud/look.css`（`rt_control.html` の `:root`、`.mark`、上の帯・下の帯・出場者の欄・タグ）
- Test: `viewer/src/aud/delay.test.ts`、`viewer/src/aud/flags.test.ts`
- Modify: `viewer/package.json`（`"test": "bun test src/aud"`）

**Interfaces:**
- Produces（`delay.ts`）：
  - `class ServerClock { see(serverNow: number, localNow: number): void; now(localNow: number): number }`：直近 30 秒の `(serverNow − localNow)` の最大を使う（遅れて届いた分で時計が遅れないように）
  - `class Timeline { push(msgs: Msg[]): void; until(t: number): Msg[]; heatAt(t: number): HMsg | null; endedBy(t: number): Set<string> }`：受けた Msg を `t` 順に持つ（60 秒＋最大の遅れ 60 秒＝ 120 秒より古い `a`／`x` は捨てる。`h`／`e` は残す）。`until(t)` は前回からの増分
  - `const DELAY = { min: 0, max: 60, step: 0.5, presets: [["低遅延", 7], ["通常", 20]] }`、`loadDelay(event: string): number`、`saveDelay(event: string, s: number): void`（キー `ghostline-delay:<大会>`、try/catch）
  - `type Msg`（「流れの形」の 4 種類）を export
- Produces（`stage.ts`）：`createStage(canvas: HTMLCanvasElement, base: string): Stage`。`Stage` は `loadScene(): Promise<void>`（`<base>index.json` の `scene`・`orbit`・`track`）、`setCourse(track: number[][] | null)`（途中の差し替え可）、`pilot(stream: string, slot: 'tl'|'tr'|'bl'|'br'): StagePilot`、`setMode(m: 'orbit'|'top'|`p${string}`)`、`frame(dt: number)`
- `StagePilot`：`show(pos: number[], trail: (number[]|null)[], state: 'flying'|'lost'|'nopic', label: string)`、`hide()`
- 色は `look.css` の `--tl` などを `getComputedStyle` で読む（ページと 3D で同じ色）

- [ ] **Step 1: 失敗するテストを書く**（`delay.test.ts`）
  - `ServerClock`：`see(1000, 0)`、`see(1100, 200)`（遅れて届いた）→ `now(300) === 1300`
  - `Timeline.heatAt`：`h(id=A, t=0)`、`e(A, t=50_000)`、`h(B, t=55_000)` を入れて、`heatAt(52_000)` が null、`heatAt(56_000).id === 'B'`、`heatAt(49_000).id === 'A'`（Review Focus 4）
  - `Timeline.until`：`a` を t=1000, 2000, 3000 → `until(2500)` が 2 件、続く `until(2500)` が 0 件、`until(3000)` が 1 件
  - `Timeline` の名前の差し替え：同じ `id` の `h` が t=0（名前空）と t=10_000（名前あり）→ `heatAt(5000).cells.R2 === ''`、`heatAt(11_000).cells.R2 === 'A'`（遅れた人に名前が先に出ない）
  - `loadDelay` は `localStorage` が投げても 0 を返す
- [ ] **Step 2:** `flags.test.ts`：`country('佐藤')==='JP'`、`country('Kim Minsu')==='KR'`、`country('Yu Tanaka')===''`、上書き `{'Yu Tanaka':'JP'}` で `'JP'`、`flagOf('JP')==='🇯🇵'`
- [ ] **Step 3: 失敗を確かめる** — `cd viewer && bun test src/aud` → FAIL
- [ ] **Step 4:** `delay.ts`、`flags.ts`、`stage.ts`、`look.css` を書く。`stage.ts` の軌跡とコースの線の描き方・カメラは `livepage.ts` の同じ部分を写し、出所を先頭のコメントに 1 行
- [ ] **Step 5:** `bun test src/aud` が PASS、`npx tsc --noEmit` がエラー無し
- [ ] **Step 6: Commit**

### Task 5: ライブのページ `watch.html` と入口 `event.html`

**Files:**
- Create: `viewer/watch.html`、`viewer/src/watch.ts`
- Create: `viewer/event.html`、`viewer/src/event.ts`、`viewer/src/aud/listing.ts`（純粋）
- Test: `viewer/src/aud/listing.test.ts`
- Modify: `viewer/vite.config.ts`（`rollupOptions.input` に `watch`・`event`・`heat`（Task 7 で作る））

**Interfaces:**
- Consumes：Task 4 の `ServerClock`・`Timeline`・`loadDelay`・`createStage`・`country`/`flagOf`、`live.ts` の `LiveDraw`（答えの `i` はヒートの最初の `a` の `t` からのフレーム数 `round((t − t0) / 1000 × 30)`、`done` は `(t − t0) / 1000 + 0.5`。`LiveDraw.dot(ts, 'delay', 0.1)` の `ts` は `(serverNow − delay − t0) / 1000`）
- Produces（`listing.ts`）：`visibleIndex(ix: HeatIndex, serverNow: number, delayMs: number): { live: HeatEntry | null; done: HeatEntry[] }`：`end > serverNow − delay` のヒートはまだ LIVE（`done` に出さない）、`start > serverNow − delay` のヒートはまだ出さない
- `watch.html?e=<大会>`：WebSocket `wss://<host>/<大会>/api/live`（切れたら 2 秒・4 秒・8 秒…最大 30 秒でつなぎ直し）。上の帯：`.mark`、「<大会のタイトル> · HEAT 7 · EXPERT」（番号が無ければ開始時刻）、LIVE の赤い点（`--alarm`、ヒートが無ければ「待機中」）、DELAY の −／値／＋と目安ボタン 2 つ、ORBIT・TOP・FOLLOW。左上の出場者の欄：色・マス名・国旗・名前（無ければマス名だけ）・状態（直近 1 秒に `a` → FLYING、`x` だけ → LOST、何も無い → NO PICTURE）、押すとその人を追う。下の帯：「位置は DVR の映像から推定したものです。公式の記録ではありません。」と、遅れた時刻で見えている最後に終わったヒートへのリンク（`heat.html?e=<大会>&h=<id>`）
- 時刻は全部 `ServerClock.now() − delay` で決め、`Timeline.until`／`heatAt` を通す。ヒートが変わったら球と軌跡を消す
- コースの線：`<base>track.json`（無ければ線なし、60 秒ごとに読み直して差し替え）。`<base>` は `/<大会>/`
- `event.html`（R2 では `index.html` として置く）：`heats.json` を 10 秒ごとに読み、`visibleIndex` で並べる。LIVE があれば先頭に大きく（→ `watch.html`）、下に終わったヒート（番号・クラス・出場者・開始時刻、→ `heat.html`）。DELAY は同じキーで読む（ここでは変えない、値だけ出す）

- [ ] **Step 1: 失敗するテストを書く**（`listing.test.ts`）
  - 終わって 40 秒のヒート、遅れ 60 秒 → `live` はそのヒート、`done` に無い（Review Focus 5）
  - 同じヒート、遅れ 0 → `live` null、`done[0]` がそれ
  - 始まって 10 秒の新しいヒート、遅れ 20 秒、その前のヒートが 15 秒前に終わった → `live` は前のヒート
  - `heat: null`・`pilots: ['', '']` のヒートが落ちずに並ぶ（Review Focus 1）
- [ ] **Step 2: 失敗を確かめる** — `bun test src/aud` → FAIL
- [ ] **Step 3:** `listing.ts`、`watch.ts`、`event.ts`、2 枚の HTML、`vite.config.ts` を書く
- [ ] **Step 4:** `bun test src/aud` PASS、`bun run build` が通り `dist/watch.html` と `dist/event.html` がある
- [ ] **Step 5: 目で確かめる**：`wrangler dev`（Task 1）＋偽 PUSH と `rt_relay.py`（Task 2）、`VITE_BASE=/staging-fdf/` で build して `dist` を `wrangler dev` の R2（`--local` の永続化先）に置く手順を `worker/test/smoke.sh` の隣に `worker/test/local-page.sh` として書く。ヘッドレス Chrome（MCP の隔離ページ）で `watch.html` を開き、DELAY 0 と 20 秒で球の位置と名前が切り替わる時刻が 20 秒ずれることを `window.ghostline` の値で確かめ、スクリーンショットを 1 枚
- [ ] **Step 6: Commit**

### Task 6: 公開の台本と大会の 1 日の用意

**Files:**
- Create: `tools/publish-event.sh`
- Modify: `CLAUDE.md`（= AGENTS.md）の「構成」の表と「公開」に 1 行ずつ

**Interfaces:**
- `tools/publish-event.sh <大会> <シーンのフォルダ> [タイトル]`：`publish-dvr-viewer.sh` と同じ鍵の読み方（`~/.claude/1p-mounts/vdgs.env`、rclone）。`VITE_BASE=/<大会>/ bun run build`、`dist` の `event.html` を `index.html` として、`watch.html`・`heat.html`・`assets/` と一緒に `dvr/<大会>/` へ。シーンのフォルダから `index.json`・`scene/*.sog`・`sky.jpg`（あれば）・`track.json`（あれば）・`countries.json`（あれば）。`heats.json` が無ければ `{"event","title","live":null,"heats":[]}` を置く（あれば触らない）
- `track.json` だけを差し替える `tools/publish-event.sh <大会> --track <race.json>`（`rt_trackline.py` の出力、`{"track": [[x,y,z],...]}`）

- [ ] **Step 1:** 台本を書く。大会名は Global Constraints の正規表現で弾く
- [ ] **Step 2:** `bash -n tools/publish-event.sh` が通る。`--dry-run`（rclone の `--dry-run` を渡す）で `staging-fdf-2026-10-17` と rt4090 から持ってきた `fdf-2026-10-17` のシーン（`~/rt/view/fdf-2026-10-17/`）を指定し、上げるファイルの一覧が出る
- [ ] **Step 3: Commit**

### Task 7: ヒート直後のリプレイ `heat.html`

**Files:**
- Create: `viewer/heat.html`、`viewer/src/heat.ts`
- Create: `viewer/src/aud/record.ts`（純粋：`HeatRecord` と夜の `race.json` を同じ形にそろえる）
- Create: `viewer/src/aud/racekit.ts`（ブランチ `reconstruct-mgp-qualifier-tracks` の `viewer/src/racekit.ts` を写す。出所をコメントに）
- Test: `viewer/src/aud/record.test.ts`

**Interfaces:**
- Produces：`type Replay = { id: string; start: number; heat: string | null; cls: string | null; version: 'live' | 'night'; fps: number; dur: number; pilots: { stream: string; slot: Slot; name: string; pos: (number[] | null)[]; video?: string }[] }`
- `fromLive(r: HeatRecord, fps = 30): Replay`：答えを 30 fps の格子に `LiveDraw` の `fit`（`trailAt` と同じ平滑、`ws = 0.25`）で並べ直す。答えの間が 0.3 秒より開いたフレームは null
- `fromNight(race: RaceJson): Replay`（Task 8 が書く `race.json`：`pilots[].pos` は 1 フレームに 1 つ、`video` は相対パス）
- `heat.html?e=<大会>&h=<id>`：`heats/<id>/race.json` があればそれ（夜の版）、無ければ `heats/<id>/live.json`。上の帯はライブと同じ＋右に「速報」「DVR 付き」の小さな印。下の帯にタイムライン（再生・一時停止・ドラッグ・速さ 0.5／1／2）。夜の版は右に DVR のタイル（`<video>` を 1 人 1 枚、タイムラインに合わせて `currentTime`。ずれが 0.2 秒を超えたら合わせ直す。MGP のページのタイルの作法を写す）
- DELAY は持たない（リプレイは入口が遅れの分だけ遅らせて出す）

- [ ] **Step 1: 失敗するテストを書く**（`record.test.ts`）
  - `fromLive`：30 Hz で 3 秒の直線（x = t 秒）の答え → `dur ≈ 3`、`pos[45][0] ≈ 1.5`（±0.01）
  - 1 秒の途切れ → その間のフレームが null
  - `cells` に名前の無いマス → `name === stream`
  - `order` の位置で `slot` が tl・tr・bl・br
- [ ] **Step 2: 失敗を確かめる** → FAIL
- [ ] **Step 3:** `record.ts`、`racekit.ts`、`heat.ts`、`heat.html` を書く
- [ ] **Step 4:** `bun test src/aud` PASS、`bun run build` が通る
- [ ] **Step 5: 目で確かめる**：Task 2 の Step 5 で `wrangler dev` に置いた `live.json` を `heat.html` で開き、再生・ドラッグ・速さが効く、終わりで止まる。スクリーンショット 1 枚
- [ ] **Step 6: Commit**

### Task 8: 夜の作り直し `night_heats.py`

**Files:**
- Create: `tools/dvr/night_heats.py`（rt4090 で回す）
- Modify: `tools/publish-event.sh`（`--night <フォルダ>`：`heats/<id>/race.json` と `<stream>.mp4` を上げる）

**Interfaces:**
- `python night_heats.py <日付 YYYYMMDD> <出力フォルダ> [--answers <rt_replay_day.sh の出力フォルダ>]`
- 入力：`live/heats-<日付>.jsonl`（Task 2 が書く `h`／`e`）、`rec/<日付>-*.json`（`start` は最初のフレームの Unix 秒、`fps`、`frames`）、`live/relay/<日付>/<id>.json`（ヒートの答え）
- ヒートごとに、`[start − 3 秒, end]` にかかる録画を探し（`start + frames / fps` で終わりを出す）、マスごとに `ffmpeg -ss … -t … -vf crop=960:540:<x>:<y>` で 1 人 1 本の mp4（h264、CRF 26、音なし、`+faststart`）。マスの位置は `order` の順に tl・tr・bl・br
- 答え：`--answers` があり、そのヒートの録画の流し直しの `.jsonl` があればそれ（フレームの時刻は `rec の start + (i の時刻)`。**流し直しの `i` と録画のフレームの対応は先に確かめる**：Step 2）、無ければ Task 2 のヒートの答え。どちらも 30 fps に並べ直して `race.json`
- `race.json`：`{ id, start, end, heat, cls, fps: 30, version: "night", pilots: [{ stream, name, pos: [[x,y,z]|null,...], video: "<stream>.mp4" }] }`。`fromNight` が読む形

- [ ] **Step 1:** 時刻の対応だけの関数 `clip_window(rec: dict, t0: float, t1: float) -> tuple[float, float] | None`（録画の中の秒）のテストを `tools/dvr/test_night_heats.py` に書く：`start=1000, fps=60, frames=6000`（100 秒）に対して `(1010, 1020)` → `(10, 20)`、`(990, 1005)` → `(0, 5)`、`(1200, 1210)` → `None`。FAIL を見る
- [ ] **Step 2:** rt4090 で（kf1017 の後）、`rt_replay_day.sh` の 1 本の出力の `.jsonl` と、同じ時間の Task 2 のヒートの答え（Task 9 で作る）を比べ、`i` を録画のフレームに対応させる式を決める（位置の差の中央値が 0.3 m 以下になる式）。決めた式と数字を `night_heats.py` の先頭のコメントに書く
- [ ] **Step 3:** `night_heats.py` を書き、`python -m unittest test_night_heats -v` PASS
- [ ] **Step 4:** rt4090 で FDF の 1 ヒート分を作り、`publish-event.sh staging-fdf-2026-10-17 --night` で上げ、`heat.html` に DVR のタイルが出て球と映像が合っていることを目で確かめる
- [ ] **Step 5: Commit**

### Task 9: 通し（ステージング）と電力

**Files:**
- Modify: `docs/realtime-tracking.ja.md`（「観客向けの配信」の節：流れ・設定・確かめた数字）

- [ ] **Step 1:** Saqoosha の OK で `wrangler deploy`、`wrangler secret put RELAY_KEY`（値は 1Password の Environment から。Mac の 1 回だけの手順で rt4090 の `~/.config/ghostline/key` にも置く。値を表示しない）
- [ ] **Step 2:** `publish-event.sh staging-fdf-2026-10-17 <~/rt/view/fdf-2026-10-17 を手元に写したもの>`
- [ ] **Step 3:** rt4090（kf1017 の後）：`chronyc tracking` の System time のずれが 50 ms 以下。`PUSH=8766 PORT=9100 bash rt_replay_day.sh …` で 15:17 台の録画 1 本、同時に `python rt_relay.py --push http://127.0.0.1:8766 --base https://ghostline.saqoo.sh --event staging-fdf-2026-10-17`
- [ ] **Step 4:** `https://ghostline.saqoo.sh/staging-fdf-2026-10-17/` で LIVE が出る、`watch.html` で 4 人が動く、DELAY 20 秒で 20 秒遅れる、流し終わって 30 秒後にヒートが一覧に出る（遅れ 20 秒なら 50 秒後）、`heat.html` で見返せる
- [ ] **Step 5:** 電力：同じ録画を送り手あり・なしで 1 回ずつ（`PACE=1`）、`rt_power_linux.sh` の GPU＋CPU の平均の差が 2 W 以内
- [ ] **Step 6:** 遅れ：同じ録画を YouTube の非公開の配信に流し、ページの DELAY でそろえられることを見る（Saqoosha と一緒に）
- [ ] **Step 7:** 数字を `docs/realtime-tracking.ja.md` に書いて Commit
