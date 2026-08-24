import Foundation

enum SnapshotRepositoryError: Error, Equatable, LocalizedError, Sendable {
    case incompatibleSchema(Int)
    case unreadable(String)

    var errorDescription: String? {
        switch self {
        case let .incompatibleSchema(version):
            "Snapshot schema \(version) is not supported by this version of Nowcaster."
        case let .unreadable(message):
            "The research snapshot could not be read: \(message)"
        }
    }
}

struct SnapshotRepository: Sendable {
    static let supportedSchemaVersion = 2

    private struct SchemaEnvelope: Decodable {
        let schemaVersion: Int
    }

    func load(data: Data) async throws -> NowcasterSnapshot {
        guard data.count <= SnapshotDecodingLimits.maximumSnapshotBytes else {
            throw SnapshotRepositoryError.unreadable(
                "Snapshot exceeds the \(SnapshotDecodingLimits.maximumSnapshotBytes)-byte safety limit."
            )
        }
        let envelope: SchemaEnvelope
        do {
            envelope = try JSONDecoder.nowcaster.decode(SchemaEnvelope.self, from: data)
        } catch {
            throw SnapshotRepositoryError.unreadable(error.localizedDescription)
        }
        guard envelope.schemaVersion == Self.supportedSchemaVersion else {
            throw SnapshotRepositoryError.incompatibleSchema(envelope.schemaVersion)
        }
        do {
            let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: data)
            try snapshot.validateSchemaV2()
            return snapshot
        } catch {
            throw SnapshotRepositoryError.unreadable(error.localizedDescription)
        }
    }

    func load(url: URL) async throws -> NowcasterSnapshot {
        do {
            if let fileSize = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize,
               fileSize > SnapshotDecodingLimits.maximumSnapshotBytes {
                throw SnapshotRepositoryError.unreadable(
                    "Snapshot exceeds the \(SnapshotDecodingLimits.maximumSnapshotBytes)-byte safety limit."
                )
            }
            return try await load(data: Data(contentsOf: url, options: [.mappedIfSafe]))
        } catch let error as SnapshotRepositoryError {
            throw error
        } catch {
            throw SnapshotRepositoryError.unreadable(error.localizedDescription)
        }
    }
}
