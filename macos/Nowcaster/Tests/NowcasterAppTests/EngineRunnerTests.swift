import Foundation
import Testing

@testable import NowcasterApp

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
    strategyIDs: ["rsi_reversal", "ema_adx_trend"],
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
            "--strategy-id", "ema_adx_trend",
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

    let export = try EngineJob.exportSnapshot.invocation(configuration: fixtureConfiguration)
    #expect(
        export.arguments == [
            "-m", "src.cli", "strategy", "export",
            "--output", "/tmp/Nowcaster Project/data/app/nowcaster-snapshot.json",
            "--project-root", "/tmp/Nowcaster Project",
        ]
    )
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

@Test func runnerEmitsACompletedPartialJSONLineBeforeProcessExit() async throws {
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

    var progressInstant: ContinuousClock.Instant?
    var completedInstant: ContinuousClock.Instant?
    for try await event in EngineRunner().run(.exportSnapshot, configuration: configuration) {
        if event.event == "progress" {
            progressInstant = .now
        } else if event.event == "job_completed" {
            completedInstant = .now
        }
    }

    let progress = try #require(progressInstant)
    let completed = try #require(completedInstant)
    #expect(progress.duration(to: completed) > .milliseconds(500))
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
