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

    private enum CodingKeys: String, CodingKey {
        case id, tripId, category, amount, currency, occurredOn, note, receiptRef
    }

    init(
        id: String,
        tripId: String,
        category: String,
        amount: Decimal,
        currency: String,
        occurredOn: Date? = nil,
        note: String? = nil,
        receiptRef: String? = nil
    ) {
        self.id = id
        self.tripId = tripId
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
        tripId = try container.decode(String.self, forKey: .tripId)
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

enum TripExpenseCategory: String, Codable, CaseIterable, Identifiable {
    case transport = "TRANSPORT"
    case lodging = "LODGING"
    case meals = "MEALS"
    case other = "OTHER"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .transport: return "交通"
        case .lodging: return "住宿"
        case .meals: return "餐饮"
        case .other: return "其他"
        }
    }
}

struct TripExpenseCreateRequest: Encodable, Equatable {
    var category: TripExpenseCategory
    var amount: Decimal
    var currency: String
    var occurredOn: String
    var note: String?

    private enum CodingKeys: String, CodingKey {
        case category, amount, currency, occurredOn, note
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(category, forKey: .category)
        try container.encode(amount, forKey: .amount)
        try container.encode(currency, forKey: .currency)
        try container.encode(occurredOn, forKey: .occurredOn)
        try container.encodeIfPresent(note, forKey: .note)
    }
}

struct CurrencyTotal: Codable, Equatable, Identifiable {
    var currency: String
    var amount: Double
    var count: Int

    var id: String { currency }
}

struct FxConversion: Codable, Equatable {
    var currency: String
    var rate: Double
    var asOf: Date?
    var source: String?
    var viaInverse: Bool?
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
    var conversionBaseCurrency: String?
    var convertedActualTotal: Decimal?
    var convertedPlannedCostTotal: Decimal?
    var convertedVarianceVsBudget: Decimal?
    var conversionComplete: Bool?
    var unconvertedCurrencies: [String]?
    var fxConversions: [FxConversion]?

    init(
        budgetAmount: Decimal? = nil,
        budgetCurrency: String,
        plannedCostTotal: Double? = nil,
        plannedCostCurrency: String? = nil,
        plannedCostUnknownCount: Int? = nil,
        actualByCurrency: [CurrencyTotal]? = nil,
        actualTotalInBudgetCurrency: Decimal? = nil,
        varianceVsBudget: Decimal? = nil,
        varianceVsPlan: Double? = nil,
        conversionBaseCurrency: String? = nil,
        convertedActualTotal: Decimal? = nil,
        convertedPlannedCostTotal: Decimal? = nil,
        convertedVarianceVsBudget: Decimal? = nil,
        conversionComplete: Bool? = nil,
        unconvertedCurrencies: [String]? = nil,
        fxConversions: [FxConversion]? = nil
    ) {
        self.budgetAmount = budgetAmount
        self.budgetCurrency = budgetCurrency
        self.plannedCostTotal = plannedCostTotal
        self.plannedCostCurrency = plannedCostCurrency
        self.plannedCostUnknownCount = plannedCostUnknownCount
        self.actualByCurrency = actualByCurrency
        self.actualTotalInBudgetCurrency = actualTotalInBudgetCurrency
        self.varianceVsBudget = varianceVsBudget
        self.varianceVsPlan = varianceVsPlan
        self.conversionBaseCurrency = conversionBaseCurrency
        self.convertedActualTotal = convertedActualTotal
        self.convertedPlannedCostTotal = convertedPlannedCostTotal
        self.convertedVarianceVsBudget = convertedVarianceVsBudget
        self.conversionComplete = conversionComplete
        self.unconvertedCurrencies = unconvertedCurrencies
        self.fxConversions = fxConversions
    }

    private enum CodingKeys: String, CodingKey {
        case budgetAmount, budgetCurrency, plannedCostTotal, plannedCostCurrency
        case plannedCostUnknownCount, actualByCurrency, actualTotalInBudgetCurrency
        case varianceVsBudget, varianceVsPlan, conversionBaseCurrency
        case convertedActualTotal, convertedPlannedCostTotal, convertedVarianceVsBudget
        case conversionComplete, unconvertedCurrencies, fxConversions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        budgetAmount = try container.decodeDecimalIfPresent(forKey: .budgetAmount)
        budgetCurrency = try container.decode(String.self, forKey: .budgetCurrency)
        plannedCostTotal = try container.decodeIfPresent(Double.self, forKey: .plannedCostTotal)
        plannedCostCurrency = try container.decodeIfPresent(String.self, forKey: .plannedCostCurrency)
        plannedCostUnknownCount = try container.decodeIfPresent(Int.self, forKey: .plannedCostUnknownCount)
        actualByCurrency = try container.decodeIfPresent([CurrencyTotal].self, forKey: .actualByCurrency)
        actualTotalInBudgetCurrency = try container.decodeDecimalIfPresent(
            forKey: .actualTotalInBudgetCurrency
        )
        varianceVsBudget = try container.decodeDecimalIfPresent(forKey: .varianceVsBudget)
        varianceVsPlan = try container.decodeIfPresent(Double.self, forKey: .varianceVsPlan)
        conversionBaseCurrency = try container.decodeIfPresent(
            String.self,
            forKey: .conversionBaseCurrency
        )
        convertedActualTotal = try container.decodeDecimalIfPresent(forKey: .convertedActualTotal)
        convertedPlannedCostTotal = try container.decodeDecimalIfPresent(
            forKey: .convertedPlannedCostTotal
        )
        convertedVarianceVsBudget = try container.decodeDecimalIfPresent(
            forKey: .convertedVarianceVsBudget
        )
        conversionComplete = try container.decodeIfPresent(Bool.self, forKey: .conversionComplete)
        unconvertedCurrencies = try container.decodeIfPresent(
            [String].self,
            forKey: .unconvertedCurrencies
        )
        fxConversions = try container.decodeIfPresent([FxConversion].self, forKey: .fxConversions)
    }

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

    var convertedActualLabel: String? {
        guard let conversionBaseCurrency, let convertedActualTotal else { return nil }
        return "\(conversionBaseCurrency) \(NSDecimalNumber(decimal: convertedActualTotal).stringValue)"
    }

    var conversionNote: String? {
        guard let conversionBaseCurrency else { return nil }
        if conversionComplete == true {
            return "已按组织录入汇率折算到 \(conversionBaseCurrency)"
        }
        let missing = (unconvertedCurrencies ?? []).joined(separator: "、")
        return missing.isEmpty ? "折算不完整：部分费用未知" : "未折算币种：\(missing)"
    }
}
