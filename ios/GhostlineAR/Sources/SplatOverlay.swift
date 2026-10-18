import ARKit
import MetalKit
import MetalSplatter
import SplatIO

/// The scan's splats, drawn by MetalSplatter into an MTKView laid over the ARSCNView, through whatever camera `camera` gives
/// for this frame: ARKit's own (to check the alignment, and where a transition starts and ends, so the splats sit on the
/// camera image) or the top view's. With `flight`, the trail and the drone are drawn over the splats in a second pass.
@MainActor
final class SplatOverlay: NSObject, MTKViewDelegate {
    let view: MTKView
    private let device: MTLDevice
    private let queue: MTLCommandQueue
    private var renderer: SplatRenderer?
    private let inFlight = DispatchSemaphore(value: 3)
    /// (scene -> camera, projection) for this frame; nil draws nothing
    var camera: () -> (view: simd_float4x4, proj: simd_float4x4)? = { nil }
    var flight: PathRenderer?
    /// The FPV transition: the frame is drawn through `camera` (a pinhole of focal fTex px, wider than the screen once the lens
    /// bends in) into a texture, then bent onto the screen through the DVR's fisheye (Points.metal warpFragment). Layout as WU.
    struct Warp { var size = SIMD2<Float>(0, 0); var fOut: Float; var fTex: Float; var s: Float; var bars: Float; var halfW: Float; var thMax: Float; var k: SIMD4<Float> }
    var warp: Warp? { didSet { if warp == nil { wide = nil } } }   // the texture is let go between FPV round trips
    private var wide: MTLTexture?
    private var warpPipe: MTLRenderPipelineState?

    override init() {
        device = MTLCreateSystemDefaultDevice()!
        queue = device.makeCommandQueue()!
        view = MTKView(frame: .zero, device: device)
        super.init()
        view.colorPixelFormat = .bgra8Unorm_srgb
        view.depthStencilPixelFormat = .depth32Float
        view.sampleCount = 1
        view.clearColor = MTLClearColor(red: 0, green: 0, blue: 0, alpha: 0)
        view.isOpaque = false
        view.backgroundColor = .clear
        view.isUserInteractionEnabled = false
        view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.framebufferOnly = false
        view.preferredFramesPerSecond = 30         // with the AR view: 60 throttled the phone
        view.delegate = self
    }

    var splatCount: Int { renderer?.splatCount ?? 0 }

    /// the splats above `ceiling` m, kept as their own chunk: the top view looks through the room's ceiling (as the PC viewer
    /// keeps the cut-off part, index.json "top"), the AR view and a camera still inside the room need it
    private var ceilingChunk: ChunkID?
    private var ceilingShown = true

