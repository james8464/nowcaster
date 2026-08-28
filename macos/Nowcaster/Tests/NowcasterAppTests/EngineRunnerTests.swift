import Foundation
import Testing
import Darwin

@testable import NowcasterApp

@Test func brokerSecretsAreConsumedForEnvironmentAndRedactedFromDiagnostics() throws {
    let secret = "never-display-this-secret"
    let environment = EngineSecretEnvironment(
        credentials: BrokerCredentials(keyID: "paper-key", secret: secret),
        environment: .paper
    )
    let values = environment.consume()
    #expect(values["APCA_API_KEY_ID"] == "paper-key")
    #expect(values["APCA_API_SECRET_KEY"] == secret)
    #expect(environment.isCleared)
    #expect(environment.consume().isEmpty)

    var decoder = EngineOutputDecoder(redactedValues: Array(values.values))
    _ = decoder.append(Data("broker said \(secret) and paper-key\n".utf8))
    let rendered = decoder.diagnostics.joined()
    #expect(!rendered.contains(secret))
    #expect(!rendered.contains("paper-key"))
    #expect(rendered.contains("[REDACTED]"))
}

private let fixtureConfiguration = EngineConfiguration(
    projectRoot: URL(fileURLWithPath: "/tmp/Nowcaster Project"),
    pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
    snapshotURL: URL(fileURLWithPath: "/tmp/Nowcaster Project/data/app/nowcaster-snapshot.json"),
    mode: .demo
)

private let csvContext = StrategyAssetContext(
    provider: .csv,
    feed: "local",
    symbol: "BTCUSDT",
    interval: .fiveMinutes,
    databaseURL: "duckdb:////tmp/Nowcaster Project/research.duckdb",
    csvURL: URL(fileURLWithPath: "/tmp/Nowcaster Project/bars;literal.csv")
)

private let learningConfiguration = EngineConfiguration(
    projectRoot: fixtureConfiguration.projectRoot,
    pythonExecutable: fixtureConfiguration.pythonExecutable,
    snapshotURL: fixtureConfiguration.snapshotURL,
    mode: fixtureConfiguration.mode,
    strategyIDs: ["rsi_reversal"],
    strategyAsset: csvContext
)

@Test func legacyEngineArgumentsNeverUseAShell() throws {
    let invocation = try EngineJob.fullBacktest.invocation(configuration: fixtureConfiguration)
    #expect(invocation.executableURL.lastPathComponent == "python3")
    #expect(
        invocation.arguments == [
            "-m", "src.cli", "run-all", "--mode", "demo", "--project-root", "/tmp/Nowcaster Project",
        ]
    )
    #expect(!invocation.arguments.contains("sh"))
}

@Test func strategyEvaluationRepeatsTypedArgumentsWithoutShellInterpolation() throws {
    let suspiciousLiteral = "rsi_reversal; touch /tmp/not-created"
    let invocation = try EngineJob.evaluateStrategies(
        strategyIDs: ["rsi_reversal", suspiciousLiteral],
        mode: .paper,
        asset: csvContext
    ).invocation(configuration: fixtureConfiguration)

    #expect(
        invocation.arguments == [
            "-m", "src.cli", "strategy", "evaluate",
            "--strategy-id", "rsi_reversal",
            "--strategy-id", suspiciousLiteral,
            "--provider", "csv",
            "--feed", "local",
            "--symbol", "BTCUSDT",
            "--interval", "5m",
            "--mode", "paper",
            "--database-url", "duckdb:////tmp/Nowcaster Project/research.duckdb",
            "--csv-path", "/tmp/Nowcaster Project/bars;literal.csv",
            "--project-root", "/tmp/Nowcaster Project",
        ]
    )
    #expect(!invocation.arguments.contains(where: { $0 == "sh" || $0 == "-c" }))
}

