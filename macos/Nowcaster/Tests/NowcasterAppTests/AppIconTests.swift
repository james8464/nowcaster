import AppKit
import XCTest

final class AppIconTests: XCTestCase {
    func testAppIconIsAValid1024PixelSquarePNG() throws {
        let packageRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let iconURL = packageRoot
            .appending(path: "Sources/NowcasterApp/Resources/AppIcon.png")

        let image = try XCTUnwrap(NSImage(contentsOf: iconURL))
        let representation = try XCTUnwrap(image.representations.first)

        XCTAssertEqual(representation.pixelsWide, 1_024)
        XCTAssertEqual(representation.pixelsHigh, 1_024)
    }
}
