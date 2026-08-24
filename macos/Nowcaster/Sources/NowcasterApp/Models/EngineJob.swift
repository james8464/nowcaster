import Foundation

enum EngineMode: String, Codable, CaseIterable, Sendable {
    case demo
    case live
}

enum StrategyRunMode: String, Codable, CaseIterable, Sendable {
    case development
    case walkForwardLearning = "walk_forward_learning"
    case frozen
    case paper
}

enum StrategyProvider: String, Codable, CaseIterable, Sendable {
    case alpaca
    case binance
    case csv
}

enum StrategyInterval: String, Codable, CaseIterable, Sendable {
    case oneMinute = "1m"
    case fiveMinutes = "5m"
    case fifteenMinutes = "15m"
    case thirtyMinutes = "30m"
    case oneHour = "1h"
    case fourHours = "4h"
    case oneDay = "1d"
}

struct StrategyAssetContext: Equatable, Sendable {
    let provider: StrategyProvider
    let feed: String
    let symbol: String
    let interval: StrategyInterval
    let databaseURL: String?
    let csvURL: URL?

    init(
        provider: StrategyProvider,
        feed: String,
        symbol: String,
        interval: StrategyInterval,
        databaseURL: String? = nil,
        csvURL: URL? = nil
    ) {
        self.provider = provider
        self.feed = feed
        self.symbol = symbol
        self.interval = interval
        self.databaseURL = databaseURL
        self.csvURL = csvURL
    }
}

struct EngineConfiguration: Sendable {
    let projectRoot: URL
    let pythonExecutable: URL
    let snapshotURL: URL
    let mode: EngineMode
    let strategyIDs: [String]
    let strategyAsset: StrategyAssetContext?

    init(
        projectRoot: URL,
        pythonExecutable: URL,
        snapshotURL: URL,
        mode: EngineMode,
        strategyIDs: [String] = [],
        strategyAsset: StrategyAssetContext? = nil
    ) {
        self.projectRoot = projectRoot
        self.pythonExecutable = pythonExecutable
        self.snapshotURL = snapshotURL
        self.mode = mode
        self.strategyIDs = strategyIDs
        self.strategyAsset = strategyAsset
    }

    func scoped(strategyIDs: [String], asset: StrategyAssetContext) -> EngineConfiguration {
        EngineConfiguration(
            projectRoot: projectRoot,
            pythonExecutable: pythonExecutable,
            snapshotURL: snapshotURL,
            mode: mode,
            strategyIDs: strategyIDs,
            strategyAsset: asset
        )
    }
}

struct EngineInvocation: Sendable {
    let executableURL: URL
    let arguments: [String]
    let workingDirectoryURL: URL
}

enum EngineJobError: Error, Equatable, LocalizedError, Sendable {
    case emptyStrategyIDs
    case learningRequiresSingleStrategy
    case invalidAsset(String)
    case invalidInterval(String)
    case invalidBudget(Int)

    var errorDescription: String? {
        switch self {
        case .emptyStrategyIDs: "Select at least one strategy to evaluate."
        case .learningRequiresSingleStrategy: "Bounded learning requires exactly one unique strategy."
        case let .invalidAsset(value): "The strategy asset is invalid: \(value)"
        case let .invalidInterval(value): "The strategy interval is invalid: \(value)"
        case let .invalidBudget(value): "The learning budget must be between 1 and 100, not \(value)."
        }
    }
}

enum EngineJob: Sendable, Equatable {
    case rebuildAll
    case fullBacktest
    case evaluateStrategies(strategyIDs: [String], mode: StrategyRunMode, asset: StrategyAssetContext)
    case learn(assetID: String, interval: String, budget: Int)
    case exportSnapshot

    var title: String {
        switch self {
        case .rebuildAll: "Rebuild all research"
        case .fullBacktest: "Run full backtest"
        case .evaluateStrategies: "Evaluate selected strategies"
        case .learn: "Run bounded learning"
        case .exportSnapshot: "Export app snapshot"
        }
    }

    var stageName: String {
        switch self {
        case .rebuildAll: "rebuild_all"
        case .fullBacktest: "full_backtest"
        case .evaluateStrategies: "evaluate"
        case .learn: "learn"
        case .exportSnapshot: "export"
        }
    }

    var exportsSnapshotAfterSuccess: Bool {
        switch self {
        case .evaluateStrategies, .learn: true
        default: false
        }
    }

