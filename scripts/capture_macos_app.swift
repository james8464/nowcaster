#!/usr/bin/env swift

import AppKit
import CoreGraphics
import Foundation
import ImageIO

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    FileHandle.standardError.write(Data("Usage: capture_macos_app.swift APP_PATH OUTPUT_DIR [--verify-only|--strategy-lab-only] [--stale-banner]\n".utf8))
    exit(2)
}

let appURL = URL(fileURLWithPath: arguments[1]).standardizedFileURL
let outputDirectory = URL(fileURLWithPath: arguments[2]).standardizedFileURL
let verifyOnly = arguments.contains("--verify-only")
let strategyLabOnly = arguments.contains("--strategy-lab-only")
let staleBanner = arguments.contains("--stale-banner")
let bundleIdentifier = "com.james8464.nowcaster"
try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

struct Capture {
    let destination: String
    let appearance: String
    let narrow: Bool

    var name: String { "\(destination)-\(appearance)\(narrow ? "-narrow" : "")" }
    var expectedSize: CGSize { narrow ? CGSize(width: 900, height: 700) : CGSize(width: 1_440, height: 900) }
}

let destinations = ["today", "markets", "earnings", "signals", "backtests", "strategyLab", "modelLab", "dataQuality", "pipelineRuns"]
var captures = destinations.flatMap { destination in
    [Capture(destination: destination, appearance: "light", narrow: false),
     Capture(destination: destination, appearance: "dark", narrow: false)]
}
captures.append(Capture(destination: "today", appearance: "light", narrow: true))
captures.append(Capture(destination: "backtests", appearance: "dark", narrow: true))
captures.append(Capture(destination: "strategyLab", appearance: "light", narrow: true))
captures.append(Capture(destination: "strategyLab", appearance: "dark", narrow: true))
if strategyLabOnly { captures = captures.filter { $0.destination == "strategyLab" } }
if verifyOnly { captures = [Capture(destination: "strategyLab", appearance: "light", narrow: true)] }

func terminateExisting() throws {
    let deadline = Date().addingTimeInterval(6)
    var forced = false
    while true {
        let running = NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
            .filter { !$0.isTerminated }
        if running.isEmpty { return }
        for app in running {
            if forced { app.forceTerminate() } else { app.terminate() }
        }
        guard Date() < deadline else {
            throw CocoaError(
                .fileWriteUnknown,
                userInfo: [NSLocalizedDescriptionKey: "Existing Nowcaster instance did not terminate"]
            )
        }
        if deadline.timeIntervalSinceNow < 3 { forced = true }
        Thread.sleep(forTimeInterval: 0.1)
    }
}

func launch(_ capture: Capture) throws -> NSRunningApplication {
    try terminateExisting()
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    configuration.createsNewApplicationInstance = true
    configuration.arguments = [
        "--destination=\(capture.destination)",
        capture.appearance == "dark" ? "--ui-dark" : "--ui-light",
        capture.narrow ? "--ui-narrow" : "--ui-wide",
    ] + (staleBanner ? ["--ui-stale"] : [])
    let semaphore = DispatchSemaphore(value: 0)
    var result: Result<NSRunningApplication, Error>?
    NSWorkspace.shared.openApplication(at: appURL, configuration: configuration) { application, error in
        if let application { result = .success(application) }
        else { result = .failure(error ?? CocoaError(.fileNoSuchFile)) }
        semaphore.signal()
    }
    _ = semaphore.wait(timeout: .now() + 15)
    guard let result else { throw CocoaError(.fileReadUnknown) }
    let application = try result.get()
    application.activate(options: [.activateAllWindows])
    return application
}

struct WindowCaptureTarget {
    let id: CGWindowID
    let bounds: CGRect
}

