// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "tauri-plugin-workspace-voice",
    platforms: [.iOS(.v16)],
    products: [
        .library(name: "tauri-plugin-workspace-voice", type: .static, targets: ["WorkspaceVoice"])
    ],
    dependencies: [.package(name: "Tauri", path: "../.tauri/tauri-api")],
    targets: [
        .target(
            name: "WorkspaceVoice",
            dependencies: [.byName(name: "Tauri")],
            path: "Sources",
            linkerSettings: [.linkedFramework("AVFoundation"), .linkedFramework("Speech")]
        )
    ]
)
