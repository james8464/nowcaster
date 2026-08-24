import Foundation
import Testing

@testable import NowcasterApp

@Test func bundledSnapshotIsExplicitlyLiveLockedWithoutRecommendationCopy() async throws {
    let url = try #require(Bundle.module.url(forResource: "nowcaster-snapshot", withExtension: "json", subdirectory: "Fixtures"))
    let snapshot = try await SnapshotRepository().load(url: url)
    let presentation = ExecutionPresentation(snapshot: snapshot)
    #expect(presentation.stateTitle == "Live Locked")
    #expect(!presentation.summary.localizedCaseInsensitiveContains("recommended"))
    #expect(presentation.readinessGates.contains { !$0.passed })
    #expect(presentation.accountLabel == "No broker account connected")
}
