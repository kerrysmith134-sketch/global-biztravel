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
