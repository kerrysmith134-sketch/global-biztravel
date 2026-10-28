import Foundation

/// Reads the trip list and its cursor pages.
///
/// Deliberately thin: it owns the paging convention (`limit` + `cursor`) so the view model never
/// has to remember which cursor belongs to which page.
final class TripListUseCase {
    static let defaultPageSize = 20

    private let repository: TripRepository

    init(repository: TripRepository) {
        self.repository = repository
    }

    func loadFirstPage(limit: Int = TripListUseCase.defaultPageSize) async throws -> Page<Trip> {
        try await repository.listTrips(page: PageRequest(limit: limit, cursor: nil))
    }

    func loadNextPage(
        after cursor: String,
        limit: Int = TripListUseCase.defaultPageSize
    ) async throws -> Page<Trip> {
        try await repository.listTrips(page: PageRequest(limit: limit, cursor: cursor))
    }

    /// Merges a freshly loaded page into the rows already on screen, keeping list order and
    /// dropping duplicates (a trip can appear twice when a create lands between two page fetches).
    func merge(_ page: Page<Trip>, into existing: [Trip]) -> (items: [Trip], nextCursor: String?) {
        var seen = Set(existing.map(\.id))
        var merged = existing
        for trip in page.items where !seen.contains(trip.id) {
            seen.insert(trip.id)
            merged.append(trip)
        }
        return (merged, page.nextCursor)
    }

    func trip(id: String) async throws -> Trip {
        try await repository.trip(id: id)
    }

    /// Keeps only the trips that are still open — the list screen's default filter.
    func activeTrips(from trips: [Trip]) -> [Trip] {
        trips.filter { trip in
            let status = (trip.status ?? "").uppercased()
            return status != "ARCHIVED" && status != "CANCELLED" && status != "COMPLETED"
        }
    }
}

/// Creates a trip. Split out from `TripListUseCase` because the write path has its own failure
/// modes (duplicate name, invalid window) that the list screen surfaces differently.
final class CreateTripUseCase {
    private let repository: TripRepository

    init(repository: TripRepository) {
        self.repository = repository
    }

    static let defaultPurpose = "商务拜访"

    func create(
        name: String,
        purpose: String,
        origin: PlaceRef,
        window: TripWindow
    ) async throws -> Trip {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedName.isEmpty else {
            throw TripValidationError.missingName
        }
        guard window.end >= window.start else {
            throw TripValidationError.invalidWindow
        }
        let request = CreateTripRequest(
            name: trimmedName,
            purpose: purpose.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ? CreateTripUseCase.defaultPurpose
                : purpose.trimmingCharacters(in: .whitespacesAndNewlines),
            originPlace: origin,
            window: window
        )
        return try await repository.createTrip(request)
    }

    /// Convenience for the form: builds a `PlaceRef` and rejects half-filled coordinates.
    func create(
        name: String,
        purpose: String,
        originName: String,
        countryCode: String,
        timezone: String,
        latitude: Double?,
        longitude: Double?,
        start: Date,
        end: Date
    ) async throws -> Trip {
        guard let latitude, let longitude else {
            throw TripValidationError.missingCoordinates
        }
        let origin = PlaceRef(
            name: originName.trimmingCharacters(in: .whitespacesAndNewlines),
            countryCode: countryCode.trimmingCharacters(in: .whitespacesAndNewlines).uppercased(),
            timezone: timezone,
            latitude: latitude,
            longitude: longitude
        )
        guard !origin.name.isEmpty else { throw TripValidationError.missingOriginName }
        guard !origin.countryCode.isEmpty else { throw TripValidationError.missingCountryCode }
        return try await create(
            name: name,
            purpose: purpose,
            origin: origin,
            window: TripWindow(start: start, end: end)
        )
    }
}

/// Validation failures the create form can show inline, without a server round-trip.
enum TripValidationError: Error, Equatable, LocalizedError {
    case missingName
    case missingOriginName
    case missingCountryCode
    case missingCoordinates
    case invalidWindow

    var errorDescription: String? { message }

    var message: String {
        switch self {
        case .missingName: return "差旅名称不能为空"
        case .missingOriginName: return "出发地名称不能为空"
        case .missingCountryCode: return "出发地国家代码不能为空（如 CN / US / DE）"
        case .missingCoordinates: return "出发地需要经纬度，求解器用它计算首段行程"
        case .invalidWindow: return "结束日期不能早于开始日期"
        }
    }
}
