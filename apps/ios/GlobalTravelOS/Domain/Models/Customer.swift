import Foundation

/// Customer / contact record attached to a trip.
///
/// The backend contract does not enumerate customer fields, so the model is deliberately open:
/// the small set of fields the client renders is typed, and anything else a deployment adds is
/// ignored by `Codable` rather than crashing the screen.
struct Customer: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var company: String?
    var title: String?
    var email: String?
    var phone: String?
    var countryCode: String?
    var city: String?
    var placeId: String?
    var placeName: String?
    var tags: [String]?
    var notes: String?
    var priority: String?
    var version: Int?
    var createdAt: Date?
    var updatedAt: Date?

    init(
        id: String,
        name: String,
        company: String? = nil,
        title: String? = nil,
        email: String? = nil,
        phone: String? = nil,
        countryCode: String? = nil,
        city: String? = nil,
        placeId: String? = nil,
        placeName: String? = nil,
        tags: [String]? = nil,
        notes: String? = nil,
        priority: String? = nil,
        version: Int? = nil,
        createdAt: Date? = nil,
        updatedAt: Date? = nil
    ) {
        self.id = id
        self.name = name
        self.company = company
        self.title = title
        self.email = email
        self.phone = phone
        self.countryCode = countryCode
        self.city = city
        self.placeId = placeId
        self.placeName = placeName
        self.tags = tags
        self.notes = notes
        self.priority = priority
        self.version = version
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    var displayOrganization: String {
        [company, title].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var displayLocation: String {
        [city, countryCode].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    var tagList: [String] { tags ?? [] }
}