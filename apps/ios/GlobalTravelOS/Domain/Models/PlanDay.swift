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
        self.locked = locked
        self.timeWindow = timeWindow
    }

    var id: String { visitId }

    var displayName: String { placeName ?? visitId }

    var hasCoordinates: Bool {
        latitude != nil && longitude != nil
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