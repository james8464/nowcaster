import AppKit
import Foundation
import UserNotifications

enum LiveNotificationCategory: String, CaseIterable, Sendable {
    case entry, target, stop, close, health
}

struct LiveNotificationCandidate: Equatable, Sendable {
    let id: String
    let category: LiveNotificationCategory
    let title: String
    let body: String
}

struct LiveNotificationPolicy: Sendable {
    private var admitted: Set<String> = []

    mutating func admit(_ candidate: LiveNotificationCandidate, appIsActive: Bool, quietHours: Bool) -> Bool {
        guard !admitted.contains(candidate.id), !appIsActive else { return false }
        if quietHours, candidate.category == .entry { return false }
        admitted.insert(candidate.id)
        return true
    }
}

@MainActor
final class NotificationService {
    private var policy = LiveNotificationPolicy()

    func requestAuthorization() async -> Bool {
        let center = UNUserNotificationCenter.current()
        let categories = [
            LiveNotificationCategory.entry,
            .target,
            .stop,
            .close,
            .health,
        ].map {
            UNNotificationCategory(identifier: $0.rawValue, actions: [], intentIdentifiers: [], options: [])
        }
        center.setNotificationCategories(Set(categories))
        return (try? await center.requestAuthorization(
            options: [.alert, .sound, .badge, .providesAppNotificationSettings]
        )) ?? false
    }

    func deliver(
        _ candidate: LiveNotificationCandidate,
        quietHours: Bool = false,
        enabledCategories: Set<LiveNotificationCategory> = Set(LiveNotificationCategory.allCases)
    ) async -> Bool {
        guard enabledCategories.contains(candidate.category) else { return false }
        guard policy.admit(candidate, appIsActive: NSApplication.shared.isActive, quietHours: quietHours) else {
            return false
        }
        let center = UNUserNotificationCenter.current()
        let settings = await center.notificationSettings()
        guard settings.authorizationStatus == .authorized || settings.authorizationStatus == .provisional else {
            return false
        }
        let content = UNMutableNotificationContent()
        content.title = candidate.title
        content.body = "Open Nowcaster to review the setup and risk levels."
        content.sound = .default
        content.categoryIdentifier = candidate.category.rawValue
        content.userInfo = ["event_id": candidate.id]
        do {
            try await center.add(UNNotificationRequest(identifier: candidate.id, content: content, trigger: nil))
            return true
        } catch {
            return false
        }
    }
}
