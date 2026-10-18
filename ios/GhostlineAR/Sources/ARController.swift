import ARKit
import SceneKit
import CoreImage
import Observation
import AVFoundation

/// One ARKit frame goes to the VPS (tools/dvr/rt_vps.py), which answers where that camera is on the scan. With the ARKit pose
/// of the same frame that gives ARKit-from-scene; the scan's points, the flight and its DVR are drawn through it over the
/// camera image, and the view can fly to the scan seen from above (the splats).
@MainActor @Observable
final class ARController {
    var status = "点群を読んでから「合わせる」"
    var busy = false
    var auto = true
    var opacity: Double = 0.8 { didSet { points.alpha = Float(opacity); if mode == .ar { splats.view.alpha = opacity } } }
    /// tap the screen: the splats instead of the points
    var showSplats = false { didSet { updateVisibility() } }
    /// The camera image with the flight over it (ar), or the scan from above drawn with the 3DGS (top). Between them the
    /// view flies: it starts from ARKit's own camera (same pose, same lens - the splats sit on the camera image, so the
    /// image dissolves into them), eases up to the top, and back down onto wherever ARKit's camera is by then.
    /// fpv: the DVR full screen (height-fitted, black either side). The way there flies the same way, onto the drone's camera at
    /// the DVR's clock, the splats bent into the DVR's fisheye on the way so they land on its picture; then the DVR dissolves in.
    enum Mode { case ar, toTop, top, toAR, toFPV, fpv, fromFPV }
    private(set) var mode = Mode.ar { didSet { updateVisibility() } }
    var isFPV: Bool { mode == .toFPV || mode == .fpv || mode == .fromFPV }
    /// the full-screen DVR's opacity
    var dvrAlpha: Double = 0
    /// the DVR's lens (lens.json "source_fisheye", pixels of its 4:3 picture)
    @ObservationIgnored private var lens: (fx: Float, cx: Float, cy: Float, k: SIMD4<Float>)?
    /// the flying camera's projection when it is not ARKit's (the FPV transition's wide pinhole)
    @ObservationIgnored private var virtualProj: simd_float4x4?
    @ObservationIgnored private var flyStart = 0.0
    @ObservationIgnored private var fromCam = matrix_identity_float4x4   // camera -> scene, x right / y up / z back
    @ObservationIgnored private var topCam = matrix_identity_float4x4
    @ObservationIgnored private var virtualCam: simd_float4x4?
    @ObservationIgnored private var flying = false   // the dissolve is over and the camera has left where it started
    @ObservationIgnored private var topSince = 0.0
    /// splats above this height (m, scene) are the ceiling: hidden while the view is above it
    @ObservationIgnored private var ceiling: Float?
    /// the room's floor plan (index.json "room"), for framing the top view
    @ObservationIgnored private var room: (x0: Float, z0: Float, y0: Float, circle: [Float]?)?
    /// the camera image dissolves into the 3DGS first, then the view flies (and back: it lands, then dissolves) - never both at once
    private let fadeTime = 0.3, flyTime = 2.0
    /// the flight hides behind the room (its solid splats as discs in depth)
    var occlude = true { didSet { path.occlude = occlude } }
    /// seconds either side in each frame's fit (PathRenderer.setPath); the PC pages default to 0.4
    var smoothing = 0.8
    @ObservationIgnored private var rawPath: [[Double]] = []

    @ObservationIgnored private let view = ARSCNView()
    @ObservationIgnored private let splats = SplatOverlay()
    @ObservationIgnored private let points = PointRenderer()
    @ObservationIgnored private let path = PathRenderer()
    /// scene -> ARKit world, with the points and the flight under it (each drawn by its renderer delegate)
    @ObservationIgnored private let anchor = SCNNode()
    @ObservationIgnored private let cloud = SCNNode()
    @ObservationIgnored private let flight = SCNNode()
    /// the recorded flight's DVR; its clock moves the drone along the path
    @ObservationIgnored let player = AVPlayer()
    /// the pilot's name tag, kept over the drone every display frame
    @ObservationIgnored private let tag = PaddedLabel()
    @ObservationIgnored private var ticker: Ticker?
    private let pilot = "Saqoosha"
    @ObservationIgnored private let ci = CIContext()
    @ObservationIgnored private var arFromScene: simd_float4x4?
    @ObservationIgnored private var nPoints = 0
    /// Sent image size relative to the captured one: the server scales to the map's width anyway.
    private let sendScale: CGFloat = 0.5
    /// ARKit's camera has y up and z back; the server's (OpenCV) y down and z forward.
    private let flip = simd_float4x4(diagonal: SIMD4(1, -1, -1, 1))

