// List (and optionally download) a USB camera's files through ImageCaptureCore.
//   swift theta.swift list
//   swift theta.swift get <out dir> <name> [<name> ...]
import Foundation
import ImageCaptureCore

let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : "list"
if mode == "get" && args.count < 4 { print("usage: theta get <out dir> <name> ..."); exit(2) }
let outDir = mode == "get" ? URL(fileURLWithPath: args[2]) : nil
let wanted = mode == "get" ? Set(args.dropFirst(3)) : []

final class D: NSObject, ICDeviceBrowserDelegate, ICCameraDeviceDelegate, ICCameraDeviceDownloadDelegate {
    var cam: ICCameraDevice?
    var pending = 0, failed = 0
    func deviceBrowser(_ b: ICDeviceBrowser, didAdd d: ICDevice, moreComing: Bool) {
        guard cam == nil, let c = d as? ICCameraDevice else { return }
        cam = c; c.delegate = self; c.requestOpenSession()
    }
    func deviceBrowser(_ b: ICDeviceBrowser, didRemove d: ICDevice, moreGoing: Bool) {}
    func device(_ d: ICDevice, didOpenSessionWithError e: Error?) { if let e { print("open:", e); exit(1) } }
    func device(_ d: ICDevice, didCloseSessionWithError e: Error?) {}
    func didRemove(_ d: ICDevice) {}
    func cameraDevice(_ c: ICCameraDevice, didAdd items: [ICCameraItem]) {}
    func cameraDevice(_ c: ICCameraDevice, didRemove items: [ICCameraItem]) {}
    func cameraDevice(_ c: ICCameraDevice, didReceiveThumbnail t: CGImage?, for i: ICCameraItem, error: Error?) {}
    func cameraDevice(_ c: ICCameraDevice, didReceiveMetadata m: [AnyHashable: Any]?, for i: ICCameraItem, error: Error?) {}
    func cameraDevice(_ c: ICCameraDevice, didRenameItems items: [ICCameraItem]) {}
    func cameraDeviceDidChangeCapability(_ c: ICCameraDevice) {}
    func cameraDevice(_ c: ICCameraDevice, didReceivePTPEvent e: Data) {}
    func cameraDeviceDidRemoveAccessRestriction(_ d: ICDevice) {}
    func cameraDeviceDidEnableAccessRestriction(_ d: ICDevice) {}
    func deviceDidBecomeReady(withCompleteContentCatalog c: ICCameraDevice) {
        let files = (c.mediaFiles ?? []).compactMap { $0 as? ICCameraFile }
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
        if mode == "list" {
            for x in files { print(x.name ?? "?", x.fileSize, f.string(from: x.creationDate ?? .distantPast)) }
            exit(0)
        }
        let todo = files.filter { wanted.contains($0.name ?? "") }
        let missing = wanted.subtracting(todo.compactMap { $0.name })
        if !missing.isEmpty { print("not on the camera: \(missing.sorted().joined(separator: " "))"); exit(1) }
        pending = todo.count
        for x in todo {
            c.requestDownloadFile(x, options: [.downloadsDirectoryURL: outDir!, .overwrite: true],
                                  downloadDelegate: self, didDownloadSelector: #selector(didDownload(_:error:options:contextInfo:)), contextInfo: nil)
        }
    }
    @objc func didDownload(_ file: ICCameraFile, error: Error?, options: [String: Any] = [:], contextInfo: UnsafeMutableRawPointer?) {
        print(error == nil ? "got \(file.name ?? "?")" : "fail \(file.name ?? "?"): \(error!)")
        if error != nil { failed += 1 }
        pending -= 1; if pending == 0 { exit(failed == 0 ? 0 : 1) }
    }
}

let d = D(); let b = ICDeviceBrowser(); b.delegate = d; b.start()
RunLoop.main.run(until: Date().addingTimeInterval(mode == "list" ? 60 : 3600))
print("timeout"); exit(2)
