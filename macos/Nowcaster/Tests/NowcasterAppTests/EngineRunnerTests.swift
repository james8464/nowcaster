import Foundation
import Testing

@testable import NowcasterApp

private let fixtureConfiguration = EngineConfiguration(
    projectRoot: URL(fileURLWithPath: "/tmp/Nowcaster Project"),
    pythonExecutable: URL(fileURLWithPath: "/usr/bin/python3"),
    snapshotURL: URL(fileURLWithPath: "/tmp/Nowcaster Project/data/app/nowcaster-snapshot.json"),
    mode: .demo
)

@Test func engineArgumentsNeverUseAShell() {
    let invocation = EngineJob.fullBacktest.invocation(configuration: fixtureConfiguration)
    #expect(invocation.executableURL.lastPathComponent == "python3")
    #expect(
        invocation.arguments == [
            "-m", "src.cli", "run-all", "--mode", "demo", "--project-root", "/tmp/Nowcaster Project",
        ]
    )
    #expect(!invocation.arguments.contains("sh"))
}

@Test func parsesStructuredProgressLine() throws {
    let event = try EngineProgressEvent.parse(
        "{\"event\":\"stage_started\",\"stage\":\"train\",\"progress\":0.6}"
    )
    #expect(event.stage == "train")
    #expect(event.progress == 0.6)
}

@Test func runnerSurfacesNonzeroExit() async throws {
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