    func makeView() -> ARSCNView {
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = false
        view.rendersCameraGrain = false
        view.antialiasingMode = .none          // the point and path pipelines are single-sample
        view.preferredFramesPerSecond = 30     // 60 throttled the phone within minutes (with the splats in the top view)
        // depth cleared to far = 1 as the projection SceneKit hands the renderer delegates expects; with the default reversed
        // depth the buffer starts at 0 and every depth-tested line of the flight failed
        view.usesReverseZ = false
        anchor.isHidden = true
        cloud.rendererDelegate = points
        points.alpha = Float(opacity)
        flight.rendererDelegate = path
        anchor.addChildNode(cloud)
        anchor.addChildNode(flight)
        view.scene.rootNode.addChildNode(anchor)
        tag.text = pilot
        tag.font = .systemFont(ofSize: 14, weight: .semibold)
        tag.textColor = .white
        tag.backgroundColor = UIColor(red: 1, green: 0.25, blue: 0.2, alpha: 0.85)
        tag.layer.cornerRadius = 6
        tag.layer.masksToBounds = true
        tag.isHidden = true
        tag.sizeToFit()
        view.addSubview(tag)
        ticker = Ticker { [unowned self] in
            fly()
            // the DVR's clock moves the drone (drawn) and its tag: one clock for both. Before the DVR has loaded it is not a number
            let t = player.currentTime().seconds
            guard t.isFinite else { tag.isHidden = true; return }
            path.cursor = t
            // the drone projected to the screen, the tag just above it
            guard arFromScene != nil, !isFPV, let p = path.position(at: t) else { tag.isHidden = true; return }
            if mode == .ar {
                let w = anchor.presentation.simdWorldTransform * SIMD4(p, 1)
                let s = view.projectPoint(SCNVector3(w.x, w.y, w.z))
                tag.isHidden = s.z <= 0 || s.z >= 1    // behind the camera or past the far plane
                tag.center = CGPoint(x: CGFloat(s.x), y: CGFloat(s.y) - 22)
            } else if let c = virtualCam, let P = projection() {
                let clip = P * c.inverse * SIMD4(p, 1)
                tag.isHidden = clip.w <= 0
                let b = view.bounds.size
                tag.center = CGPoint(x: CGFloat(clip.x / clip.w + 1) / 2 * b.width, y: CGFloat(1 - clip.y / clip.w) / 2 * b.height - 22)
            }
        }
        splats.view.frame = view.bounds
        splats.view.alpha = opacity
        splats.view.isHidden = true
        splats.view.isPaused = true
        view.addSubview(splats.view)
        splats.camera = { [unowned self] in
            guard let c = mode == .ar ? arCamera() : virtualCam, let P = virtualProj ?? projection() else { return nil }
            return (c.inverse, P)
        }
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        // the map's keyframes are 4:3 (the server checks the aspect); 30 fps as the views draw; the smallest from 1280 wide up -
        // a phone that lists 3840 x 2880 first would otherwise send 4K frames the server shrinks to the map's width anyway
        let fourThree = ARWorldTrackingConfiguration.supportedVideoFormats
            .filter { Int($0.imageResolution.width) * 3 == Int($0.imageResolution.height) * 4 }
            .sorted { ($0.framesPerSecond != 30 ? 1 : 0, $0.imageResolution.width) < ($1.framesPerSecond != 30 ? 1 : 0, $1.imageResolution.width) }
        if let f = fourThree.first(where: { $0.imageResolution.width >= 1280 }) ?? fourThree.first { config.videoFormat = f }
        else { problems["カメラ"] = "4:3 の映像形式が無い: 位置合わせができない" }
        view.session.run(config)
        return view
    }

    /// what failed to load, by what it was; shown apart from `status`, which every answer rewrites
    var problems: [String: String] = [:]
    /// the server address the settings hold now (ContentView's @AppStorage)
    private var server: String { UserDefaults.standard.string(forKey: "server") ?? "http://rt4090:8790" }