    func invocation(configuration: EngineConfiguration) throws -> EngineInvocation {
        var command: [String]
        switch self {
        case .rebuildAll:
            command = ["demo", "--mode", configuration.mode.rawValue]
        case .fullBacktest:
            command = ["run-all", "--mode", configuration.mode.rawValue]
        case let .evaluateStrategies(strategyIDs, mode, asset):
            let normalizedIDs = normalizedStrategyIDs(strategyIDs)
            guard !normalizedIDs.isEmpty, normalizedIDs.allSatisfy({ !$0.isEmpty }) else {
                throw EngineJobError.emptyStrategyIDs
            }
            try validate(asset: asset)
            command = ["strategy", "evaluate"]
            for strategyID in normalizedIDs {
                command += ["--strategy-id", strategyID]
            }
            command += strategyArguments(asset: asset, mode: mode)
        case let .learn(assetID, rawInterval, budget):
            let assetID = assetID.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !assetID.isEmpty else { throw EngineJobError.invalidAsset(assetID) }
            guard let interval = StrategyInterval(rawValue: rawInterval) else {
                throw EngineJobError.invalidInterval(rawInterval)
            }
            guard (1 ... 100).contains(budget) else { throw EngineJobError.invalidBudget(budget) }
            let normalizedIDs = normalizedStrategyIDs(configuration.strategyIDs)
            guard normalizedIDs.count == 1 else { throw EngineJobError.learningRequiresSingleStrategy }
            guard let asset = configuration.strategyAsset,
                  asset.symbol == assetID,
                  asset.interval == interval
            else { throw EngineJobError.invalidAsset(assetID) }
            try validate(asset: asset)
            command = ["strategy", "learn"]
            for strategyID in normalizedIDs {
                command += ["--strategy-id", strategyID]
            }
            command += strategyArguments(asset: asset, mode: .walkForwardLearning)
            command += ["--evaluation-budget", String(budget)]
        case .exportSnapshot:
            command = ["strategy", "export", "--output", configuration.snapshotURL.path]
        }
        command += ["--project-root", configuration.projectRoot.path]
        return EngineInvocation(
            executableURL: configuration.pythonExecutable,
            arguments: ["-m", "src.cli"] + command,
            workingDirectoryURL: configuration.projectRoot
        )
    }

    private func validate(asset: StrategyAssetContext) throws {
        guard !asset.feed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !asset.symbol.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else { throw EngineJobError.invalidAsset(asset.symbol) }
    }

    private func normalizedStrategyIDs(_ strategyIDs: [String]) -> [String] {
        var seen: Set<String> = []
        return strategyIDs.compactMap { rawValue in
            let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty, seen.insert(value).inserted else { return nil }
            return value
        }
    }

    private func strategyArguments(asset: StrategyAssetContext, mode: StrategyRunMode) -> [String] {
        var arguments = [
            "--provider", asset.provider.rawValue,
            "--feed", asset.feed,
            "--symbol", asset.symbol,
            "--interval", asset.interval.rawValue,
            "--mode", mode.rawValue,
        ]
        if let databaseURL = asset.databaseURL {
            arguments += ["--database-url", databaseURL]
        }
        if let csvURL = asset.csvURL {
            arguments += ["--csv-path", csvURL.path]
        }
        return arguments
    }
}

struct EngineProgressEvent: Codable, Equatable, Sendable, Identifiable {
    let id: UUID
    let event: String
    let stage: String?
    let progress: Double?
    let message: String?

    private enum CodingKeys: String, CodingKey {
        case event, stage, progress, message
    }

    init(event: String, stage: String? = nil, progress: Double? = nil, message: String? = nil) {
        id = UUID()
        self.event = event
        self.stage = stage
        self.progress = progress
        self.message = message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = UUID()
        event = try container.decode(String.self, forKey: .event)
        stage = try container.decodeIfPresent(String.self, forKey: .stage)
        progress = try container.decodeIfPresent(Double.self, forKey: .progress)
        message = try container.decodeIfPresent(String.self, forKey: .message)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(event, forKey: .event)
        try container.encodeIfPresent(stage, forKey: .stage)
        try container.encodeIfPresent(progress, forKey: .progress)
        try container.encodeIfPresent(message, forKey: .message)
    }

    static func parse(_ line: String) throws -> EngineProgressEvent {
        try JSONDecoder().decode(EngineProgressEvent.self, from: Data(line.utf8))
    }
}
