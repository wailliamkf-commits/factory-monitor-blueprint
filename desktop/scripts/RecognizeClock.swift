// Local macOS diagnostic OCR. One JSON request per line; no network or audio.
// This is an optional diagnostic adapter, not the production Chinese model backend.
import Foundation
import Vision
while let line = readLine() {
    autoreleasepool {
        var result: [String: Any] = [:]
        let start = Date()
        do {
            guard let bytes = line.data(using: .utf8),
                  let input = try JSONSerialization.jsonObject(with: bytes) as? [String: String],
                  let encoded = input["image_base64"], encoded.count <= 4_000_000,
                  let data = Data(base64Encoded: encoded) else {
                throw NSError(domain: "LocalClockOCR", code: 1)
            }
            result["id"] = input["id"] ?? ""
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["en-US", "zh-Hans"]
            request.usesLanguageCorrection = false
            request.minimumTextHeight = 0.005
            try VNImageRequestHandler(data: data).perform([request])
            let observations: [[String: Any]] = (request.results ?? []).compactMap { o in
                guard let t = o.topCandidates(1).first else { return nil }
                return ["text": t.string, "confidence": t.confidence,
                        "box": [o.boundingBox.minX, o.boundingBox.minY,
                                o.boundingBox.width, o.boundingBox.height]]
            }
            result["observations"] = observations
        } catch { result["error"] = "OCR request failed" }
        result["seconds"] = Date().timeIntervalSince(start)
        if let bytes = try? JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]),
           let text = String(data: bytes, encoding: .utf8) {
            FileHandle.standardOutput.write(Data((text + "\n").utf8))
        }
    }
}
