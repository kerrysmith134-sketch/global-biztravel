import Foundation

struct TripExpense: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String
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

struct CurrencyTotal: Codable, Equatable, Identifiable {
    var currency: String
    var amount: Double
    var count: Int

    var id: String { currency }
}

struct BudgetSummary: Codable, Equatable {
    var budgetAmount: Decimal?
    var budgetCurrency: String
    var plannedCostTotal: Double?
    var plannedCostCurrency: String?
    var plannedCostUnknownCount: Int?
    var actualByCurrency: [CurrencyTotal]?
    var actualTotalInBudgetCurrency: Decimal?
    var varianceVsBudget: Decimal?
    var varianceVsPlan: Double?

    var actualList: [CurrencyTotal] { actualByCurrency ?? [] }

    var budgetLabel: String {
        guard let budgetAmount else { return "未设置预算" }
        return "\(budgetCurrency) \(NSDecimalNumber(decimal: budgetAmount).stringValue)"
    }

    var plannedLabel: String {
        guard let plannedCostTotal else { return "暂无计划估算" }
        let unknown = (plannedCostUnknownCount ?? 0) > 0 ? "（\(plannedCostUnknownCount ?? 0) 段费用未知）" : ""
        return "\(plannedCostCurrency ?? budgetCurrency) \(String(format: "%.2f", plannedCostTotal))\(unknown)"
    }

    var actualLabel: String {
        guard !actualList.isEmpty else { return "暂无实际费用" }
        return actualList.map { "\($0.currency) \(String(format: "%.2f", $0.amount))" }.joined(separator: " · ")
    }

    var varianceLabel: String {
        guard let varianceVsBudget else { return "—" }
        return "\(budgetCurrency) \(NSDecimalNumber(decimal: varianceVsBudget).stringValue)"
    }
}
