import Foundation
import Observation

struct BackgroundResourceSnapshot: Equatable, Sendable {
    enum Thermal: Sendable { case nominal, fair, serious, critical, unknown }
    var thermal: Thermal = .nominal
    var lowPower = false
    var memoryAdmissible = true
    var diskAdmissible = true
    var collectorHealthy = true
    static let healthy = Self()
}

enum BackgroundResourcePolicy {
    static func efficientWorkers(_ cores: Int) -> Int { min(max(cores / 2, 1), max(cores - 2, 1)) }
    static func shouldPause(_ snapshot: BackgroundResourceSnapshot) -> Bool {
        ![.nominal, .fair].contains(snapshot.thermal) || snapshot.lowPower || !snapshot.memoryAdmissible || !snapshot.diskAdmissible || !snapshot.collectorHealthy
    }
}

@MainActor @Observable final class BackgroundResourceMonitor {
    private(set) var snapshot = BackgroundResourceSnapshot(thermal: .unknown)
    @ObservationIgnored private let sample: (@MainActor () -> BackgroundResourceSnapshot)?
    @ObservationIgnored private var task: Task<Void, Never>?
    @ObservationIgnored private var pressureSource: DispatchSourceMemoryPressure?
    @ObservationIgnored private var memoryAdmissible = true

    init(sample: (@MainActor () -> BackgroundResourceSnapshot)? = nil) { self.sample = sample }

    func start(_ changed: @escaping @MainActor (BackgroundResourceSnapshot) async -> Void) {
        guard task == nil else { return }
        let pressure = DispatchSource.makeMemoryPressureSource(eventMask: [.normal, .warning, .critical], queue: .main)
        pressure.setEventHandler { [weak self] in
            let normal = pressure.data.contains(.normal)
            Task { @MainActor [weak self] in self?.memoryAdmissible = normal }
        }
        pressure.resume(); pressureSource = pressure
        task = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                var sample: BackgroundResourceSnapshot
                if let provider = self.sample { sample = provider() }
                else { sample = await Task.detached(priority: .utility) { Self.systemSnapshot() }.value }
                guard !Task.isCancelled else { return }
                sample.memoryAdmissible = sample.memoryAdmissible && self.memoryAdmissible
                self.snapshot = sample
                await changed(sample)
                try? await Task.sleep(for: .seconds(2))
            }
        }
    }
    func stop() { task?.cancel(); task = nil; pressureSource?.cancel(); pressureSource = nil }
    func current() -> BackgroundResourceSnapshot {
        var value = sample?() ?? snapshot; value.memoryAdmissible = value.memoryAdmissible && memoryAdmissible
        snapshot = value; return value
    }

    nonisolated static func systemSnapshot(directory: URL = FileManager.default.homeDirectoryForCurrentUser) -> BackgroundResourceSnapshot {
        let thermal: BackgroundResourceSnapshot.Thermal
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: thermal = .nominal
        case .fair: thermal = .fair
        case .serious: thermal = .serious
        case .critical: thermal = .critical
        @unknown default: thermal = .unknown
        }
        let capacity = try? directory.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]).volumeAvailableCapacityForImportantUsage
        return .init(thermal: thermal, lowPower: ProcessInfo.processInfo.isLowPowerModeEnabled,
            diskAdmissible: (capacity ?? 0) >= 1_073_741_824)
    }
}
