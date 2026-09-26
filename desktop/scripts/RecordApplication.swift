// Native macOS 15+ screen recording; includes only the explicitly selected PID.
import AppKit
import AVFoundation
import Foundation
import ScreenCaptureKit

final class FrameCounter: NSObject, SCStreamOutput, @unchecked Sendable {
    private let lock = NSLock()
    private var frames = 0
    private var statuses: [Int: Int] = [:]
    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        if type == .screen && buffer.isValid {
            let attachment = CMSampleBufferGetSampleAttachmentsArray(buffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]]
            let status = attachment?.first?[.status] as? Int ?? -1
            lock.lock(); frames += 1; statuses[status, default: 0] += 1
            let n = frames; lock.unlock()
            if n <= 5 || n % 60 == 0 {
                FileHandle.standardOutput.write(Data("SAMPLE n=\(n) status=\(status) pts=\(CMSampleBufferGetPresentationTimeStamp(buffer).seconds)\n".utf8))
            }
        }
    }
    func count() -> Int { lock.lock(); defer { lock.unlock() }; return frames }
    func summary() -> [Int: Int] { lock.lock(); defer { lock.unlock() }; return statuses }
}

@available(macOS 15.0, *)
final class RecordingDelegate: NSObject, SCRecordingOutputDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var done = false
    private var failure: String?
    func recordingOutputDidStartRecording(_ output: SCRecordingOutput) {
        FileHandle.standardOutput.write(Data("RECORDING_STARTED\n".utf8))
    }
    func recordingOutput(_ output: SCRecordingOutput, didFailWithError error: Error) {
        lock.lock(); failure = error.localizedDescription; done = true; lock.unlock()
    }
    func recordingOutputDidFinishRecording(_ output: SCRecordingOutput) {
        lock.lock(); done = true; lock.unlock()
    }
    func status() -> (Bool, String?) {
        lock.lock(); defer { lock.unlock() }; return (done, failure)
    }
}

final class StreamDelegate: NSObject, SCStreamDelegate, @unchecked Sendable {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        FileHandle.standardError.write(Data("STREAM_STOPPED \(error as NSError)\n".utf8))
    }
}

@main
struct RecordApplication {
    static func validateMedia(_ destination: URL, expected: Double) async throws -> Double {
        let asset = AVURLAsset(url: destination)
        let duration = try await asset.load(.duration).seconds
        let tracks = try await asset.loadTracks(withMediaType: .video)
        guard let track = tracks.first else { throw NSError(domain: "Recorder", code: 6) }
        let reader = try AVAssetReader(asset: asset)
        let output = AVAssetReaderTrackOutput(track: track, outputSettings: nil)
        reader.add(output)
        guard reader.startReading() else { throw reader.error ?? NSError(domain: "Recorder", code: 6) }
        var count = 0
        while let sample = output.copyNextSampleBuffer() { count += CMSampleBufferGetNumSamples(sample) }
        guard reader.status == .completed && duration.isFinite && duration >= expected * 0.9
              && count >= Int(expected * 2) else {
            throw NSError(domain: "Recorder", code: 6, userInfo: [NSLocalizedDescriptionKey:
                "Rejected incomplete dynamic demo: expected \(expected)s with >=2 fps, asset duration \(duration)s, actual video samples \(count)"])
        }
        return duration
    }
    static func main() async {
        guard #available(macOS 15.0, *) else { exit(2) }
        let args = CommandLine.arguments
        if args.count == 4 && args[1] == "--verify-only", let expected = Double(args[3]),
           expected.isFinite && expected >= 1 && expected <= 240 {
            do {
                let duration = try await validateMedia(URL(fileURLWithPath: args[2]), expected: expected)
                print("MEDIA_VALIDATED duration=\(duration)")
                return
            } catch {
                FileHandle.standardError.write(Data("Media validation failed: \(error.localizedDescription)\n".utf8))
                exit(1)
            }
        }
        guard (5...6).contains(args.count), let pid = Int32(args[1]), let seconds = Double(args[3]),
              seconds >= 1, seconds <= 240 else {
            FileHandle.standardError.write(Data("Usage: record-app PID exact-window-title seconds output.mp4\n".utf8))
            exit(2)
        }
        do {
            let destination = URL(fileURLWithPath: args[4])
            guard !FileManager.default.fileExists(atPath: destination.path) else {
                throw NSError(domain: "Recorder", code: 2, userInfo: [NSLocalizedDescriptionKey: "Output already exists"])
            }
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            let windows = content.windows.filter { $0.owningApplication?.processID == pid && $0.title == args[2] }
            guard windows.count == 1, let window = windows.first, let application = window.owningApplication,
                  let display = content.displays.first(where: { $0.frame.contains(window.frame) }) else {
                throw NSError(domain: "Recorder", code: 3, userInfo: [NSLocalizedDescriptionKey: "Expected one target window fully on one display"])
            }
            let independent = args.count == 6 && args[5] == "window"
            let filter = independent ? SCContentFilter(desktopIndependentWindow: window)
                : SCContentFilter(display: display, including: [application], exceptingWindows: [])
            let config = SCStreamConfiguration()
            config.width = Int(window.frame.width) / 2 * 2
            config.height = Int(window.frame.height) / 2 * 2
            if !independent { config.sourceRect = CGRect(x: window.frame.minX - display.frame.minX,
                                       y: window.frame.minY - display.frame.minY,
                                       width: window.frame.width, height: window.frame.height) }
            config.minimumFrameInterval = CMTime(value: 1, timescale: 12)
            config.queueDepth = 3
            config.showsCursor = true
            config.capturesAudio = false
            config.captureMicrophone = false
            let streamDelegate = StreamDelegate()
            let stream = SCStream(filter: filter, configuration: config, delegate: streamDelegate)
            let frameCounter = FrameCounter()
            try stream.addStreamOutput(frameCounter, type: .screen,
                                       sampleHandlerQueue: DispatchQueue(label: "record-app.frames"))
            let recordingConfig = SCRecordingOutputConfiguration()
            recordingConfig.outputURL = destination
            recordingConfig.outputFileType = .mp4
            recordingConfig.videoCodecType = .h264
            let delegate = RecordingDelegate()
            let output = SCRecordingOutput(configuration: recordingConfig, delegate: delegate)
            try stream.addRecordingOutput(output)
            try await stream.startCapture()
            try await Task.sleep(for: .seconds(seconds))
            FileHandle.standardOutput.write(Data("PRE_STOP duration=\(output.recordedDuration.seconds) bytes=\(output.recordedFileSize) statuses=\(frameCounter.summary())\n".utf8))
            try await stream.stopCapture()
            for _ in 0..<100 {
                let (done, error) = delegate.status()
                if let error { throw NSError(domain: "Recorder", code: 4, userInfo: [NSLocalizedDescriptionKey: error]) }
                if done {
                    _ = try await validateMedia(destination, expected: seconds)
                    FileHandle.standardOutput.write(Data("RECORDING_COMPLETE pid=\(pid) sample_buffers=\(frameCounter.count()) file=\(destination.path)\n".utf8))
                    return
                }
                try await Task.sleep(for: .milliseconds(100))
            }
            throw NSError(domain: "Recorder", code: 5, userInfo: [NSLocalizedDescriptionKey: "Recording did not finalize"])
        } catch {
            FileHandle.standardError.write(Data("Recording failed: \(error.localizedDescription)\n".utf8))
            exit(1)
        }
    }
}
