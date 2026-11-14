import Foundation

/// A place as it appears inside a trip payload: `origin_place:{name,country_code,timezone,latitude,longitude}`.
///
/// Fields are optional so a partial server payload never kills a whole screen; the create flow uses
/// `PlaceRef`, which hard-requires every field so the wire body always matches the contract.
struct Place: Codable, Equatable, Identifiable {
    var id: String?
    var name: String
    var countryCode: String?
    var timezone: String?
    var latitude: Double?
    var longitude: Double?
    var city: String?
    var address: String?

    init(
        id: String? = nil,
        name: String,
        countryCode: String? = nil,
        timezone: String? = nil,
        latitude: Double? = nil,
        longitude: Double? = nil,
        city: String? = nil,
        address: String? = nil
    ) {
        self.id = id
        self.name = name
        self.countryCode = countryCode
        self.timezone = timezone
        self.latitude = latitude
        self.longitude = longitude
        self.city = city
        self.address = address
    }

    var hasCoordinates: Bool {
        latitude != nil && longitude != nil
    }

    var displayRegion: String {
        [city, countryCode].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · ")
    }

    func timeZone() -> TimeZone {
        if let timezone, let zone = TimeZone(identifier: timezone) { return zone }
        return .current
    }
}

/// Strict shape used when *sending* a place, e.g. `POST /v1/trips` → `origin_place`.
struct PlaceRef: Codable, Equatable {
    var name: String
    var countryCode: String
    var timezone: String
    var latitude: Double
    var longitude: Double

    init(
        name: String,
        countryCode: String,
        timezone: String,
        latitude: Double,
        longitude: Double
    ) {
        self.name = name
        self.countryCode = countryCode
        self.timezone = timezone
        self.latitude = latitude
        self.longitude = longitude
    }

    var place: Place {
        Place(
            name: name,
            countryCode: countryCode,
            timezone: timezone,
            latitude: latitude,
            longitude: longitude
        )
    }
}

/// `window:{start,end}` — trip travel window.
struct TripWindow: Codable, Equatable {
    var start: Date
    var end: Date

    init(start: Date, end: Date) {
        self.start = start
        self.end = end
    }

    var dayCount: Int {
        let calendar = Calendar(identifier: .gregorian)
        let days = calendar.dateComponents([.day], from: start, to: end).day ?? 0
        return max(days + 1, 1)
    }

    var displayRange: String {
        "\(ISO8601.dayString(from: start)) → \(ISO8601.dayString(from: end))"
    }
}

struct TripStats: Codable, Equatable {
    var customerCount: Int?
    var visitCount: Int?
    var planVersionCount: Int?

    var displayText: String {
        let customers = customerCount ?? 0
        let visits = visitCount ?? 0
        let plans = planVersionCount ?? 0
        return "客户 \(customers) · 拜访 \(visits) · 计划版本 \(plans)"
    }
}

/// `POST /v1/trips` request body.
struct CreateTripRequest: Codable, Equatable {
    var name: String
    var purpose: String
    var originPlace: PlaceRef
    var window: TripWindow
    /// 目的地国家列表（ISO alpha-2，大写）。与出发地国家语义分离（P0-2）。
    var countries: [String]

    init(
        name: String,
        purpose: String,
        originPlace: PlaceRef,
        window: TripWindow,
        countries: [String] = []
    ) {
        self.name = name
        self.purpose = purpose
        self.originPlace = originPlace
        self.window = window
        self.countries = countries
    }
}

/// Trip as returned by `POST /v1/trips`, `GET /v1/trips` and `GET /v1/trips/{id}`.
///
/// `activePlan` and `stats` are only populated by the detail endpoint; the list endpoint simply
/// omits them, which is why they are optional here instead of living in a separate `TripDetail`.
struct Trip: Codable, Equatable, Identifiable {
    var id: String
    var organizationId: String?
    var name: String
    var purpose: String?
    var status: String?
    var version: Int
    var window: TripWindow?
    var originPlace: Place?
    var originPlaceId: String?
    var activePlanVersionId: String?
    var createdAt: Date?
    var updatedAt: Date?
    var activePlan: PlanVersionDetail?
    var stats: TripStats?
    var currency: String?
    var countries: [String]?

    init(
        id: String,
        organizationId: String? = nil,
        name: String,
        purpose: String? = nil,
        status: String? = nil,
        version: Int,
        window: TripWindow? = nil,
        originPlace: Place? = nil,
        originPlaceId: String? = nil,
        activePlanVersionId: String? = nil,
        createdAt: Date? = nil,
        updatedAt: Date? = nil,
        activePlan: PlanVersionDetail? = nil,
        stats: TripStats? = nil,
        currency: String? = nil,
        countries: [String]? = nil
    ) {
        self.id = id
        self.organizationId = organizationId
        self.name = name
        self.purpose = purpose
        self.status = status
        self.version = version
        self.window = window
        self.originPlace = originPlace
        self.originPlaceId = originPlaceId
        self.activePlanVersionId = activePlanVersionId
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.activePlan = activePlan
        self.stats = stats
        self.currency = currency
        self.countries = countries
    }

