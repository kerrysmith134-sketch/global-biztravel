import Foundation

/// 客户（对齐服务端契约 CustomerRead，审计 5.5：客户端此前无客户管理）。
struct Customer: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var externalRef: String?
    var tier: String            // A / B / C
    var countryCode: String?
    var industry: String?
    var primaryContactName: String?
    var primaryContactEmail: String?
    var primaryContactPhone: String?
    var status: String          // ACTIVE / INACTIVE
    var ownerUserId: String?
    var createdAt: Date?
    var updatedAt: Date?

    init(
        id: String,
        name: String,
        externalRef: String? = nil,
        tier: String = "C",
        countryCode: String? = nil,
        industry: String? = nil,
        primaryContactName: String? = nil,
        primaryContactEmail: String? = nil,
        primaryContactPhone: String? = nil,
        status: String = "ACTIVE",
        ownerUserId: String? = nil,
        createdAt: Date? = nil,
        updatedAt: Date? = nil
    ) {
        self.id = id
        self.name = name
        self.externalRef = externalRef
        self.tier = tier
        self.countryCode = countryCode
        self.industry = industry
        self.primaryContactName = primaryContactName
        self.primaryContactEmail = primaryContactEmail
        self.primaryContactPhone = primaryContactPhone
        self.status = status
        self.ownerUserId = ownerUserId
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    var tierLabel: String {
        switch tier.uppercased() {
        case "A": return "A 级战略客户"
        case "B": return "B 级重点客户"
        default: return "C 级一般客户"
        }
    }

    var isActive: Bool { status.uppercased() == "ACTIVE" }

    var contactSummary: String? {
        [primaryContactName, primaryContactEmail, primaryContactPhone]
            .compactMap { $0 }.filter { !$0.isEmpty }.first
    }
}

/// 新建客户请求（对齐 CustomerCreate）。
struct CustomerCreateRequest: Codable, Equatable {
    var name: String
    var externalRef: String?
    var tier: String
    var countryCode: String?
    var industry: String?
    var primaryContactName: String?
    var primaryContactEmail: String?
    var primaryContactPhone: String?
    var notes: String?

    init(
        name: String,
        externalRef: String? = nil,
        tier: String = "C",
        countryCode: String? = nil,
        industry: String? = nil,
        primaryContactName: String? = nil,
        primaryContactEmail: String? = nil,
        primaryContactPhone: String? = nil,
        notes: String? = nil
    ) {
        self.name = name
        self.externalRef = externalRef
        self.tier = tier
        self.countryCode = countryCode
        self.industry = industry
        self.primaryContactName = primaryContactName
        self.primaryContactEmail = primaryContactEmail
        self.primaryContactPhone = primaryContactPhone
        self.notes = notes
    }
}

/// One address explicitly linked to a customer by the backend customer_places relation.
/// Coordinates and geocoding provenance belong to the address, not to the customer record.
struct CustomerPlace: Decodable, Equatable, Identifiable {
    var placeId: String
    var label: String?
    var isPrimary: Bool
    var name: String
    var addressLine: String?
    var city: String?
    var region: String?
    var countryCode: String?
    var lat: Double?
    var lon: Double?
    var geocodeStatus: String
    var geocodeSource: String?
    var geocodeConfidence: Decimal?

    var id: String { placeId }

    var hasValidCoordinates: Bool {
        guard let lat, let lon else { return false }
        return (-90.0...90.0).contains(lat) && (-180.0...180.0).contains(lon)
    }

    var displayAddress: String {
        let normalizedCity = city?.trimmingCharacters(in: .whitespacesAndNewlines)
        let distinctRegion = region?.trimmingCharacters(in: .whitespacesAndNewlines)
            .flatMap { value in
                value.isEmpty || value.caseInsensitiveCompare(normalizedCity ?? "") == .orderedSame
                    ? nil : value
            }
        return [addressLine, normalizedCity, distinctRegion, countryCode]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    var confidenceLabel: String? {
        guard let geocodeConfidence else { return nil }
        let value = NSDecimalNumber(decimal: geocodeConfidence).doubleValue
        return String(format: "%.0f%%", value * 100)
    }

    private enum CodingKeys: String, CodingKey {
        case placeId
        case label
        case isPrimary
        case name
        case addressLine
        case city
        case region
        case countryCode
        case lat
        case lon
        case geocodeStatus
        case geocodeSource
        case geocodeConfidence
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        placeId = try container.decode(String.self, forKey: .placeId)
        label = try container.decodeIfPresent(String.self, forKey: .label)
        isPrimary = try container.decodeIfPresent(Bool.self, forKey: .isPrimary) ?? false
        name = try container.decode(String.self, forKey: .name)
        addressLine = try container.decodeIfPresent(String.self, forKey: .addressLine)
        city = try container.decodeIfPresent(String.self, forKey: .city)
        region = try container.decodeIfPresent(String.self, forKey: .region)
        countryCode = try container.decodeIfPresent(String.self, forKey: .countryCode)
        lat = try container.decodeIfPresent(Double.self, forKey: .lat)
        lon = try container.decodeIfPresent(Double.self, forKey: .lon)
        geocodeStatus = try container.decode(String.self, forKey: .geocodeStatus)
        geocodeSource = try container.decodeIfPresent(String.self, forKey: .geocodeSource)
        geocodeConfidence = try container.decodeDecimalIfPresent(forKey: .geocodeConfidence)
    }
}

/// Map projection keeps the customer identity beside its address relation.
struct CustomerMapLocation: Equatable, Identifiable {
    var customer: Customer
    var place: CustomerPlace

    var id: String { place.id }
}

/// Request to attach an existing organization place to a CRM customer.
struct CustomerPlaceLinkRequest: Equatable {
    var placeId: String
    var label: String?
    var isPrimary: Bool

    init(placeId: String, label: String? = nil, isPrimary: Bool = false) {
        self.placeId = placeId
        self.label = label
        self.isPrimary = isPrimary
    }
}
