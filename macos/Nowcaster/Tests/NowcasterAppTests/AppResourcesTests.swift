import XCTest
@testable import NowcasterApp

final class AppResourcesTests: XCTestCase {
    func testProvidesTheBundledSnapshotFixture() {
        XCTAssertNotNil(AppResources.url(
            forResource: "nowcaster-snapshot",
            withExtension: "json",
            subdirectory: "Fixtures"
        ))
    }
}
