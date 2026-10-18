import SwiftUI
import ARKit
import AVKit

/// Landscape: the room with the scan and the flight over it, the flight's DVR top right (its own controls play and scrub it,
/// and the drone on the path follows its clock), the alignment controls bottom left behind the top-left button, and what
/// failed to load always on show.
struct ContentView: View {
    @State private var ar = ARController()
    @AppStorage("server") private var server = "http://rt4090:8790"
    @State private var editing = false
    @State private var controls = false

    var body: some View {
        ZStack {
            ARViewContainer(ar: ar).ignoresSafeArea()
                .onTapGesture { if ar.mode == .ar { ar.showSplats.toggle() } }
            if ar.dvrAlpha > 0 {                    // FPV: the same player, full screen, height-fitted, black either side
                PlayerLayerView(player: ar.player).ignoresSafeArea().opacity(ar.dvrAlpha).allowsHitTesting(false)
            }
            VStack {
                HStack(alignment: .top) {
                    VStack(spacing: 12) {
                        Button { controls.toggle() } label: {
                            Image(systemName: controls ? "xmark.circle.fill" : "slider.horizontal.3")
                                .font(.title2).foregroundStyle(.white)
                                .frame(width: 30, height: 30)
                                .padding(8).background(.black.opacity(0.4), in: Circle())
                        }
                        if !ar.isFPV {
                            Button { ar.toggleTop() } label: {   // the camera image <-> the scan from above
                                Image(systemName: ar.mode == .ar || ar.mode == .toAR ? "map" : "camera.viewfinder")
                                    .font(.title2).foregroundStyle(.white)
                                    .frame(width: 30, height: 30)
                                    .padding(8).background(.black.opacity(0.4), in: Circle())
                            }
                        }
                        if ar.mode == .ar || ar.isFPV {
                            Button { ar.toggleFPV() } label: {   // the camera image <-> the drone's own view (the DVR)
                                Image(systemName: ar.mode == .ar || ar.mode == .fromFPV ? "airplane" : "camera.viewfinder")
                                    .font(.title2).foregroundStyle(.white)
                                    .frame(width: 30, height: 30)
                                    .padding(8).background(.black.opacity(0.4), in: Circle())
                            }
                        }
                    }
                    Spacer()
                    VideoPlayer(player: ar.player)          // dvr.mp4 is cut to 4:3 beforehand (docs/ar-iphone.ja.md)
                        .aspectRatio(4 / 3, contentMode: .fit)
                        .frame(width: 220)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .opacity(1 - ar.dvrAlpha)           // full screen in FPV
                }
                Spacer()
                if !ar.problems.isEmpty {
                    Text(ar.problems.sorted { $0.key < $1.key }.map { "\($0.key): \($0.value)" }.joined(separator: "\n"))
                        .font(.system(.caption2, design: .monospaced))
                        .padding(6)
                        .background(.red.opacity(0.7), in: RoundedRectangle(cornerRadius: 6))
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if controls { HStack(alignment: .bottom) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(ar.status)
                            .font(.system(.caption2, design: .monospaced))
                            .padding(6)
                            .background(.black.opacity(0.6), in: RoundedRectangle(cornerRadius: 6))
                            .foregroundStyle(.white)
                        HStack(spacing: 10) {
                            Button("読む") { ar.loadPoints(); ar.loadFlight() }
                                .buttonStyle(.bordered)
                            Button("合わせる") { Task { await ar.locate() } }
                                .buttonStyle(.borderedProminent)
                                .disabled(ar.auto)
                            Toggle("続けて", isOn: $ar.auto).fixedSize()
                                .foregroundStyle(.white)
                            Toggle("隠す", isOn: $ar.occlude).fixedSize()
                                .foregroundStyle(.white)
                            Text("点の濃さ").foregroundStyle(.white).font(.caption)
                            Slider(value: $ar.opacity, in: 0...1).frame(width: 140)
                        }
                        HStack(spacing: 10) {
                            Text(String(format: "なめらか ±%.2f 秒", ar.smoothing)).foregroundStyle(.white).font(.caption.monospacedDigit())
                            Slider(value: $ar.smoothing, in: 0.2...1.5, step: 0.05) { editing in if !editing { ar.resmooth() } }
                                .frame(width: 220)
                        }
                        Button(server) { editing = true }
                            .font(.caption2).foregroundStyle(.white.opacity(0.7))
                    }
                    Spacer()
                } }
            }
            .padding()
        }
        .onChange(of: ar.auto) { _, on in if on { ar.startAuto() } }
        .task {
            ar.loadPoints()
            ar.loadFlight()
            if ar.auto { ar.startAuto() }           // on by default; onChange only fires on a change
        }
        .alert("サーバー", isPresented: $editing) {
            TextField("http://host:8790", text: $server).textInputAutocapitalization(.never).autocorrectionDisabled()
            Button("OK") { ar.loadPoints(); ar.loadFlight() }   // the loop reads the new address itself
        }
    }
}

/// An AVPlayerLayer on black: the picture fitted to the height of a landscape screen leaves black either side.
struct PlayerLayerView: UIViewRepresentable {
    let player: AVPlayer
    final class V: UIView {
        override class var layerClass: AnyClass { AVPlayerLayer.self }
        var playerLayer: AVPlayerLayer { layer as! AVPlayerLayer }
    }
    func makeUIView(context: Context) -> V {
        let v = V(); v.backgroundColor = .black
        v.playerLayer.videoGravity = .resizeAspect; v.playerLayer.player = player
        return v
    }
    func updateUIView(_ v: V, context: Context) {}
}

struct ARViewContainer: UIViewRepresentable {
    let ar: ARController
    func makeUIView(context: Context) -> ARSCNView { ar.makeView() }
    func updateUIView(_ uiView: ARSCNView, context: Context) {}
}
