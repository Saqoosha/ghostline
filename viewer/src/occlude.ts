// Seeing into a scanned room from outside: the scan has splats on every wall, so the near walls hide the inside. The
// splats outside the room that stand between the camera and the room are not drawn (occHidden below); the far walls,
// the floor and what shows through the far windows stay. The room is convex: x0 <= x <= x1, z0 <= z <= z1 (x1 and z1
// optional, the office has none), y0 <= y <= y1, and optionally inside a circle in x-z (the office's curved facade), each pulled in by the margin so
// that the walls' own splats (and the scan's few centimetres of error) lie outside it. With the camera inside the room
// nothing is hidden.
// Or a floor plan ("polygon": [[x, z], ...], up to 32 corners, convex or not, drawn along the walls and pulled in by the margin too - an indoor
// venue's L-shaped hall, UDGP 2026 R3) between y0 and y1: then a splat outside it goes when the line from the camera
// through it, carried on past it, enters the plan's prism anywhere beyond it.
// With a floor plan the top may follow a pitched roof ("roof": planes, each y = p.y + g[0] (x - p.x) + g[1] (z - p.z), optionally
// only over x in [xr] and z in [zr]): the prism's top is the lowest of y1 and the planes over each point, lowered by the margin
// (UDGP 2026 R3: the long hall's gable and the side hall's single slope, 12.5 m at the ridge down to 6.7 m at the eaves).
// Also a cut (setCut): splats above a height fade out, wherever the camera is - an indoor venue's upper floor hides the
// floor below it from above (UDGP 2026 R3, the audience pages' stage.ts). The work buffer takes one modifier, so the cut lives here.
// The test runs where the splats are written to PlayCanvas' work buffer, so the buffer is rewritten when the camera or the
// cut moves (setParameter marks it dirty).
import * as pc from 'playcanvas'

// "room" in index.json, in the scene's frame (y up)
export type Room = { x0?: number; z0?: number; y0: number; y1: number; x1?: number; z1?: number; circle?: [number, number, number]; margin?: number
  polygon?: [number, number][]; roof?: RoofPlane[] }
export type RoofPlane = { p: [number, number, number]; g: [number, number]; xr?: [number, number]; zr?: [number, number] }
const MAX_ROOF = 8