@Test func learningAndExportArgumentsMatchTheTask7CLI() throws {
    let learning = try EngineJob.learn(
        assetID: "BTCUSDT",
        interval: "5m",
        budget: 24
    ).invocation(configuration: learningConfiguration)
    #expect(
        learning.arguments == [
            "-m", "src.cli", "strategy", "learn",
            "--strategy-id", "rsi_reversal",
            "--provider", "csv",
            "--feed", "local",
            "--symbol", "BTCUSDT",
            "--interval", "5m",
            "--mode", "walk_forward_learning",
            "--database-url", "duckdb:////tmp/Nowcaster Project/research.duckdb",
            "--csv-path", "/tmp/Nowcaster Project/bars;literal.csv",
            "--evaluation-budget", "24",
            "--project-root", "/tmp/Nowcaster Project",
        ]
    )

    let databaseLiteral = "duckdb:////tmp/Nowcaster Project/export;literal.duckdb"
    let export = try EngineJob.exportSnapshot(databaseURL: databaseLiteral)
        .invocation(configuration: fixtureConfiguration)
    #expect(
        export.arguments == [
            "-m", "src.cli", "strategy", "export",
            "--database-url", databaseLiteral,
            "--output", "/tmp/Nowcaster Project/data/app/nowcaster-snapshot.json",
            "--project-root", "/tmp/Nowcaster Project",
        ]
    )
}

@Test func deepResearchArgumentsUseTypedResourceAndPrivateControlValuesWithoutAShell() throws {
    let request = DeepResearchRequest(
        strategyIDs: ["rsi_reversal"],
        asset: csvContext,
        workers: 8,
        evaluationBudget: nil,
        continuous: true,
        timeBudgetSeconds: 3_600,
        seed: 43,
        runID: "deep-run-1",
        controlDirectory: URL(fileURLWithPath: "/tmp/Nowcaster Project/control"),
        controlNonce: String(repeating: "n", count: 64),
        resumeRunID: nil
    )

    let invocation = try EngineJob.deepResearch(request).invocation(configuration: fixtureConfiguration)

    #expect(invocation.arguments == [
        "-m", "src.cli", "strategy", "deep-research",
        "--strategy-id", "rsi_reversal",
        "--provider", "csv", "--feed", "local", "--symbol", "BTCUSDT", "--interval", "5m",
        "--database-url", "duckdb:////tmp/Nowcaster Project/research.duckdb",
        "--csv-path", "/tmp/Nowcaster Project/bars;literal.csv",
        "--workers", "8", "--continuous", "--time-budget-seconds", "3600", "--seed", "43",
        "--control-directory", "/tmp/Nowcaster Project/control",
        "--run-id", "deep-run-1", "--project-root", "/tmp/Nowcaster Project",
    ])
    #expect(invocation.environment["NOWCASTER_DEEP_RESEARCH_CONTROL_NONCE"] == String(repeating: "n", count: 64))
    #expect(!invocation.arguments.contains(String(repeating: "n", count: 64)))
    #expect(!invocation.arguments.contains(where: { $0 == "sh" || $0 == "-c" }))
    #expect(EngineJob.deepResearch(request).followUpExport(configuration: fixtureConfiguration) == .exportSnapshot(databaseURL: csvContext.databaseURL))
}

@Test func deepResearchResourceProfilesBoundWorkersToAvailableProcessors() {
    #expect(DeepResearchResourcePolicy.maximumWorkerCount(activeProcessors: 12) == 10)
    #expect(DeepResearchResourceProfile.performance.workerCount(activeProcessors: 12, customWorkers: 99) == 10)
    #expect(DeepResearchResourceProfile.balanced.workerCount(activeProcessors: 12, customWorkers: 99) == 9)
    #expect(DeepResearchResourceProfile.efficient.workerCount(activeProcessors: 12, customWorkers: 99) == 6)
    #expect(DeepResearchResourceProfile.custom.workerCount(activeProcessors: 12, customWorkers: 99) == 10)
    #expect(DeepResearchResourceProfile.custom.workerCount(activeProcessors: 12, customWorkers: 3) == 3)
    #expect(DeepResearchResourcePolicy.maximumWorkerCount(activeProcessors: 2) == 1)
}

