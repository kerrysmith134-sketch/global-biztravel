import Foundation

/// Locale-independent currency rendering for trip budget / business-value fields.
enum Currency {
    static func format(_ amount: Double, code: String, locale: Locale = Locale(identifier: "en_US")) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = code
        formatter.locale = locale
        formatter.maximumFractionDigits = 2
        return formatter.string(from: NSNumber(value: amount)) ?? "\(code) \(amount)"
    }

    /// Compact form used inside dense list rows: `12.3K`, `1.2M`.
    static func formatCompact(_ amount: Double, code: String) -> String {
        let magnitude = abs(amount)
        switch magnitude {
        case 1_000_000...:
            return "\(code) \(String(format: "%.1f", amount / 1_000_000))M"
        case 1_000...:
            return "\(code) \(String(format: "%.1f", amount / 1_000))K"
        default:
            return "\(code) \(String(format: "%.0f", amount))"
        }
    }
}
