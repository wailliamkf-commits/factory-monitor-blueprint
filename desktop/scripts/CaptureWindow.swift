import AppKit
import CoreMedia
import CoreVideo
import Foundation
import ScreenCaptureKit

private func appendBE<T: FixedWidthInteger>(_ value: T, to data: inout Data) {
    var encoded = value.bigEndian
    withUnsafeBytes(of: &encoded) { data.append(contentsOf: $0) }
}

final class Output: NSObject, SCStreamOutput, @unchecked Sendable {
    private let lock = NSLock()
    private var callbacks = 0
    func callbackCount() -> Int { lock.lock(); defer { lock.unlock() }; return callbacks }

    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        lock.lock(); callbacks += 1; let n = callbacks; lock.unlock()
        let attachments = CMSampleBufferGetSampleAttachmentsArray(buffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]]
        let rawStatus = attachments?.first?[.status] as? Int
        let pts = CMSampleBufferGetPresentationTimeStamp(buffer).seconds
        let image = buffer.imageBuffer
        let detail = "CALLBACK n=\(n) type=\(type) valid=\(buffer.isValid) image_buffer=\(image != nil) status=\(rawStatus.map(String.init) ?? "missing") sample_pts=\(pts)"
        FileHandle.standardError.write(Data((detail + "\n").utf8))
        guard type == .screen, buffer.isValid, rawStatus == SCFrameStatus.complete.rawValue, let image else { return }
        CVPixelBufferLockBaseAddress(image, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(image, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(image) else {
            FileHandle.standardError.write(Data("CALLBACK_IMAGE_ERROR n=\(n) reason=no_base_address\n".utf8))
            return
        }
        let width = CVPixelBufferGetWidth(image), height = CVPixelBufferGetHeight(image)
        let stride = CVPixelBufferGetBytesPerRow(image), rowBytes = width * 4
        var pixels = Data(capacity: rowBytes * height)
        for row in 0..<height {
            pixels.append(base.advanced(by: row * stride).assumingMemoryBound(to: UInt8.self), count: rowBytes)
        }
        let status = UInt32(SCFrameStatus.complete.rawValue)
        var packet = Data(capacity: 32 + pixels.count)
        appendBE(Date().timeIntervalSince1970.bitPattern, to: &packet) // callback wall clock, not source-video PTS
        appendBE(pts.bitPattern, to: &packet)                         // ScreenCaptureKit sample PTS
        appendBE(status, to: &packet)
        appendBE(UInt32(width), to: &packet)
        appendBE(UInt32(height), to: &packet)
        appendBE(UInt32(pixels.count), to: &packet)
        packet.append(pixels)
        FileHandle.standardOutput.write(packet)
    }
}

final class CaptureKeeper: @unchecked Sendable {
    let stream: SCStream
    let output: Output
    init(stream: SCStream, output: Output) { self.stream = stream; self.output = output }
}

@main
struct CaptureWindow {
    static func main() async {
        guard #available(macOS 15.0, *) else {
            FileHandle.standardError.write(Data("macOS 15 or newer is required\n".utf8)); exit(2)
        }
        let args = CommandLine.arguments
        guard args.count == 5 || args.count == 7,
              let pid = Int32(args[1]), let width = Int(args[3]), let height = Int(args[4]),
              pid > 0, width > 0, height > 0, width <= 4096, height <= 4096, width * height <= 8_388_608 else {
            FileHandle.standardError.write(Data("usage: helper PID EXACT_TITLE WIDTH HEIGHT [--screenshot PNG_PATH]\n".utf8)); exit(2)
        }
        _ = NSApplication.shared
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let matches = content.windows.filter { $0.owningApplication?.processID == pid && $0.title == args[2] }
            guard matches.count == 1, let window = matches.first else {
                throw NSError(domain: "ExactWindowProbe", code: 3, userInfo: [NSLocalizedDescriptionKey:
                    "expected exactly one on-screen window for pid=\(pid), title=\(args[2]); found \(matches.count)"])
            }
            let filter = SCContentFilter(desktopIndependentWindow: window)
            let config = SCStreamConfiguration()
            config.width = width; config.height = height
            config.minimumFrameInterval = CMTime(value: 1, timescale: 5)
            config.queueDepth = 3; config.pixelFormat = kCVPixelFormatType_32BGRA
            config.showsCursor = false; config.capturesAudio = false; config.captureMicrophone = false

            if args.count == 7 && args[5] == "--screenshot" {
                let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
                let rep = NSBitmapImageRep(cgImage: image)
                guard let png = rep.representation(using: .png, properties: [:]) else {
                    throw NSError(domain: "ExactWindowProbe", code: 4, userInfo: [NSLocalizedDescriptionKey: "could not encode the target-window image"])
                }
                try png.write(to: URL(fileURLWithPath: args[6]), options: .atomic)
                FileHandle.standardError.write(Data("SCREENSHOT_OK pid=\(pid) window_id=\(window.windowID) title=\(window.title ?? "") width=\(image.width) height=\(image.height)\n".utf8))
                return
            }

            let output = Output()
            let stream = SCStream(filter: filter, configuration: config, delegate: nil)
            let keeper = CaptureKeeper(stream: stream, output: output)
            try keeper.stream.addStreamOutput(keeper.output, type: .screen,
                                              sampleHandlerQueue: DispatchQueue(label: "probe.frames"))
            try await keeper.stream.startCapture()
            FileHandle.standardError.write(Data("READY pid=\(pid) window_id=\(window.windowID) title=\(window.title ?? "")\n".utf8))
            while true {
                try await Task.sleep(for: .seconds(2))
                withExtendedLifetime(keeper) {
                    FileHandle.standardError.write(Data("KEEPALIVE callbacks=\(keeper.output.callbackCount())\n".utf8))
                }
            }
        } catch {
            FileHandle.standardError.write(Data("CAPTURE_ERROR \(error.localizedDescription)\n".utf8)); exit(1)
        }
    }
}
