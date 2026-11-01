// swift-tools-version: 5.9
import PackageDescription
let package = Package(
    name: "MantleDesktop",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "Mantle", targets: ["MantleDesktop"])],
    targets: [.executableTarget(name: "MantleDesktop", path: "Sources/MantleDesktop")]
)
