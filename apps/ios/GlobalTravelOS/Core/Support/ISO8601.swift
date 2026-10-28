import Foundation

/// ISO-8601 helpers used by the wire layer.
///
/// The backend mixes two shapes:
/// - full instants, e.g. `2026-11-04T09:00:00+01:00` or `2026-10-27T09:30:00.500+00:00`
/// - calendar dates without a zone, e.g. `2026-11-04`
///
/// Calendar dates are deliberately kept as `String` in the domain models (see `PlanDay.date`)
/// because turning them into a `Date` would shift the day once a timezone is applied.
/// Only real instants are parsed into `Date`.
enum ISO8601 {
    static let utc = TimeZone(identifier: "UTC") ?? TimeZone(secondsFromGMT: 0)!

    private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        formatter.timeZone = utc
        return formatter
    }()

    private static let internetFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        formatter.timeZone = utc
        return formatter
    }()

    private static let offsetFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = utc
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ssXXXXX"
        return formatter
    }()

    private static let localDateTimeFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = utc
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return formatter
    }()

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = utc
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()

    /// Parses a full ISO-8601 instant. Returns `nil` for calendar-date-only strings.
    static func date(from string: String) -> Date? {
        if let value = fractionalFormatter.date(from: string) { return value }
        if let value = internetFormatter.date(from: string) { return value }
        if let value = offsetFormatter.date(from: string) { return value }
        if string.count > 10, let value = localDateTimeFormatter.date(from: string) { return value }
        return nil
    }

    /// Parses a `yyyy-MM-dd` calendar date at UTC midnight.
    static func day(from string: String) -> Date? {
        guard string.count == 10 else { return nil }
        return dayFormatter.date(from: string)
    }

    static func string(from date: Date) -> String {
        internetFormatter.string(from: date)
    }

    static func stringWithFractionalSeconds(from date: Date) -> String {
        fractionalFormatter.string(from: date)
    }

    static func dayString(from date: Date) -> String {
        dayFormatter.string(from: date)
    }

    /// Short, locale-independent rendering used by list rows.
    static func shortDisplay(from date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "MM-dd HH:mm"
        return formatter.string(from: date)
    }

    /// `HH:mm` rendering in the supplied zone — stops are displayed in the place's own time.
    static func timeDisplay(from date: Date, timeZone: TimeZone = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
}

/// Single source of truth for JSON encoding/decoding across the client.
///
/// `convertFromSnakeCase` / `convertToSnakeCase` map the backend's `snake_case` wire names onto
/// Swift property names, so domain models never carry hand-written string keys.
enum DateCoding {
    static func decoder() -> JSONDecoder {
        let jsonDecoder = JSONDecoder()
        jsonDecoder.keyDecodingStrategy = .convertFromSnakeCase
        jsonDecoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            if let value = ISO8601.date(from: raw) { return value }
            if let value = ISO8601.day(from: raw) { return value }
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported ISO-8601 value: \(raw)"
            )
        }
        return jsonDecoder
    }

    static func encoder() -> JSONEncoder {
        let jsonEncoder = JSONEncoder()
        jsonEncoder.keyEncodingStrategy = .convertToSnakeCase
        jsonEncoder.dateEncodingStrategy = .custom { date, encoder in
            var container = encoder.singleValueContainer()
            try container.encode(ISO8601.stringWithFractionalSeconds(from: date))
        }
        jsonEncoder.outputFormatting = [.sortedKeys]
        return jsonEncoder
    }

    static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try decoder().decode(type, from: data)
    }

    static func decode<T: Decodable>(_ type: T.Type, fromJSON string: String) throws -> T {
        guard let data = string.data(using: .utf8) else {
            throw APIError.invalidResponse("Response body is not valid UTF-8")
        }
        return try decode(type, from: data)
    }

    static func encode<T: Encodable>(_ value: T) throws -> Data {
        try encoder().encode(value)
    }

    static func encodeToJSONString<T: Encodable>(_ value: T) -> String {
        guard let data = try? encoder().encode(value), let string = String(data: data, encoding: .utf8) else {
            return "<unencodable \(type(of: value))>"
        }
        return string
    }
}
