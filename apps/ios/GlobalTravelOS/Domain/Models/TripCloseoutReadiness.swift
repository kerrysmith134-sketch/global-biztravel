import Foundation

enum TripCloseoutStatus: String, Codable, CaseIterable, Equatable {
    case blocked = "BLOCKED"
    case readyForReview = "READY_FOR_REVIEW"
    case reportSubmitted = "REPORT_SUBMITTED"
    case reportApproved = "REPORT_APPROVED"
    case readyWithExceptions = "READY_WITH_EXCEPTIONS"
    case unknown = "UNKNOWN"

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TripCloseoutStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .blocked: return "需先处理"
        case .readyForReview: return "可进入审核"
        case .reportSubmitted: return "报销单已提交"
        case .reportApproved: return "报销已通过"
        case .readyWithExceptions: return "存在例外，需确认"
        case .unknown: return "状态未知"
        }
    }

    var symbolName: String {
        switch self {
        case .blocked: return "exclamationmark.octagon.fill"
        case .readyForReview: return "checkmark.circle"
        case .reportSubmitted: return "paperplane.circle"
        case .reportApproved: return "checkmark.seal.fill"
        case .readyWithExceptions: return "exclamationmark.triangle"
        case .unknown: return "questionmark.circle"
        }
    }
}

enum TripCloseoutIssueSeverity: String, Codable, Equatable {
    case blocker = "BLOCKER"
    case warning = "WARNING"
    case unknown = "UNKNOWN"

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = TripCloseoutIssueSeverity(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .blocker: return "阻断项"
        case .warning: return "提醒"
        case .unknown: return "待确认"
        }
    }

    var symbolName: String {
        switch self {
        case .blocker: return "xmark.octagon.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .unknown: return "questionmark.circle"
        }
    }
}

struct TripCloseoutIssue: Codable, Equatable, Identifiable {
    var id: String
    var code: String
    var severity: TripCloseoutIssueSeverity
    var title: String
    var detail: String
    var sourceType: String
    var sourceId: String
    var actionHint: String
    var sourceVisitId: String?

    var sourceLabel: String {
        switch sourceType.uppercased() {
        case "VISIT": return "拜访记录"
        case "VISIT_FOLLOW_UP": return "商务跟进"
        case "EXPENSE": return "费用记录"
        case "EXPENSE_REPORT": return "报销单"
        case "RECEIPT_ASSET": return "原始票据"
        case "TRIP": return "行程"
        default: return sourceType
        }
    }
}

struct TripCloseoutSummary: Codable, Equatable {
    var visitCount: Int
    var visitOutcomeCount: Int
    var visitsOpenCount: Int
    var visitsMissingOutcomeCount: Int
    var expenseCount: Int
    var expensesInReportCount: Int
    var expensesNotInReportCount: Int
    var expensesWithoutReceiptCount: Int
    var receiptAssetCount: Int
    var receiptAssetUnavailableCount: Int
    var ocrNeedsReviewCount: Int
    var openBusinessFollowUpCount: Int
    var latestReportId: String?
    var latestReportStatus: String?
    var blockerCount: Int
    var warningCount: Int
}

struct TripCloseoutReadiness: Codable, Equatable, Identifiable {
    var tripId: String
    var status: TripCloseoutStatus
    var generatedAt: Date
    var summary: TripCloseoutSummary
    var totalsByCurrency: [CurrencyTotal]
    var blockers: [TripCloseoutIssue]
    var warnings: [TripCloseoutIssue]

    var id: String { tripId }
    var issues: [TripCloseoutIssue] { blockers + warnings }
}
