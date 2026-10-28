import Foundation

/// Backend-shaped create bodies. The domain keeps a nested `originPlace`/`window` (convenient for
/// forms), while `POST /v1/trips` takes a flat window and requires an existing origin place id.
/// The repository bridges the two: create the place, then create the trip.
struct BackendPlaceCreate: Encodable {
    var name: String
    var city: String?
    var countryCode: String
    var timezone: String?
    var lat: Double
    var lon: Double
}

struct BackendTripCreate: Encodable {
    var name: String
    var purpose: String?
    var originPlaceId: String
    var windowStart: Date
    var windowEnd: Date
    var countries: [String]
    var currency: String?
}

private struct PlaceCreated: Decodable {
    var id: String
}

/// Trip reads and writes. Writes (`createTrip`) are not offlined — only field records go through
/// the outbox, per design doc §10.5; a trip has to exist on the server before anything can be
/// planned against it.
protocol TripRepository {
    func listTrips(page: PageRequest) async throws -> Page<Trip>
    func trip(id: String) async throws -> Trip
    func createTrip(_ request: CreateTripRequest) async throws -> Trip
    func parseImport(tripId: String, fileData: Data, filename: String) async throws -> ImportParseResult
    func confirmImport(tripId: String, request: ConfirmImportRequest) async throws -> ConfirmImportResult
}

/// Talks to `/v1/trips` and `/v1/trips/{id}/imports:*`.
final class RemoteTripRepository: TripRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func listTrips(page: PageRequest) async throws -> Page<Trip> {
        try await client.send(Endpoints.listTrips(page: page), as: Page<Trip>.self)
    }

    func trip(id: String) async throws -> Trip {
        try await client.send(Endpoints.tripDetail(id: id), as: Trip.self)
    }

    func createTrip(_ request: CreateTripRequest) async throws -> Trip {
        let origin = request.originPlace
        let placeBody = BackendPlaceCreate(
            name: origin.name,
            city: origin.name,
            countryCode: origin.countryCode.uppercased(),
            timezone: origin.timezone,
            lat: origin.latitude,
            lon: origin.longitude
        )
        let createdPlace = try await client.send(
            try Endpoints.createPlace(placeBody), as: PlaceCreated.self
        )
        let body = BackendTripCreate(
            name: request.name,
            purpose: request.purpose,
            originPlaceId: createdPlace.id,
            windowStart: request.window.start,
            windowEnd: request.window.end,
            countries: [origin.countryCode.uppercased()],
            currency: nil
        )
        return try await client.send(try Endpoints.createTrip(body), as: Trip.self)
    }

    func parseImport(
        tripId: String,
        fileData: Data,
        filename: String
    ) async throws -> ImportParseResult {
        let request = Endpoints.parseImport(tripId: tripId, fileData: fileData, filename: filename)
        return try await client.send(request, as: ImportParseResult.self)
    }

    func confirmImport(
        tripId: String,
        request: ConfirmImportRequest
    ) async throws -> ConfirmImportResult {
        try await client.send(try Endpoints.confirmImport(tripId: tripId, body: request), as: ConfirmImportResult.self)
    }
}

/// Read-through wrapper: serve the network when it answers, otherwise serve the last good response
/// and tell `OfflineIndicator` so the screen can label the data.
final class OfflineTripRepository: TripRepository {
    private let remote: TripRepository
    private let listCache: JSONFileCache<[Trip]>
    private let detailCache: JSONFileCache<[String: Trip]>
    private let indicator: OfflineIndicator

    init(
        remote: TripRepository,
        listCache: JSONFileCache<[Trip]>,
        detailCache: JSONFileCache<[String: Trip]>,
        indicator: OfflineIndicator
    ) {
        self.remote = remote
        self.listCache = listCache
        self.detailCache = detailCache
        self.indicator = indicator
    }

    /// Convenience wiring for the app: one directory holds every cache file.
    convenience init(
        remote: TripRepository,
        directory: URL,
        indicator: OfflineIndicator
    ) {
        self.init(
            remote: remote,
            listCache: JSONFileCache<[Trip]>(directory: directory, fileName: "trips-first-page.json"),
            detailCache: JSONFileCache<[String: Trip]>(directory: directory, fileName: "trip-details.json"),
            indicator: indicator
        )
    }

    func listTrips(page: PageRequest) async throws -> Page<Trip> {
        do {
            let result = try await remote.listTrips(page: page)
            if page.cursor == nil {
                listCache.save(result.items)
            }
            indicator.markServedFromServer()
            return result
        } catch {
            guard page.cursor == nil, let cached = listCache.load(), !cached.isEmpty else {
                throw error
            }
            indicator.markServedFromCache("行程列表")
            return Page(items: cached, nextCursor: nil)
        }
    }

    func trip(id: String) async throws -> Trip {
        do {
            let trip = try await remote.trip(id: id)
            var stored = detailCache.load() ?? [:]
            stored[id] = trip
            detailCache.save(stored)
            indicator.markServedFromServer()
            return trip
        } catch {
            guard let cached = detailCache.load()?[id] else { throw error }
            indicator.markServedFromCache("行程详情")
            return cached
        }
    }

    /// Creates always require the server; there is no offline path for a trip that does not exist yet.
    func createTrip(_ request: CreateTripRequest) async throws -> Trip {
        do {
            let trip = try await remote.createTrip(request)
            indicator.markServedFromServer()
            return trip
        } catch {
            indicator.markServedFromCache("创建行程失败，仍显示本地数据")
            throw error
        }
    }

    func parseImport(
        tripId: String,
        fileData: Data,
        filename: String
    ) async throws -> ImportParseResult {
        try await remote.parseImport(tripId: tripId, fileData: fileData, filename: filename)
    }

    func confirmImport(
        tripId: String,
        request: ConfirmImportRequest
    ) async throws -> ConfirmImportResult {
        try await remote.confirmImport(tripId: tripId, request: request)
    }
}