@Test func deepResearchControlFileIsPrivateAtomicAndRejectsTerminalResume() throws {
    let directory = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterControl-\(UUID().uuidString)", directoryHint: .isDirectory)
    defer { try? FileManager.default.removeItem(at: directory) }
    let identity = DeepResearchControlIdentity(
        runID: "deep-run",
        nonce: String(repeating: "x", count: 64),
        directory: directory
    )
    let control = DeepResearchControlFile(identity: identity)

    try control.initialize()
    try control.request(.paused)
    #expect(try control.read() == .paused)
    let attributes = try FileManager.default.attributesOfItem(atPath: identity.fileURL.path)
    #expect((attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600)
    try control.request(.stopped)
    #expect(throws: DeepResearchControlError.terminal) { try control.request(.running) }
}

@Test func strategyRequestsDedupeIDsBoundLearningAndReuseStoredCSV() throws {
    let storedCSV = StrategyAssetContext(
        provider: .csv,
        feed: "local",
        symbol: "BTCUSDT",
        interval: .fiveMinutes
    )
    let evaluation = try EngineJob.evaluateStrategies(
        strategyIDs: ["rsi_reversal", " rsi_reversal ", "ema_adx_trend"],
        mode: .paper,
        asset: storedCSV
    ).invocation(configuration: fixtureConfiguration)
    #expect(evaluation.arguments.filter { $0 == "--strategy-id" }.count == 2)
    #expect(!evaluation.arguments.contains("--csv-path"))

    let storedConfiguration = EngineConfiguration(
        projectRoot: fixtureConfiguration.projectRoot,
        pythonExecutable: fixtureConfiguration.pythonExecutable,
        snapshotURL: fixtureConfiguration.snapshotURL,
        mode: fixtureConfiguration.mode,
        strategyIDs: ["rsi_reversal", "rsi_reversal"],
        strategyAsset: storedCSV
    )
    let learning = try EngineJob.learn(assetID: "BTCUSDT", interval: "5m", budget: 100)
        .invocation(configuration: storedConfiguration)
    #expect(learning.arguments.filter { $0 == "--strategy-id" }.count == 1)
    #expect(!learning.arguments.contains("--csv-path"))

    let plural = EngineConfiguration(
        projectRoot: fixtureConfiguration.projectRoot,
        pythonExecutable: fixtureConfiguration.pythonExecutable,
        snapshotURL: fixtureConfiguration.snapshotURL,
        mode: fixtureConfiguration.mode,
        strategyIDs: ["rsi_reversal", "ema_adx_trend"],
        strategyAsset: storedCSV
    )
    #expect(throws: EngineJobError.learningRequiresSingleStrategy) {
        try EngineJob.learn(assetID: "BTCUSDT", interval: "5m", budget: 20)
            .invocation(configuration: plural)
    }
    #expect(throws: EngineJobError.invalidBudget(0)) {
        try EngineJob.learn(assetID: "BTCUSDT", interval: "5m", budget: 0)
            .invocation(configuration: storedConfiguration)
    }
    #expect(throws: EngineJobError.invalidBudget(101)) {
        try EngineJob.learn(assetID: "BTCUSDT", interval: "5m", budget: 101)
            .invocation(configuration: storedConfiguration)
    }
    #expect(EngineJobError.invalidBudget(101).localizedDescription == "The learning budget must be between 1 and 100, not 101.")
}