    /// GET from the server: a non-200 reply or an empty body is an error, not data
    private func fetch(_ path: String, recordSize: Int = 1) async throws -> Data {
        guard let url = URL(string: server + path) else { throw Problem("URL にならない: \(server + path)") }
        var req = URLRequest(url: url); req.timeoutInterval = 15
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
        guard code == 200 else { throw Problem("\(url.path): HTTP \(code)") }
        guard !data.isEmpty else { throw Problem("\(url.path): 空（サーバーに中身が無い）") }
        guard data.count % recordSize == 0 else { throw Problem("\(url.path): \(data.count) バイト（\(recordSize) の倍数でない）") }
        return data
    }

    /// a reload (読む, a new address) supersedes the loads still running: their results are dropped, not applied late
    @ObservationIgnored private var pointsGen = 0
    @ObservationIgnored private var flightGen = 0
    @ObservationIgnored private var dvrFile: URL?

    func loadPoints() {
        status = "点群を読んでいる…"
        pointsGen += 1; let gen = pointsGen
        Task {
            do {
                let data = try await fetch("/points?n=300000", recordSize: 16)
                guard gen == pointsGen else { return }
                points.setPoints(data, device: view.device!)
                nPoints = data.count / 16
                problems["点群"] = nil
                status = "点 \(nPoints) 個。「合わせる」（画面をタップで splat と切り替え）"
                updateVisibility()
            } catch { problems["点群"] = error.localizedDescription }
        }
    }

    /// The recorded flight, the occluders, the DVR (into a file for the player: the server sends the whole mp4, no ranges) and
    /// the splats, each on its own: one failing does not keep the others away.
    func loadFlight() {
        flightGen += 1; let gen = flightGen
        ceiling = nil; room = nil; lens = nil       // a new server's flight, without a room or failing, must not keep the last one's
        problems["レンズ"] = nil
        Task {
            let current = { gen == self.flightGen }
            do {
                let f = try JSONDecoder().decode(Flight.self, from: try await fetch("/flight"))
                guard f.path.count > 1, f.path.allSatisfy({ $0.count >= 4 }) else { throw Problem("/flight: 姿勢が足りない") }
                guard current() else { return }
                rawPath = f.path
                path.setPath(f.path, device: view.device!, ws: smoothing)
                if let r = f.room {
                    ceiling = Float(r.y1 - (r.margin ?? 0.25))
                    room = (Float(r.x0), Float(r.z0), Float(r.y0), r.circle?.map { Float($0) })
                }
                if let l = f.lens, l.k.count == 4, [l.fx, l.cx, l.cy].allSatisfy({ $0.isFinite && $0 > 0 }), l.k.allSatisfy(\.isFinite) {
                    lens = (Float(l.fx), Float(l.cx), Float(l.cy), SIMD4(l.k.map { Float($0) }))
                    problems["レンズ"] = nil
                } else if f.lensBroken || f.lens != nil {
                    problems["レンズ"] = "lens.json の source_fisheye が読めないか値がおかしい（FPV は使えない）"
                }
                problems["飛行"] = nil
            } catch { problems["飛行"] = error.localizedDescription }
            guard current() else { return }
            do {
                let data = try await fetch("/occluders?n=300000", recordSize: 16)
                guard current() else { return }
                path.setOccluders(data, device: view.device!)
                problems["隠す円"] = nil
            } catch { problems["隠す円"] = error.localizedDescription }
            guard current() else { return }
            do {
                let data = try await fetch("/dvr")
                guard current() else { return }
                let file = FileManager.default.temporaryDirectory.appendingPathComponent("dvr-\(UUID().uuidString).mp4")   // the player may still read the last one
                try data.write(to: file)
                player.replaceCurrentItem(with: AVPlayerItem(url: file))
                if let old = dvrFile { try? FileManager.default.removeItem(at: old) }   // no longer the player's
                dvrFile = file
                problems["DVR"] = nil
            } catch { problems["DVR"] = error.localizedDescription }
            guard current() else { return }
            do {                                    // after the room: what is above its ceiling goes in a chunk of its own
                let data = try await fetch("/splats?n=600000", recordSize: 32)
                guard current() else { return }
                try await splats.load(data, ceiling: ceiling)
                problems["splat"] = nil
            } catch { problems["splat"] = error.localizedDescription }
        }
    }

    func resmooth() {
        guard !rawPath.isEmpty else { return }
        path.setPath(rawPath, device: view.device!, ws: smoothing)
    }

