import Foundation

enum ExpenseReportStatus: String, Codable, CaseIterable, Identifiable {
    case draft = "DRAFT"
    case submitted = "SUBMITTED"
    case approved = "APPROVED"
    case rejected = "REJECTED"
    case unknown = "UNKNOWN"

    var id: String { rawValue }

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = ExpenseReportStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .draft: return "草稿"
        case .submitted: return "已提交"
        case .approved: return "已通过"
        case .rejected: return "已驳回"
        case .unknown: return "未知"
        }
    }
}

struct ExpenseReportItem: Codable, Equatable, Identifiable {
    var id: String
    var reportId: String
    var expenseId: String?
    var category: String
    var amount: Decimal
    var currency: String
    var occurredOn: Date?
    var note: String?
    var receiptRef: String?

    private enum CodingKeys: String, CodingKey {
        case id, reportId, expenseId, category, amount, currency, occurredOn, note, receiptRef
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        reportId = try container.decode(String.self, forKey: .reportId)
        expenseId = try container.decodeIfPresent(String.self, forKey: .expenseId)
        category = try container.decode(String.self, forKey: .category)
        amount = try container.decodeDecimal(forKey: .amount)
        currency = try container.decode(String.self, forKey: .currency)
        occurredOn = try container.decodeIfPresent(Date.self, forKey: .occurredOn)
        note = try container.decodeIfPresent(String.self, forKey: .note)
        receiptRef = try container.decodeIfPresent(String.self, forKey: .receiptRef)
    }

    var categoryLabel: String {
        switch category.uppercased() {
        case "TRANSPORT": return "交通"
        case "LODGING": return "住宿"
        case "MEALS": return "餐饮"
        default: return "其他"
        }
    }

    var amountLabel: String {
        "\(currency) \(NSDecimalNumber(decimal: amount).stringValue)"
    }
}

struct ExpenseReport: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String
    var status: ExpenseReportStatus
    var notes: String?
    var submittedAt: Date?
    var decidedAt: Date?
    var decidedByUserId: String?
    var items: [ExpenseReportItem]?
    var totals: [CurrencyTotal]?
    var createdAt: Date?
    var updatedAt: Date?

    init(
        id: String,
        tripId: String,
        status: ExpenseReportStatus = .draft,
        notes: String? = nil,
        submittedAt: Date? = nil,
        decidedAt: Date? = nil,
        decidedByUserId: String? = nil,
        items: [ExpenseReportItem]? = nil,
        totals: [CurrencyTotal]? = nil,
        createdAt: Date? = nil,
        updatedAt: Date? = nil
    ) {
        self.id = id
        self.tripId = tripId
        self.status = status
        self.notes = notes
        self.submittedAt = submittedAt
        self.decidedAt = decidedAt
        self.decidedByUserId = decidedByUserId
        self.items = items
        self.totals = totals
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    var itemList: [ExpenseReportItem] { items ?? [] }
    var totalList: [CurrencyTotal] { totals ?? [] }

    var totalsLabel: String {
        guard !totalList.isEmpty else { return "无明细" }
        return totalList.map { "\($0.currency) \(String(format: "%.2f", $0.amount))" }.joined(separator: " · ")
    }

    var canSubmit: Bool { status == .draft }
}

struct ExpenseReportCreateRequest: Codable, Equatable {
    var notes: String?
    var expenseIds: [String]?
}


// Read-only, evidence-backed closeout assessment returned by
// GET /v1/trips/{trip_id}/closeout-readiness.
enum TripCloseoutStatus: String, Codable {
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
        case .blocked: return "存在待处理事项"
        case .readyForReview: return "可以提交复核"
        case .reportSubmitted: return "报销单已提交"
        case .reportApproved: return "报销单已批准"
        case .readyWithExceptions: return "存在例外，需核对"
        case .unknown: return "状态未知"
        }
    }
}

enum TripCloseoutIssueSeverity: String, Codable {
    case blocker = "BLOCKER"
    case warning = "WARNING"
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
    var latestReportStatus: ExpenseReportStatus?
    var blockerCount: Int
    var warningCount: Int
}

struct TripCloseoutReadiness: Codable, Equatable {
    var tripId: String
    var status: TripCloseoutStatus
    var generatedAt: Date
    var summary: TripCloseoutSummary
    var totalsByCurrency: [CurrencyTotal]
    var blockers: [TripCloseoutIssue]
    var warnings: [TripCloseoutIssue]
}
