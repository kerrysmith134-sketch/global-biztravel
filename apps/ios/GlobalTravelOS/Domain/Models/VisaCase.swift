import Foundation

enum VisaCaseStatus: String, Codable, CaseIterable, Identifiable {
    case new = "NEW"
    case preparing = "PREPARING"
    case submitted = "SUBMITTED"
    case approved = "APPROVED"
    case rejected = "REJECTED"
    case cancelled = "CANCELLED"
    case unknown = "UNKNOWN"

    var id: String { rawValue }

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = VisaCaseStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .new: return "新建"
        case .preparing: return "准备中"
        case .submitted: return "已提交"
        case .approved: return "已通过"
        case .rejected: return "被拒绝"
        case .cancelled: return "已取消"
        case .unknown: return "未知"
        }
    }

    var nextStatuses: [VisaCaseStatus] {
        switch self {
        case .new: return [.preparing, .cancelled]
        case .preparing: return [.submitted, .cancelled]
        case .submitted: return [.approved, .rejected, .cancelled]
        default: return []
        }
    }
}

struct VisaChecklistItem: Codable, Equatable, Identifiable {
    var key: String
    var label: String
    var status: String
    var note: String?

    var id: String { key }

    var statusLabel: String {
        switch status.uppercased() {
        case "READY": return "已备齐"
        case "NOT_APPLICABLE": return "不适用"
        default: return "缺失"
        }
    }

    var isReady: Bool { status.uppercased() == "READY" }
}

struct VisaCase: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String
    var countryCode: String
    var status: VisaCaseStatus
    var checklist: [VisaChecklistItem]?
    var notes: String?
    var passportExpiresAt: Date?
    var confidence: Double?
    var submittedAt: Date?
    var decisionAt: Date?
    var createdAt: Date?
    var updatedAt: Date?

    var checklistItems: [VisaChecklistItem] { checklist ?? [] }

    var readyCount: Int { checklistItems.filter(\.isReady).count }

    var checklistLabel: String {
        "清单 \(readyCount)/\(checklistItems.count)"
    }
}

struct EntryAssessment: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String
    var countryCode: String
    var summary: String
    var source: String
    var sourceVersion: String
    var confidence: Double?
    var manualReviewRequired: Bool?
    var assessedAt: Date?

    var sourceLabel: String {
        "\(source) · \(sourceVersion)"
    }

    var confidenceLabel: String {
        guard let confidence else { return "置信度未知" }
        return String(format: "置信度 %.0f%%", confidence * 100)
    }
}

struct VisaCaseCreateRequest: Codable, Equatable {
    var countries: [String]?
}

struct VisaCaseUpdateRequest: Codable, Equatable {
    var status: VisaCaseStatus?
}
