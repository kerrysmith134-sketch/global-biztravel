import Foundation

enum ExpenseReportStatus: String, Codable, CaseIterable, Identifiable {
    case draft = "DRAFT"
    case submitted = "SUBMITTED"
    case approved = "APPROVED"
    case rejected = "REJECTED"
    case cancelled = "CANCELLED"
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
        case .cancelled: return "已撤销"
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

    init(
        id: String,
        reportId: String,
        expenseId: String?,
        category: String,
        amount: Decimal,
        currency: String,
        occurredOn: Date? = nil,
        note: String? = nil,
        receiptRef: String? = nil
    ) {
        self.id = id
        self.reportId = reportId
        self.expenseId = expenseId
        self.category = category
        self.amount = amount
        self.currency = currency
        self.occurredOn = occurredOn
        self.note = note
        self.receiptRef = receiptRef
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
    var cancelledAt: Date?
    var cancelledByUserId: String?
    var cancellationReason: String?
    var canCancel: Bool?
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
        cancelledAt: Date? = nil,
        cancelledByUserId: String? = nil,
        cancellationReason: String? = nil,
        canCancel: Bool? = nil,
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
        self.cancelledAt = cancelledAt
        self.cancelledByUserId = cancelledByUserId
        self.cancellationReason = cancellationReason
        self.canCancel = canCancel
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

    /// Multiple reports may split a Trip's expenses. Their non-rejected immutable
    /// snapshots must collectively cover every current expense before any is submitted.
    static func snapshotsCoverCurrentExpenses(
        _ reports: [ExpenseReport],
        currentExpenseIDs: Set<String>
    ) -> Bool {
        let snapshotIDs = Set(
            reports
                .filter { $0.status != .rejected && $0.status != .cancelled }
                .flatMap(\.itemList)
                .compactMap(\.expenseId)
        )
        return currentExpenseIDs.isSubset(of: snapshotIDs)
    }
    var canSubmit: Bool { status == .draft }
    /// Cancellation is only exposed when the server has confirmed caller authorization.
    var canCancelInUI: Bool { canCancel ?? false }
}

struct ExpenseReportCreateRequest: Codable, Equatable {
    var notes: String?
    var expenseIds: [String]?
}
