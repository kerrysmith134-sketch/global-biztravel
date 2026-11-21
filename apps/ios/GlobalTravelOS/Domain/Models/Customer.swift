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
    var canScheduleVisit: Bool { status.uppercased() != "INACTIVE" }

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

    init(
        placeId: String,
        label: String? = nil,
        isPrimary: Bool = false,
        name: String,
        addressLine: String? = nil,
        city: String? = nil,
        region: String? = nil,
        countryCode: String? = nil,
        lat: Double? = nil,
        lon: Double? = nil,
        geocodeStatus: String,
        geocodeSource: String? = nil,
        geocodeConfidence: Decimal? = nil
    ) {
        self.placeId = placeId
        self.label = label
        self.isPrimary = isPrimary
        self.name = name
        self.addressLine = addressLine
        self.city = city
        self.region = region
        self.countryCode = countryCode
        self.lat = lat
        self.lon = lon
        self.geocodeStatus = geocodeStatus
        self.geocodeSource = geocodeSource
        self.geocodeConfidence = geocodeConfidence
    }

    var id: String { placeId }

    var hasValidCoordinates: Bool {
        guard let lat, let lon else { return false }
        return (-90.0...90.0).contains(lat) && (-180.0...180.0).contains(lon)
    }

    var displayAddress: String {
        let normalizedCity = city?.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedRegion = region?.trimmingCharacters(in: .whitespacesAndNewlines)
        let distinctRegion: String?
        if let normalizedRegion,
           !normalizedRegion.isEmpty,
           normalizedRegion.caseInsensitiveCompare(normalizedCity ?? "") != .orderedSame {
            distinctRegion = normalizedRegion
        } else {
            distinctRegion = nil
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

    /// One place may be linked to multiple customer records; the pair is the map identity.
    var id: String { "\(customer.id)::\(place.id)" }
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

/// Local-only persistence payload for the customer_places join relation.
struct LocalCustomerPlaceLink: Codable, Equatable {
    var customerId: String
    var placeId: String
    var label: String?
    var isPrimary: Bool
}


/// Searchable organization-level place returned by GET /v1/places.
struct PlaceCatalogItem: Decodable, Equatable, Identifiable {
    var id: String
    var name: String
    var addressLine: String?
    var city: String?
    var region: String?
    var countryCode: String?
    var timezone: String?
    var lat: Double?
    var lon: Double?
    var geocodeStatus: String
    var geocodeSource: String?
    var geocodeConfidence: Decimal?

    init(
        id: String,
        name: String,
        addressLine: String? = nil,
        city: String? = nil,
        region: String? = nil,
        countryCode: String? = nil,
        timezone: String? = nil,
        lat: Double? = nil,
        lon: Double? = nil,
        geocodeStatus: String = "MISSING",
        geocodeSource: String? = nil,
        geocodeConfidence: Decimal? = nil
    ) {
        self.id = id
        self.name = name
        self.addressLine = addressLine
        self.city = city
        self.region = region
        self.countryCode = countryCode
        self.timezone = timezone
        self.lat = lat
        self.lon = lon
        self.geocodeStatus = geocodeStatus
        self.geocodeSource = geocodeSource
        self.geocodeConfidence = geocodeConfidence
    }

    var hasValidCoordinates: Bool {
        guard let lat, let lon else { return false }
        return (-90.0...90.0).contains(lat) && (-180.0...180.0).contains(lon)
    }

    var displayAddress: String {
        [addressLine, city, region, countryCode]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    var confidenceLabel: String? {
        guard let geocodeConfidence else { return nil }
        return String(format: "%.0f%%", NSDecimalNumber(decimal: geocodeConfidence).doubleValue * 100)
    }

    private enum CodingKeys: String, CodingKey {
        case id, name, addressLine, city, region, countryCode, timezone
        case lat, lon, geocodeStatus, geocodeSource, geocodeConfidence
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = try container.decode(String.self, forKey: .name)
        addressLine = try container.decodeIfPresent(String.self, forKey: .addressLine)
        city = try container.decodeIfPresent(String.self, forKey: .city)
        region = try container.decodeIfPresent(String.self, forKey: .region)
        countryCode = try container.decodeIfPresent(String.self, forKey: .countryCode)
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone)
        lat = try container.decodeIfPresent(Double.self, forKey: .lat)
        lon = try container.decodeIfPresent(Double.self, forKey: .lon)
        geocodeStatus = try container.decodeIfPresent(String.self, forKey: .geocodeStatus) ?? "MISSING"
        geocodeSource = try container.decodeIfPresent(String.self, forKey: .geocodeSource)
        geocodeConfidence = try container.decodeDecimalIfPresent(forKey: .geocodeConfidence)
    }
}

/// Request body for POST /v1/places. Coordinates are optional but must be supplied as a pair.
struct PlaceCreateRequest: Encodable, Equatable {
    var name: String
    var addressLine: String?
    var city: String?
    var region: String?
    var countryCode: String?
    var timezone: String?
    var lat: Double?
    var lon: Double?

    init(
        name: String,
        addressLine: String? = nil,
        city: String? = nil,
        region: String? = nil,
        countryCode: String? = nil,
        timezone: String? = nil,
        lat: Double? = nil,
        lon: Double? = nil
    ) {
        self.name = name
        self.addressLine = addressLine
        self.city = city
        self.region = region
        self.countryCode = countryCode?.uppercased()
        self.timezone = timezone
        self.lat = lat
        self.lon = lon
    }

    var validationMessage: String? {
        guard !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return "地点名称不能为空"
        }
        guard (lat == nil) == (lon == nil) else {
            return "纬度和经度必须同时填写，或同时留空"
        }
        if let lat, !(-90.0...90.0).contains(lat) {
            return "纬度必须在 -90 到 90 之间"
        }
        if let lon, !(-180.0...180.0).contains(lon) {
            return "经度必须在 -180 到 180 之间"
        }
        if let countryCode, countryCode.count != 2 {
            return "国家代码必须使用 ISO 二位代码，例如 DE"
        }
        return nil
    }
}


struct GlobalCustomerMapFilters: Equatable {
    var query: String?
    var tier: String?
    var countryCode: String?
    var geocodeStatus: String?
    static let all = GlobalCustomerMapFilters()
}

struct GlobalMapCustomer: Decodable, Equatable, Identifiable {
    var id: String
    var name: String
    var tier: String
    var countryCode: String?
    var industry: String?
    var status: String
}

struct GlobalMapPlace: Decodable, Equatable, Identifiable {
    var id: String
    var label: String?
    var isPrimary: Bool
    var name: String
    var addressLine: String?
    var city: String?
    var region: String?
    var countryCode: String?
    var timezone: String?
    var lat: Double?
    var lon: Double?
    var geocodeStatus: String
    var geocodeSource: String?
    var geocodeConfidence: Decimal?

    private enum CodingKeys: String, CodingKey {
        case id, label, isPrimary, name, addressLine, city, region, countryCode
        case timezone, lat, lon, geocodeStatus, geocodeSource, geocodeConfidence
    }

    init(
        id: String,
        label: String? = nil,
        isPrimary: Bool = false,
        name: String,
        addressLine: String? = nil,
        city: String? = nil,
        region: String? = nil,
        countryCode: String? = nil,
        timezone: String? = nil,
        lat: Double? = nil,
        lon: Double? = nil,
        geocodeStatus: String = "MISSING",
        geocodeSource: String? = nil,
        geocodeConfidence: Decimal? = nil
    ) {
        self.id = id
        self.label = label
        self.isPrimary = isPrimary
        self.name = name
        self.addressLine = addressLine
        self.city = city
        self.region = region
        self.countryCode = countryCode
        self.timezone = timezone
        self.lat = lat
        self.lon = lon
        self.geocodeStatus = geocodeStatus
        self.geocodeSource = geocodeSource
        self.geocodeConfidence = geocodeConfidence
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        label = try values.decodeIfPresent(String.self, forKey: .label)
        isPrimary = try values.decodeIfPresent(Bool.self, forKey: .isPrimary) ?? false
        name = try values.decode(String.self, forKey: .name)
        addressLine = try values.decodeIfPresent(String.self, forKey: .addressLine)
        city = try values.decodeIfPresent(String.self, forKey: .city)
        region = try values.decodeIfPresent(String.self, forKey: .region)
        countryCode = try values.decodeIfPresent(String.self, forKey: .countryCode)
        timezone = try values.decodeIfPresent(String.self, forKey: .timezone)
        lat = try values.decodeIfPresent(Double.self, forKey: .lat)
        lon = try values.decodeIfPresent(Double.self, forKey: .lon)
        geocodeStatus = try values.decodeIfPresent(String.self, forKey: .geocodeStatus) ?? "MISSING"
        geocodeSource = try values.decodeIfPresent(String.self, forKey: .geocodeSource)
        geocodeConfidence = try values.decodeDecimalIfPresent(forKey: .geocodeConfidence)
    }

    var hasValidCoordinates: Bool {
        guard let lat, let lon else { return false }
        return (-90.0...90.0).contains(lat) && (-180.0...180.0).contains(lon)
    }

    var displayAddress: String {
        let cityText = city?.trimmingCharacters(in: .whitespacesAndNewlines)
        let regionText = region?.trimmingCharacters(in: .whitespacesAndNewlines)
        let visibleRegion = regionText.flatMap {
            $0.isEmpty || $0.caseInsensitiveCompare(cityText ?? "") == .orderedSame ? nil : $0
        }
        return [addressLine, cityText, visibleRegion, countryCode]
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    var confidenceLabel: String? {
        guard let geocodeConfidence else { return nil }
        return String(format: "%.0f%%", NSDecimalNumber(decimal: geocodeConfidence).doubleValue * 100)
    }
}

struct GlobalCustomerMapLocation: Decodable, Equatable, Identifiable {
    var customer: GlobalMapCustomer
    var place: GlobalMapPlace
    var id: String { "\(customer.id)::\(place.id)" }
}

struct GlobalCustomerCountryCoverage: Decodable, Equatable, Identifiable {
    var countryCode: String
    var customersTotal: Int
    var tierACustomers: Int
    var tierBCustomers: Int
    var tierCCustomers: Int
    var activeCustomers: Int
    var prospectCustomers: Int
    var inactiveCustomers: Int
    var locationsTotal: Int
    var locationsMapped: Int

    var id: String { countryCode }
}

struct GlobalCustomerMapSummary: Decodable, Equatable {
    var customersWithLocations: Int
    var customersWithoutLocations: Int
    var locationsTotal: Int
    var locationsMapped: Int
    var locationsMissingCoordinates: Int
    var countries: [String: Int]
    var countryStats: [GlobalCustomerCountryCoverage]

    private enum CodingKeys: String, CodingKey {
        case customersWithLocations, customersWithoutLocations, locationsTotal
        case locationsMapped, locationsMissingCoordinates, countries, countryStats
    }

    init(
        customersWithLocations: Int,
        customersWithoutLocations: Int,
        locationsTotal: Int,
        locationsMapped: Int,
        locationsMissingCoordinates: Int,
        countries: [String: Int],
        countryStats: [GlobalCustomerCountryCoverage] = []
    ) {
        self.customersWithLocations = customersWithLocations
        self.customersWithoutLocations = customersWithoutLocations
        self.locationsTotal = locationsTotal
        self.locationsMapped = locationsMapped
        self.locationsMissingCoordinates = locationsMissingCoordinates
        self.countries = countries
        self.countryStats = countryStats
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        customersWithLocations = try values.decodeIfPresent(Int.self, forKey: .customersWithLocations) ?? 0
        customersWithoutLocations = try values.decodeIfPresent(Int.self, forKey: .customersWithoutLocations) ?? 0
        locationsTotal = try values.decodeIfPresent(Int.self, forKey: .locationsTotal) ?? 0
        locationsMapped = try values.decodeIfPresent(Int.self, forKey: .locationsMapped) ?? 0
        locationsMissingCoordinates = try values.decodeIfPresent(Int.self, forKey: .locationsMissingCoordinates) ?? 0
        countries = try values.decodeIfPresent([String: Int].self, forKey: .countries) ?? [:]
        countryStats = try values.decodeIfPresent(
            [GlobalCustomerCountryCoverage].self,
            forKey: .countryStats
        ) ?? []
    }

    static let empty = GlobalCustomerMapSummary(
        customersWithLocations: 0, customersWithoutLocations: 0, locationsTotal: 0,
        locationsMapped: 0, locationsMissingCoordinates: 0, countries: [:], countryStats: []
    )
}

struct GlobalCustomerMapResponse: Decodable, Equatable {
    var items: [GlobalCustomerMapLocation]
    var summary: GlobalCustomerMapSummary
    var nextCursor: String?
}
