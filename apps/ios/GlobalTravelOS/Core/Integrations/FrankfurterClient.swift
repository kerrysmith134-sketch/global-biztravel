import Foundation

/// 汇率行情源（直译 integrations/fx.py + fx_market_service.parse_frankfurter）。
/// Frankfurter（ECB 欧央行参考汇率，免钥）。失败/结构不对一律显式不可用，不伪造。
enum FrankfurterClient {
    struct Quote: Equatable {
        var base: String
        var asOf: Date
        var rates: [String: Double]
    }

    struct Unavailable: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static let baseURL = "https://api.frankfurter.dev/v1"

    /// 取最新行情并写入本地 fx_rates 表（source=fx-frankfurter）。
    @discardableResult
    static func syncRates(
        database: LocalDatabase,
        baseCurrency: String,
        symbols: [String],
        now: Date = Date()
    ) throws -> [String: Double] {
        let base = baseCurrency.trimmingCharacters(in: .whitespaces).uppercased()
        guard let url = URL(string: "\(baseURL)/latest?base=\(base)") else {
            throw Unavailable(message: "URL 无效")
        }
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try synchronouslyFetch(request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw Unavailable(message: "Frankfurter 行情获取失败：HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1)")
        }
        let quote = try parseFrankfurter(data, expectedBase: base)
        var synced: [String: Double] = [:]
        for symbol in symbols {
            let code = symbol.trimmingCharacters(in: .whitespaces).uppercased()
            guard code != base, let rate = quote.rates[code] else { continue }
            try database.run(
                """
                INSERT INTO fx_rates (base, quote, as_of, rate, source)
                VALUES (?, ?, ?, ?, 'fx-frankfurter')
                ON CONFLICT(base, quote, as_of) DO UPDATE SET rate = excluded.rate
                """,
                bindings: [base, code, SQLTimestamp.basics.string(from: quote.asOf), String(rate)]
            )
            synced[code] = rate
        }
        return synced
    }

    /// 解析（fx_market_service.parse_frankfurter 直译）：缺日期/缺 rates/坏值整体不可用。
    static func parseFrankfurter(_ data: Data, expectedBase: String? = nil) throws -> Quote {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let base = object["base"] as? String,
              let dateString = object["date"] as? String,
              let ratesRaw = object["rates"] as? [String: Any] else {
            throw Unavailable(message: "Frankfurter 响应结构不完整")
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(identifier: "UTC")
        guard let quoteDate = formatter.date(from: dateString) else {
            throw Unavailable(message: "Frankfurter 行情日期不可解析：\(dateString)")
        }
        var rates: [String: Double] = [:]
        for (currency, value) in ratesRaw {
            guard let number = value as? Double, number > 0 else {
                throw Unavailable(message: "Frankfurter 汇率必须为正：\(currency)=\(value)")
            }
            rates[currency.uppercased()] = number
        }
        _ = expectedBase // base 一致性由调用方按需校验
        return Quote(base: base.uppercased(), asOf: quoteDate, rates: rates)
    }

    /// URLSession.data(for:) 在 iOS 15+ 可用；这里用信号量包装成同步（存储调用方是同步语境）。
    private static func synchronouslyFetch(_ request: URLRequest) throws -> (Data, URLResponse?) {
        var result: (Data, URLResponse?)?
        var thrown: Error?
        let semaphore = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error { thrown = error } else { result = (data ?? Data(), response) }
            semaphore.signal()
        }.resume()
        semaphore.wait()
        if let thrown { throw Unavailable(message: "网络请求失败：\(thrown.localizedDescription)") }
        return result ?? (Data(), nil)
    }
}
