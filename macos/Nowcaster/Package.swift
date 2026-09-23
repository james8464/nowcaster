// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "Nowcaster",
    platforms: [.macOS(.v15)],
    products: [
        .executable(name: "NowcasterApp", targets: ["NowcasterApp"]),
    ],
    targets: [
        .executableTarget(
            name: "NowcasterApp",
            exclude: [
                "Resources/AppIcon.icon",
                "Resources/AppIconSource.svg",
            ],
            resources: [
                .copy("Resources/AppIcon.png"),
                .copy("Resources/Fixtures"),
            ],
            linkerSettings: [
                .linkedFramework("Security"),
                .linkedFramework("UserNotifications"),
                .linkedFramework("ServiceManagement"),
            ]
        ),
        .testTarget(name: "NowcasterAppTests", dependencies: ["NowcasterApp"]),
    ]
)
