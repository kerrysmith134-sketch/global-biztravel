import Foundation

/// 免钥公开源 iOS 直连（ADR-0034 本地版）。
/// 全部遵循同一红线：失败/结构不对按"不可用"抛出，走 data_gap/降级，绝不伪造。
enum OpenMeteoClient {
    struct Observation: Equatable {
        var temperatureC: Double?
        var windKmh: Double?
        var precipitationMm: Double?
        var weatherCode: Int?
        var observedAt: Date?
    }

    static let severeWindKmh = 50.0
    static let severePrecipitationMm = 10.0

    static func fetchCurrent(lat: Double, lon: Double) throws -> Observation {
        let urlString = "https://api.open-meteo.com/v1/forecast"
            + "?latitude=\(lat)&longitude=\(lon)"
            + "&current=temperature_2m,wind_speed_10m,precipitation,weather_code"
            + "&wind_speed_unit=kmh&timezone=UTC"
        guard let url = URL(string: urlString) else {
            throw LLMService.Unavailable(message: "URL 无效")
        }
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, _) = try synchronouslyFetch(request)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let current = object["current"] as? [String: Any] else {
            throw LLMService.Unavailable(message: "Open-Meteo 响应缺少 current 块")
        }
        func number(_ key: String) -> Double? {
            current[key] as? Double
        }
        var observedAt: Date?
        if let time = current["time"] as? String {
            observedAt = ISO8601.day(from: time) ?? ISO8601.date(from: time)
        }
        return Observation(
            temperatureC: number("temperature_2m"),
            windKmh: number("wind_speed_10m"),
            precipitationMm: number("precipitation"),
            weatherCode: current["weather_code"] as? Int,
            observedAt: observedAt
        )
    }

    static func isSevere(_ observation: Observation) -> Bool {
        if let wind = observation.windKmh, wind >= severeWindKmh { return true }
        if let rain = observation.precipitationMm, rain >= severePrecipitationMm { return true }
        return false
    }
}

enum OpenSkyClient {
    struct FlightState: Equatable {
        var icao24: String
        var callsign: String
        var originCountry: String
        var altitudeM: Double?
        var velocityMs: Double?
        var onGround: Bool
    }

    static func fetchByCallsign(_ callsign: String) throws -> [FlightState] {
        let normalized = callsign.trimmingCharacters(in: .whitespaces).uppercased()
        guard let url = URL(string: "https://opensky-network.org/api/states/all?callsign=\(normalized)") else {
            throw LLMService.Unavailable(message: "URL 无效")
        }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, _) = try synchronouslyFetch(request)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let states = object["states"] as? [[Any]] else {
            throw LLMService.Unavailable(message: "OpenSky 响应缺少 states 数组")
        }
        // 冻结下标：0 icao24 / 1 callsign / 2 origin_country / 5 lon / 6 lat / 7 alt / 8 on_ground / 9 velocity
        return states.compactMap { row in
            guard row.count >= 10 else { return nil }
            let callsignValue = (row[1] as? String)?.trimmingCharacters(in: .whitespaces).uppercased() ?? ""
            guard callsignValue == normalized else { return nil }
            return FlightState(
                icao24: row[0] as? String ?? "",
                callsign: callsignValue,
                originCountry: row[2] as? String ?? "",
                altitudeM: row[7] as? Double,
                velocityMs: row[9] as? Double,
                onGround: (row[8] as? Int).map { $0 != 0 } ?? (row[8] as? Bool ?? false)
            )
        }
    }
}

enum OsrmClient {
    static let defaultBaseURL = "https://router.project-osrm.org"

    struct TableResult {
        var durationsSeconds: [[Double]]
        var distancesMeters: [[Double]]
    }

    /// GROUND 段驾车矩阵（失败由调用方回退离线估算并标 stale）。
    static func table(originLat: Double, originLon: Double, stops: [(Double, Double)]) throws -> TableResult {
        let coords = [(originLon, originLat)] + stops.map { ($1, $0) }
        let path = coords.map { String(format: "%.6f,%.6f", $0.0, $0.1) }.joined(separator: ";")
        guard let url = URL(string: "\(defaultBaseURL)/table/v1/driving/\(path)?annotations=duration,distance") else {
            throw LLMService.Unavailable(message: "URL 无效")
        }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, _) = try synchronouslyFetch(request)
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["code"] == nil || object["code"] as? String == "Ok",
              let durations = object["durations"] as? [[Double]],
              let distances = object["distances"] as? [[Double]] else {
            throw LLMService.Unavailable(message: "OSRM 响应不可用")
        }
        return TableResult(durationsSeconds: durations, distancesMeters: distances)
    }
}

// MARK: - 共享同步 fetch

func synchronouslyFetch(_ request: URLRequest) throws -> (Data, URLResponse?) {
    var result: (Data, URLResponse?)?
    var thrown: Error?
    let semaphore = DispatchSemaphore(value: 0)
    URLSession.shared.dataTask(with: request) { data, response, error in
        if let error { thrown = error } else { result = (data ?? Data(), response) }
        semaphore.signal()
    }.resume()
    semaphore.wait()
    if let thrown { throw LLMService.Unavailable(message: "网络失败：\(thrown.localizedDescription)") }
    return result ?? (Data(), nil)
}
