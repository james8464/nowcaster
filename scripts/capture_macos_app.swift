#!/usr/bin/env swift

import AppKit
import CoreGraphics
import Foundation

let arguments = CommandLine.arguments
guard arguments.count >= 3 else {
    FileHandle.standardError.write(Data("Usage: capture_macos_app.swift APP_PATH OUTPUT_DIR [--verify-only|--strategy-lab-only]\n".utf8))
    exit(2)
}

let appURL = URL(fileURLWithPath: arguments[1]).standardizedFileURL
let outputDirectory = URL(fileURLWithPath: arguments[2]).standardizedFileURL
let verifyOnly = arguments.contains("--verify-only")
let strategyLabOnly = arguments.contains("--strategy-lab-only")
let bundleIdentifier = "com.james8464.nowcaster"
try FileManager.default.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

struct Capture {
    let destination: String
    let appearance: String
    let narrow: Bool

    var name: String { "\(destination)-\(appearance)\(narrow ? "-narrow" : "")" }
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

func terminateExisting() {
    for app in NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier) {
        app.terminate()
    }
    Thread.sleep(forTimeInterval: 0.7)
}

func launch(_ capture: Capture) throws -> NSRunningApplication {
    terminateExisting()
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.activates = true
    configuration.createsNewApplicationInstance = true
    configuration.arguments = [
        "--destination=\(capture.destination)",
        capture.appearance == "dark" ? "--ui-dark" : "--ui-light",
        capture.narrow ? "--ui-narrow" : "--ui-wide",
    ]
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
    Thread.sleep(forTimeInterval: 4.0)
    return application
}

func largestWindowID(for processIdentifier: pid_t) -> CGWindowID? {
    guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
        as? [[String: Any]] else { return nil }
    return windows
        .filter { ($0[kCGWindowOwnerPID as String] as? pid_t) == processIdentifier }
        .filter { ($0[kCGWindowLayer as String] as? Int) == 0 }
        .compactMap { window -> (CGWindowID, Double)? in
            guard let id = window[kCGWindowNumber as String] as? CGWindowID,
                  let bounds = window[kCGWindowBounds as String] as? [String: CGFloat] else { return nil }
            return (id, (bounds["Width"] ?? 0) * (bounds["Height"] ?? 0))
        }
        .max(by: { $0.1 < $1.1 })?.0
}

for capture in captures {
    let application = try launch(capture)
    guard let windowID = largestWindowID(for: application.processIdentifier) else {
        throw CocoaError(.fileReadUnknown, userInfo: [NSLocalizedDescriptionKey: "No Nowcaster window for \(capture.name)"])
    }
    if !verifyOnly {
        let output = outputDirectory.appendingPathComponent("\(capture.name).png")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = ["-x", "-o", "-l", "\(windowID)", output.path]
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0, FileManager.default.fileExists(atPath: output.path) else {
            throw CocoaError(.fileWriteUnknown)
        }
        print(output.path)
    }
}
terminateExisting()
print(verifyOnly ? "Nowcaster UI smoke test passed" : "Captured \(captures.count) Nowcaster screenshots")
