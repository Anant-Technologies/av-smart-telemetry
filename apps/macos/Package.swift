// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Fitvid",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "Fitvid", targets: ["FitvidApp"])
    ],
    targets: [
        .executableTarget(
            name: "FitvidApp",
            path: "Sources/FitvidApp"
        )
    ]
)
