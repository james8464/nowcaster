import Darwin
import Foundation
import Testing
@testable import NowcasterApp

@Test @MainActor func livePaperContenderPauseNeverStopsExternalLockHolder() async throws {
    try await exerciseCollectorStop(externalHolder: true)
}

@Test @MainActor func livePaperOwnedCollectorInterruptsInsideLockWithoutSharedStopCommand() async throws {
    try await exerciseCollectorStop(externalHolder: false)
}

@Test @MainActor func livePaperEarlyContenderExitPublishesTerminationAndCanRetryWithoutAffectingHolder() async throws {
    try await exerciseCollectorStop(externalHolder: true, exitContender: true)
}

@MainActor private func exerciseCollectorStop(externalHolder: Bool, exitContender: Bool = false) async throws {
    let root = FileManager.default.temporaryDirectory.appending(path: "native-collector-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    try Data("{}".utf8).write(to: root.appending(path: "protocol.json"))
    if exitContender { try Data().write(to: root.appending(path: "exit-contender")) }
    let script = root.appending(path: "run_live_paper_signals.py")
    let code = #"""
import fcntl, json, os, sys, time
root = os.path.dirname(__file__)
def write(name):
    with open(os.path.join(root, name), 'w') as f: f.write('retained')
def exists(name): return os.path.exists(os.path.join(root, name))
command = sys.argv[1]
if command == 'status':
    print(json.dumps(dict(kind='stopped', protocol_hash='a'*64, updated_at='2026-09-21T12:00:00Z', evaluated_at=None, reasons=[], suggestion=None)))
elif command == 'stop':
    write('shared-stop-command')
    write('STOP')
elif command in ('external', 'start'):
    with open(os.path.join(root, 'live-paper-signal.lock'), 'a+b') as lock:
        if command == 'start' and exists('external-ready'):
            write('contender-ready')
            if exists('exit-contender'): sys.exit(3)
            while True: time.sleep(.01)  # Deterministic pre-acquisition barrier.
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        write('external-ready' if command == 'external' else 'owned-ready')
        try:
            while not exists('STOP'): time.sleep(.01)
        except KeyboardInterrupt:
            write('owned-interrupt-ack')
else: sys.exit(1)
"""#
    try Data(code.utf8).write(to: script)
    let external = Process()
    if externalHolder {
        external.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        external.arguments = [script.path, "external"]
        external.standardOutput = FileHandle.nullDevice; external.standardError = FileHandle.nullDevice
        try external.run()
    }
    defer {
        if external.isRunning { external.terminate(); external.waitUntilExit() }
    }
    func waitFor(_ name: String) async throws {
        let deadline = ContinuousClock.now + .seconds(30)
        while !FileManager.default.fileExists(atPath: root.appending(path: name).path), ContinuousClock.now < deadline {
            try await Task.sleep(for: .milliseconds(10))
        }
        #expect(FileManager.default.fileExists(atPath: root.appending(path: name).path))
    }
    if externalHolder { try await waitFor("external-ready") }
    let service = LivePaperSignalService()
    var terminations = 0
    service.onTermination = { @MainActor _ in terminations += 1 }
    let config = LivePaperSignalConfiguration(projectRoot: root, executable: URL(fileURLWithPath: "/usr/bin/python3"),
        script: script, directory: root, protocolHash: String(repeating: "a", count: 64))
    await service.start(configuration: config)
    try await waitFor(externalHolder ? "contender-ready" : "owned-ready")
    if exitContender {
        for expected in 1...2 {
            for _ in 0..<300 where terminations < expected { try await Task.sleep(for: .milliseconds(10)) }
            #expect(terminations == expected)
            #expect(!service.isRunning)
            #expect(external.isRunning)
            if expected == 1 { await service.start(configuration: config) }
        }
    }
    _ = await service.shutdown(timeout: .milliseconds(500))
    #expect(!service.isRunning)
    #expect(service.state == nil)
    #expect(!FileManager.default.fileExists(atPath: root.appending(path: "shared-stop-command").path))
    #expect(!FileManager.default.fileExists(atPath: root.appending(path: "STOP").path))
    if externalHolder { #expect(external.isRunning) }
    else { #expect(FileManager.default.fileExists(atPath: root.appending(path: "owned-interrupt-ack").path)) }
}