    func load(_ data: Data, ceiling: Float?) async throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("scan-\(UUID().uuidString).splat")   // its own file per load
        try data.write(to: url)
        defer { try? FileManager.default.removeItem(at: url) }
        ceilingChunk = nil                          // a reload: the last renderer's chunk ID means nothing to the new one
        let points = try await AutodetectSceneReader(url).readAll()
        let r = try SplatRenderer(device: device, colorFormat: view.colorPixelFormat, depthFormat: view.depthStencilPixelFormat,
                                  sampleCount: view.sampleCount, maxViewCount: 1, maxSimultaneousRenders: 3)
        if let ceiling {
            let below = points.filter { $0.position.y <= ceiling }, above = points.filter { $0.position.y > ceiling }
            await r.addChunk(try SplatChunk(device: device, from: below))
            if !above.isEmpty { ceilingChunk = await r.addChunk(try SplatChunk(device: device, from: above)) }
        } else {
            await r.addChunk(try SplatChunk(device: device, from: points))
        }
        ceilingShown = true
        renderer = r
    }

    /// Show or hide the ceiling chunk (from the next sort on; the chunk keeps being sorted while hidden).
    func showCeiling(_ on: Bool) {
        guard on != ceilingShown, let renderer, let id = ceilingChunk else { return }
        ceilingShown = on
        Task { await renderer.setChunkEnabled(id, enabled: on) }
    }

    /// true: the camera has stopped (the top view) - the next frame's splats are kept, and from then on only that picture is
    /// copied in under the flight; the splats are not sorted or drawn again until it is false
    var freeze = false { didSet { if !freeze { still = nil } } }
    private var still: MTLTexture?

    func draw(in view: MTKView) {
        guard let renderer, renderer.isReadyToRender, let cam = camera(), let drawable = view.currentDrawable else { return }
        let px = view.drawableSize
        if var w = warp { drawWarped(&w, cam, drawable, px, renderer); return }
        if freeze, let still, still.width == drawable.texture.width, still.height == drawable.texture.height {
            _ = inFlight.wait(timeout: .distantFuture)
            guard let cb = queue.makeCommandBuffer() else { inFlight.signal(); return }
            let sem = inFlight
            cb.addCompletedHandler { _ in sem.signal() }
            if let blit = cb.makeBlitCommandEncoder() { blit.copy(from: still, to: drawable.texture); blit.endEncoding() }
            encodeFlight(cb, drawable.texture, cam, px)
            cb.present(drawable)
            cb.commit()
            return
        }
        let viewport = SplatRenderer.ViewportDescriptor(
            viewport: MTLViewport(originX: 0, originY: 0, width: px.width, height: px.height, znear: 0, zfar: 1),
            projectionMatrix: cam.proj, viewMatrix: cam.view, screenSize: SIMD2(Int(px.width), Int(px.height)))
        _ = inFlight.wait(timeout: .distantFuture)
        guard let cb = queue.makeCommandBuffer() else { inFlight.signal(); return }
        let sem = inFlight
        cb.addCompletedHandler { _ in sem.signal() }
        let drawn = (try? renderer.render(viewports: [viewport], colorTexture: drawable.texture, colorStoreAction: .store,
                                          depthTexture: view.depthStencilTexture, rasterizationRateMap: nil,
                                          renderTargetArrayLength: 0, to: cb)) ?? false
        if drawn, freeze, still == nil {            // keep this frame's splats (before the flight goes over them)
            let d = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: drawable.texture.pixelFormat, width: drawable.texture.width,
                                                             height: drawable.texture.height, mipmapped: false)
            d.storageMode = .private; d.usage = [.shaderRead]
            if let t = device.makeTexture(descriptor: d), let blit = cb.makeBlitCommandEncoder() {
                blit.copy(from: drawable.texture, to: t); blit.endEncoding(); still = t
            }
        }
        if drawn { encodeFlight(cb, drawable.texture, cam, px) }
        if drawn { cb.present(drawable) }
        cb.commit()
    }

    private func drawWarped(_ w: inout Warp, _ cam: (view: simd_float4x4, proj: simd_float4x4), _ drawable: CAMetalDrawable, _ px: CGSize,
                            _ renderer: SplatRenderer) {
        let W = Int(px.width), H = Int(px.height)
        if wide == nil || wide!.width != W || wide!.height != H {
            let c = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: view.colorPixelFormat, width: W, height: H, mipmapped: false)
            c.usage = [.renderTarget, .shaderRead]; c.storageMode = .private
            wide = device.makeTexture(descriptor: c)
        }
        if warpPipe == nil, let lib = device.makeDefaultLibrary() {
            let p = MTLRenderPipelineDescriptor()
            p.vertexFunction = lib.makeFunction(name: "warpVertex"); p.fragmentFunction = lib.makeFunction(name: "warpFragment")
            p.colorAttachments[0].pixelFormat = view.colorPixelFormat
            warpPipe = try? device.makeRenderPipelineState(descriptor: p)
        }
        guard let wide, let warpPipe, let depth = view.depthStencilTexture else { return }   // the view's own depth: same size
        let viewport = SplatRenderer.ViewportDescriptor(
            viewport: MTLViewport(originX: 0, originY: 0, width: px.width, height: px.height, znear: 0, zfar: 1),
            projectionMatrix: cam.proj, viewMatrix: cam.view, screenSize: SIMD2(W, H))
        _ = inFlight.wait(timeout: .distantFuture)
        guard let cb = queue.makeCommandBuffer() else { inFlight.signal(); return }
        let sem = inFlight
        cb.addCompletedHandler { _ in sem.signal() }
        let drawn = (try? renderer.render(viewports: [viewport], colorTexture: wide, colorStoreAction: .store,
                                          depthTexture: depth, rasterizationRateMap: nil, renderTargetArrayLength: 0, to: cb)) ?? false
        guard drawn else { cb.commit(); return }
        encodeFlight(cb, wide, cam, px)
        let rp = MTLRenderPassDescriptor()
        rp.colorAttachments[0].texture = drawable.texture
        rp.colorAttachments[0].loadAction = .dontCare
        rp.colorAttachments[0].storeAction = .store
        if let enc = cb.makeRenderCommandEncoder(descriptor: rp) {
            w.size = SIMD2(Float(W), Float(H))
            enc.setRenderPipelineState(warpPipe)
            enc.setFragmentTexture(wide, index: 0)
            enc.setFragmentBytes(&w, length: MemoryLayout<Warp>.stride, index: 0)
            enc.drawPrimitives(type: .triangle, vertexStart: 0, vertexCount: 3)
            enc.endEncoding()
        }
        cb.present(drawable)
        cb.commit()
    }

    /// the trail and the drone over what is already in the texture
    private func encodeFlight(_ cb: MTLCommandBuffer, _ target: MTLTexture, _ cam: (view: simd_float4x4, proj: simd_float4x4), _ px: CGSize) {
        guard let flight else { return }
        let rp = MTLRenderPassDescriptor()
        rp.colorAttachments[0].texture = target
        rp.colorAttachments[0].loadAction = .load
        rp.colorAttachments[0].storeAction = .store
        guard let enc = cb.makeRenderCommandEncoder(descriptor: rp) else { return }
        flight.encodeFlight(enc, device: device, mvp: cam.proj * cam.view, viewport: SIMD2(Float(px.width), Float(px.height)),
                            color: view.colorPixelFormat, depth: .invalid, stencil: .invalid, depthState: nil)
        enc.endEncoding()
    }

    func mtkView(_ view: MTKView, drawableSizeWillChange size: CGSize) {}
}
