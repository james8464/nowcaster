import Foundation
import Testing

@testable import NowcasterApp

@Test func rejectsUnknownSchemaBeforeDecodingPayload() async {
    let repository = SnapshotRepository()
    await #expect(throws: SnapshotRepositoryError.incompatibleSchema(999)) {
        try await repository.load(data: Data("{\"schema_version\":999}".utf8))
    }
}

@Test @MainActor func environmentPreservesLastKnownGoodAfterFailure() async throws {
    let repository = SnapshotRepository()
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    let environment = AppEnvironment(repository: repository)
    await environment.load(data: try Data(contentsOf: url))
    let loadedCommit = try #require(environment.snapshot?.metadata.gitCommit)
    await environment.load(data: Data("not-json".utf8))
    #expect(environment.snapshot?.metadata.gitCommit == loadedCommit)
    if case .stale = environment.state {
        // Expected: the visible snapshot remains usable while the error is surfaced.
    } else {
        Issue.record("Expected a stale last-known-good state")
    }
}
