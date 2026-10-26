// make_icon.swift — render the imefuse app icon into an .iconset folder.
//
//   swiftc -O tools/make_icon.swift -o /tmp/make_icon
//   /tmp/make_icon /tmp/AppIcon.iconset
//   iconutil -c icns /tmp/AppIcon.iconset -o AppIcon.icns
//
// Design: a rounded square with a deep indigo gradient, two overlapping word
// cards (the left one older/dimmer, the right one brighter) and a merge arrow
// between them.  Legible down to 16 px, no text.
import AppKit
import CoreGraphics
import Foundation

let sizes = [16, 32, 64, 128, 256, 512, 1024]

func draw(size: Int, into ctx: CGContext) {
    let s = CGFloat(size)
    let r = s * 0.22                      // corner radius of the tile
    let inset = s * 0.06
    let rect = CGRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)

    // --- background: rounded rect with vertical gradient
    let path = CGPath(roundedRect: rect, cornerWidth: r, cornerHeight: r, transform: nil)
    ctx.saveGState()
    ctx.addPath(path)
    ctx.clip()
    let colors = [
        CGColor(red: 0.20, green: 0.24, blue: 0.62, alpha: 1.0),
        CGColor(red: 0.36, green: 0.30, blue: 0.86, alpha: 1.0),
    ] as CFArray
    if let grad = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(),
                             colors: colors, locations: [0, 1]) {
        ctx.drawLinearGradient(grad,
                               start: CGPoint(x: 0, y: s),
                               end: CGPoint(x: s, y: 0),
                               options: [])
    }
    ctx.restoreGState()

    // --- two "word cards" and a merge arrow
    let cardW = s * 0.30
    let cardH = s * 0.38
    let cy = s * 0.5
    let leftX = s * 0.16
    let rightX = s * 0.54
    let cr = s * 0.06

    func card(_ x: CGFloat, _ alpha: CGFloat, _ lines: Int) {
        let box = CGRect(x: x, y: cy - cardH / 2, width: cardW, height: cardH)
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: alpha))
        ctx.addPath(CGPath(roundedRect: box, cornerWidth: cr, cornerHeight: cr, transform: nil))
        ctx.fillPath()
        // text lines inside the card
        ctx.setFillColor(CGColor(red: 0.30, green: 0.30, blue: 0.70, alpha: alpha * 0.85))
        for i in 0..<lines {
            let lw = cardW * (i == lines - 1 ? 0.45 : 0.66)
            let ly = box.maxY - cardH * 0.26 - CGFloat(i) * cardH * 0.22
            let lr = CGRect(x: box.minX + cardW * 0.17, y: ly,
                            width: lw, height: max(1.0, s * 0.035))
            ctx.addPath(CGPath(roundedRect: lr, cornerWidth: lr.height / 2,
                               cornerHeight: lr.height / 2, transform: nil))
            ctx.fillPath()
        }
    }

    card(leftX, 0.55, 3)
    card(rightX, 0.97, 3)

    // --- arrow between the cards
    let ax = s * 0.455
    let ay = cy
    let len = s * 0.075
    let head = s * 0.05
    ctx.setStrokeColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.95))
    ctx.setLineWidth(max(1.5, s * 0.035))
    ctx.setLineCap(.round)
    ctx.move(to: CGPoint(x: ax - len / 2, y: ay))
    ctx.addLine(to: CGPoint(x: ax + len / 2, y: ay))
    ctx.strokePath()
    ctx.beginPath()
    ctx.move(to: CGPoint(x: ax + len / 2 - head, y: ay + head))
    ctx.addLine(to: CGPoint(x: ax + len / 2, y: ay))
    ctx.addLine(to: CGPoint(x: ax + len / 2 - head, y: ay - head))
    ctx.strokePath()
}

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "/tmp/AppIcon.iconset"
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)

func render(_ px: Int) -> Data? {
    let cs = CGColorSpaceCreateDeviceRGB()
    guard let ctx = CGContext(data: nil, width: px, height: px, bitsPerComponent: 8,
                              bytesPerRow: 0, space: cs,
                              bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
    else { return nil }
    ctx.setAllowsAntialiasing(true)
    ctx.interpolationQuality = .high
    draw(size: px, into: ctx)
    guard let img = ctx.makeImage() else { return nil }
    let rep = NSBitmapImageRep(cgImage: img)
    return rep.representation(using: .png, properties: [:])
}

for size in sizes {
    guard let data = render(size) else { continue }
    let name = size == 1024 ? "icon_512x512@2x.png"
        : (size == 64 ? "icon_32x32@2x.png"
           : (size == 32 ? "icon_32x32.png"
              : (size == 128 ? "icon_128x128.png"
                 : (size == 256 ? "icon_128x128@2x.png"
                    : (size == 512 ? "icon_256x256@2x.png" : "icon_16x16.png")))))
    try? data.write(to: URL(fileURLWithPath: "\(outDir)/\(name)"))
}
// a couple of extra retina variants the iconset format wants
if let d = render(32) { try? d.write(to: URL(fileURLWithPath: "\(outDir)/icon_16x16@2x.png")) }
if let d = render(512) { try? d.write(to: URL(fileURLWithPath: "\(outDir)/icon_256x256.png")) }
if let d = render(512) { try? d.write(to: URL(fileURLWithPath: "\(outDir)/icon_512x512.png")) }
print("wrote iconset to \(outDir)")
