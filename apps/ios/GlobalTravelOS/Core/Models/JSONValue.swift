import Foundation

/// A lossless-enough representation of an arbitrary JSON value.
///
/// Used for the untyped parts of the contract: error `details`, `proposed_action.params` and
/// sync `payload`, plus the `server` / `client` snapshots inside a conflict record.
enum JSONValue: Codable, Equatable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
            return
        }
        if let value = try? container.decode(Bool.self) {
            self = .bool(value)
            return
        }
        if let value = try? container.decode(Double.self) {
            self = .number(value)
            return
        }
        if let value = try? container.decode(String.self) {
            self = .string(value)
            return
        }
        if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
            return
        }
        if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
            return
        }
        throw DecodingError.dataCorruptedError(
            in: container,
            debugDescription: "Unsupported JSON value"
        )
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null:
            try container.encodeNil()
        case .bool(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .string(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        }
    }

    var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    var numberValue: Double? {
        if case .number(let value) = self { return value }
        return nil
    }

    var integerValue: Int? {
        if case .number(let value) = self { return Int(value) }
        return nil
    }

    var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    var arrayValue: [JSONValue]? {
        if case .array(let value) = self { return value }
        return nil
    }

    var isNull: Bool {
        if case .null = self { return true }
        return false
    }

    /// Human-readable rendering used by the offline queue detail screen.
    var displayDescription: String {
        switch self {
        case .null:
            return "null"
        case .bool(let value):
            return value ? "true" : "false"
        case .number(let value):
            return value == value.rounded() ? String(Int(value)) : String(value)
        case .string(let value):
            return value
        case .array(let values):
            return "[" + values.map(\.displayDescription).joined(separator: ", ") + "]"
        case .object(let values):
            let pairs = values.keys.sorted().map { "\($0): \(values[$0]?.displayDescription ?? "null")" }
            return "{" + pairs.joined(separator: ", ") + "}"
        }
    }
}

extension Dictionary where Key == String, Value == JSONValue {
    subscript(string key: String) -> String? {
        self[key]?.stringValue
    }

    subscript(int key: String) -> Int? {
        self[key]?.integerValue
    }

    subscript(bool key: String) -> Bool? {
        self[key]?.boolValue
    }
}