    private func updateVisibility() {
        let ar = mode == .ar
        anchor.isHidden = arFromScene == nil || !ar  // away from the camera image the splat view draws the flight itself
        cloud.isHidden = nPoints == 0 || showSplats
        let splatsOn = ar ? showSplats : mode != .fpv   // in FPV the DVR alone is on screen
        if !isFPV { splats.warp = nil; virtualProj = nil }
        if ar { dvrAlpha = 0; fpvLens = nil }
        splats.view.isHidden = !splatsOn
        splats.view.isPaused = !splatsOn        // hidden, an MTKView still draws (and sorts 600k splats) every frame
        splats.flight = ar ? nil : path
        splats.view.backgroundColor = ar ? .clear : .black
        if ar { splats.view.alpha = opacity }
    }

    // MARK: - top view

    /// ARKit's camera of the current frame in scene coordinates (camera -> scene; the app is landscape right only, the
    /// orientation ARKit's camera axes are in)
    private func arCamera() -> simd_float4x4? {
        guard arFromScene != nil, let frame = view.session.currentFrame else { return nil }
        return anchor.presentation.simdWorldTransform.inverse * frame.camera.transform
    }

    private func projection() -> simd_float4x4? {
        view.session.currentFrame?.camera.projectionMatrix(for: .landscapeRight, viewportSize: view.bounds.size, zNear: 0.05, zFar: 100)
    }

    var canFly: Bool { arFromScene != nil && splats.splatCount > 0 }

    func toggleTop() {
        switch mode {
        case .ar:
            guard canFly, let c = arCamera() else { return }
            guard let top = topCamera(from: c) else { status = "真上の絵を決められない（部屋も飛行の範囲も無い）"; return }
            virtualCam = c; topCam = top; fromCam = c; flying = false
            splats.view.alpha = 0
            flyStart = CACurrentMediaTime(); mode = .toTop
        case .top:
            splats.freeze = false
            flyStart = CACurrentMediaTime(); mode = .toAR
        default: break                          // already flying
        }
    }

    /// Straight down. With the room known: the whole floor, its right-angle corner (x0, z0) bottom left - the z0 wall up the
    /// left edge, the x0 wall along the bottom, the facade's arc across the top right (the room's long side, z, across the
    /// landscape screen). Without: the flight, the way the camera was looking up on the screen.
    private func topCamera(from c: simd_float4x4) -> simd_float4x4? {
        guard let P = projection() else { return nil }
        let z = SIMD3<Float>(0, 1, 0)
        var b: (min: SIMD3<Float>, max: SIMD3<Float>)
        var y: SIMD3<Float>
        if let r = room {
            var xmax = r.x0 + 20, zmax = r.z0 + 20
            if let c = r.circle {                   // the arc's reach along each wall
                let (cx, cz, rad) = (c[0], c[1], c[2])
                let dz = max(r.z0 - cz, 0), dx = max(r.x0 - cx, 0)
                xmax = max(r.x0 + 1, cx + max(0, rad * rad - dz * dz).squareRoot())   // an arc that misses a wall: no NaN
                zmax = max(r.z0 + 1, cz + max(0, rad * rad - dx * dx).squareRoot())
            }
            b = (SIMD3(r.x0, r.y0, r.z0), SIMD3(xmax, r.y0, zmax))   // the floor
            y = SIMD3(1, 0, 0)                      // up the screen: +x; right: +z
        } else {
            guard let pb = path.bounds else { return nil }
            b = pb
            let f = -SIMD3(c.columns.2.x, 0, c.columns.2.z)
            y = simd_length(f) < 1e-3 ? SIMD3(0, 0, -1) : simd_normalize(f)
        }
        let center = (b.min + b.max) / 2
        let x = simd_cross(y, z)
        var hx: Float = 0, hy: Float = 0
        for k in 0..<8 {                        // the box's corners on the screen's axes
            let p = SIMD3(k & 1 == 0 ? b.min.x : b.max.x, k & 2 == 0 ? b.min.y : b.max.y, k & 4 == 0 ? b.min.z : b.max.z) - center
            hx = max(hx, abs(simd_dot(p, x))); hy = max(hy, abs(simd_dot(p, y)))
        }
        let h = max(hy * P.columns.1.y, hx * P.columns.0.x) * 1.15   // distance that fits it (P11 = 1 / tan(fovy / 2))
        var m = simd_float4x4(SIMD4(x, 0), SIMD4(y, 0), SIMD4(z, 0), SIMD4(center.x, b.max.y + h, center.z, 1))
        m.columns.3.w = 1
        return m
    }

