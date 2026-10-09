// Finds people in a folder of video frames with Apple's Vision framework, so
// a vertical snippet can keep the pastor centred as he moves.
//
//   vc-track <folder of .jpg frames>
//
// Prints one JSON line per frame: its file name, then every face and every
// upper body found, as boxes in 0-1 coordinates with the origin top-left.
import CoreGraphics
import Foundation
import ImageIO
import Vision

struct Box: Codable { let x: Double; let y: Double; let w: Double; let h: Double; let c: Double }
struct Frame: Codable { let file: String; let faces: [Box]; let bodies: [Box] }

func box(_ r: CGRect, _ c: Float) -> Box {
    Box(x: Double(r.origin.x), y: Double(1 - r.origin.y - r.height),
        w: Double(r.width), h: Double(r.height), c: Double(c))
}

let args = CommandLine.arguments
guard args.count > 1 else {
    FileHandle.standardError.write("usage: vc-track <frames folder>\n".data(using: .utf8)!)
    exit(2)
}
let folder = URL(fileURLWithPath: args[1])
let names = ((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? [])
    .filter { $0.hasSuffix(".jpg") }.sorted()
let encoder = JSONEncoder()

for name in names {
    autoreleasepool {
        let url = folder.appendingPathComponent(name)
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return }
        let faces = VNDetectFaceRectanglesRequest()
        let bodies = VNDetectHumanRectanglesRequest()
        bodies.upperBodyOnly = true
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try? handler.perform([faces, bodies])
        let frame = Frame(
            file: name,
            faces: (faces.results ?? []).map { box($0.boundingBox, $0.confidence) },
            bodies: (bodies.results ?? []).map { box($0.boundingBox, $0.confidence) })
        if let data = try? encoder.encode(frame), let line = String(data: data, encoding: .utf8) {
            print(line)
            fflush(stdout)
        }
    }
}