func largestWindow(for processIdentifier: pid_t) -> WindowCaptureTarget? {
    guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] else { return nil }
    return windows
        .filter { ($0[kCGWindowOwnerPID as String] as? pid_t) == processIdentifier }
        .filter { ($0[kCGWindowLayer as String] as? Int) == 0 }
        .compactMap { window -> WindowCaptureTarget? in
            guard let id = window[kCGWindowNumber as String] as? CGWindowID,
                  let bounds = window[kCGWindowBounds as String] as? [String: CGFloat] else { return nil }
            return WindowCaptureTarget(
                id: id,
                bounds: CGRect(
                    x: bounds["X"] ?? 0,
                    y: bounds["Y"] ?? 0,
                    width: bounds["Width"] ?? 0,
                    height: bounds["Height"] ?? 0
                )
            )
        }
        .max(by: { $0.bounds.width * $0.bounds.height < $1.bounds.width * $1.bounds.height })
}

func expectedWindow(for application: NSRunningApplication, capture: Capture) throws -> WindowCaptureTarget {
    let deadline = Date().addingTimeInterval(15)
    let tolerance: CGFloat = 3
    var stableSamples = 0
    var lastBounds: CGRect?
    while Date() < deadline, !application.isTerminated {
        if let target = largestWindow(for: application.processIdentifier) {
            lastBounds = target.bounds
            let matches = abs(target.bounds.width - capture.expectedSize.width) <= tolerance
                && abs(target.bounds.height - capture.expectedSize.height) <= tolerance
            stableSamples = matches ? stableSamples + 1 : 0
            if stableSamples >= 3 {
                return target
            }
        }
        Thread.sleep(forTimeInterval: 0.1)
    }
    let actual = lastBounds.map { "\(Int($0.width))x\(Int($0.height))" } ?? "unavailable"
    throw CocoaError(
        .fileReadUnknown,
        userInfo: [
            NSLocalizedDescriptionKey:
                "\(capture.name) expected \(Int(capture.expectedSize.width))x\(Int(capture.expectedSize.height)); got \(actual)",
        ]
    )
}

func validatePNG(_ url: URL, capture: Capture) throws {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let width = properties[kCGImagePropertyPixelWidth] as? Int,
          let height = properties[kCGImagePropertyPixelHeight] as? Int
    else { throw CocoaError(.fileReadCorruptFile) }
    let scale = NSScreen.main?.backingScaleFactor ?? 1
    let expectedWidth = Int((capture.expectedSize.width * scale).rounded())
    let expectedHeight = Int((capture.expectedSize.height * scale).rounded())
    guard abs(width - expectedWidth) <= 4, abs(height - expectedHeight) <= 4 else {
        throw CocoaError(
            .fileReadCorruptFile,
            userInfo: [
                NSLocalizedDescriptionKey:
                    "\(capture.name) PNG expected \(expectedWidth)x\(expectedHeight); got \(width)x\(height)",
            ]
        )
    }
    print("\(url.path) [\(width)x\(height)]")
}

defer { try? terminateExisting() }
for capture in captures {
    let application = try launch(capture)
    let target = try expectedWindow(for: application, capture: capture)
    Thread.sleep(forTimeInterval: 3)
    if !verifyOnly {
        let output = outputDirectory.appendingPathComponent("\(capture.name).png")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = ["-x", "-o", "-l", "\(target.id)", output.path]
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0, FileManager.default.fileExists(atPath: output.path) else {
            throw CocoaError(.fileWriteUnknown)
        }
        try validatePNG(output, capture: capture)
    }
}
if !verifyOnly {
    for appearance in ["light", "dark"] {
        let wide = outputDirectory.appendingPathComponent("strategyLab-\(appearance).png")
        let narrow = outputDirectory.appendingPathComponent("strategyLab-\(appearance)-narrow.png")
        if FileManager.default.fileExists(atPath: wide.path), FileManager.default.fileExists(atPath: narrow.path) {
            guard try Data(contentsOf: wide) != Data(contentsOf: narrow) else {
                throw CocoaError(
                    .fileReadCorruptFile,
                    userInfo: [NSLocalizedDescriptionKey: "Wide and narrow \(appearance) captures are identical"]
                )
            }
        }
    }
}
try terminateExisting()
print(verifyOnly ? "Nowcaster UI smoke test passed" : "Captured \(captures.count) Nowcaster screenshots")