    /// Every display frame: where the flying camera is, and how much of the 3DGS covers the camera image.
    private func fly() {
        defer {                                 // the ceiling goes once the view is above it, and comes back below it
            if let ceiling { splats.showCeiling(mode == .ar || (virtualCam.map { $0.columns.3.y <= ceiling } ?? true)) }
        }
        guard mode != .ar else { virtualCam = nil; return }
        let t = CACurrentMediaTime() - flyStart
        let ease = { (s: Float) -> Float in s < 0.5 ? 4 * s * s * s : 1 - pow(-2 * s + 2, 3) / 2 }   // in-out, cubic
        if isFPV { flyFPV(t, ease); return }
        switch mode {
        case .toTop:
            if t < fadeTime {                   // 1. the camera image dissolves into the splats, the view held on ARKit's camera
                virtualCam = arCamera() ?? virtualCam
                splats.view.alpha = CGFloat(t / fadeTime)
            } else {                            // 2. then it rises, from wherever ARKit's camera was when the dissolve ended
                if !flying {                    // from where the dissolve ended (the top as planned if it cannot be framed again)
                    if let c = arCamera() ?? virtualCam { fromCam = c; if let top = topCamera(from: c) { topCam = top } }
                    flying = true
                }
                let s = min(1, Float((t - fadeTime) / flyTime))
                splats.view.alpha = 1
                virtualCam = blend(fromCam, topCam, ease(s))
                if s >= 1 { topSince = CACurrentMediaTime(); mode = .top }
            }
        case .top:
            virtualCam = topCam
            splats.view.alpha = 1
            // the view has stopped: once the splats' sort has caught up with it, one picture of them is enough
            splats.freeze = CACurrentMediaTime() - topSince > 0.5
        case .toAR:
            if t < flyTime {                    // 1. down onto ARKit's camera as it is now
                virtualCam = blend(topCam, arCamera() ?? fromCam, ease(Float(t / flyTime)))
                splats.view.alpha = 1
            } else {                            // 2. landed: the splats dissolve into the camera image
                virtualCam = arCamera() ?? virtualCam
                splats.view.alpha = CGFloat(max(0, 1 - (t - flyTime) / fadeTime))
                if t >= flyTime + fadeTime { virtualCam = nil; mode = .ar }
            }
        case .ar, .toFPV, .fpv, .fromFPV: break     // FPV: flyFPV
        }
    }

    // MARK: - FPV

    func toggleFPV() {
        switch mode {
        case .ar:
            let why = arFromScene == nil ? "まだ位置が合っていない" : splats.splatCount == 0 ? "splat がまだ" : lens == nil ? "DVR のレンズが無い（サーバーの lens.json）"
                : player.currentItem?.status != .readyToPlay ? "DVR がまだ" : droneCamera() == nil ? "飛行にドローンの向きが無い" : nil
            if let why { status = "FPV にできない：\(why)"; return }
            guard let c = arCamera() else { status = "FPV にできない：ARKit のフレームがまだ"; return }
            guard let l = lenses() else { status = "FPV にできない：DVR のレンズの値から画角を出せない"; return }
            fpvLens = l                             // kept for the whole round trip: a reload clears `lens`
            virtualCam = c; flying = false
            splats.view.alpha = 0
            flyStart = CACurrentMediaTime(); mode = .toFPV
        case .fpv:
            flying = false
            splats.view.alpha = 1
            flyStart = CACurrentMediaTime(); mode = .fromFPV
        default: break                          // already flying
        }
    }

    /// the drone's camera at the DVR's clock (camera -> scene, x right / y up / z back, as ARKit's)
    private func droneCamera() -> simd_float4x4? {
        let t = player.currentTime().seconds
        guard t.isFinite, let d = path.pose(at: t) else { return nil }
        var m = simd_float4x4(d.rot) * flip
        m.columns.3 = SIMD4(d.pos, 1)
        return m
    }

