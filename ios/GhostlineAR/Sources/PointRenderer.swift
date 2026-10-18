import SceneKit
import Metal
import simd

/// Draws the scan's points for one node inside SceneKit's own render pass, with the node's model-view-projection of that very
/// frame - so they never lag the camera image (a second view on top did, by a frame), and with real alpha blending, which
/// SceneKit's .point primitives ignore (node opacity and material transparency left them fully opaque on the iPhone).
/// Called on SceneKit's render thread.
final class PointRenderer: NSObject, SCNNodeRendererDelegate, @unchecked Sendable {
    private struct Uniforms { var mvp: simd_float4x4; var alpha: Float; var size: Float }
    private let lock = NSLock()
    private var buffer: MTLBuffer?
    private var count = 0
    private var pipeline: MTLRenderPipelineState?
    private var depth: MTLDepthStencilState?
    private var _alpha: Float = 0.8
    var alpha: Float {
        get { lock.withLock { _alpha } }
        set { lock.withLock { _alpha = newValue } }
    }
    /// pixels across
    var size: Float = 2

    func setPoints(_ data: Data, device: MTLDevice) {
        guard !data.isEmpty else { return }
        let b = data.withUnsafeBytes { device.makeBuffer(bytes: $0.baseAddress!, length: data.count, options: .storageModeShared) }
        lock.withLock { buffer = b; count = data.count / 16 }
    }

    func renderNode(_ node: SCNNode, renderer: SCNRenderer, arguments: [String: Any]) {
        guard let encoder = renderer.currentRenderCommandEncoder, let device = renderer.device,
              let m = arguments[SCNModelViewProjectionTransform] as? NSValue else { return }
        let (b, n, a) = lock.withLock { (buffer, count, _alpha) }
        guard let b, n > 0 else { return }
        if pipeline == nil {
            guard let lib = device.makeDefaultLibrary() else { return }
            let d = MTLRenderPipelineDescriptor()
            d.vertexFunction = lib.makeFunction(name: "pointsVertex")
            d.fragmentFunction = lib.makeFunction(name: "pointsFragment")
            let c = d.colorAttachments[0]!
            c.pixelFormat = renderer.colorPixelFormat
            c.isBlendingEnabled = true
            c.sourceRGBBlendFactor = .one; c.destinationRGBBlendFactor = .oneMinusSourceAlpha
            c.sourceAlphaBlendFactor = .one; c.destinationAlphaBlendFactor = .oneMinusSourceAlpha
            d.depthAttachmentPixelFormat = renderer.depthPixelFormat
            d.stencilAttachmentPixelFormat = renderer.stencilPixelFormat
            d.rasterSampleCount = 1
            pipeline = try? device.makeRenderPipelineState(descriptor: d)
            let s = MTLDepthStencilDescriptor(); s.depthCompareFunction = .always; s.isDepthWriteEnabled = false
            depth = device.makeDepthStencilState(descriptor: s)
        }
        guard let pipeline else { return }
        var u = Uniforms(mvp: simd_float4x4(m.scnMatrix4Value), alpha: a, size: size)
        encoder.setRenderPipelineState(pipeline)
        encoder.setDepthStencilState(depth)
        encoder.setVertexBuffer(b, offset: 0, index: 0)
        encoder.setVertexBytes(&u, length: MemoryLayout<Uniforms>.stride, index: 1)
        encoder.drawPrimitives(type: .point, vertexStart: 0, vertexCount: n)
    }
}