const GLSL = /* glsl */ `
uniform vec4 uOccCam;   // xyz the camera, w its distance to the room (a lower bound; -1 inside)
uniform vec4 uOccBox;    // x0, z0, y0, y1 (already pulled in)
uniform vec4 uOccDisk;   // cx, cz, r (pulled in), 1 if the circle is used
uniform vec4 uOccFar;    // x1, z1 (pulled in; 1e30 without), the cut's height (1e30 without), its fade (m either side)
uniform vec4 uOccPoly[16];   // the floor plan's corners (x, z), two to a vec4
uniform float uOccPolyN;     // how many (0: the box)
uniform vec4 uOccRoof[8];    // the roof planes (a, gx, gz): y = a + gx x + gz z, already lowered by the margin
uniform vec4 uOccRoofBox[8]; // where each applies: x0, x1, z0, z1
uniform float uOccRoofN;
float occTop(vec2 q) {                   // the prism's top over q
    float y = uOccBox.w;
    for (int i = 0; i < 8; i++) {
        if (float(i) >= uOccRoofN) break;
        vec4 b = uOccRoofBox[i];
        if (q.x < b.x || q.x > b.y || q.y < b.z || q.y > b.w) continue;
        vec4 r = uOccRoof[i]; y = min(y, r.x + r.y * q.x + r.z * q.y);
    }
    return y;
}
vec2 occV(int i) { vec4 q = uOccPoly[i / 2]; return (i % 2 == 0) ? q.xy : q.zw; }
bool occInPoly(vec2 q) {                 // even-odd
    int n = int(uOccPolyN); bool c = false;
    for (int i = 0; i < 32; i++) {
        if (i >= n) break;
        vec2 a = occV(i), b = occV(i == n - 1 ? 0 : i + 1);
        if ((a.y > q.y) != (b.y > q.y) && q.x < (b.x - a.x) * (q.y - a.y) / (b.y - a.y) + a.x) c = !c;
    }
    return c;
}
// the line cam + t d enters the plan's prism somewhere at t > 1: through a wall between y0 and y1, or through its floor or roof
bool occPolyBeyond(vec3 cam, vec3 d) {
    int n = int(uOccPolyN);
    for (int i = 0; i < 32; i++) {
        if (i >= n) break;
        vec2 a = occV(i), e = occV(i == n - 1 ? 0 : i + 1) - a, w = a - cam.xz;
        float det = e.x * d.z - d.x * e.y;
        if (abs(det) < 1e-9) continue;
        float t = (e.x * w.y - w.x * e.y) / det, s = (d.x * w.y - d.z * w.x) / det;
        if (t > 1.0 && s >= 0.0 && s <= 1.0) { float y = cam.y + t * d.y; if (y > uOccBox.z && y < occTop(cam.xz + t * d.xz)) return true; }
    }
    if (abs(d.y) > 1e-7) {
        float ta = (uOccBox.z - cam.y) / d.y, tb = (uOccBox.w - cam.y) / d.y;
        if (ta > 1.0 && occInPoly(cam.xz + ta * d.xz)) return true;
        if (tb > 1.0) { vec2 q = cam.xz + tb * d.xz; if (occInPoly(q) && uOccBox.w <= occTop(q) + 1e-3) return true; }
    }
    // through a roof plane, where that plane is the top
    for (int i = 0; i < 8; i++) {
        if (float(i) >= uOccRoofN) break;
        vec4 r = uOccRoof[i];
        float den = d.y - r.y * d.x - r.z * d.z;
        if (abs(den) < 1e-7) continue;
        float t = (r.x + r.y * cam.x + r.z * cam.z - cam.y) / den;
        if (t <= 1.0) continue;
        vec2 q = cam.xz + t * d.xz; float y = cam.y + t * d.y;
        if (y > uOccBox.z && y <= occTop(q) + 1e-3 && occInPoly(q)) return true;
    }
    return false;
}
void occSlab(float o, float d, float lo, float hi, inout float t0, inout float t1) {
    if (abs(d) < 1e-7) { if (o < lo || o > hi) { t0 = 1e30; } return; }
    float a = (lo - o) / d, b = (hi - o) / d;
    t0 = max(t0, min(a, b)); t1 = min(t1, max(a, b));
}
bool occInside(vec3 p) {
    if (uOccPolyN > 0.5) return p.y > uOccBox.z && p.y < occTop(p.xz) && occInPoly(p.xz);
    return p.x > uOccBox.x && p.z > uOccBox.y && p.x < uOccFar.x && p.z < uOccFar.y && p.y > uOccBox.z && p.y < uOccBox.w &&
        (uOccDisk.w < 0.5 || distance(p.xz, uOccDisk.xy) < uOccDisk.z);
}
// A splat (a sphere of radius rad around p) outside the room is in front of it when it reaches nearer to the camera than
// the room does, or when its line of sight from the camera, carried on past it (t = 1 at p), enters the room - grown by
// the sphere's size as seen at the room's distance, so a large splat beside the line still counts - only beyond p.
bool occHidden(vec3 p, float rad) {
    if (occInside(p) || uOccCam.w < 0.0) return false;   // the camera in the room: nothing (a non-convex plan would hide its own walls)
    vec3 d = p - uOccCam.xyz;
    float dist = length(d);
    if (dist - rad < uOccCam.w) return true;
    if (uOccPolyN > 0.5) return occPolyBeyond(uOccCam.xyz, d);
    float g = rad * max(1.0, uOccCam.w / dist);
    float t0 = 0.0, t1 = 1e30;
    occSlab(uOccCam.x, d.x, uOccBox.x - g, uOccFar.x + g, t0, t1);
    occSlab(uOccCam.z, d.z, uOccBox.y - g, uOccFar.y + g, t0, t1);
    occSlab(uOccCam.y, d.y, uOccBox.z - g, uOccBox.w + g, t0, t1);
    if (uOccDisk.w > 0.5) {
        vec2 o = uOccCam.xz - uOccDisk.xy, e = d.xz;
        float r = uOccDisk.z + g;
        float A = dot(e, e), B = dot(o, e), C = dot(o, o) - r * r;
        if (A < 1e-12) { if (C > 0.0) t0 = 1e30; }
        else {
            float D = B * B - A * C;
            if (D < 0.0) t0 = 1e30;
            else { float s = sqrt(D); t0 = max(t0, (-B - s) / A); t1 = min(t1, (-B + s) / A); }
        }
    }
    return t0 < t1 && t0 > 1.0;
}
bool occHit = false;
void modifySplatCenter(inout vec3 center) {}
// hidden by a zero scale: the work buffer's colour-only rewrite keeps the geometry written here, and any change of the
// camera's uniform rewrites everything
void modifySplatRotationScale(vec3 originalCenter, vec3 modifiedCenter, inout vec4 rotation, inout vec3 scale) {
    occHit = occHidden(modifiedCenter, 2.0 * max(scale.x, max(scale.y, scale.z)));
#ifndef OCC_DEBUG
    if (occHit) scale = vec3(0.0);
#endif
}
void modifySplatColor(vec3 center, inout vec4 color) {
    if (uOccFar.z < 1e29) color.a *= 1.0 - smoothstep(uOccFar.z - uOccFar.w, uOccFar.z + uOccFar.w + 1e-3, center.y);   // equal edges are undefined
#ifdef OCC_DEBUG
    // the colour-only rewrite (the camera still) skips the geometry pass, so the test runs again here
    if (occHidden(center, 0.0)) color.rgb = vec3(1.0, 0.0, 0.0);
#endif
}
`