@Test func rejectsInvalidTypedStrategyRequests() {
    #expect(throws: EngineJobError.self) {
        try EngineJob.evaluateStrategies(strategyIDs: [], mode: .paper, asset: csvContext)
            .invocation(configuration: fixtureConfiguration)
    }
    #expect(throws: EngineJobError.self) {
        try EngineJob.learn(assetID: "", interval: "5m", budget: 0)
            .invocation(configuration: fixtureConfiguration)
    }
    #expect(throws: EngineJobError.self) {
        try EngineJob.learn(assetID: "BTCUSDT", interval: "5 minutes", budget: 4)
            .invocation(configuration: fixtureConfiguration)
    }
    let missingAssetContext = EngineConfiguration(
        projectRoot: fixtureConfiguration.projectRoot,
        pythonExecutable: fixtureConfiguration.pythonExecutable,
        snapshotURL: fixtureConfiguration.snapshotURL,
        mode: fixtureConfiguration.mode,
        strategyIDs: ["rsi_reversal"]
    )
    #expect(throws: EngineJobError.self) {
        try EngineJob.learn(assetID: "BTCUSDT", interval: "5m", budget: 4)
            .invocation(configuration: missingAssetContext)
    }
}

@Test func parsesStructuredProgressLine() throws {
    let event = try EngineProgressEvent.parse(
        "{\"event\":\"stage_started\",\"stage\":\"train\",\"progress\":0.6}"
    )
    #expect(event.stage == "train")
    #expect(event.progress == 0.6)
}

@Test func incrementalDecoderRetainsPartialLinesAndBoundsDiagnostics() {
    var decoder = EngineOutputDecoder(maximumDiagnostics: 2)
    #expect(decoder.append(Data("{\"event\":\"progress\",\"progress\":".utf8)).isEmpty)
    let events = decoder.append(Data("0.5}\nfirst diagnostic\nsecond diagnostic\nthird diagnostic\n".utf8))

    #expect(events.first?.progress == 0.5)
    #expect(decoder.diagnostics == ["second diagnostic", "third diagnostic"])
    #expect(decoder.emittedDiagnostics == ["second diagnostic", "third diagnostic"])
    #expect(decoder.finish().isEmpty)
}

@Test func runnerOrdersACompletedPartialJSONLineBeforeJobCompletion() async throws {
    let temporaryRoot = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterRunnerTests-(UUID().uuidString)", directoryHint: .isDirectory)
    let sourceRoot = temporaryRoot.appending(path: "src", directoryHint: .isDirectory)
    try FileManager.default.createDirectory(at: sourceRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    try Data().write(to: sourceRoot.appending(path: "__init__.py"))
    try Data(
        """
        import sys, time
        sys.stdout.write('{"event":"progress","stage":"evaluate",')
        sys.stdout.flush()
        time.sleep(0.15)
        sys.stdout.write('"progress":0.5}\\n')
        sys.stdout.flush()
        time.sleep(0.8)
        """.utf8
    ).write(to: sourceRoot.appending(path: "cli.py"))
    let configuration = EngineConfiguration(
        projectRoot: temporaryRoot,
        pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
        snapshotURL: temporaryRoot.appending(path: "snapshot.json"),
        mode: .demo
    )

    var events: [EngineProgressEvent] = []
    for try await event in EngineRunner().run(.exportSnapshot(databaseURL: nil), configuration: configuration) {
        events.append(event)
    }

    let progressIndex = try #require(events.firstIndex(where: { $0.event == "progress" }))
    let completedIndex = try #require(events.firstIndex(where: { $0.event == "job_completed" }))
    #expect(events[progressIndex].progress == 0.5)
    #expect(progressIndex < completedIndex)
}

@Test func runnerSurfacesBoundedNonzeroExitDiagnostics() async throws {
    let configuration = EngineConfiguration(
        projectRoot: URL(fileURLWithPath: "/tmp"),
        pythonExecutable: URL(fileURLWithPath: "/usr/bin/false"),
        snapshotURL: URL(fileURLWithPath: "/tmp/snapshot.json"),
        mode: .demo
    )
    let runner = EngineRunner()
    await #expect(throws: EngineRunnerError.nonzeroExit(1, diagnostics: "")) {
        for try await _ in runner.run(.fullBacktest, configuration: configuration) {}
    }
}

