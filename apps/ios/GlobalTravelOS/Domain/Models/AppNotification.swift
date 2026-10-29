import Foundation

/// `GET /v1/notifications` 的一条通知（PRD S8-08 分级通知）。
struct AppNotification: Codable, Equatable, Identifiable {
    var id: String
    var suggestionId: String?
    var kind: String
    var title: String
    var body: String
    var status: String
    var provider: String
    var priority: String?
    var issueKey: String?
    var attempts: Int?
    var sentAt: Date?
    var readAt: Date?
    var createdAt: Date?

    var isUnread: Bool { readAt == nil }

    var priorityLabel: String {
        switch (priority ?? "P2").uppercased() {
        case "P0": return "紧急"
        case "P1": return "高优先级"
        case "P2": return "一般"
        case "P3": return "信息性"
        default: return priority ?? ""
        }
    }

    var tintName: String {
        switch (priority ?? "P2").uppercased() {
        case "P0": return "red"
        case "P1": return "orange"
        case "P2": return "blue"
        case "P3": return "gray"
        default: return "gray"
        }
    }
}

struct NotificationFeed: Codable, Equatable {
    var items: [AppNotification]
    var unread: Int?
    var unreadByPriority: [String: Int]?

    var unreadCount: Int { unread ?? items.filter(\.isUnread).count }

    var unreadBreakdown: String {
        guard let unreadByPriority else { return unreadCount > 0 ? "未读 \(unreadCount)" : "全部已读" }
        let parts = ["P0", "P1", "P2", "P3"].compactMap { key -> String? in
            guard let count = unreadByPriority[key], count > 0 else { return nil }
            return "\(key)×\(count)"
        }
        return parts.isEmpty ? "全部已读" : "未读 " + parts.joined(separator: " · ")
    }
}
