import SceneKit
import Metal
import simd

/// A flight on the scan. In the AR view it is drawn inside SceneKit's pass like PointRenderer: first the room's solid splats
/// as discs into depth only (so the flight hides behind pillars and furniture - the phone has no LiDAR, ARKit gives no depth),
/// then the last `trail` seconds as a band that thins and fades to its old end, and the drone where the DVR is now. The top
/// view draws the same trail and drone into its own pass (encodeFlight). `cursor` (seconds into the flight) is set from the
/// main thread; renderNode runs on SceneKit's render thread.
final class PathRenderer: NSObject, SCNNodeRendererDelegate, @unchecked Sendable {
    private struct LineU { var mvp: simd_float4x4; var color: SIMD4<Float>; var viewport: SIMD2<Float>; var width: Float; var n: Int32; var tail: Int32 = 0; var head: Int32 = 0 }
    private struct OccU { var mv: simd_float4x4; var p: simd_float4x4; var viewport: SIMD2<Float>; var bias: Float }
    private struct Formats: Hashable { let color: UInt; let depth: UInt; let stencil: UInt }
    private let lock = NSLock()
    private var buffer: MTLBuffer?
    private var occluders: MTLBuffer?
    private var nOcc = 0
    private var times: [Double] = []
    private var _cursor: Double = 0
    private var occ: MTLRenderPipelineState?
    private var test, write: MTLDepthStencilState?
    /// line and dot pipelines per target (SceneKit's, the top view's)
    private var pipes: [Formats: (line: MTLRenderPipelineState, dot: MTLRenderPipelineState)] = [:]
    private let pipeLock = NSLock()
    var cursor: Double {
        get { lock.withLock { _cursor } }
        set { lock.withLock { _cursor = newValue } }
    }
    private let trail = 3.0
    private let bias: Float = 0.15

    /// the projection's depth direction (the depth tests follow it), for the status line
    var info: String { lock.withLock { _info } }
    private var _info = ""
    /// false: the flight is drawn over everything
    var occlude: Bool {
        get { lock.withLock { _occlude } }
        set { lock.withLock { _occlude = newValue } }
    }
    private var _occlude = true
    /// the AR view's drawable size in pixels, set from the main thread
    var viewport: SIMD2<Float> {
        get { lock.withLock { _viewport } }
        set { lock.withLock { _viewport = newValue } }
    }
    private var _viewport = SIMD2<Float>(0, 0)
    private var smooth: [SIMD3<Float>] = []

    /// path: [t, x, y, z(, qx, qy, qz, qw)] per frame, in time order, evenly spaced (with the rotations, pose(at:) works). Smoothed as the PC pages do (viewer/src/live.ts fit, without
    /// its inlier weights): each frame a quadratic through the frames within `ws` seconds either side, biweight-tapered.
    func setPath(_ path: [[Double]], device: MTLDevice, ws: Double = 0.4) {
        let n = path.count, dt = n > 1 ? (path[n - 1][0] - path[0][0]) / Double(n - 1) : 0.04
        let W = max(1, Int((ws / dt).rounded()))
        var out = [SIMD3<Float>](); out.reserveCapacity(n)
        for j in 0..<n {
            var s = [Double](repeating: 0, count: 5); var b0 = SIMD3<Double>(), b1 = SIMD3<Double>(), b2 = SIMD3<Double>()
            for i in max(0, j - W)...min(n - 1, j + W) {
                let x = Double(i - j), u = x / Double(W + 1), w = (1 - u * u) * (1 - u * u)
                let p = SIMD3(path[i][1], path[i][2], path[i][3])
                s[0] += w; s[1] += w * x; s[2] += w * x * x; s[3] += w * x * x * x; s[4] += w * x * x * x * x
                b0 += w * p; b1 += w * x * p; b2 += w * x * x * p
            }
            let det = s[0] * (s[2] * s[4] - s[3] * s[3]) - s[1] * (s[1] * s[4] - s[3] * s[2]) + s[2] * (s[1] * s[3] - s[2] * s[2])
            let c0 = abs(det) < 1e-9 ? SIMD3(path[j][1], path[j][2], path[j][3])
                : (b0 * (s[2] * s[4] - s[3] * s[3]) - s[1] * (b1 * s[4] - s[3] * b2) + s[2] * (b1 * s[3] - s[2] * b2)) / det
            out.append(SIMD3<Float>(c0))
        }
        // the camera's turn, when the rows carry it (qx qy qz qw): a biweight average over ±0.15 s, each sign-aligned to the frame's own
        var rots: [simd_quatf] = []
        if path.allSatisfy({ $0.count >= 8 }) {
            let q = path.map { simd_normalize(simd_quatf(ix: Float($0[4]), iy: Float($0[5]), iz: Float($0[6]), r: Float($0[7]))) }
            let Wq = max(1, Int((0.15 / dt).rounded()))
            rots = (0..<n).map { j in
                var s = simd_quatf(vector: .zero)
                for i in max(0, j - Wq)...min(n - 1, j + Wq) {
                    let u = Float(i - j) / Float(Wq + 1), w = (1 - u * u) * (1 - u * u)
                    s.vector += w * (simd_dot(q[i].vector, q[j].vector) < 0 ? -q[i].vector : q[i].vector)
                }
                return simd_normalize(s)
            }
        }
        let xyz = out.flatMap { [$0.x, $0.y, $0.z] }
        let b = device.makeBuffer(bytes: xyz, length: xyz.count * 4, options: .storageModeShared)
        lock.withLock { buffer = b; times = path.map { $0[0] }; smooth = out; turns = rots }
    }
    private var turns: [simd_quatf] = []