// the floor plan moved m inwards: every edge shifted along its inward normal, each corner where its two edges now meet
// (a simple polygon, either winding)
export function insetPolygon(P: [number, number][], m: number): [number, number][] {
  const n = P.length; if (!m || n < 3) return P
  let area = 0; for (let i = 0; i < n; i++) { const [ax, az] = P[i], [bx, bz] = P[(i + 1) % n]; area += ax * bz - bx * az }
  const s = area > 0 ? 1 : -1                         // inward normal of edge (dx, dz): s * (-dz, dx)
  const line = (i: number) => {                       // edge i shifted: a point on it and its direction
    const [ax, az] = P[i], [bx, bz] = P[(i + 1) % n], dx = bx - ax, dz = bz - az, l = Math.hypot(dx, dz) || 1
    return { x: ax + s * -dz / l * m, z: az + s * dx / l * m, dx, dz }
  }
  return P.map((_, i) => {
    const a = line((i + n - 1) % n), b = line(i), det = a.dx * b.dz - a.dz * b.dx
    if (Math.abs(det) < 1e-9) return [b.x, b.z] as [number, number]
    const t = ((b.x - a.x) * b.dz - (b.z - a.z) * b.dx) / det
    return [a.x + t * a.dx, a.z + t * a.dz] as [number, number]
  })
}

