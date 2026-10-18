import ARKit
import SceneKit
import CoreImage
import Observation
import AVFoundation

/// One ARKit frame goes to the VPS (tools/dvr/rt_vps.py), which answers where that camera is on the scan. With the ARKit pose
/// of the same frame that gives ARKit-from-scene, and the scan's points are drawn through it over the camera image.
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
    enum Mode { case ar, toTop, top, toAR }
    private(set) var mode = Mode.ar { didSet { updateVisibility() } }
    @ObservationIgnored private var flyStart = 0.0
    @ObservationIgnored private var fromCam = matrix_identity_float4x4   // camera -> scene, x right / y up / z back
    @ObservationIgnored private var topCam = matrix_identity_float4x4
    @ObservationIgnored private var virtualCam: simd_float4x4?
    @ObservationIgnored private var flying = false   // toTop: the dissolve is over and the camera has left ARKit's
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
    /// Sent image size relative to the captured one: the server scales to the map's 640 wide anyway.
    private let sendScale: CGFloat = 0.5
    /// ARKit's camera has y up and z back; the server's (OpenCV) y down and z forward.
    private let flip = simd_float4x4(diagonal: SIMD4(1, -1, -1, 1))

    func makeView() -> ARSCNView {
        view.scene = SCNScene()
        view.automaticallyUpdatesLighting = false
        view.rendersCameraGrain = false
        view.antialiasingMode = .none          // PointRenderer's pipeline is single-sample
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
            // the drone projected to the screen, the tag just above it
            guard arFromScene != nil, let p = path.position(at: player.currentTime().seconds) else { tag.isHidden = true; return }
            if mode == .ar {
                let w = anchor.presentation.simdWorldTransform * SIMD4(p, 1)
                let s = view.projectPoint(SCNVector3(w.x, w.y, w.z))
                tag.isHidden = s.z <= 0 || s.z >= 1    // behind the camera
                tag.center = CGPoint(x: CGFloat(s.x), y: CGFloat(s.y) - 22)
            } else if let c = virtualCam, let P = projection() {
                let clip = P * c.inverse * SIMD4(p, 1)
                tag.isHidden = clip.w <= 0
                let b = view.bounds.size
                tag.center = CGPoint(x: CGFloat(clip.x / clip.w + 1) / 2 * b.width, y: CGFloat(1 - clip.y / clip.w) / 2 * b.height - 22)
            }
        }
        player.addPeriodicTimeObserver(forInterval: CMTime(value: 1, timescale: 60), queue: .main) { [path] t in path.cursor = t.seconds }
        splats.view.frame = view.bounds
        splats.view.alpha = opacity
        splats.view.isHidden = true
        splats.view.isPaused = true
        view.addSubview(splats.view)
        splats.camera = { [unowned self] in
            guard let c = mode == .ar ? arCamera() : virtualCam, let P = projection() else { return nil }
            return (c.inverse, P)
        }
        let config = ARWorldTrackingConfiguration()
        config.worldAlignment = .gravity
        // the map's keyframes are 4:3; the server checks the aspect
        let fourThree = ARWorldTrackingConfiguration.supportedVideoFormats.filter { Int($0.imageResolution.width) * 3 == Int($0.imageResolution.height) * 4 }
        if let f = fourThree.first(where: { $0.framesPerSecond == 30 }) ?? fourThree.first {   // 30 fps, as the views draw
            config.videoFormat = f
        }
        view.session.run(config)
        return view
    }

    func loadPoints(server: String) {
        guard let url = URL(string: server + "/points?n=300000") else { return }
        status = "点群を読んでいる…"
        Task {
            do {
                let (data, _) = try await URLSession.shared.data(from: url)
                let n = data.count / 16
                points.setPoints(data, device: view.device!)
                nPoints = n
                status = "点 \(n) 個。「合わせる」（画面をタップで splat と切り替え）"
                updateVisibility()
            } catch {
                status = "読めない: \(error.localizedDescription)"
            }
        }
    }

    /// The recorded flight: its path, and its DVR into a file for the player (the server sends the whole mp4, no ranges).
    func loadFlight(server: String) {
        guard let fu = URL(string: server + "/flight"), let du = URL(string: server + "/dvr") else { return }
        Task {
            do {
                let (fdata, _) = try await URLSession.shared.data(from: fu)
                let f = try JSONDecoder().decode(Flight.self, from: fdata)
                rawPath = f.path
                path.setPath(f.path, device: view.device!, ws: smoothing)
                if let ou = URL(string: server + "/occluders?n=300000") {
                    let (odata, _) = try await URLSession.shared.data(from: ou)
                    path.setOccluders(odata, device: view.device!)
                }
                let (ddata, _) = try await URLSession.shared.data(from: du)
                let file = FileManager.default.temporaryDirectory.appendingPathComponent("dvr.mp4")
                try ddata.write(to: file)
                player.replaceCurrentItem(with: AVPlayerItem(url: file))
                // the splats, after the room: what is above its ceiling (pulled in by its margin) goes in a chunk of its own
                if let r = f.room {
                    ceiling = Float(r.y1 - (r.margin ?? 0.25))
                    room = (Float(r.x0), Float(r.z0), Float(r.y0), r.circle?.map { Float($0) })
                }
                if let surl = URL(string: server + "/splats?n=600000") {
                    let (sdata, _) = try await URLSession.shared.data(from: surl)
                    try await splats.load(sdata, ceiling: ceiling)
                }
            } catch {
                status = "飛行を読めない: \(error.localizedDescription)"
            }
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
        let splatsOn = !ar || showSplats
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
            virtualCam = c; flying = false
            splats.view.alpha = 0
            flyStart = CACurrentMediaTime(); mode = .toTop
        case .top:
            splats.freeze = false
            flyStart = CACurrentMediaTime(); mode = .toAR
        default: break                          // already flying
        }
    }

    /// Straight down. With the room known: the whole floor, its right-angle corner (x0, z0) bottom left - the x0 wall up the
    /// left edge, the z0 wall along the bottom, the facade's arc across the top right (the room's long side, z, across the
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
                xmax = cx + (rad * rad - dz * dz).squareRoot(); zmax = cz + (rad * rad - dx * dx).squareRoot()
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
        switch mode {
        case .toTop:
            if t < fadeTime {                   // 1. the camera image dissolves into the splats, the view held on ARKit's camera
                virtualCam = arCamera() ?? virtualCam
                splats.view.alpha = CGFloat(t / fadeTime)
            } else {                            // 2. then it rises, from wherever ARKit's camera was when the dissolve ended
                if !flying, let c = arCamera() ?? virtualCam, let top = topCamera(from: c) { fromCam = c; topCam = top; flying = true }
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
        case .ar: break
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
    func startAuto(server: String) {
        Task {
            while auto {
                if !busy && mode != .top { await locate(server: server) }   // the top view does not use the camera image
                try? await Task.sleep(for: .milliseconds(30))   // locate returns at once while ARKit has no frame yet
            }
        }
    }

    func locate(server: String) async {
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
              let url = URL(string: server + query) else { return }
        busy = true
        defer { busy = false }
        let t0 = Date()
        do {
            var req = URLRequest(url: url); req.httpMethod = "POST"; req.httpBody = jpeg
            req.setValue("image/jpeg", forHTTPHeaderField: "Content-Type")
            let (data, _) = try await URLSession.shared.data(for: req)
            let r = try JSONDecoder().decode(Answer.self, from: data)
            let rtt = Int(Date().timeIntervalSince(t0) * 1000)
            guard r.ok, let p = r.pos, let q = r.quat else {
                status = "見つからない（インライア \(r.inliers ?? 0)）\(r.error ?? "")"; return
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

private struct Flight: Decodable {
    let fps: Double?
    let path: [[Double]]
    let room: Room?
    /// the viewer's index.json "room" (scene frame, y up); only its height is used here
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
