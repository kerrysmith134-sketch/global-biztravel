import Foundation

enum DistanceUnit: String, Codable, CaseIterable, Identifiable {
    case kilometers = "km"
    case miles = "mi"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .kilometers: return "公里"
        case .miles: return "英里"
        }
    }
}

enum Distance {
    static let earthRadiusKm = 6371.0088

    static func kilometers(fromMeters meters: Double) -> Double {
        meters / 1000.0
    }

    static func meters(fromKilometers km: Double) -> Double {
        km * 1000.0
    }

    static func kilometers(fromMiles miles: Double) -> Double {
        miles * 1.609344
    }

    /// Great-circle distance, used by the map screen to sanity-check solver distances offline.
    static func haversineKm(
        latitude1: Double,
        longitude1: Double,
        latitude2: Double,
        longitude2: Double
    ) -> Double {
        let lat1 = latitude1 * .pi / 180
        let lat2 = latitude2 * .pi / 180
        let deltaLat = (latitude2 - latitude1) * .pi / 180
        let deltaLon = (longitude2 - longitude1) * .pi / 180
        let a = sin(deltaLat / 2) * sin(deltaLat / 2)
            + cos(lat1) * cos(lat2) * sin(deltaLon / 2) * sin(deltaLon / 2)
        let c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return earthRadiusKm * c
    }

    static func format(kilometers km: Double, unit: DistanceUnit = .kilometers) -> String {
        switch unit {
        case .kilometers:
            return String(format: "%.1f km", km)
        case .miles:
            return String(format: "%.1f mi", km / 1.609344)
        }
    }

    static func formatDuration(minutes: Int) -> String {
        guard minutes >= 60 else { return "\(minutes) min" }
        let hours = minutes / 60
        let remainder = minutes % 60
        return remainder == 0 ? "\(hours) h" : "\(hours) h \(remainder) min"
    }
}