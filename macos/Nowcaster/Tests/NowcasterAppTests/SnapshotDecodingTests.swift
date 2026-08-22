import Foundation
import Testing

@testable import NowcasterApp

@Test func decodesPythonGeneratedFixture() throws {
    let url = try #require(
        Bundle.module.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        )
    )
    let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: Data(contentsOf: url))
    #expect(snapshot.schemaVersion == 1)
    #expect(snapshot.instruments.contains { $0.assetClass == .crypto })
    #expect(snapshot.backtests.contains { $0.assetClass == .crypto })
}

@Test func datesDecodeWithAndWithoutFractionalSeconds() throws {
    let data = Data(
        """
        {"generated_at":"2026-08-22T12:34:56.123456Z","git_commit":"abc","data_mode":"demo",\
        "source_posture":"fixture","expectation_mode":"proxy","last_refresh":"2026-08-22T12:34:56Z"}
        """.utf8
    )
    let metadata = try JSONDecoder.nowcaster.decode(SnapshotMetadata.self, from: data)
    #expect(metadata.lastRefresh != nil)
}