    /// pixels of the drawable, and the focal lengths of ARKit's camera and of the DVR fitted to the screen's height
    /// The drawable's pixels; the focal lengths of ARKit's camera and of the DVR with its picture (2cx x 2cy, principal point at
    /// the centre, fx for both axes) fitted to the screen's height; halfW, that picture's half width on screen; overscan, how
    /// much wider the pinhole render must reach for the bent picture's edges (its corners stay black); thMax, where the lens
    /// polynomial stops growing (an upgrade lens turns back near 1.34 rad: past it the inversion has no answer); pp, ARKit's
    /// principal point offset (projection column 2).
    private func lenses() -> Lenses? {
        guard let lens, let P = projection() else { return nil }
        let s = Float(view.contentScaleFactor), W = Float(view.bounds.width) * s, H = Float(view.bounds.height) * s
        let scale = H / (2 * lens.cy), dvr = lens.fx * scale, k = lens.k
        let g = { (th: Float) -> Float in let t2 = th * th; return 1 + t2 * (3 * k.x + t2 * (5 * k.y + t2 * (7 * k.z + t2 * 9 * k.w))) }
        let thMax = stride(from: Float(0), through: 1.5, by: 0.001).first { g($0) <= 0 } ?? 1.5
        func theta(_ td: Float) -> Float {          // as warpFragment
            var th = min(td, thMax)
            for _ in 0..<6 {
                let t2 = th * th
                th = min(max(th - (th * (1 + t2 * (k.x + t2 * (k.y + t2 * (k.z + t2 * k.w)))) - td) / max(g(th), 1e-3), 0), thMax)
            }
            return th
        }
        let overscan = max(tan(theta(lens.cx / lens.fx)) * dvr / (W / 2), tan(theta(lens.cy / lens.fx)) * dvr / (H / 2), 1)
        let out: Lenses = (SIMD2(W, H), P.columns.1.y * H / 2, dvr, lens.cx * scale, overscan, k, thMax, SIMD2(P.columns.2.x, P.columns.2.y))
        return [out.phone, out.dvr, out.halfW, out.overscan].allSatisfy { $0.isFinite && $0 > 0 } ? out : nil
    }

    /// a pinhole of focal f px over the drawable, the screen's centre on the axis (Metal depth 0...1: near 0, far 1)
    private func perspective(_ f: Float, _ size: SIMD2<Float>, near: Float = 0.05, far: Float = 100) -> simd_float4x4 {
        let a = far / (near - far), b = near * far / (near - far)
        return simd_float4x4(SIMD4(2 * f / size.x, 0, 0, 0), SIMD4(0, 2 * f / size.y, 0, 0), SIMD4(0, 0, a, -1), SIMD4(0, 0, b, 0))
    }

    /// the lens at s (0 the phone's pinhole, 1 the DVR's fisheye): the render's projection and the bend that goes with it
    private func lensAt(_ s: Float, _ l: Lenses) {
        let fOut = l.phone + (l.dvr - l.phone) * s, fTex = fOut / (1 + (l.overscan - 1) * s)
        var P = perspective(fTex, l.size)
        P.columns.2.x = l.pp.x * (1 - s); P.columns.2.y = l.pp.y * (1 - s)   // ARKit's principal point at s = 0, the DVR's centred one at 1
        virtualProj = P
        splats.warp = SplatOverlay.Warp(fOut: fOut, fTex: fTex, s: s, bars: s, halfW: s > 0 ? l.halfW : .greatestFiniteMagnitude, thMax: l.thMax, k: l.k)
    }

    private typealias Lenses = (size: SIMD2<Float>, phone: Float, dvr: Float, halfW: Float, overscan: Float, k: SIMD4<Float>, thMax: Float, pp: SIMD2<Float>)
    @ObservationIgnored private var fpvLens: Lenses?

