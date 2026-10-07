// rt_topview.js: the top view shared by the control page and the OBS page (rt_control.py serves it). x east to the right,
// z south downwards, so north is up. Expects svg#top (image#aerial, path#course, path#track, path#gate, g#trails) and div#tags; a div#scale, if present,
// gets its first child's width set to 10 m. Positions come straight from the tracker's own stream (its PUSH port): every answer, so
// the trails move at the video's rate; when no row has come for 2.5 s, the state's trail (once a second) is drawn instead.
const TopView = (() => {
  const $ = id => document.getElementById(id), CELLS = ["tl", "tr", "bl", "br"];
  let courseKey = null, courseBox = null, trackKey = null, keep = 12000, onError = () => {}, onFit = () => {};
  let view = { delay: 250, smooth: 80 };               // ms, from the control page (/api/state's view): the same on every page
  $("trails").innerHTML = CELLS.map(c => `<g id="tr-${c}" style="stroke: var(--${c})">${[1, .5, .25, .1].map(o => `<path opacity="${o}"/>`).join("")}<path class="now"/></g>`).join("");
  $("tags").innerHTML = CELLS.map(c => `<div class="tag" id="tag-${c}"></div>`).join("");

  async function loadCourse(name, scene, line) {     // with a course line, the box is centred on it
    const key = name + "|" + scene + "|" + line; if (!name || key === courseKey) return; courseKey = key;
    const r = await fetch(`/api/map?name=${encodeURIComponent(name)}&track=${encodeURIComponent(line)}`).catch(() => null);
    if (!r || !r.ok) { courseKey = null; onError("地図を読めない: " + (r ? (await r.json().catch(() => ({}))).error || r.status : "サーバーに届かない")); return; }
    const c = await r.json(); if (key !== courseKey) return;
    onError("");
    courseBox = c.box; $("top").setAttribute("viewBox", c.box.join(" ")); $("top").parentElement.style.aspectRatio = c.box[2] / c.box[3];
    $("course").setAttribute("d", c.pts.map(p => `M${p[0]},${p[1]}h0`).join("")); fit();
    // under it, the scan seen from above: the server renders it once (a few seconds) and keeps it
    const img = $("aerial"); img.removeAttribute("href"); $("top").classList.remove("pic");
    for (const [k, v] of [["x", c.box[0]], ["y", c.box[1]], ["width", c.box[2]], ["height", c.box[3]]]) img.setAttribute(k, v);
    const url = `/api/topview.jpg?map=${encodeURIComponent(name)}&scene=${encodeURIComponent(scene)}&track=${encodeURIComponent(line)}`;
    for (let i = 0; scene && i < 40 && key === courseKey; i++) {
      const t = await fetch(url).catch(() => null); if (!t) { courseKey = null; break; }
      if (t.status === 200) { img.setAttribute("href", URL.createObjectURL(await t.blob())); $("top").classList.add("pic"); break; }
      if (t.status !== 202) { onError("真上からの絵: " + ((await t.json().catch(() => ({}))).error || t.status)); break; }
      await new Promise(f => setTimeout(f, 2000));
    }
  }
  // the course line (a race.json's averaged laps) and its start / finish gate, or none
  async function loadTrack(name) {
    if ((name || "") === trackKey) return; trackKey = name || "";
    if (!name) { $("track").removeAttribute("d"); $("gate").removeAttribute("d"); return; }
    const r = await fetch("/api/track?name=" + encodeURIComponent(name)).catch(() => null);
    if (!r || !r.ok) { trackKey = null; onError("コースの線を読めない: " + (r ? (await r.json().catch(() => ({}))).error || r.status : "サーバーに届かない")); return; }
    const t = await r.json(); if ((name || "") !== trackKey) return; onError("");
    $("track").setAttribute("d", smoothLoop(t.pts));
    $("gate").setAttribute("d", t.gate ? `M${t.gate[0].join(",")}L${t.gate[1].join(",")}` : "");
  }
  // a closed Catmull-Rom curve through the points, as cubic Béziers, so the line has no corners between them
  function smoothLoop(q) {
    const n = q.length; if (n < 4) return n ? "M" + q.map(p => p.join(",")).join("L") + "Z" : "";
    const f = v => v.toFixed(2), at = i => q[(i + n) % n];
    let d = `M${f(q[0][0])},${f(q[0][1])}`;
    for (let i = 0; i < n; i++) {
      const [p0, p1, p2, p3] = [at(i - 1), at(i), at(i + 1), at(i + 2)];
      d += `C${f(p1[0] + (p2[0] - p0[0]) / 6)},${f(p1[1] + (p2[1] - p0[1]) / 6)} ${f(p2[0] - (p3[0] - p1[0]) / 6)},${f(p2[1] - (p3[1] - p1[1]) / 6)} ${f(p2[0])},${f(p2[1])}`;
    }
    return d + "Z";
  }
  function fit() {                                  // the page may size the view to the box; then the 10 m bar, if there is one
    if (!courseBox) return; onFit(courseBox); const r = $("top").getBoundingClientRect(), bar = $("scale");
    if (bar) bar.firstElementChild.style.width = 10 * Math.min(r.width / courseBox[2], r.height / courseBox[3]) + "px";
  }
  addEventListener("resize", fit);
  // Positions as they are solved, straight from the tracker's own stream (its PUSH port): every answer, so the trails move at the video's
  // rate instead of once a second. When no row has come for 2.5 s (the port unreachable, or a gap), the state's trail is drawn instead.
  const live = {}, names = {}, held = {}, clock = {}; let es = null, lastRow = 0, dirty = true;   // live: cell -> [[x, z, its time on the page clock (ms, when()), cut?]], oldest first
  // An answer that would need more than VMAX from the last drawn point is held back: alone it is a stray answer and is dropped; if the
  // next one continues from it the pilot really is there (found again elsewhere), and the trail goes on from it without a line across.
  const VMAX = 60;                                     // m/s, 216 km/h: above what the quads fly
  function add(cell, x, z, t) {
    const pts = live[cell] ||= [], last = pts[pts.length - 1], h = held[cell];
    const near = a => Math.hypot(x - a[0], z - a[1]) <= VMAX * Math.max((t - a[2]) / 1000, 1 / 30);
    if (!last || t - last[2] > 700 || near(last)) { pts.push([x, z, t]); held[cell] = null; }
    else if (h && near(h)) { pts.push([h[0], h[1], h[2], true], [x, z, t]); held[cell] = null; }
    else held[cell] = [x, z, t];
  }
  // An answer's time is its frame's time in the video (i / fps), put on the page's clock by the earliest any answer of that cell
  // arrived after its frame: the solving time (30-40 ms, uneven) then no longer spaces the points out unevenly. The offset creeps
  // up slowly in case the clocks drift, and starts again when the frames jump (a new run of the tracker, a long gap).
  function when(cell, r, now) {
    if (!(r.fps > 0) || r.i == null) return now;
    const f = r.i * 1000 / r.fps, o = now - f, c = clock[cell];
    // a stall of the source does not skip frame numbers (only frames received are counted): over a second late also starts again
    if (!c || f < c.f - 1000 || f - c.f > 2000 || o > c.o + 1000) clock[cell] = { o, f, t: -Infinity };
    else { c.o = o < c.o ? o : c.o + (o - c.o) * 0.002; c.f = Math.max(c.f, f); }
    const k = clock[cell]; return k.t = Math.max(f + k.o, k.t);   // never before the last one: the trail is drawn in time order
  }
  function listen(s) {
    if (s.phase === "stopped") { if (es) { es.close(); es = null; } for (const o of [live, clock, held]) for (const k in o) delete o[k]; dirty = true; return; }
    for (const [n, c] of Object.entries(s.cells)) names[n] = c.cell;
    if (!es) {
      es = new EventSource(`http://${location.hostname}:${s.push}/`);
      es.onmessage = e => {
        const r = JSON.parse(e.data), cell = names[r.stream]; if (!cell || !r.pos) return;
        add(cell, r.pos[0], r.pos[2], when(cell, r, lastRow = performance.now())); dirty = true;
      };
    }
    const now = performance.now();
    for (const c of Object.values(s.cells)) if (now - lastRow > 2500 || !(live[c.cell] || []).length) { live[c.cell] = c.trail.map(p => [p[0], p[1], now - p[2] * 1000]); dirty = true; }
  }
  // A pilot's trail as drawn view.delay ms late, each point a Gaussian-weighted mean over view.smooth ms (σ) of the answers around it in
  // time, within its run (a gap or a jump starts a new run). With the delay the mean also sees answers after the drawn point, so the
  // head is not dragged behind; without it the head is a mean of the past only.
  function smoothed(pts, now) {
    const tEnd = now - view.delay, sg = view.smooth, out = []; let run = 0, n = 0;
    while (n < pts.length && pts[n][2] <= tEnd) n++;
    const upto = view.delay > 0 && n < pts.length && n > 0 && !pts[n][3] && pts[n][2] - pts[n - 1][2] < 700 ? n + 1 : n;   // and the one after, to place the head between
    for (let i = 0; i < upto; i++) {
      if (i && (pts[i][2] - pts[i - 1][2] >= 700 || pts[i][3])) run = i;
      if (!(sg > 0)) { out.push(pts[i]); continue; }
      let x = 0, z = 0, w = 0;
      for (let j = i; j >= run && pts[i][2] - pts[j][2] <= 2.5 * sg; j--) { const k = Math.exp(-(((pts[i][2] - pts[j][2]) / sg) ** 2) / 2); x += k * pts[j][0]; z += k * pts[j][1]; w += k; }
      for (let j = i + 1; j < pts.length && pts[j][2] - pts[i][2] <= 2.5 * sg && pts[j][2] - pts[j - 1][2] < 700 && !pts[j][3]; j++) { const k = Math.exp(-(((pts[j][2] - pts[i][2]) / sg) ** 2) / 2); x += k * pts[j][0]; z += k * pts[j][1]; w += k; }
      out.push([x / w, z / w, pts[i][2], pts[i][3]]);
    }
    if (upto > n) {                                   // the head at tEnd, between the smoothed points either side
      const a = out[n - 1], b = out[n], u = (tEnd - a[2]) / (b[2] - a[2]);
      out[n] = [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, tEnd];
    }
    return [out, tEnd];
  }
  // a pilot's trail in four bands that fade with age, broken where no position came for a while; the point and the name where they are now
  function drawTrail(cell, all, now) {
    const [pts, t] = smoothed(all, now), paths = $("tr-" + cell).children, d = ["", "", "", ""];
    for (let i = 0; i < pts.length - 1; i++) {
      const p = pts[i], q = pts[i + 1];
      if (q[2] - p[2] < 700 && !q[3]) d[Math.min(3, Math.max(0, Math.floor((t - p[2]) / 3000)))] += `M${p[0].toFixed(2)},${p[1].toFixed(2)}L${q[0].toFixed(2)},${q[1].toFixed(2)}`;
    }
    d.forEach((v, b) => paths[b].setAttribute("d", v));
    const last = pts[pts.length - 1], fresh = !!last && t - last[2] < 1500 && !!courseBox, tag = $("tag-" + cell);
    paths[4].setAttribute("d", fresh ? `M${last[0]},${last[1]}h0` : ""); tag.classList.toggle("on", fresh);
    if (fresh) { tag.style.left = (last[0] - courseBox[0]) / courseBox[2] * 100 + "%"; tag.style.top = (last[1] - courseBox[1]) / courseBox[3] * 100 + "%"; }
  }
  (function frame() {                                  // redrawn when a position arrived, and a few times a second anyway so the trails fade
    requestAnimationFrame(frame); const now = performance.now();
    if (!dirty && !(view.delay > 0) && now - frame.t < 250) return;   // delayed, the head moves between the answers: every frame
    dirty = false; frame.t = now;
    for (const cell of CELLS) { const pts = live[cell] || []; while (pts.length && now - pts[0][2] > keep + view.delay) pts.shift(); drawTrail(cell, pts, now); }
  })();

  // one state a second from /api/state: the names on the tags, the stream, the course for the map and scene given, the track line
  function update(s, map, scene, track) {
    if (s.view) setView(s.view); listen(s); const stopped = s.phase === "stopped";
    for (const c of CELLS) $("tag-" + c).textContent = stopped ? "" : (Object.entries(s.cells).find(([, x]) => x.cell === c) || [""])[0];
    loadCourse(map, scene || "", track || ""); loadTrack(track);
  }
  function setView(v) { if (v.delay !== view.delay || v.smooth !== view.smooth) { view = { delay: +v.delay || 0, smooth: +v.smooth || 0 }; dirty = true; } }
  return { update, setView, fit, box: () => courseBox, onError: f => { onError = f; }, onFit: f => { onFit = f; }, keep: ms => { keep = ms; } };
})();
