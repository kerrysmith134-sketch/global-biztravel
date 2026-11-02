import Foundation

/// `GET /v1/trips/{id}/outcome` — 出差结果分析与客户机会复盘（PRD P1）。
struct TripOutcome: Codable, Equatable {
    var tripId: String
    var status: String?
    var window: [String: String]?
    var visits: [String: Int]?
    var visitLabels: [String: String]?
    var recordOutcomes: [String: Int]?
    var recordOutcomeLabels: [String: String]?
    var planCost: OutcomeCost?
    var actualCost: [OutcomeCurrencyTotal]?
    var insightsByKind: [String: Int]?
    var insightKindLabels: [String: String]?
    var opportunitySignals: [OutcomeSignal]?
    var memorySummaries: [OutcomeSummary]?

    var visitList: [OutcomeVisitCount] {
        (visits ?? [String: Int]())
            .sorted { $0.key < $1.key }
            .map { OutcomeVisitCount(status: $0.key, count: $0.value) }
    }
    var labelMap: [String: String] { visitLabels ?? [:] }
    var outcomeMap: [String: String] { recordOutcomeLabels ?? [:] }
    var actualCostList: [OutcomeCurrencyTotal] { actualCost ?? [] }
    var signalList: [OutcomeSignal] { opportunitySignals ?? [] }
}

struct OutcomeCost: Codable, Equatable {
    var total: Double?
    var currency: String?
    var unknownCount: Int?

    var label: String {
        guard let total else { return "未知" }
        let unknown = (unknownCount ?? 0) > 0 ? "（\(unknownCount ?? 0) 段未知）" : ""
        return "\(currency ?? "") \(String(format: "%.2f", total))\(unknown)"
    }
}

struct OutcomeCurrencyTotal: Codable, Equatable, Identifiable {
    var currency: String
    var amount: Double
    var count: Int

    var id: String { currency }
    var label: String { "\(currency) \(String(format: "%.2f", amount))" }
}

struct OutcomeSignal: Codable, Equatable, Identifiable {
    var kind: String
    var kindLabel: String
    var text: String
    var visitId: String?
    var requiresConfirmation: Bool?

    var id: String { "\(kind):\(text.prefix(24))" }
}

struct OutcomeSummary: Codable, Equatable, Identifiable {
    var kind: String
    var summary: String

    var id: String { "\(kind):\(summary.prefix(24))" }
}

struct OutcomeVisitCount: Identifiable {
    var status: String
    var count: Int
    var id: String { status }
}
