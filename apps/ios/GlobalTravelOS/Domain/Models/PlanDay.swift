import Foundation

/// `days:[{date, stops:[...]}]`.
///
/// `date` stays a `String` (`yyyy-MM-dd`) on purpose: converting a calendar day into a `Date`
/// requires picking a timezone, and any pick can shift the day. Only real instants
/// (`arrive` / `depart`) are parsed into `Date`.
struct PlanDay: Codable, Equatable, Identifiable {
    var date: String
    var stops: [PlanStop]

    init(date: String, stops: [PlanStop] = []) {
        self.date = date
        self.stops = stops
    }

    var id: String { date }

    var displayDate: String {
        guard let day = ISO8601.day(from: date) else { return date }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = ISO8601.utc
        formatter.dateFormat = "MM-dd EEE"
        return formatter.string(from: day)
    }

    var serviceMinutes: Int {
        stops.reduce(0) { $0 + ($1.serviceMinutes ?? 0) }
    }

    var travelMinutes: Int {
        stops.reduce(0) { $0 + ($1.travelMinutes ?? 0) }
    }
}

struct PlanStop: Codable, Equatable, Identifiable {
    var visitId: String
    var sequence: Int
    var placeId: String?
    var placeName: String?
    var city: String?
    var countryCode: String?
    var latitude: Double?
    var longitude: Double?
    var arrive: Date?
    var depart: Date?
    var travelMinutes: Int?
    var distanceKm: Double?
    var serviceMinutes: Int?
    var costAmount: Double?
    var costCurrency: String?
    var travelSource: String?
    var stale: Bool?
    var locked: Bool?
    var timeWindow: TimeWindowRef?

    init(
        visitId: String,
        sequence: Int,
        placeId: String? = nil,
        placeName: String? = nil,
        city: String? = nil,
        countryCode: String? = nil,
        latitude: Double? = nil,
        longitude: Double? = nil,
        arrive: Date? = nil,
        depart: Date? = nil,
        travelMinutes: Int? = nil,
        distanceKm: Double? = nil,
        serviceMinutes: Int? = nil,
        costAmount: Double? = nil,
        costCurrency: String? = nil,
        travelSource: String? = nil,
        stale: Bool? = nil,
        locked: Bool? = nil,
        timeWindow: TimeWindowRef? = nil
    ) {
        self.visitId = visitId
        self.sequence = sequence
        self.placeId = placeId
        self.placeName = placeName
        self.city = city
        self.countryCode = countryCode
        self.latitude = latitude
        self.longitude = longitude
        self.arrive = arrive
        self.depart = depart
        self.travelMinutes = travelMinutes
        self.distanceKm = distanceKm
        self.serviceMinutes = serviceMinutes
        self.costAmount = costAmount
        self.costCurrency = costCurrency
        self.travelSource = travelSource
        self.stale = stale
        self.locked = locked
        self.timeWindow = timeWindow
    }

    var id: String { visitId }

    var displayName: String { placeName ?? visitId }

    var hasCoordinates: Bool {
        latitude != nil && longitude != nil
    }

    /// Coordinates used for map handoff must be geographically valid, not merely non-nil.
    var hasValidCoordinates: Bool {
        guard let latitude, let longitude else { return false }
        return (-90.0...90.0).contains(latitude) && (-180.0...180.0).contains(longitude)
    }

    var arriveLabel: String {
        guard let arrive else { return "—" }
        return ISO8601.timeDisplay(from: arrive)
    }

    var departLabel: String {
        guard let depart else { return "—" }
        return ISO8601.timeDisplay(from: depart)
    }

    func timeZone(originFallback: TimeZone = .current) -> TimeZone { originFallback }

    var travelLabel: String {
        Distance.formatDuration(minutes: travelMinutes ?? 0)
    }

    var distanceLabel: String {
        guard let distanceKm else { return "—" }
        return Distance.format(kilometers: distanceKm)
    }

    var costLabel: String {
        guard let costAmount else { return "费用未知" }
        return "\(costCurrency ?? "") \(String(format: "%.2f", costAmount))"
    }

    var travelIsStale: Bool { stale ?? false }

    var travelSourceLabel: String {
        TravelSourcePresentation.label(source: travelSource, stale: travelIsStale)
    }
}

/// Human-readable provenance for estimated travel times. OSRM road-network routing is not
/// live traffic data; straight-line fallbacks are explicitly not navigation routes.
enum TravelSourcePresentation {
    static func label(source: String?, stale: Bool) -> String {
        let base: String
        switch source {
        case .some("osrm-road"):
            base = "道路网络估算（无实时路况）"
        case .some("haversine-estimate"):
            base = "直线距离估算（非导航路线）"
        case .some("fallback-haversine"):
            base = "降级直线估算"
        case let .some(value):
            base = "估算来源：\(value)"
        case .none:
            base = "交通来源未知"
        }
        return stale ? "\(base) · 需要复核" : base
    }
}

/// One `plan_travel_legs` row. `fromStopId == nil` means the leg starts at the trip origin.
struct PlanTravelLeg: Codable, Equatable, Identifiable {
    var id: String
    var fromStopId: String?
    var toStopId: String
    var dayIndex: Int
    var mode: String
    var travelMinutes: Int
    var distanceKm: Double
    var costAmount: Double?
    var costCurrency: String?
    var source: String
    var stale: Bool

    init(
        id: String,
        fromStopId: String? = nil,
        toStopId: String,
        dayIndex: Int,
        mode: String,
        travelMinutes: Int,
        distanceKm: Double,
        costAmount: Double? = nil,
        costCurrency: String? = nil,
        source: String = "haversine-estimate",
        stale: Bool = false
    ) {
        self.id = id
        self.fromStopId = fromStopId
        self.toStopId = toStopId
        self.dayIndex = dayIndex
        self.mode = mode
        self.travelMinutes = travelMinutes
        self.distanceKm = distanceKm
        self.costAmount = costAmount
        self.costCurrency = costCurrency
        self.source = source
        self.stale = stale
    }

    var durationLabel: String { Distance.formatDuration(minutes: travelMinutes) }
    var distanceLabel: String { Distance.format(kilometers: distanceKm) }
    var costLabel: String {
        guard let costAmount else { return "费用未知" }
        return "\(costCurrency ?? "") \(String(format: "%.2f", costAmount))"
    }
    var sourceLabel: String {
        TravelSourcePresentation.label(source: source, stale: stale)
    }
}

/// `time_window:{start,end}|null` — kept as raw strings because the contract does not pin the
/// format (local `HH:mm` vs. a full instant); rendering passes it through untouched.
struct TimeWindowRef: Codable, Equatable {
    var start: String?
    var end: String?

    init(start: String? = nil, end: String? = nil) {
        self.start = start
        self.end = end
    }

    var displayText: String {
        switch (start, end) {
        case let (start?, end?):
            return "\(start) – \(end)"
        case let (start?, nil):
            return "从 \(start) 起"
        case let (nil, end?):
            return "不晚于 \(end)"
        case (nil, nil):
            return "无时间窗"
        }
    }
}
