import Foundation

/// A visit as returned by `GET /v1/trips/{trip_id}/visits`.
///
/// This is a read model for the field workspace: it carries enough context to show "where/when"
/// without pulling the full plan. The backend calls the resource `Visit`; the client keeps the
/// `TripVisit` name so it never collides with `VisitRecord` (the field note).
struct TripVisit: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String?
    var placeId: String?
    var customerId: String?
    var title: String?
    var objective: String?
    var serviceMinutes: Int?
    var timeWindowStart: Date?
    var timeWindowEnd: Date?
    var priority: Int?
    var movementMode: String?
    var status: String?
    var createdAt: Date?

    init(
        id: String,
        tripId: String? = nil,
        placeId: String? = nil,
        customerId: String? = nil,
        title: String? = nil,
        objective: String? = nil,
        serviceMinutes: Int? = nil,
        timeWindowStart: Date? = nil,
        timeWindowEnd: Date? = nil,
        priority: Int? = nil,
        movementMode: String? = nil,
        status: String? = nil,
        createdAt: Date? = nil
    ) {
        self.id = id
        self.tripId = tripId
        self.placeId = placeId
        self.customerId = customerId
        self.title = title
        self.objective = objective
        self.serviceMinutes = serviceMinutes
        self.timeWindowStart = timeWindowStart
        self.timeWindowEnd = timeWindowEnd
        self.priority = priority
        self.movementMode = movementMode
        self.status = status
        self.createdAt = createdAt
    }

    var displayName: String { title ?? "拜访 \(id.prefix(8))" }

    var statusLabel: String {
        switch (status ?? "").uppercased() {
        case "PLANNED": return "待排期"
        case "SCHEDULED": return "已排期"
        case "DONE": return "已完成"
        case "MISSED": return "未会面"
        case "CANCELLED": return "已取消"
        default: return status ?? "未知状态"
        }
    }

    var isOpen: Bool {
        let value = (status ?? "PLANNED").uppercased()
        return value == "PLANNED" || value == "SCHEDULED"
    }

    var timeWindowLabel: String {
        switch (timeWindowStart, timeWindowEnd) {
        case let (start?, end?):
            return "\(ISO8601.shortDisplay(from: start)) – \(ISO8601.shortDisplay(from: end))"
        case let (start?, nil):
            return "从 \(ISO8601.shortDisplay(from: start)) 起"
        case let (nil, end?):
            return "不晚于 \(ISO8601.shortDisplay(from: end))"
        case (nil, nil):
            return "无时间窗"
        }
    }
}

/// 新增拜访请求（客户端本地组装；服务端契约 POST /trips/{id}/visits 的字段子集）。
struct VisitCreateRequest: Codable, Equatable {
    var placeId: String?
    var customerId: String?
    /// 新客户地点（placeId 为空时由仓储先创建地点）。
    var placeName: String?
    var city: String?
    var countryCode: String?
    var latitude: Double?
    var longitude: Double?
    var timezone: String?
    var title: String
    var serviceMinutes: Int
    var priority: Int
    var businessValue: Double?
    /// 交通方式（GROUND/RAIL/AIR/SEA）；跨洲拜访必须 AIR，否则陆路估算不可行。
    var movementMode: String

    init(
        placeId: String? = nil,
        customerId: String? = nil,
        placeName: String? = nil,
        city: String? = nil,
        countryCode: String? = nil,
        latitude: Double? = nil,
        longitude: Double? = nil,
        timezone: String? = nil,
        title: String,
        serviceMinutes: Int = 60,
        priority: Int = 2,
        businessValue: Double? = nil,
        movementMode: String = "GROUND"
    ) {
        self.placeId = placeId
        self.customerId = customerId
        self.placeName = placeName
        self.city = city
        self.countryCode = countryCode
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = timezone
        self.title = title
        self.serviceMinutes = serviceMinutes
        self.priority = priority
        self.businessValue = businessValue
        self.movementMode = movementMode
    }
}
