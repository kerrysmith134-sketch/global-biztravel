import Foundation

/// Unified error envelope returned for every non-2xx response:
///
/// ```json
/// {"error":{"code":"PLAN_VERSION_CONFLICT","message":"...","details":{},"trace_id":"..."}}
/// ```
struct APIErrorEnvelope: Codable, Equatable {
    var error: APIErrorBody
}

struct APIErrorBody: Codable, Equatable {
    var code: String
    var message: String
    var details: [String: JSONValue]
    var traceId: String?

    enum CodingKeys: String, CodingKey {
        case code
        case message
        case details
        case traceId
    }

    init(code: String, message: String, details: [String: JSONValue] = [:], traceId: String? = nil) {
        self.code = code
        self.message = message
        self.details = details
        self.traceId = traceId
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.code = try container.decode(String.self, forKey: .code)
        self.message = try container.decodeIfPresent(String.self, forKey: .message) ?? ""
        self.details = try container.decodeIfPresent([String: JSONValue].self, forKey: .details) ?? [:]
        self.traceId = try container.decodeIfPresent(String.self, forKey: .traceId)
    }
}
