import Foundation

struct ActionCenter: Codable, Equatable {
    var generatedAt: Date?
    var total: Int
    var counts: [String: Int]?
    var sections: [ActionSection]

    var sectionList: [ActionSection] { sections }

    var actionableCount: Int {
        (counts?["DECISION"] ?? 0)
            + (counts?["APPROVAL"] ?? 0)
            + (counts?["FAILED"] ?? 0)
    }
}

struct ActionSection: Codable, Equatable, Identifiable {
    var key: String
    var title: String
    var items: [ActionItem]

    var id: String { key }

    var count: Int { items.count }
}

struct ActionItem: Codable, Equatable, Identifiable {
    var category: String
    var kind: String
    var entityId: String
    var title: String
    var detail: String
    var status: String
    var severity: String?
    var tripId: String?
    var tripName: String?
    var actionHint: String
    var availableActions: [String]?
    var createdAt: Date?
    var updatedAt: Date?

    var id: String { "\(kind):\(entityId)" }

    var categoryLabel: String {
        switch category.uppercased() {
        case "DECISION": return "待决定"
        case "APPROVAL": return "待审批"
        case "RUNNING": return "执行中"
        case "FAILED": return "失败"
        case "PREPARATION": return "待确认"
        case "COMPLETED": return "已完成"
        default: return category
        }
    }

    var kindLabel: String {
        switch kind.uppercased() {
        case "SUGGESTION": return "建议"
        case "EXPENSE_REPORT": return "报销单"
        case "JOB": return "后台任务"
        case "CRM_SYNC": return "CRM 同步"
        default: return kind
        }
    }

    var glyph: String {
        switch category.uppercased() {
        case "DECISION": return "questionmark.circle.fill"
        case "APPROVAL": return "checkmark.seal.fill"
        case "RUNNING": return "arrow.triangle.2.circlepath"
        case "FAILED": return "exclamationmark.triangle.fill"
        case "PREPARATION": return "tray.and.arrow.up.fill"
        case "COMPLETED": return "checkmark.circle.fill"
        default: return "circle"
        }
    }

    var tint: String {
        switch category.uppercased() {
        case "DECISION": return "blue"
        case "APPROVAL": return "indigo"
        case "RUNNING": return "teal"
        case "FAILED": return "red"
        case "PREPARATION": return "orange"
        case "COMPLETED": return "green"
        default: return "gray"
        }
    }

    var tripLabel: String? {
        guard let tripName, !tripName.isEmpty else { return nil }
        return tripName
    }

    var canApproveSuggestion: Bool {
        kind.uppercased() == "SUGGESTION"
            && (availableActions ?? []).contains("APPROVE")
    }
}
