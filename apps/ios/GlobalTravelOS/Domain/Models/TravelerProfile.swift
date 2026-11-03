import Foundation

/// `GET/PUT /v1/traveler/profile` — 出差人画像（事实记忆层）。
struct TravelerProfile: Codable, Equatable {
    var version: Int
    var preferences: [String: JSONValue]?
    var nationality: String?
    var notes: String?
    var confidence: Double?
}

struct TravelerProfileUpdate: Encodable {
    var preferences: [String: JSONValue]?
    var nationality: String?
    var notes: String?
    var confidence: Double?

    private enum CodingKeys: String, CodingKey {
        case preferences, nationality, notes, confidence
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(preferences, forKey: .preferences)
        try container.encodeIfPresent(nationality, forKey: .nationality)
        try container.encodeIfPresent(notes, forKey: .notes)
        try container.encodeIfPresent(confidence, forKey: .confidence)
    }
}
