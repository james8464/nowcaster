import Foundation

enum AppResources {
    static var bundle: Bundle {
        #if SWIFT_PACKAGE
        Bundle.module
        #else
        Bundle.main
        #endif
    }

    static func url(forResource name: String, withExtension extensionName: String?, subdirectory: String? = nil) -> URL? {
        #if SWIFT_PACKAGE
        bundle.url(forResource: name, withExtension: extensionName, subdirectory: subdirectory)
        #else
        bundle.url(forResource: name, withExtension: extensionName, subdirectory: subdirectory)
            ?? bundle.url(forResource: name, withExtension: extensionName)
        #endif
    }
}
