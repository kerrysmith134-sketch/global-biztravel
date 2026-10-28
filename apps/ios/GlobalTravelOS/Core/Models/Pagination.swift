import Foundation

/// Cursor page shape shared by every list endpoint: `{"items":[...],"next_cursor":null}`.
struct Page<Item: Decodable>: Decodable {
    var items: [Item]
    var nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case items
        case nextCursor
    }

    init(items: [Item], nextCursor: String? = nil) {
        self.items = items
        self.nextCursor = nextCursor
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.items = try container.decodeIfPresent([Item].self, forKey: .items) ?? []
        self.nextCursor = try container.decodeIfPresent(String.self, forKey: .nextCursor)
    }

    var isEmpty: Bool { items.isEmpty }

    func map<Other>(_ transform: (Item) -> Other) -> Page<Other> where Other: Decodable {
        Page<Other>(items: items.map(transform), nextCursor: nextCursor)
    }
}

/// Cursor request helpers so paging arguments are spelled the same way everywhere.
struct PageRequest {
    var limit: Int
    var cursor: String?

    static let `default` = PageRequest(limit: 20, cursor: nil)

    init(limit: Int = 20, cursor: String? = nil) {
        self.limit = limit
        self.cursor = cursor
    }

    var queryItems: [URLQueryItem] {
        var items = [URLQueryItem(name: "limit", value: String(limit))]
        if let cursor = cursor, !cursor.isEmpty {
            items.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return items
    }
}