// Turns a presentation into slide pictures and the words on them, using the
// frameworks macOS already has: PDFKit to render and read PDF pages, Vision
// to read slides that are only pictures.
//
// Nothing here needs installing. There is no pdftoppm, no poppler, no
// LibreOffice and no PyObjC on this machine, and asking for any of them to
// put slides on screen would be a poor trade.
//
//   vc-deck pdf <file.pdf> <out folder> <max width>
//   vc-deck ocr <image> [<image> …]
//
// Prints one JSON line per slide: its number, the file written, and the text
// found on it.
import CoreGraphics
import Foundation
import ImageIO
import PDFKit
import Vision

struct Slide: Codable { let index: Int; let file: String; let text: String; let source: String }

let out = FileHandle.standardOutput
let err = FileHandle.standardError

func emit<T: Encodable>(_ value: T) {
    if let data = try? JSONEncoder().encode(value) {
        out.write(data)
        out.write("\n".data(using: .utf8)!)
    }
}

func fail(_ message: String) -> Never {
    err.write((message + "\n").data(using: .utf8)!)
    exit(1)
}

func writePNG(_ image: CGImage, to url: URL) -> Bool {
    guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)
    else { return false }
    CGImageDestinationAddImage(dest, image, nil)
    return CGImageDestinationFinalize(dest)
}

/// Everything Vision can read on one picture, in reading order.
///
/// Tried more than once on purpose. The first text request made after a boot
/// comes back empty while Vision gets its models ready - which showed up as a
/// deck importing with no words on any slide, and the run taking two minutes
/// while barely using the processor. Once it is warm it answers in
/// milliseconds, so a couple of retries cost nothing and remove the whole
/// class of empty first import.
func readText(_ image: CGImage) -> String {
    for attempt in 0..<3 {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            if attempt == 2 {
                err.write("vision: \(error.localizedDescription)\n".data(using: .utf8)!)
            }
        }
        let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        if !lines.isEmpty {
            return lines.joined(separator: "\n")
        }
        if attempt < 2 {
            usleep(500_000)
        }
    }
    return ""
}

/// How much readable text a string holds: the letters in runs of three or
/// more that contain a vowel.
///
/// This is for telling a good text layer from a bad one. A font with no
/// usable character map drops characters rather than inventing them - "31
/// October 1517" came back as "3 Octobe 5" - so the broken version always
/// scores lower than the truth, whatever shape the remains happen to have.
/// Word-shape tests alone were fooled by exactly that string.
func readableScore(_ text: String) -> Int {
    let vowels = Set("aeiouyAEIOUY")
    return text.split(whereSeparator: { !$0.isLetter })
        .filter { $0.count >= 3 && $0.contains(where: { vowels.contains($0) }) }
        .reduce(0) { $0 + $1.count }
}

func loadImage(_ path: String) -> CGImage? {
    guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil)
    else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

// ---------------------------------------------------------------- commands
let args = CommandLine.arguments
guard args.count > 2 else {
    fail("usage: vc-deck pdf <file> <out folder> <max width>  |  vc-deck ocr <image> …")
}

switch args[1] {
case "pdf":
    guard args.count > 3 else { fail("usage: vc-deck pdf <file> <out folder> <max width>") }
    let url = URL(fileURLWithPath: args[2])
    let folder = URL(fileURLWithPath: args[3])
    let maxWidth = args.count > 4 ? (Double(args[4]) ?? 1920.0) : 1920.0
    guard let pdf = CGPDFDocument(url as CFURL), pdf.numberOfPages > 0 else {
        fail("That PDF could not be opened, or it has no pages.")
    }
    // PDFKit reads the text layer; the drawing is done through CoreGraphics,
    // which is the part that has to respect each page's own box and rotation.
    let text = PDFDocument(url: url)
    try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)

    for number in 1...pdf.numberOfPages {
        autoreleasepool {
            guard let page = pdf.page(at: number) else { return }
            let box = page.getBoxRect(.cropBox).isEmpty ? page.getBoxRect(.mediaBox)
                                                        : page.getBoxRect(.cropBox)
            // A rotated page is drawn through the transform below, so the
            // picture written out is the right way up and the right shape.
            let turned = abs(page.rotationAngle % 180) == 90
            let width = turned ? box.height : box.width
            let height = turned ? box.width : box.height
            guard width > 0, height > 0 else { return }
            let scale = min(maxWidth / width, 8.0)
            let pixels = (w: Int((width * scale).rounded()), h: Int((height * scale).rounded()))
            guard pixels.w > 0, pixels.h > 0,
                  let ctx = CGContext(data: nil, width: pixels.w, height: pixels.h,
                                      bitsPerComponent: 8, bytesPerRow: 0,
                                      space: CGColorSpaceCreateDeviceRGB(),
                                      bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
            else { return }
            // Slides are made to be seen on white, and a PDF page has no
            // background of its own.
            ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: pixels.w, height: pixels.h))
            // The scaling is done here rather than with the page's own
            // drawing transform. `getDrawingTransform` only ever shrinks a
            // page to fit the rect it is given: handed a 960x540 slide and a
            // 1920x1080 canvas it returns a scale of 1 and an offset of
            // (480, 270), so every slide came out at half size in the middle
            // of a blank frame - which is exactly how they looked on screen.
            let angle = CGFloat(((page.rotationAngle % 360) + 360) % 360)
            ctx.translateBy(x: CGFloat(pixels.w) / 2, y: CGFloat(pixels.h) / 2)
            if angle != 0 { ctx.rotate(by: -angle * .pi / 180) }
            ctx.scaleBy(x: scale, y: scale)
            ctx.translateBy(x: -box.midX, y: -box.midY)
            ctx.drawPDFPage(page)
            guard let image = ctx.makeImage() else { return }

            let name = String(format: "slide-%03d.png", number)
            guard writePNG(image, to: folder.appendingPathComponent(name)) else { return }

            // The words on the slide: both ways, then whichever reads
            // better. Vision is quick enough on a slide that there is no
            // reason to gamble on the text layer being sound, and a deck
            // exported with a broken font encoding is otherwise useless for
            // matching against what the speaker said. The text layer wins
            // ties because it keeps punctuation and reading order exactly.
            let layer = text?.page(at: number - 1)?.string ?? ""
            let seen = readText(image)
            let useLayer = readableScore(layer) >= Int(Double(readableScore(seen)) * 0.9)
                && readableScore(layer) > 0
            let words = useLayer ? layer : seen
            let from = useLayer ? "pdf" : "ocr"
            emit(Slide(index: number, file: name, text: words, source: from))
        }
    }

case "ocr":
    for (offset, path) in args.dropFirst(2).enumerated() {
        autoreleasepool {
            guard let image = loadImage(path) else { return }
            emit(Slide(index: offset + 1, file: URL(fileURLWithPath: path).lastPathComponent,
                       text: readText(image), source: "ocr"))
        }
    }

default:
    fail("Don't know how to \(args[1]). Use pdf or ocr.")
}
