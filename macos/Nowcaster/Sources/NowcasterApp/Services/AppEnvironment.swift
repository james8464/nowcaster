import Foundation
import Observation

enum SnapshotLoadState: Sendable {
    case idle
    case loading
    case loaded
    case stale(String)
    case incompatible(Int)
    case failure(String)
}

@MainActor
@Observable
final class AppEnvironment {
    private(set) var state: SnapshotLoadState = .idle
    private(set) var snapshot: NowcasterSnapshot?
    @ObservationIgnored private let repository: SnapshotRepository

    init(repository: SnapshotRepository = SnapshotRepository()) {
        self.repository = repository
    }

    func load(data: Data) async {
        state = .loading
        do {
            snapshot = try await repository.load(data: data)
            state = .loaded
        } catch let SnapshotRepositoryError.incompatibleSchema(version) {
            state = snapshot == nil ? .incompatible(version) : .stale("Incompatible snapshot schema \(version)")
        } catch {
            state = snapshot == nil ? .failure(error.localizedDescription) : .stale(error.localizedDescription)
        }
    }

    func load(url: URL) async {
        state = .loading
        do {
            snapshot = try await repository.load(url: url)
            state = .loaded
        } catch let SnapshotRepositoryError.incompatibleSchema(version) {
            state = snapshot == nil ? .incompatible(version) : .stale("Incompatible snapshot schema \(version)")
        } catch {
            state = snapshot == nil ? .failure(error.localizedDescription) : .stale(error.localizedDescription)
        }
    }
}
