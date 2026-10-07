// rt_topview.js: the top view shared by the control page and the OBS page (rt_control.py serves it). x east to the right,
// z south downwards, so north is up. Expects svg#top (image#aerial, path#course, path#track, path#gate, g#trails) and div#tags; a div#scale, if present,
// gets its first child's width set to 10 m. Positions come straight from the tracker's own stream (its PUSH port): every answer, so
// the trails move at the video's rate; when no row has come for 2.5 s, the state's trail (once a second) is drawn instead.
const TopView = (() => {
  const $ = id => document.getElementById(id), CELLS = ["tl", "tr", "bl", "br"];
  let courseKey = null, courseBox = null, trackKey = null, keep = 12000, onError = () => {}, onFit = () => {};
  $("trails").innerHTML = CELLS.map(c => `<g id="tr-${c}" style="stroke: var(--${c})">${[1, .5, .25, .1].map(o => `<path opacity="${o}"/>`).join("")}<path class="now"/></g>`).join("");
  $("tags").innerHTML = CELLS.map(c => `<div class="tag" id="tag-${c}"></div>`).join("");

  async function loadCourse(name, scene) {
    const key = name + "|" + scene; if (!name || key === courseKey) return; courseKey = key;
    const r = await fetch("/api/map?name=" + encodeURIComponent(name)).catch(() => null);
    if (!r || !r.ok) { courseKey = null; onError("地図を読めない: " + (r ? (await r.json().catch(() => ({}))).error || r.status : "サーバーに届かない")); return; }
    const c = await r.json(); if (key !== courseKey) return;
    onError("");
    courseBox = c.box; $("top").setAttribute("viewBox", c.box.join(" ")); $("top").parentElement.style.aspectRatio = c.box[2] / c.box[3];
    $("course").setAttribute("d", c.pts.map(p => `M${p[0]},${p[1]}h0`).join("")); fit();
    // under it, the scan seen from above: the server renders it once (a few seconds) and keeps it
    const img = $("aerial"); img.removeAttribute("href"); $("top").classList.remove("pic");
    for (const [k, v] of [["x", c.box[0]], ["y", c.box[1]], ["width", c.box[2]], ["height", c.box[3]]]) img.setAttribute(k, v);
    const url = `/api/topview.jpg?map=${encodeURIComponent(name)}&scene=${encodeURIComponent(scene)}`;
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
    const t = await r.json(); if ((name || "") !== trackKey) return;
    $("track").setAttribute("d", t.pts.length ? "M" + t.pts.map(p => p.join(",")).join("L") + "Z" : "");
    $("gate").setAttribute("d", t.gate ? `M${t.gate[0].join(",")}L${t.gate[1].join(",")}` : "");
  }
  function fit() {                                  // the page may size the view to the box; then the 10 m bar, if there is one
    if (!courseBox) return; onFit(courseBox); const r = $("top").getBoundingClientRect(), bar = $("scale");
    if (bar) bar.firstElementChild.style.width = 10 * Math.min(r.width / courseBox[2], r.height / courseBox[3]) + "px";
  }
  addEventListener("resize", fit);
  // Positions as they are solved, straight from the tracker's own stream (its PUSH port): every answer, so the trails move at the video's
  // rate instead of once a second. When no row has come for 2.5 s (the port unreachable, or a gap), the state's trail is drawn instead.
  const live = {}, names = {}, held = {}; let es = null, lastRow = 0, dirty = true;   // live: cell -> [[x, z, when it arrived (ms), cut?]], oldest first
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
  function listen(s) {
    if (s.phase === "stopped") { if (es) { es.close(); es = null; } for (const k in live) delete live[k]; dirty = true; return; }
    for (const [n, c] of Object.entries(s.cells)) names[n] = c.cell;
    if (!es) {
      es = new EventSource(`http://${location.hostname}:${s.push}/`);
      es.onmessage = e => {
        const r = JSON.parse(e.data), cell = names[r.stream]; if (!cell || !r.pos) return;
        add(cell, r.pos[0], r.pos[2], lastRow = performance.now()); dirty = true;
      };
    }
    const now = performance.now();
    for (const c of Object.values(s.cells)) if (now - lastRow > 2500 || !(live[c.cell] || []).length) { live[c.cell] = c.trail.map(p => [p[0], p[1], now - p[2] * 1000]); dirty = true; }
  }
  // a pilot's trail in four bands that fade with age, broken where no position came for a while; the point and the name where they are now
  function drawTrail(cell, pts, now) {
    const paths = $("tr-" + cell).children, d = ["", "", "", ""];
    for (let i = 0; i < pts.length - 1; i++) {
      const p = pts[i], q = pts[i + 1];
      if (q[2] - p[2] < 700 && !q[3]) d[Math.min(3, Math.floor((now - p[2]) / 3000))] += `M${p[0]},${p[1]}L${q[0]},${q[1]}`;
    }
    d.forEach((v, b) => paths[b].setAttribute("d", v));
    const last = pts[pts.length - 1], fresh = !!last && now - last[2] < 1500 && !!courseBox, tag = $("tag-" + cell);
    paths[4].setAttribute("d", fresh ? `M${last[0]},${last[1]}h0` : ""); tag.classList.toggle("on", fresh);
    if (fresh) { tag.style.left = (last[0] - courseBox[0]) / courseBox[2] * 100 + "%"; tag.style.top = (last[1] - courseBox[1]) / courseBox[3] * 100 + "%"; }
  }
  (function frame() {                                  // redrawn when a position arrived, and a few times a second anyway so the trails fade
    requestAnimationFrame(frame); const now = performance.now();
    if (!dirty && now - frame.t < 250) return; dirty = false; frame.t = now;
    for (const cell of CELLS) { const pts = live[cell] || []; while (pts.length && now - pts[0][2] > keep) pts.shift(); drawTrail(cell, pts, now); }
  })();

  // one state a second from /api/state: the names on the tags, the stream, the course for the map and scene given, the track line
  function update(s, map, scene, track) {
    listen(s); const stopped = s.phase === "stopped";
    for (const c of CELLS) $("tag-" + c).textContent = stopped ? "" : (Object.entries(s.cells).find(([, x]) => x.cell === c) || [""])[0];
    loadCourse(map, scene || ""); loadTrack(track);
  }
  return { update, fit, box: () => courseBox, onError: f => { onError = f; }, onFit: f => { onFit = f; }, keep: ms => { keep = ms; } };
})();
