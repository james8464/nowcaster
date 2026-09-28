import Foundation
import Testing
import Darwin
@testable import NowcasterApp

func backgroundStatusFixture(state: String = "waiting", reason: String = "Waiting for data") -> String {
    """
    {"schema_version":1,"campaign_hash":"\(String(repeating: "b", count: 64))","campaign_id":"synthetic","batch_id":null,"state":"\(state)","reason":"\(reason)","attempt_count":0,"failure_count":0,"batch_attempt_count":0,"batch_failure_count":0,"last_checkpoint":null,"next_eligible_at":null,"paper_only":true}
    """
}

@Test func backgroundResearchStrictStatusAndPayloadSurviveDecoder() throws {
    let value = backgroundStatusFixture()
    let decoded = try EngineProgressEvent.parse("{\"event\":\"progress\",\"schema_version\":1,\"status\":\(value)}")
    #expect(decoded.status?.state == .waiting)
    #expect(throws: (any Error).self) { try LearningStatus.decode(Data(value.replacingOccurrences(of: "\"schema_version\":1", with: "\"schema_version\":2").utf8)) }
    #expect(throws: (any Error).self) { try LearningStatus.decode(Data(value.replacingOccurrences(of: "\"paper_only\":true", with: "\"paper_only\":false").utf8)) }
    #expect(throws: (any Error).self) { try LearningStatus.decode(Data(value.replacingOccurrences(of: "\"attempt_count\":0", with: "\"attempt_count\":-1").utf8)) }
    #expect(throws: (any Error).self) { try LearningStatus.decode(Data(value.replacingOccurrences(of: "\"schema_version\":1", with: "\"schema_version\":1.0").utf8)) }
}

@Test @MainActor func backgroundResearchExitWithoutCheckpointIsInterruptedNotCleanStop() async throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: root) }
    let service = BackgroundResearchService()
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    try await service.start(campaignHash: String(repeating: "b", count: 64), registryURL: root,
        configuration: .init(projectRoot: root, pythonExecutable: URL(fileURLWithPath: "/usr/bin/true"), snapshotURL: root, mode: .demo))
    let deadline = ContinuousClock.now + .seconds(30)
    while service.isRunning && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    #expect(service.interrupted)
    #expect(await service.shutdown(timeout: .seconds(1)) == false)
}

@Test @MainActor func backgroundResearchPauseBeforeProcessLaunchCancelsWithoutLateFailure() async throws {
    let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
    try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: root) }
    let service = BackgroundResearchService()
    try await service.start(campaignHash: String(repeating: "b", count: 64), registryURL: root,
        configuration: .init(projectRoot: root, pythonExecutable: URL(fileURLWithPath: "/usr/bin/true"), snapshotURL: root, mode: .demo))
    #expect(await service.shutdown(timeout: .seconds(1)))
    #expect(!service.isRunning); #expect(!service.interrupted)
}

@Test func backgroundResearchEnvironmentRejectsInheritedSecretsAndBootloaderKeys() {
    let environment = BackgroundResearchEnvironment.make(from: ["HOME": "/tmp/home", "APCA_API_KEY_ID": "secret", "PYTHONPATH": "/unsafe", "_PYI_ARCHIVE_FILE": "unsafe", "OMP_NUM_THREADS": "99"])
    #expect(environment["HOME"] == "/tmp/home")
    #expect(environment["APCA_API_KEY_ID"] == nil); #expect(environment["PYTHONPATH"] == nil)
    #expect(environment["_PYI_ARCHIVE_FILE"] == nil); #expect(environment["OMP_NUM_THREADS"] == "1")
}

@Test func backgroundResearchRunnerNeverConsumesSecretEnvironment() async throws {
    let root = FileManager.default.temporaryDirectory
    let secret = EngineSecretEnvironment(credentials: BrokerCredentials(keyID: "sentinel", secret: "sentinel-secret"), environment: .paper)
    let config = EngineConfiguration(projectRoot: root, pythonExecutable: URL(fileURLWithPath: "/usr/bin/true"), snapshotURL: root, mode: .demo, secretEnvironment: secret)
    for try await _ in EngineRunner().run(.registerBackgroundResearch(.init(registryURL: root, manifestURL: root)), configuration: config) {}
    #expect(!secret.isCleared)
}

