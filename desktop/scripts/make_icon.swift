import AppKit
import Foundation

// Draw the existing Mantle cloak/M mark in a restrained macOS icon enclosure.
// Output paths must be new; earlier icon sets are never overwritten.
guard CommandLine.arguments.count == 2 else { fatalError("Usage: swift make_icon.swift NEW_ICONSET_DIRECTORY") }
let destination = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
guard !FileManager.default.fileExists(atPath: destination.path) else { fatalError("Refusing to overwrite an existing icon set") }
try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: true)

func color(_ hex: UInt32, alpha: CGFloat = 1) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255, green: CGFloat((hex >> 8) & 255) / 255, blue: CGFloat(hex & 255) / 255, alpha: alpha)
}
func render(size: Int, file: String) throws {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    let transform = AffineTransform(scale: CGFloat(size) / 1024)
    (transform as NSAffineTransform).concat()
    let rect = NSRect(x: 76, y: 76, width: 872, height: 872)
    let shell = NSBezierPath(roundedRect: rect, xRadius: 204, yRadius: 204)
    let shadow = NSShadow(); shadow.shadowColor = color(0x000000,alpha: 0.30); shadow.shadowBlurRadius = 30; shadow.shadowOffset = NSSize(width: 0, height: -12)
    NSGraphicsContext.saveGraphicsState(); shadow.set(); color(0x161618).setFill(); shell.fill(); NSGraphicsContext.restoreGraphicsState()
    NSGradient(colors: [color(0x333C53), color(0x1E1E22), color(0x161618)])!.draw(in: shell, angle: -65)
    let inset = NSBezierPath(roundedRect: rect.insetBy(dx: 2, dy: 2), xRadius: 202, yRadius: 202)
    color(0xA5BDF0,alpha: 0.20).setStroke(); inset.lineWidth = 3; inset.stroke()
    // The original 32 × 32 vector is scaled to an optical 660 × 660 area.
    NSGraphicsContext.saveGraphicsState()
    let glyphTransform = NSAffineTransform(); glyphTransform.translateX(by: 182, yBy: 842); glyphTransform.scaleX(by: 660 / 32, yBy: -660 / 32); glyphTransform.concat()
    let main = NSBezierPath(); main.move(to: NSPoint(x: 6,y: 25.2)); main.curve(to: NSPoint(x: 10.4,y: 12.8), controlPoint1: NSPoint(x: 7.8,y: 20), controlPoint2: NSPoint(x: 9.2,y: 15.6)); main.line(to: NSPoint(x: 16,y: 19.4)); main.line(to: NSPoint(x: 21.6,y: 12.8)); main.curve(to: NSPoint(x: 26,y: 25.2), controlPoint1: NSPoint(x: 22.8,y: 15.6), controlPoint2: NSPoint(x: 24.2,y: 20)); main.lineWidth = 3.4; main.lineCapStyle = .round; main.lineJoinStyle = .round
    let hood = NSBezierPath(); hood.move(to: NSPoint(x: 13.2,y: 13.2)); hood.curve(to: NSPoint(x: 16,y: 6), controlPoint1: NSPoint(x: 12.9,y: 9.3), controlPoint2: NSPoint(x: 14.2,y: 6.8)); hood.curve(to: NSPoint(x: 18.8,y: 13.2), controlPoint1: NSPoint(x: 17.8,y: 6.8), controlPoint2: NSPoint(x: 19.1,y: 9.3)); hood.lineWidth = 2.2; hood.lineCapStyle = .round; hood.lineJoinStyle = .round
    color(0xE6E4E0).setStroke(); main.stroke(); hood.stroke()
    NSGraphicsContext.restoreGraphicsState()
    NSGraphicsContext.restoreGraphicsState()
    let png = bitmap.representation(using: .png, properties: [:])!
    try png.write(to: destination.appendingPathComponent(file), options: .withoutOverwriting)
}
for point in [16,32,128,256,512] {
    try render(size: point, file: "icon_\(point)x\(point).png")
    try render(size: point * 2, file: "icon_\(point)x\(point)@2x.png")
}
print(destination.path)