private actor LaunchGate {
    private var entered = false
    private var continuation: CheckedContinuation<Void, Never>?

    func wait() async {
        entered = true
        await withCheckedContinuation { continuation = $0 }
    }

    func waitUntilEntered() async {
        while !entered { await Task.yield() }
    }

    func open() {
        continuation?.resume()
        continuation = nil
    }
}

@Test func cancellationBeforeLaunchCannotStartOrOrphanAProcess() async throws {
    let temporaryRoot = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterEarlyCancel-\(UUID().uuidString)", directoryHint: .isDirectory)
    let sourceRoot = temporaryRoot.appending(path: "src", directoryHint: .isDirectory)
    let marker = temporaryRoot.appending(path: "launched.txt")
    try FileManager.default.createDirectory(at: sourceRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    try Data().write(to: sourceRoot.appending(path: "__init__.py"))
    try Data(
        "from pathlib import Path\nPath(r'\(marker.path)').write_text('launched')\n".utf8
    ).write(to: sourceRoot.appending(path: "cli.py"))
    let configuration = EngineConfiguration(
        projectRoot: temporaryRoot,
        pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
        snapshotURL: temporaryRoot.appending(path: "snapshot.json"),
        mode: .demo
    )
    let gate = LaunchGate()
    let runner = EngineRunner(beforeLaunch: { await gate.wait() })
    let consumer = Task {
        for try await _ in runner.run(.exportSnapshot(databaseURL: nil), configuration: configuration) {}
    }

    await gate.waitUntilEntered()
    consumer.cancel()
    await gate.open()
    _ = try? await consumer.value
    try await Task.sleep(for: .milliseconds(100))
    #expect(!FileManager.default.fileExists(atPath: marker.path))
}

@Test func cancellationEscalatesToKillForASignalResistantChild() async throws {
    let temporaryRoot = FileManager.default.temporaryDirectory
        .appending(path: "NowcasterKillEscalation-\(UUID().uuidString)", directoryHint: .isDirectory)
    let sourceRoot = temporaryRoot.appending(path: "src", directoryHint: .isDirectory)
    let pidFile = temporaryRoot.appending(path: "pid.txt")
    try FileManager.default.createDirectory(at: sourceRoot, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: temporaryRoot) }
    try Data().write(to: sourceRoot.appending(path: "__init__.py"))
    try Data(
        """
        import os, signal, time
        from pathlib import Path
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path(r'\(pidFile.path)').write_text(str(os.getpid()))
        while True: time.sleep(0.05)
        """.utf8
    ).write(to: sourceRoot.appending(path: "cli.py"))
    let configuration = EngineConfiguration(
        projectRoot: temporaryRoot,
        pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
        snapshotURL: temporaryRoot.appending(path: "snapshot.json"),
        mode: .demo
    )
    let consumer = Task {
        for try await _ in EngineRunner().run(.exportSnapshot(databaseURL: nil), configuration: configuration) {}
    }
    let deadline = ContinuousClock.now.advanced(by: .seconds(2))
    while !FileManager.default.fileExists(atPath: pidFile.path), ContinuousClock.now < deadline {
        try await Task.sleep(for: .milliseconds(20))
    }
    let pid = try #require(Int32(String(contentsOf: pidFile, encoding: .utf8)))

    consumer.cancel()
    _ = try? await consumer.value
    let exitDeadline = ContinuousClock.now.advanced(by: .seconds(2))
    while Darwin.kill(pid, 0) == 0, ContinuousClock.now < exitDeadline {
        try await Task.sleep(for: .milliseconds(20))
    }

    #expect(Darwin.kill(pid, 0) == -1)
}