    /// The backend returns `window_start` / `window_end`, not a nested `window`, and only an
    /// `origin_place_id` (not the place object). Decode those flat fields into the domain shape so
    /// screens keep one representation. The same keys are written back when caching to disk.
    private enum CodingKeys: String, CodingKey {
        case id
        case organizationId
        case name
        case purpose
        case status
        case version
        case windowStart
        case windowEnd
        case originPlace
        case originPlaceId
        case activePlanVersionId
        case createdAt
        case updatedAt
        case activePlan
        case stats
        case visitCount
        case currency
        case countries
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.organizationId = try container.decodeIfPresent(String.self, forKey: .organizationId)
        self.name = try container.decode(String.self, forKey: .name)
        self.purpose = try container.decodeIfPresent(String.self, forKey: .purpose)
        self.status = try container.decodeIfPresent(String.self, forKey: .status)
        self.version = try container.decodeIfPresent(Int.self, forKey: .version) ?? 1
        self.originPlace = try container.decodeIfPresent(Place.self, forKey: .originPlace)
        self.originPlaceId = try container.decodeIfPresent(String.self, forKey: .originPlaceId)
        self.activePlanVersionId = try container.decodeIfPresent(
            String.self, forKey: .activePlanVersionId
        )
        self.createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt)
        self.updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt)
        self.activePlan = try container.decodeIfPresent(PlanVersionDetail.self, forKey: .activePlan)
        self.currency = try container.decodeIfPresent(String.self, forKey: .currency)
        self.countries = try container.decodeIfPresent([String].self, forKey: .countries)

        let start = try container.decodeIfPresent(Date.self, forKey: .windowStart)
        let end = try container.decodeIfPresent(Date.self, forKey: .windowEnd)
        if let start, let end {
            self.window = TripWindow(start: start, end: end)
        } else {
            self.window = nil
        }

        let visitCount = try container.decodeIfPresent(Int.self, forKey: .visitCount)
        let decodedStats = try container.decodeIfPresent(TripStats.self, forKey: .stats)
        if decodedStats != nil {
            self.stats = decodedStats
        } else if let visitCount {
            self.stats = TripStats(visitCount: visitCount)
        } else {
            self.stats = nil
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(organizationId, forKey: .organizationId)
        try container.encode(name, forKey: .name)
        try container.encodeIfPresent(purpose, forKey: .purpose)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encode(version, forKey: .version)
        try container.encodeIfPresent(window?.start, forKey: .windowStart)
        try container.encodeIfPresent(window?.end, forKey: .windowEnd)
        try container.encodeIfPresent(originPlace, forKey: .originPlace)
        try container.encodeIfPresent(originPlaceId, forKey: .originPlaceId)
        try container.encodeIfPresent(activePlanVersionId, forKey: .activePlanVersionId)
        try container.encodeIfPresent(createdAt, forKey: .createdAt)
        try container.encodeIfPresent(updatedAt, forKey: .updatedAt)
        try container.encodeIfPresent(activePlan, forKey: .activePlan)
        try container.encodeIfPresent(stats, forKey: .stats)
        try container.encodeIfPresent(stats?.visitCount, forKey: .visitCount)
        try container.encodeIfPresent(currency, forKey: .currency)
        // 目的国必须回写：本地库与离线缓存都靠 payload 还原，漏掉会让驾驶舱与签证规则
        // 失去目的国（历史上就是这里漏了 countries）。
        try container.encodeIfPresent(countries, forKey: .countries)
    }

    var statusLabel: String {
        switch (status ?? "").uppercased() {
        case "DRAFT": return "草稿"
        case "PLANNING": return "规划中"
        case "READY": return "待激活"
        case "ACTIVE": return "进行中"
        case "HOLD": return "已暂停"
        case "COMPLETED": return "已完成"
        case "CANCELLED": return "已取消"
        default: return status ?? "未知"
        }
    }

    var windowLabel: String {
        // 按出发地时区展示：跨洲行程用设备时区会把出发/到达日整体挪一天。
        guard let window else { return "未设置差旅窗口" }
        let formatter = DateFormatter()
        formatter.timeZone = originPlace?.timeZone() ?? .current
        formatter.dateFormat = "M月d日"
        return "\(formatter.string(from: window.start)) → \(formatter.string(from: window.end))"
    }

    var originLabel: String {
        if let name = originPlace?.name, !name.isEmpty { return name }
        if originPlaceId != nil { return "出发地已设置" }
        return "未设置出发地"
    }

    var planSummary: String {
        guard let activePlan else { return "尚无可执行计划" }
        let days = activePlan.displayDayCount
        let stops = activePlan.displayStopCount
        return "v\(activePlan.versionNumber ?? 0) · \(days) 天 · \(stops) 停靠"
    }
}
