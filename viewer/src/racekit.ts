// Pieces the race page (race.ts) and the MultiGP page (mgp.ts) share: camera-facing ribbons, the button groups over a
// hidden select, and the splat re-sort once the camera settles.
import * as pc from 'playcanvas'

// trails: a ribbon per pilot, turned to face the camera and a constant few pixels wide, fading to nothing at its old
// end (WebGL lines are one pixel, and the immediate-mode ones cannot be transparent - fading them meant mixing in
// white, which drew white lines over the scan)
// The default (race.ts) is a blended ribbon in the immediate layer, drawn over the splats. `opaque` (mgp.ts, as the live
// page's trail) is an opaque, depth-writing band in the World layer's opaque pass, before the splats: splats in front of it
// blend over it and the ones behind it are rejected, so it passes behind gates and banners. It cannot fade by alpha (a
// blended band drawn before the splats would mix with the empty background and still hide what is behind it); `a2c`
// fades it by alpha-to-coverage (the MSAA coverage has samples + 1 levels, so the faded part is a fine stipple), and
// `widthAt` tapers it toward the old end; mgp.ts uses both.
export function ribbonMesh(app: pc.AppBase, parent: pc.Entity = app.root, opaque = false, a2c = false) {
  // created with a triangle of every stream: the material's shader is built from the mesh's first streams, and an
  // empty mesh gave one without vertex colours (white, never fading)
  const mesh = new pc.Mesh(app.graphicsDevice); mesh.clear(true, false)
  mesh.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); mesh.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); mesh.setColors(new Array(12).fill(0)); mesh.setIndices([0, 1, 2]); mesh.update(pc.PRIMITIVE_TRIANGLES)
  const mat = new pc.StandardMaterial(); mat.useLighting = false; mat.diffuse = new pc.Color(0, 0, 0); mat.cull = pc.CULLFACE_NONE
  mat.emissiveVertexColor = true; mat.emissive = new pc.Color(1, 1, 1); mat.opacityVertexColor = true; mat.opacityVertexColorChannel = 'a'
  if (opaque) { mat.blendType = pc.BLEND_NONE; mat.depthWrite = true; mat.depthTest = true; mat.alphaToCoverage = a2c }
  else { mat.blendType = pc.BLEND_NORMAL; mat.depthWrite = false; mat.depthTest = false }
  mat.update()
  const e = new pc.Entity('ribbon'); e.addComponent('render', { meshInstances: [new pc.MeshInstance(mesh, mat)], ...(opaque ? {} : { layers: [pc.LAYERID_IMMEDIATE] }) }); parent.addChild(e)   // immediate: after the splats
  return mesh
}
// a band through pts, turned to face the eye and widthPx pixels wide wherever it is; alpha per point, and a width per
// point (in pixels, over widthPx) when widthAt is given
export function ribbon(mesh: pc.Mesh, pts: pc.Vec3[], c: pc.Color, alpha: (k: number) => number, eye: pc.Vec3, pxWorld: number, widthPx: number, closed = false, widthAt?: (k: number) => number) {
  if (pts.length < 2) { mesh.setPositions([0, 0, 0, 0, 0, 0, 0, 0, 0]); mesh.setNormals([0, 1, 0, 0, 1, 0, 0, 1, 0]); mesh.setColors(new Array(12).fill(0)); mesh.setIndices([0, 1, 2]); mesh.update(pc.PRIMITIVE_TRIANGLES); return }
  const pos: number[] = [], col: number[] = [], nrm: number[] = [], idx: number[] = [], n = pts.length
  const tan = new pc.Vec3(), view = new pc.Vec3(), side = new pc.Vec3()
  pts.forEach((q, k) => {
    const a_ = closed ? pts[(k - 1 + n) % n] : pts[Math.max(0, k - 1)], b_ = closed ? pts[(k + 1) % n] : pts[Math.min(n - 1, k + 1)]
    tan.sub2(a_, b_).normalize(); view.sub2(eye, q)
    const w = (widthAt ? widthAt(k) : widthPx) / 2 * pxWorld * view.length(); side.cross(tan, view.normalize()).normalize().mulScalar(w); const al = alpha(k)
    pos.push(q.x + side.x, q.y + side.y, q.z + side.z, q.x - side.x, q.y - side.y, q.z - side.z)
    col.push(c.r, c.g, c.b, al, c.r, c.g, c.b, al); nrm.push(0, 1, 0, 0, 1, 0)
    if (k) { const i = 2 * k; idx.push(i - 2, i - 1, i, i - 1, i + 1, i) }
  })
  if (closed) { const i = 2 * n; idx.push(i - 2, i - 1, 0, i - 1, 1, 0) }
  mesh.setPositions(pos); mesh.setNormals(nrm); mesh.setColors(col); mesh.setIndices(idx); mesh.update(pc.PRIMITIVE_TRIANGLES)
}

// the controls as button groups over the hidden select / checkbox (the code reads those as before)
export function segment(host: HTMLElement, sel: HTMLSelectElement, label: (o: HTMLOptionElement, i: number) => Node[]) {
  const btns = Array.from(sel.options).map((o, i) => { const b = document.createElement('button'); b.append(...label(o, i))
    b.onclick = () => { sel.selectedIndex = i; sel.onchange?.(new Event('change')); b.blur() }; return b })
  host.replaceChildren(...btns)
  return () => btns.forEach((b, i) => b.classList.toggle('on', i === sel.selectedIndex))
}

// PlayCanvas's CPU splat sort drops a request made while the previous one is still running, yet records the view as
// sorted; a camera that then stays still (top, paused) keeps the order of an earlier pose, which smears dark haze over
// the ground. So once the camera settles, one more sort is asked for directly, on a frame when the sorter is idle (so it
// is not dropped too). Turning the camera to trigger it showed as a shake.
export function resortWhenIdle(app: pc.AppBase) {
  let sent = false
  ;(app.renderer as any).gsplatDirector?.camerasMap.forEach((cd: any) => cd.layersMap.forEach((ld: any) => {
    const m = ld.gsplatManager; if (m && (!m.cpuSorter || m.cpuSorter.jobsInFlight === 0)) { m.sortNeeded = true; sent = true }
  }))
  return sent
}
