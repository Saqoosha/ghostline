// Seeing into a scanned room from outside: the scan has splats on every wall, so the near walls hide the inside. The
// splats outside the room that stand between the camera and the room are not drawn (occHidden below); the far walls,
// the floor and what shows through the far windows stay. The room is convex: x >= x0, z >= z0,
// y0 <= y <= y1, and optionally inside a circle in x-z (the office's curved facade), each pulled in by the margin so
// that the walls' own splats (and the scan's few centimetres of error) lie outside it. With the camera inside the room
// every line of sight starts inside it, and nothing is hidden.
// The test runs where the splats are written to PlayCanvas' work buffer, so the buffer is rewritten when the camera moves
// (setParameter marks it dirty) and not while it stays still.
import * as pc from 'playcanvas'

// "room" in a flight's index.json, in the scene's frame (y up)
export type Room = { x0: number; z0: number; y0: number; y1: number; circle?: [number, number, number]; margin?: number }

const GLSL = /* glsl */ `
uniform vec4 uOccCam;   // xyz the camera, w its distance to the room (0 inside)
uniform vec4 uOccBox;    // x0, z0, y0, y1 (already pulled in)
uniform vec4 uOccDisk;   // cx, cz, r (pulled in), 1 if the circle is used
void occSlab(float o, float d, float lo, float hi, inout float t0, inout float t1) {
    if (abs(d) < 1e-7) { if (o < lo || o > hi) { t0 = 1e30; } return; }
    float a = (lo - o) / d, b = (hi - o) / d;
    t0 = max(t0, min(a, b)); t1 = min(t1, max(a, b));
}
bool occInside(vec3 p) {
    return p.x > uOccBox.x && p.z > uOccBox.y && p.y > uOccBox.z && p.y < uOccBox.w &&
        (uOccDisk.w < 0.5 || distance(p.xz, uOccDisk.xy) < uOccDisk.z);
}
// A splat (a sphere of radius rad around p) outside the room is in front of it when it reaches nearer to the camera than
// the room does, or when its line of sight from the camera, carried on past it (t = 1 at p), enters the room - grown by
// the sphere's size as seen at the room's distance, so a large splat beside the line still counts - only beyond p.
bool occHidden(vec3 p, float rad) {
    if (occInside(p)) return false;
    vec3 d = p - uOccCam.xyz;
    float dist = length(d);
    if (dist - rad < uOccCam.w) return true;
    float g = rad * max(1.0, uOccCam.w / dist);
    float t0 = 0.0, t1 = 1e30;
    occSlab(uOccCam.x, d.x, uOccBox.x - g, 1e30, t0, t1);
    occSlab(uOccCam.z, d.z, uOccBox.y - g, 1e30, t0, t1);
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
#ifdef OCC_DEBUG
    if (occHit) color.rgb = vec3(1.0, 0.0, 0.0);
#endif
}
`

export class RoomOcclusion {
  private last = new pc.Vec3(NaN, NaN, NaN)
  private camArr = [0, 0, 0, 0]
  private on = false
  private box: number[]; private disk: number[]
  constructor(private gs: pc.GSplatComponent, room: Room, private debug = false) {
    const m = room.margin ?? 0.25
    this.box = [room.x0 + m, room.z0 + m, room.y0 + m, room.y1 - m]
    this.disk = room.circle ? [room.circle[0], room.circle[1], room.circle[2] - m, 1] : [0, 0, 0, 0]
    gs.setParameter('uOccBox', this.box)
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
    const [x0, z0, y0, y1] = this.box, [cx, cz, r, useDisk] = this.disk
    let d = Math.max(x0 - p.x, z0 - p.z, y0 - p.y, p.y - y1)
    if (useDisk) d = Math.max(d, Math.hypot(p.x - cx, p.z - cz) - r)
    return Math.max(d, 0)
  }
  // the camera's position in the scene entity's frame; call every frame (the buffer is rewritten only when it moved)
  update(camPos: pc.Vec3) {
    if (!this.on || camPos.distance(this.last) < 1e-4) return
    this.last.copy(camPos); this.camArr[0] = camPos.x; this.camArr[1] = camPos.y; this.camArr[2] = camPos.z
    this.camArr[3] = this.distance(camPos)
    this.gs.setParameter('uOccCam', this.camArr)
  }
}