private func researchFixture(bootloader: Bool = false) throws -> (URL, EngineConfiguration) {
    let root = FileManager.default.temporaryDirectory.appending(path: "native-research-" + UUID().uuidString)
    try FileManager.default.createDirectory(at: root.appending(path: "scripts"), withIntermediateDirectories: true)
    let code = #"""
import os, sys, json, ctypes, time, subprocess
root = os.path.dirname(os.path.dirname(__file__))
if sys.argv[2] == 'prepare-background-research':
    open(os.path.join(root, 'preparing'), 'w').write('ready')
    time.sleep(30)
    sys.exit(0)
if sys.argv[2] == 'register-background-research':
    open(os.path.join(root, 'registering'), 'w').write('unexpected')
    sys.exit(1)
if os.path.exists(os.path.join(root, 'bootloader')) and '--native-child' not in sys.argv:
    sys.exit(subprocess.call([sys.executable, __file__, *sys.argv[1:], '--native-child']))
args = dict(zip(sys.argv[3::2], sys.argv[4::2]))
if os.getpgrp() != os.getpid(): os.setsid()
class BSD(ctypes.Structure):
    _fields_ = [('prefix', ctypes.c_uint32 * 12), ('names', ctypes.c_char * 48), ('details', ctypes.c_uint32 * 6), ('sec', ctypes.c_uint64), ('usec', ctypes.c_uint64)]
info = BSD()
ctypes.CDLL('/usr/lib/libproc.dylib').proc_pidinfo(os.getpid(), 3, 0, ctypes.byref(info), ctypes.sizeof(info))
owner = dict(event='ownership', schema_version=1, run_id=args['--run-id'], nonce=args['--control-nonce'], pid=os.getpid(), parent_pid=os.getppid(), process_group_id=os.getpgrp(), process_start_seconds=info.sec, process_start_microseconds=info.usec, campaign_hash=args['--campaign-hash'])
print(json.dumps(owner), flush=True)
status = dict(schema_version=1, campaign_hash=args['--campaign-hash'], campaign_id='synthetic', batch_id=None, state='waiting', reason='Waiting', attempt_count=0, failure_count=0, batch_attempt_count=0, batch_failure_count=0, last_checkpoint=None, next_eligible_at=None, paper_only=True)
print(json.dumps(dict(event='progress', schema_version=1, status=status)), flush=True)
control = os.path.join(args['--control-directory'], args['--run-id'] + '.control.json')
while os.path.exists(os.path.join(root, 'ignore-control')) or json.load(open(control))['state'] != 'stopped': time.sleep(.01)
time.sleep(.1)
status.update(state='paused', last_checkpoint='checkpoint-retained')
print(json.dumps(dict(event='complete', schema_version=1, status=status)), flush=True)
"""#
    try Data(code.utf8).write(to: root.appending(path: "scripts/live_engine_entry.py"))
    if bootloader { try Data().write(to: root.appending(path: "bootloader")) }
    return (root, .init(projectRoot: root, pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"), snapshotURL: root, mode: .demo))
}

@Test @MainActor func backgroundResearchCancellationBeforeRegistrationHandshakeIsBounded() async throws {
    let (root, config) = try researchFixture()
    defer { try? FileManager.default.removeItem(at: root) }
    var preferences = PaperSessionPreferences()
    preferences.source = .init(directory: root.appending(path: "source"), protocolHash: String(repeating: "a", count: 64))
    preferences.registryURL = root.appending(path: "registry")
    preferences.manifestURL = root.appending(path: "manifest.json")
    preferences.campaignID = "retained-before-launch"
    preferences.createdAt = "2026-09-28T00:00:00Z"
    let service = BackgroundResearchService()
    let preparation = Task { try await service.prepare(preferences: preferences, configuration: config) }
    let deadline = ContinuousClock.now + .seconds(30)
    while !FileManager.default.fileExists(atPath: root.appending(path: "preparing").path), ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(10))
    }
    #expect(service.isPreparing)
    let start = ContinuousClock.now
    _ = await service.shutdown(timeout: .milliseconds(100))
    #expect(start.duration(to: .now) < .seconds(1))
    if case .success = await preparation.result { Issue.record("Cancelled preparation must not complete registration") }
    #expect(!FileManager.default.fileExists(atPath: root.appending(path: "registering").path))
    #expect(preferences.campaignID == "retained-before-launch")
}

@Test @MainActor func backgroundResearchBootloaderDirectChildOwnershipAndInterruptedDeadline() async throws {
    let (root, config) = try researchFixture(bootloader: true)
    defer { try? FileManager.default.removeItem(at: root) }
    try Data().write(to: root.appending(path: "ignore-control"))
    let service = BackgroundResearchService()
    try await service.start(campaignHash: String(repeating: "b", count: 64), registryURL: root.appending(path: "registry"), configuration: config)
    let deadline = ContinuousClock.now + .seconds(30)
    while service.status == nil && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    #expect(service.status != nil)
    let started = ContinuousClock.now
    #expect(await service.shutdown(timeout: .milliseconds(100)) == false)
    #expect(started.duration(to: .now) < .seconds(1)); #expect(service.interrupted)
    let execution = try #require(service.request)
    #expect(FileManager.default.fileExists(atPath: execution.controlDirectory.appending(path: "\(execution.runID).interrupted.json").path))
}

private final class ShutdownClockFixture: @unchecked Sendable {
    private let lock = NSLock()
    private var elapsed: Duration = .zero
    var now: Duration { lock.withLock { elapsed } }
    func advance(_ amount: Duration) { lock.withLock { elapsed += amount } }
}

@Test func backgroundResearchThirtySecondBoundAndUnownedChildUntouched() async throws {
    let own = Process(); own.executableURL = URL(fileURLWithPath: "/bin/sleep"); own.arguments = ["20"]
    let external = Process(); external.executableURL = URL(fileURLWithPath: "/bin/sleep"); external.arguments = ["20"]
    try external.run()
    defer { if external.isRunning { external.terminate() }; external.waitUntilExit(); own.waitUntilExit() }
    let handle = BackgroundProcessHandle(); try handle.launch(own)
    let fake = ShutdownClockFixture()
    let result = await handle.shutdown(timeout: .seconds(120), clock: .init(now: { fake.now }, sleep: { _ in fake.advance(.seconds(1)) }))
    #expect(!result); #expect(fake.now == .seconds(30)); #expect(external.isRunning)
}

@Test func backgroundResearchLegacyRunnerDrainsOwnedWorkAndRejectsLateLaunches() async throws {
    let root = FileManager.default.temporaryDirectory
    let runner = EngineRunner()
    let config = EngineConfiguration(projectRoot: root, pythonExecutable: URL(fileURLWithPath: "/usr/bin/true"), snapshotURL: root, mode: .demo)
    await runner.shutdown(timeout: .zero)
    do {
        for try await _ in runner.run(.rebuildAll, configuration: config) {}
        Issue.record("Shutdown must prevent a follow-up legacy export or launch")
    } catch is CancellationError {} catch { Issue.record(error) }
}

@Test @MainActor func backgroundResearchCheckpointAcknowledgementPrecedesStoppedAndResumeUsesFreshExecution() async throws {
    let (root, config) = try researchFixture()
    defer { try? FileManager.default.removeItem(at: root) }
    let service = BackgroundResearchService()
    let registry = root.appending(path: "registry"), hash = String(repeating: "b", count: 64)
    try await service.start(campaignHash: hash, registryURL: registry, configuration: config)
    let first = try #require(service.request)
    let deadline = ContinuousClock.now + .seconds(30)
    while service.status == nil && ContinuousClock.now < deadline { try await Task.sleep(for: .milliseconds(10)) }
    #expect(service.status != nil)
    let stopped = await service.shutdown(timeout: .seconds(2))
    #expect(stopped); #expect(!service.interrupted)
    #expect(service.status?.lastCheckpoint == "checkpoint-retained")
    #expect(try first.control.read() == .stopped)
    try await service.start(campaignHash: hash, registryURL: registry, configuration: config)
    #expect(service.request?.runID != first.runID)
    #expect(service.request?.campaignHash == first.campaignHash)
    _ = await service.shutdown(timeout: .seconds(2))
}

@Test func backgroundResearchOwnershipRejectsRecycledBirthAndUnrelatedProcess() async throws {
    let process = Process(); process.executableURL = URL(fileURLWithPath: "/bin/sleep"); process.arguments = ["20"]
    let handle = BackgroundProcessHandle(request: .init(registryURL: URL(fileURLWithPath: "/tmp"), campaignHash: String(repeating: "b", count: 64), runID: "synthetic", controlDirectory: URL(fileURLWithPath: "/tmp"), controlNonce: String(repeating: "n", count: 32)))
    try handle.launch(process)
    defer { if process.isRunning { process.terminate() }; process.waitUntilExit() }
    let birth = try #require(BackgroundProcessBirth.read(process.processIdentifier))
    let receipt = BackgroundProcessOwnership(schemaVersion: 1, runID: "synthetic", nonce: String(repeating: "n", count: 32), pid: process.processIdentifier, parentPID: birth.parentPID, processGroupID: process.processIdentifier, processStartSeconds: birth.seconds, processStartMicroseconds: birth.microseconds + 1, campaignHash: String(repeating: "b", count: 64))
    #expect(throws: BackgroundResearchError.self) { try handle.accept(receipt) }
    #expect(process.isRunning)
}