    /// the drone's camera at t: world-from-camera rotation in COLMAP axes (x right, y down, z forward) and the trail's smoothed
    /// position; nil without rotations, with under 2 frames or at a t that is not a number
    func pose(at t: Double) -> (rot: simd_quatf, pos: SIMD3<Float>)? {
        let (times, sm, tr) = lock.withLock { (self.times, smooth, turns) }
        guard tr.count == times.count, let p = interpolate(t, times, sm) else { return nil }
        let now = index(t, times), k = min(now + 1, times.count - 1)
        let f = k == now ? 0 : Float((t - times[now]) / (times[k] - times[now]))
        return (simd_slerp(tr[now], tr[k], min(max(f, 0), 1)), p)
    }

    /// x, y, z, radius (float32) per disc
    func setOccluders(_ data: Data, device: MTLDevice) {
        guard !data.isEmpty else { return }
        let b = data.withUnsafeBytes { device.makeBuffer(bytes: $0.baseAddress!, length: data.count, options: .storageModeShared) }
        lock.withLock { occluders = b; nOcc = data.count / 16 }
    }

    /// the drone at t (scene coordinates), between the two smoothed frames around it
    func position(at t: Double) -> SIMD3<Float>? {
        let (times, sm) = lock.withLock { (self.times, smooth) }
        return interpolate(t, times, sm)
    }
    private func interpolate(_ t: Double, _ times: [Double], _ sm: [SIMD3<Float>]) -> SIMD3<Float>? {
        guard times.count > 1, t.isFinite else { return nil }
        let now = index(t, times), k = min(now + 1, times.count - 1)
        let f = k == now ? 0 : Float((t - times[now]) / (times[k] - times[now]))
        return sm[now] + (sm[k] - sm[now]) * min(max(f, 0), 1)
    }

    /// the flight's extent in the scene (smoothed path), for framing the top view
    var bounds: (min: SIMD3<Float>, max: SIMD3<Float>)? {
        let sm = lock.withLock { smooth }
        guard let f = sm.first else { return nil }
        return sm.reduce((f, f)) { (simd_min($0.0, $1), simd_max($0.1, $1)) }
    }

    private func index(_ t: Double, _ times: [Double]) -> Int {   // the last frame at or before t
        var lo = 0, hi = times.count - 1
        if t <= times[0] { return 0 }
        while lo < hi { let mid = (lo + hi + 1) / 2; if times[mid] <= t { lo = mid } else { hi = mid - 1 } }
        return lo
    }

    private func pipeline(_ device: MTLDevice, _ lib: MTLLibrary, _ fm: Formats, _ v: String, _ f: String, color: Bool) -> MTLRenderPipelineState? {
        let d = MTLRenderPipelineDescriptor()
        d.vertexFunction = lib.makeFunction(name: v)
        d.fragmentFunction = lib.makeFunction(name: f)
        let c = d.colorAttachments[0]!
        c.pixelFormat = MTLPixelFormat(rawValue: fm.color)!
        if color {
            c.isBlendingEnabled = true
            c.sourceRGBBlendFactor = .one; c.destinationRGBBlendFactor = .oneMinusSourceAlpha
            c.sourceAlphaBlendFactor = .one; c.destinationAlphaBlendFactor = .oneMinusSourceAlpha
        } else { c.writeMask = [] }
        d.depthAttachmentPixelFormat = MTLPixelFormat(rawValue: fm.depth)!
        d.stencilAttachmentPixelFormat = MTLPixelFormat(rawValue: fm.stencil)!
        d.rasterSampleCount = 1
        return try? device.makeRenderPipelineState(descriptor: d)
    }

