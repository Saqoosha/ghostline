// The Free camera (live.html and the audience pages): supersplat-viewer's fly mode (github.com/playcanvas/supersplat-viewer,
// 71ee1ae, 2026-11-16), its numbers and its damping. The camera turns in place and flies where it looks:
// - mouse: left drag looks (yaw / pitch), right drag pans 1:1 at 1 m, the wheel moves forward / back
// - keys: WASD fly along the view (W follows the pitch), Q / E down / up along the camera's up; shift 4x, ctrl 1/4
// - touch: one finger looks (grabbing the scene, so inverted), two fingers pan, spreading them moves forward
// Turning eases toward where the input points, and the keys' speed eases in and out, both frame-rate independent.
import * as pc from 'playcanvas'

const DISPLACEMENT_SCALE = 1 / 60          // src/input/shared.ts:18
const MOVE_SPEED = 4                        // m/s, src/input/devices/keyboard-mouse.ts:48 (not scaled by the scene's size there)
const SHIFT_MUL = 4, CTRL_MUL = 0.25        // keyboard-mouse.ts:164-165 (fly)
const ORBIT_SPEED = 18                      // keyboard-mouse.ts:50, touch.ts:14
const WHEEL_SPEED = 0.06                    // keyboard-mouse.ts:52
const MOUSE_ROTATE = 0.5                    // keyboard-mouse.ts:54 mouseRotateSensitivity
const TOUCH_ROTATE = 1.5                    // touch.ts:18 touchRotateSensitivity
const PINCH_SPEED = 0.4                     // touch.ts:16
const ACCEL_DAMPING = 0.992, DECEL_DAMPING = 0.993   // keyboard-mouse.ts:56-58 flyMove{Acceleration,Deceleration}Damping
const ROTATE_DAMPING = 0.95                 // src/cameras/camera-utils.ts:14 DEFAULT_CONTROLLER_DAMPING (fly-controller.ts:33)
const PAN_DEPTH = 1                         // fly-controller.ts: _distance = 1, the depth screenToWorld pans at
const damp = (d: number, dt: number) => 1 - Math.pow(d, dt * 1000)   // src/core/math.ts:10

export class FreeCam {
  pos = new pc.Vec3(0, 5, 20)
  private ang = new pc.Vec2(-20, 0)          // pitch, yaw (degrees): shown
  private goal = new pc.Vec2(-20, 0)         // where the input has turned it to
  private vel = new pc.Vec3()                // the keys' velocity, camera space (x right, y up, z forward), m/s
  private keys = new Set<string>()
  private fov = 60; private w = 1; private h = 1
  private basis() { const q = new pc.Quat().setFromEulerAngles(this.ang.x, this.ang.y, 0)
    return { f: q.transformVector(pc.Vec3.FORWARD, new pc.Vec3()), r: q.transformVector(pc.Vec3.RIGHT, new pc.Vec3()), u: q.transformVector(pc.Vec3.UP, new pc.Vec3()) } }
  private offset(x: number, y: number, z: number) { const { f, r, u } = this.basis()   // camera-space metres to world (camera-utils.ts:89)
    this.pos.add(r.mulScalar(x)).add(u.mulScalar(y)).add(f.mulScalar(z)) }
  // from the camera's present pose
  enter(cam: pc.Entity) {
    const f = cam.forward; this.pos.copy(cam.getPosition())
    this.ang.set(Math.asin(Math.max(-1, Math.min(1, f.y))) * 180 / Math.PI, Math.atan2(-f.x, -f.z) * 180 / Math.PI)
    this.goal.copy(this.ang); this.vel.set(0, 0, 0)
  }
  // a drag in pixels: the mouse looks as dragged, a finger grabs the scene (keyboard-mouse.ts:195-200, touch.ts:109,150-156)
  look(dx: number, dy: number, touch = false) {
    const k = ORBIT_SPEED * (this.fov / 120) * (touch ? -TOUCH_ROTATE : MOUSE_ROTATE) * DISPLACEMENT_SCALE
    this.goal.x = Math.max(-90, Math.min(90, this.goal.x - dy * k)); this.goal.y -= dx * k   // camera-utils.ts:30-35
  }
  // the right drag or two fingers: what is under them at 1 m follows them (shared.ts:36 screenToWorld)
  pan(dx: number, dy: number) {
    const hh = PAN_DEPTH * Math.tan(this.fov * Math.PI / 360), hw = hh * this.w / this.h
    this.offset(-(dx / this.w) * 2 * hw, (dy / this.h) * 2 * hh, 0)
  }
  wheel(deltaY: number) { this.offset(0, 0, -deltaY * WHEEL_SPEED * DISPLACEMENT_SCALE) }   // keyboard-mouse.ts:189-190
  pinch(spread: number) { this.offset(0, 0, spread * PINCH_SPEED * DISPLACEMENT_SCALE) }     // touch.ts:128-131: spreading moves forward
  // (WASD QE and shift / ctrl; not the arrows: the replay pages seek with them)
  key(e: KeyboardEvent, down: boolean) {
    const k = e.key.length === 1 ? e.key.toLowerCase() : e.key
    if (!['w', 'a', 's', 'd', 'q', 'e', 'Shift', 'Control'].includes(k)) return false
    if (down) this.keys.add(k); else this.keys.delete(k); return k !== 'Shift' && k !== 'Control'
  }
  release() { this.keys.clear() }
  apply(cam: pc.Entity, dt: number, w: number, h: number) {
    this.fov = cam.camera?.fov ?? 60; this.w = Math.max(1, w); this.h = Math.max(1, h)
    const t = damp(ROTATE_DAMPING, dt)                                   // camera-utils.ts:112-116 dampAngles (shortest path in yaw)
    let dyaw = (this.goal.y - this.ang.y) % 360; if (dyaw > 180) dyaw -= 360; if (dyaw < -180) dyaw += 360
    this.ang.x += (this.goal.x - this.ang.x) * t; this.ang.y += dyaw * t
    const has = (k: string) => this.keys.has(k)                          // keyboard-mouse.ts:115-121, 156-180
    const want = new pc.Vec3((has('d') ? 1 : 0) - (has('a') ? 1 : 0), (has('e') ? 1 : 0) - (has('q') ? 1 : 0), (has('w') ? 1 : 0) - (has('s') ? 1 : 0))
    if (want.lengthSq() > 0) want.normalize().mulScalar(MOVE_SPEED * (has('Shift') ? SHIFT_MUL : has('Control') ? CTRL_MUL : 1))
    this.vel.lerp(this.vel, want, damp(want.lengthSq() > this.vel.lengthSq() ? ACCEL_DAMPING : DECEL_DAMPING, dt))
    if (want.lengthSq() === 0 && this.vel.lengthSq() < 1e-4) this.vel.set(0, 0, 0)
    if (this.vel.lengthSq()) this.offset(this.vel.x * dt, this.vel.y * dt, this.vel.z * dt)
    cam.setPosition(this.pos); cam.setEulerAngles(this.ang.x, this.ang.y, 0)
  }
  get state() { return { pos: [this.pos.x, this.pos.y, this.pos.z], pitch: this.goal.x, yaw: this.goal.y } }
  set(o: any) {
    if (!o?.pos) return                                                  // (an orbit-style view saved by the old Free camera: ignored)
    this.pos.set(o.pos[0], o.pos[1], o.pos[2]); this.goal.set(o.pitch ?? this.goal.x, o.yaw ?? this.goal.y); this.ang.copy(this.goal); this.vel.set(0, 0, 0)
  }
}