export class RoomOcclusion {
  private last = new pc.Vec3(NaN, NaN, NaN)
  private camArr = [0, 0, 0, 0]
  private on = false
  private box: number[]; private disk: number[]; private far: number[]; private poly: [number, number][] = []
  private roof: number[][] = []   // a, gx, gz, x0, x1, z0, z1 (lowered by the margin)
  constructor(private gs: pc.GSplatComponent, room: Room, private debug = false) {
    const m = room.margin ?? 0.25
    this.box = [(room.x0 ?? -1e30) + m, (room.z0 ?? -1e30) + m, room.y0 + m, room.y1 - m]
    this.disk = room.circle ? [room.circle[0], room.circle[1], room.circle[2] - m, 1] : [0, 0, 0, 0]
    this.far = [room.x1 !== undefined ? room.x1 - m : 1e30, room.z1 !== undefined ? room.z1 - m : 1e30, 1e30, 0.25]
    gs.setParameter('uOccBox', this.box)
    gs.setParameter('uOccFar', this.far)
    const poly = room.polygon ? insetPolygon(room.polygon, m) : [], buf = new Float32Array(64)
    if (poly.length > 32) throw new Error('room polygon: at most 32 corners')
    poly.forEach(([x, z], i) => { buf[i * 2] = x; buf[i * 2 + 1] = z })
    this.poly = poly
    gs.setParameter('uOccPoly[0]', buf)
    gs.setParameter('uOccPolyN', poly.length)
    if ((room.roof?.length ?? 0) > MAX_ROOF) throw new Error(`room roof: at most ${MAX_ROOF} planes`)
    const rp = new Float32Array(4 * MAX_ROOF), rb = new Float32Array(4 * MAX_ROOF)
    this.roof = (poly.length ? room.roof ?? [] : []).map(({ p, g, xr, zr }, i) => {
      const r = [p[1] - g[0] * p[0] - g[1] * p[2] - m, g[0], g[1], xr?.[0] ?? -1e30, xr?.[1] ?? 1e30, zr?.[0] ?? -1e30, zr?.[1] ?? 1e30]
      rp.set(r.slice(0, 3), i * 4); rb.set(r.slice(3), i * 4); return r
    })
    gs.setParameter('uOccRoof[0]', rp)
    gs.setParameter('uOccRoofBox[0]', rb)
    gs.setParameter('uOccRoofN', this.roof.length)
    gs.setParameter('uOccDisk', this.disk)
    gs.setParameter('uOccCam', this.camArr)
    this.enabled = true
  }
  get enabled() { return this.on }
  set enabled(v: boolean) {
    if (v === this.on) return
    this.on = v; this.last.set(NaN, NaN, NaN)
    this.gs.setWorkBufferModifier(v ? { glsl: (this.debug ? '#define OCC_DEBUG\n' : '') + GLSL } : null)
  }
  // a lower bound of the camera's distance to the room: the largest of its distances outside each of the room's limits
  private distance(p: pc.Vec3) {
    const [x0, z0, y0, y1] = this.box, [cx, cz, r, useDisk] = this.disk, [x1, z1] = this.far
    if (this.poly.length) {
      // above the roof: the distance to each plane that holds everywhere (the room is below all of them); a plane over a
      // part only is left out, so this stays a lower bound
      let up = p.y - y1
      for (const [a, gx, gz, rx0, rx1, rz0, rz1] of this.roof)
        if (rx0 < -1e29 && rx1 > 1e29 && rz0 < -1e29 && rz1 > 1e29) up = Math.max(up, (p.y - a - gx * p.x - gz * p.z) / Math.hypot(1, gx, gz))
      return Math.max(this.planDistance(p.x, p.z), y0 - p.y, up, 0)
    }
    let d = Math.max(x0 - p.x, z0 - p.z, p.x - x1, p.z - z1, y0 - p.y, p.y - y1)
    if (useDisk) d = Math.max(d, Math.hypot(p.x - cx, p.z - cz) - r)
    return Math.max(d, 0)
  }
  // splats above y fade out over `fade` m either side (Infinity: no cut); rewrites the buffer only when it changed
  setCut(y: number, fade = 0.25) {
    const v = Number.isFinite(y) ? y : 1e30
    if (Math.abs(v - this.far[2]) < 1e-3 && fade === this.far[3]) return
    this.far = [this.far[0], this.far[1], v, fade]; this.gs.setParameter('uOccFar', this.far)
  }
  // the prism's top over (x, z), as occTop
  top(x: number, z: number) {
    let y = this.box[3]
    for (const [a, gx, gz, x0, x1, z0, z1] of this.roof) if (x >= x0 && x <= x1 && z >= z0 && z <= z1) y = Math.min(y, a + gx * x + gz * z)
    return y
  }
  // how far (x, z) lies outside the floor plan (0 inside): to its nearest edge
  private planDistance(x: number, z: number) {
    const P = this.poly, n = P.length; let inside = false, best = Infinity
    for (let i = 0; i < n; i++) {
      const [ax, az] = P[i], [bx, bz] = P[(i + 1) % n]
      if ((az > z) !== (bz > z) && x < (bx - ax) * (z - az) / (bz - az) + ax) inside = !inside
      const ex = bx - ax, ez = bz - az, u = Math.max(0, Math.min(1, ((x - ax) * ex + (z - az) * ez) / (ex * ex + ez * ez || 1)))
      best = Math.min(best, Math.hypot(x - ax - u * ex, z - az - u * ez))
    }
    return inside ? 0 : best
  }
  // the camera's position in the scene entity's frame; call every frame (the buffer is rewritten only when it moved)
  update(camPos: pc.Vec3) {
    if (!this.on || camPos.distance(this.last) < 1e-4) return
    this.last.copy(camPos); this.camArr[0] = camPos.x; this.camArr[1] = camPos.y; this.camArr[2] = camPos.z
    const d = this.distance(camPos)
    // inside, exactly (distance() is only a lower bound under a roof plane over part of the plan)
    const inside = this.poly.length ? this.planDistance(camPos.x, camPos.z) === 0 && camPos.y > this.box[2] && camPos.y < this.top(camPos.x, camPos.z) : d === 0
    this.camArr[3] = inside ? -1 : d
    this.gs.setParameter('uOccCam', this.camArr)
  }
}