    private func flyFPV(_ t: Double, _ ease: (Float) -> Float) {
        if mode == .fpv { if dvrAlpha != 1 { dvrAlpha = 1 }; return }
        guard let l = fpvLens else {                // never started properly: back to the camera image, not stuck between
            status = "FPV のレンズが無い"; virtualCam = nil; dvrAlpha = 0; mode = .ar; return
        }
        switch mode {
        case .toFPV:
            if t < fadeTime {                   // 1. the camera image dissolves into the splats, on ARKit's camera
                virtualCam = arCamera() ?? virtualCam; lensAt(0, l)
                splats.view.alpha = CGFloat(t / fadeTime)
            } else if t < fadeTime + flyTime {  // 2. onto the drone, the lens bending into the DVR's on the way
                if !flying { fromCam = arCamera() ?? virtualCam ?? fromCam; flying = true }
                let e = ease(Float((t - fadeTime) / flyTime))
                virtualCam = blend(fromCam, droneCamera() ?? fromCam, e); lensAt(e, l)
                splats.view.alpha = 1
            } else {                            // 3. on the drone: the splats dissolve into the DVR
                virtualCam = droneCamera() ?? virtualCam; lensAt(1, l)
                let u = (t - fadeTime - flyTime) / fadeTime
                dvrAlpha = min(1, u)
                if u >= 1 { mode = .fpv }
            }
        case .fromFPV:
            if t < fadeTime {                   // 1. the DVR dissolves into the splats, on the drone
                virtualCam = droneCamera() ?? virtualCam; lensAt(1, l)
                dvrAlpha = max(0, 1 - t / fadeTime)
            } else if t < fadeTime + flyTime {  // 2. back to ARKit's camera as it is now, the lens straightening
                if !flying { fromCam = droneCamera() ?? virtualCam ?? fromCam; flying = true }
                if dvrAlpha != 0 { dvrAlpha = 0 }   // observed: written only when it changes
                let e = ease(Float((t - fadeTime) / flyTime))
                virtualCam = blend(fromCam, arCamera() ?? fromCam, e); lensAt(1 - e, l)
            } else {                            // 3. landed: the splats dissolve into the camera image
                virtualCam = arCamera() ?? virtualCam; lensAt(0, l); if dvrAlpha != 0 { dvrAlpha = 0 }   // also when a stall skipped step 2
                splats.view.alpha = CGFloat(max(0, 1 - (t - fadeTime - flyTime) / fadeTime))
                if t >= 2 * fadeTime + flyTime { virtualCam = nil; mode = .ar }
            }
        default: break
        }
    }

    private func blend(_ a: simd_float4x4, _ b: simd_float4x4, _ t: Float) -> simd_float4x4 {
        let q = simd_slerp(simd_quatf(a), simd_quatf(b), t)
        var m = simd_float4x4(q)
        m.columns.3 = a.columns.3 + (b.columns.3 - a.columns.3) * t
        return m
    }

    /// Back to back: the next frame goes as soon as the last answer is in. ARKit drifts while walking (p50 3.6 / p90 21 cm
    /// over 2 s in the office), so the overlay is only as good as the last correction is fresh.
    @ObservationIgnored private var autoTask: Task<Void, Never>?
    func startAuto() {
        autoTask?.cancel()                      // one loop, however fast 続けて is flipped
        autoTask = Task {
            while auto && !Task.isCancelled {
                if !busy && mode != .top && mode != .fpv { await locate() }   // the top view and FPV do not use the camera image
                try? await Task.sleep(for: .milliseconds(30))   // locate returns at once while ARKit has no frame yet
            }
        }
    }

    func locate() async {
        path.viewport = SIMD2(Float(view.bounds.width * view.contentScaleFactor), Float(view.bounds.height * view.contentScaleFactor))
        guard !busy, let frame = view.session.currentFrame else { return }
        // what this frame was taken from, in ARKit's world, and the lens: both belong to capturedImage as it is (landscape)
        let pose = frame.camera.transform
        let K = frame.camera.intrinsics
        let image = CIImage(cvPixelBuffer: frame.capturedImage).transformed(by: CGAffineTransform(scaleX: sendScale, y: sendScale))
        let nums = { (v: [Float]) in v.map { String($0) }.joined(separator: ",") }
        var query = String(format: "/locate?fx=%f&fy=%f&cx=%f&cy=%f", K[0][0] * Float(sendScale), K[1][1] * Float(sendScale),
                           K[2][0] * Float(sendScale), K[2][1] * Float(sendScale))
        // ARKit's world-from-camera of this frame, column-major: logged by the server, to tell ARKit's drift from the answers' noise
        query += "&ar=" + nums([pose.columns.0, pose.columns.1, pose.columns.2, pose.columns.3].flatMap { [$0.x, $0.y, $0.z, $0.w] })
        if let T = arFromScene {                // where this camera is on the scan by the last answer: the server renders there, no retrieval
            let s = T.inverse * pose * flip; let q = simd_quatf(s)
            query += "&prior=" + nums([s.columns.3.x, s.columns.3.y, s.columns.3.z, q.imag.x, q.imag.y, q.imag.z, q.real])
        }
        guard let jpeg = ci.jpegRepresentation(of: image, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
                                               options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.85]),
              let url = URL(string: server + query) else { status = "送る絵か URL を作れない（\(server)）"; return }
        busy = true
        defer { busy = false }
        let t0 = Date()
        do {
            var req = URLRequest(url: url); req.httpMethod = "POST"; req.httpBody = jpeg; req.timeoutInterval = 5
            req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
            let r = try? JSONDecoder().decode(Answer.self, from: data)
            guard code == 200, let r else { status = "サーバーのエラー: HTTP \(code) \(r?.error ?? "")"; return }
            let rtt = Int(Date().timeIntervalSince(t0) * 1000)
            guard r.ok, let p = r.pos, let q = r.quat else {
                status = "見つからない（インライア \(r.inliers ?? 0)）"; return
            }
            // scene-from-camera in OpenCV axes
            var sceneFromCam = simd_float4x4(simd_quatf(ix: Float(q[0]), iy: Float(q[1]), iz: Float(q[2]), r: Float(q[3])))
            sceneFromCam.columns.3 = SIMD4(Float(p[0]), Float(p[1]), Float(p[2]), 1)
            let next = pose * flip * sceneFromCam.inverse
            var change = ""
            if let prev = arFromScene {         // how far the same scene point (where the camera is now) moved between two answers
                let c = SIMD4(Float(p[0]), Float(p[1]), Float(p[2]), 1)
                let d = simd_length((next * c - prev * c).xyz) * 100
                let rel = simd_quatf(prev.inverse * next)
                change = String(format: "\n前回との差 %.1f cm / %.2f°", d, rel.angle * 180 / .pi)
            }
            // eased over 0.3 s, not snapped: one answer's noise (a few cm) does not shake the cloud
            SCNTransaction.begin(); SCNTransaction.animationDuration = arFromScene == nil ? 0 : 0.3
            anchor.simdTransform = next
            SCNTransaction.commit()
            arFromScene = next
            updateVisibility()
            status = "インライア \(r.inliers ?? 0)（\(r.how ?? "")）サーバー \(Int(r.ms ?? 0)) ms / 往復 \(rtt) ms" + change + "\n" + path.info
        } catch {
            status = "送れない: \(error.localizedDescription)"
        }
    }
}

