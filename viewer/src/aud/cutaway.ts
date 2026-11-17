// Seeing the drones through what stands in front of them, two effects in the splats' render pass (not the work buffer):
// - cutaway (on by default, ?cutaway=0 off; ?cutaway=<degrees>[,<alpha kept>[,<depth margin m>]] tunes it): only what stands between the camera and a drone in the air fades:
//   splats within <degrees> of the line of sight to it (a window of about the same size on screen at any distance, soft
//   edge), more than <margin> nearer than it and higher than it (world y): the deck a drone flies over and anything level
//   with, behind or below it stays. Default 10 deg, 0 (a floor is many splats deep: 0.1 each adds back up to nearly
//   opaque), 2 m
// - ?xray=<k>: every splat's opacity times k (Blender's X-ray: the whole scan see-through)
// Both run in the gsplat render material's modifySplatColor (chunk gsplatModifyVS), which the vertex shader calls every
// frame for every splat, so they follow the camera (Free included) and the drones with no work-buffer rewrite. The room
// occlusion (occlude.ts) works before them in the work buffer: from outside it has already taken the near walls away,
// and these fade what is left (the 2nd-floor deck, the inner walls, pillars).
import * as pc from 'playcanvas'

export const MAX_CUT = 8
const GLSL = /* glsl */`
uniform vec4 uCutCam;            // xyz the camera, w the number of drones
uniform vec4 uCutPts[${MAX_CUT}];
uniform vec4 uCutCfg;            // tan of the window's angular radius, alpha kept, soft edge (share of it), depth margin (m)
uniform float uXray;
void modifySplatCenter(inout vec3 center) {}
void modifySplatRotationScale(vec3 originalCenter, vec3 modifiedCenter, inout vec4 rotation, inout vec3 scale) {}
void modifySplatColor(vec3 center, inout vec4 color) {
    float keep = 1.0;
    for (int i = 0; i < ${MAX_CUT}; i++) {
        if (float(i) >= uCutCam.w) break;
        vec3 d = uCutPts[i].xyz - uCutCam.xyz; float L = length(d);
        if (L < 1.5 || center.y <= uCutPts[i].y) continue;                 // never cut what is level with or under the drone
        vec3 u = d / L, v = center - uCutCam.xyz; float along = dot(v, u);
        if (along <= 0.0 || along > L - uCutCfg.w) continue;             // behind the camera, or not clearly in front of the drone
        float off = length(v - along * u) / along;                         // the tangent of its angle off the line of sight
        keep = min(keep, mix(uCutCfg.y, 1.0, smoothstep(uCutCfg.x * (1.0 - uCutCfg.z), uCutCfg.x, off)));
    }
    color.a *= keep * uXray;
}
`

export class Cutaway {
  private mats = new Set<pc.Material>()
  private cam = new Float32Array(4); private pts = new Float32Array(4 * MAX_CUT); private cfg: Float32Array
  constructor(app: pc.AppBase, degrees: number, kept: number, margin: number, private xray: number, private cut: boolean) {
    this.cfg = new Float32Array([Math.tan(degrees * Math.PI / 180), kept, 0.35, margin])
    app.systems.gsplat!.on('material:created', (m: pc.Material) => {
      m.getShaderChunks(pc.SHADERLANGUAGE_GLSL).set('gsplatModifyVS', GLSL); m.update(); this.mats.add(m); this.push(m)
    })
  }
  // on by default; ?cutaway=0 turns it off
  static fromQuery(app: pc.AppBase, q: URLSearchParams) {
    const cut = q.get('cutaway') !== '0'
    if (!cut && !q.has('xray')) return null
    const [r, k, m] = (q.get('cutaway') ?? '').split(',').map(Number)
    const x = Number(q.get('xray'))
    return new Cutaway(app, r > 0 && r < 45 ? r : 10, k >= 0 && k <= 1 ? k : 0, m >= 0 ? m : 2, x > 0 && x <= 1 ? x : 1, cut)
  }
  private push(m: pc.Material) {
    m.setParameter('uCutCam', this.cam); m.setParameter('uCutPts[0]', this.pts); m.setParameter('uCutCfg', this.cfg); m.setParameter('uXray', this.xray)
  }
  // every frame: the camera and the drones in the air
  update(eye: pc.Vec3, drones: pc.Vec3[]) {
    const n = this.cut ? Math.min(MAX_CUT, drones.length) : 0
    this.cam[0] = eye.x; this.cam[1] = eye.y; this.cam[2] = eye.z; this.cam[3] = n
    for (let i = 0; i < n; i++) { this.pts[i * 4] = drones[i].x; this.pts[i * 4 + 1] = drones[i].y; this.pts[i * 4 + 2] = drones[i].z }
    for (const m of this.mats) this.push(m)
  }
}