    func renderNode(_ node: SCNNode, renderer: SCNRenderer, arguments: [String: Any]) {
        guard let encoder = renderer.currentRenderCommandEncoder, let device = renderer.device,
              let m = arguments[SCNModelViewProjectionTransform] as? NSValue,
              let mv = arguments[SCNModelViewTransform] as? NSValue,
              let pr = arguments[SCNProjectionTransform] as? NSValue else { return }
        let (ob, no, oc) = lock.withLock { (occluders, nOcc, _occlude) }
        let P = simd_float4x4(pr.scnMatrix4Value)
        let fm = Formats(color: renderer.colorPixelFormat.rawValue, depth: renderer.depthPixelFormat.rawValue, stencil: renderer.stencilPixelFormat.rawValue)
        if occ == nil {
            guard let lib = device.makeDefaultLibrary() else { return }
            occ = pipeline(device, lib, fm, "occVertex", "occFragment", color: false)
            // the compare follows the projection SceneKit hands over; the buffer's own clear (usesReverseZ) is set to match in ARController
            let zn = P * SIMD4(0, 0, -1, 1), zf = P * SIMD4(0, 0, -10, 1)
            let rev = zn.z / zn.w > zf.z / zf.w
            let s = MTLDepthStencilDescriptor(); s.depthCompareFunction = rev ? .greaterEqual : .lessEqual
            s.isDepthWriteEnabled = false; test = device.makeDepthStencilState(descriptor: s)
            s.isDepthWriteEnabled = true; write = device.makeDepthStencilState(descriptor: s)
            lock.withLock { _info = String(format: "depth %@ (ndc 1 m %.3f, 10 m %.3f)", rev ? "reversed" : "normal", zn.z / zn.w, zf.z / zf.w) }
        }
        let viewport = lock.withLock { _viewport }   // renderer.currentViewport is not used: set from the view's own size
        guard viewport.x > 0, viewport.y > 0 else { return }
        // SceneKit leaves back-face culling on in its encoder: the path's triangle strip faced away and was culled whole
        encoder.setCullMode(.none)
        if renderer.depthPixelFormat != .invalid, oc, let occ, let ob, no > 0 {   // the room into depth (without it, the flight still draws)
            var u = OccU(mv: simd_float4x4(mv.scnMatrix4Value), p: P, viewport: viewport, bias: bias)
            encoder.setRenderPipelineState(occ)
            encoder.setDepthStencilState(write)
            encoder.setVertexBuffer(ob, offset: 0, index: 0)
            encoder.setVertexBytes(&u, length: MemoryLayout<OccU>.stride, index: 1)
            encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: no)
        }
        encodeFlight(encoder, device: device, mvp: simd_float4x4(m.scnMatrix4Value), viewport: viewport,
                     color: renderer.colorPixelFormat, depth: renderer.depthPixelFormat, stencil: renderer.stencilPixelFormat,
                     depthState: oc ? test : nil)
    }

    /// The trail and the drone into an encoder whose pass targets these formats. mvp takes scene coordinates to clip space.
    func encodeFlight(_ encoder: MTLRenderCommandEncoder, device: MTLDevice, mvp: simd_float4x4, viewport: SIMD2<Float>,
                      color: MTLPixelFormat, depth: MTLPixelFormat, stencil: MTLPixelFormat, depthState: MTLDepthStencilState?) {
        let (b, times, t, sm) = lock.withLock { (buffer, self.times, _cursor, smooth) }
        guard let b, times.count > 1 else { return }
        let fm = Formats(color: color.rawValue, depth: depth.rawValue, stencil: stencil.rawValue)
        let p: (line: MTLRenderPipelineState, dot: MTLRenderPipelineState)? = pipeLock.withLock {
            if let p = pipes[fm] { return p }
            guard let lib = device.makeDefaultLibrary(),
                  let l = pipeline(device, lib, fm, "lineVertex", "pathFragment", color: true),
                  let d = pipeline(device, lib, fm, "dotVertex", "dotFragment", color: true) else { return nil }
            pipes[fm] = (l, d); return (l, d)
        }
        guard let p else { return }
        encoder.setCullMode(.none)
        encoder.setDepthStencilState(depthState)
        let now = index(t, times), from = index(t - trail, times)
        var u = LineU(mvp: mvp, color: SIMD4(1, 0.8, 0, 1), viewport: viewport, width: 14, n: Int32(times.count), tail: Int32(from), head: Int32(now))
        encoder.setRenderPipelineState(p.line)
        encoder.setVertexBuffer(b, offset: 0, index: 0)
        encoder.setVertexBytes(&u, length: MemoryLayout<LineU>.stride, index: 1)
        if now > from { encoder.drawPrimitives(type: .triangleStrip, vertexStart: 2 * from, vertexCount: 2 * (now - from + 1)) }
        guard var here = interpolate(t, times, sm) else { return }   // the drone between frames, as its tag
        var d = LineU(mvp: mvp, color: SIMD4(1, 0.25, 0.2, 1), viewport: viewport, width: 24, n: 1)
        encoder.setRenderPipelineState(p.dot)
        encoder.setVertexBytes(&here, length: MemoryLayout<SIMD3<Float>>.size, index: 0)   // 12 bytes: packed_float3
        encoder.setVertexBytes(&d, length: MemoryLayout<LineU>.stride, index: 1)
        encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: 1)
    }
}