/// A display link calling back on the main thread every frame.
@MainActor
private final class Ticker: NSObject {
    private let tick: () -> Void
    init(_ tick: @escaping () -> Void) {
        self.tick = tick
        super.init()
        let link = CADisplayLink(target: self, selector: #selector(step))
        link.preferredFrameRateRange = CAFrameRateRange(minimum: 30, maximum: 30, preferred: 30)
        link.add(to: .main, forMode: .common)
    }
    @objc private func step() { tick() }
}

/// A label with room around its text.
final class PaddedLabel: UILabel {
    override func drawText(in rect: CGRect) { super.drawText(in: rect.insetBy(dx: 8, dy: 3)) }
    override var intrinsicContentSize: CGSize { let s = super.intrinsicContentSize; return CGSize(width: s.width + 16, height: s.height + 6) }
    override func sizeThatFits(_ size: CGSize) -> CGSize { intrinsicContentSize }
}

private struct Problem: LocalizedError {
    let errorDescription: String?
    init(_ s: String) { errorDescription = s }
}

private struct Flight: Decodable {
    let fps: Double?
    let path: [[Double]]
    let room: Room?
    /// the DVR's fisheye; a malformed one is dropped (lensBroken), not the whole flight
    let lens: Lens?
    let lensBroken: Bool
    /// the DVR's fisheye (OpenCV), in pixels of its 4:3 picture; fy is not used (fx ≈ fy)
    struct Lens: Decodable { let fx: Double; let cx: Double; let cy: Double; let k: [Double] }
    private enum Keys: String, CodingKey { case fps, path, room, lens }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        fps = try c.decodeIfPresent(Double.self, forKey: .fps)
        path = try c.decode([[Double]].self, forKey: .path)
        room = try c.decodeIfPresent(Room.self, forKey: .room)
        let l = try? c.decodeIfPresent(Lens.self, forKey: .lens)
        lens = l ?? nil
        lensBroken = l == nil && (try? c.decodeNil(forKey: .lens)) == false
    }
    /// the viewer's index.json "room" (scene frame, y up): x0 / z0 / y0 / circle frame the top view, y1 - margin cuts the ceiling
    struct Room: Decodable { let x0: Double; let z0: Double; let y0: Double; let y1: Double; let circle: [Double]?; let margin: Double? }
}

private struct Answer: Decodable {
    let ok: Bool
    let pos: [Double]?
    let quat: [Double]?
    let inliers: Int?
    let how: String?
    let ms: Double?
    let error: String?
}

private extension SIMD4 where Scalar == Float {
    var xyz: SIMD3<Float> { SIMD3(x, y, z) }
}
