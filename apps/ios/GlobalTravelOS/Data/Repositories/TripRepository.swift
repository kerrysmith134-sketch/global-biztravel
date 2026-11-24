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

struct PlaceCreated: Decodable {
    var id: String
}

/// Trip reads and writes. Writes (`createTrip`) are not offlined — only field records go through
/// the outbox, per design doc §10.5; a trip has to exist on the server before anything can be
/// planned against it.
protocol TripRepository {
    func listTrips(page: PageRequest) async throws -> Page<Trip>
    func trip(id: String) async throws -> Trip
    func holdTrip(id: String, version: Int) async throws -> Trip
    func resumeTrip(id: String, version: Int) async throws -> Trip
    func completeTrip(id: String, version: Int) async throws -> Trip
    func visaCases(tripId: String) async throws -> [VisaCase]
    func createVisaCases(tripId: String, countries: [String]?) async throws -> [VisaCase]
    func updateVisaCase(id: String, status: VisaCaseStatus) async throws -> VisaCase
    func expenses(tripId: String) async throws -> [TripExpense]
    func createExpense(tripId: String, body: TripExpenseCreateRequest) async throws -> TripExpense
    func createExpense(
        tripId: String,
        body: TripExpenseCreateRequest,
        idempotencyKey: String?
    ) async throws -> TripExpense
    func budgetSummary(tripId: String, baseCurrency: String?) async throws -> BudgetSummary
    func closeoutReadiness(tripId: String) async throws -> TripCloseoutReadiness
    func expenseReports(tripId: String) async throws -> [ExpenseReport]
    func createExpenseReport(tripId: String) async throws -> ExpenseReport
    func submitExpenseReport(id: String) async throws -> ExpenseReport
    func entryAssessments(tripId: String) async throws -> [EntryAssessment]
    func refreshEntryAssessments(tripId: String) async throws -> [EntryAssessment]
    func createTrip(_ request: CreateTripRequest) async throws -> Trip
    func localGuide(tripId: String) async throws -> LocalGuide
    func tripOutcome(tripId: String) async throws -> TripOutcome
    func parseImport(tripId: String, fileData: Data, filename: String) async throws -> ImportParseResult
    func confirmImport(tripId: String, request: ConfirmImportRequest) async throws -> ConfirmImportResult
}

extension TripRepository {
    /// Local-only repositories do not have a server evidence ledger to assess.
    func closeoutReadiness(tripId: String) async throws -> TripCloseoutReadiness {
        throw APIError.serverError(
            code: "CLOSEOUT_UNAVAILABLE",
            message: "本地模式没有服务端结账评估",
            traceId: nil,
            details: [:]
        )
    }

    /// Repositories without remote idempotency support keep their existing local write path.
    func createExpense(
        tripId: String,
        body: TripExpenseCreateRequest,
        idempotencyKey: String?
    ) async throws -> TripExpense {
        try await createExpense(tripId: tripId, body: body)
    }
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

    func holdTrip(id: String, version: Int) async throws -> Trip {
        try await client.send(Endpoints.holdTrip(id: id, version: version), as: Trip.self)
    }

    func resumeTrip(id: String, version: Int) async throws -> Trip {
        try await client.send(Endpoints.resumeTrip(id: id, version: version), as: Trip.self)
    }

    func completeTrip(id: String, version: Int) async throws -> Trip {
        try await client.send(Endpoints.completeTrip(id: id, version: version), as: Trip.self)
    }

    func visaCases(tripId: String) async throws -> [VisaCase] {
        try await client.send(Endpoints.visaCases(tripId: tripId), as: [VisaCase].self)
    }

    func createVisaCases(tripId: String, countries: [String]?) async throws -> [VisaCase] {
        try await client.send(
            try Endpoints.createVisaCases(tripId: tripId, countries: countries),
            as: [VisaCase].self
        )
    }

    func updateVisaCase(id: String, status: VisaCaseStatus) async throws -> VisaCase {
        try await client.send(
            try Endpoints.updateVisaCase(id: id, status: status),
            as: VisaCase.self
        )
    }

    func expenses(tripId: String) async throws -> [TripExpense] {
        try await client.send(Endpoints.tripExpenses(tripId: tripId), as: [TripExpense].self)
    }

    func createExpense(
        tripId: String,
        body: TripExpenseCreateRequest
    ) async throws -> TripExpense {
        try await createExpense(tripId: tripId, body: body, idempotencyKey: nil)
    }

    func createExpense(
        tripId: String,
        body: TripExpenseCreateRequest,
        idempotencyKey: String?
    ) async throws -> TripExpense {
        try await client.send(
            try Endpoints.createTripExpense(
                tripId: tripId,
                body: body,
                idempotencyKey: idempotencyKey
            ),
            as: TripExpense.self
        )
    }

    func budgetSummary(tripId: String, baseCurrency: String?) async throws -> BudgetSummary {
        try await client.send(
            Endpoints.tripBudget(tripId: tripId, baseCurrency: baseCurrency),
            as: BudgetSummary.self
        )
    }

    func closeoutReadiness(tripId: String) async throws -> TripCloseoutReadiness {
        try await client.send(
            Endpoints.tripCloseoutReadiness(tripId: tripId),
            as: TripCloseoutReadiness.self
        )
    }

    func localGuide(tripId: String) async throws -> LocalGuide {
        try await client.send(Endpoints.localGuide(tripId: tripId), as: LocalGuide.self)
    }

    func tripOutcome(tripId: String) async throws -> TripOutcome {
        try await client.send(Endpoints.tripOutcome(tripId: tripId), as: TripOutcome.self)
    }

    func expenseReports(tripId: String) async throws -> [ExpenseReport] {
        try await client.send(Endpoints.expenseReports(tripId: tripId), as: [ExpenseReport].self)
    }

    func createExpenseReport(tripId: String) async throws -> ExpenseReport {
        try await client.send(
            try Endpoints.createExpenseReport(tripId: tripId),
            as: ExpenseReport.self
        )
    }

    func submitExpenseReport(id: String) async throws -> ExpenseReport {
        try await client.send(
            Endpoints.submitExpenseReport(id: id),
            as: ExpenseReport.self
        )
    }

    func entryAssessments(tripId: String) async throws -> [EntryAssessment] {
        try await client.send(
            Endpoints.entryAssessments(tripId: tripId),
            as: [EntryAssessment].self
        )
    }

    func refreshEntryAssessments(tripId: String) async throws -> [EntryAssessment] {
        try await client.send(
            Endpoints.refreshEntryAssessments(tripId: tripId),
            as: [EntryAssessment].self
        )
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
            countries: request.countries,
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

    func holdTrip(id: String, version: Int) async throws -> Trip {
        try await remote.holdTrip(id: id, version: version)
    }

    func resumeTrip(id: String, version: Int) async throws -> Trip {
        try await remote.resumeTrip(id: id, version: version)
    }

    func completeTrip(id: String, version: Int) async throws -> Trip {
        try await remote.completeTrip(id: id, version: version)
    }

    func visaCases(tripId: String) async throws -> [VisaCase] {
        try await remote.visaCases(tripId: tripId)
    }

    func createVisaCases(tripId: String, countries: [String]?) async throws -> [VisaCase] {
        try await remote.createVisaCases(tripId: tripId, countries: countries)
    }

    func updateVisaCase(id: String, status: VisaCaseStatus) async throws -> VisaCase {
        try await remote.updateVisaCase(id: id, status: status)
    }

    func expenses(tripId: String) async throws -> [TripExpense] {
        try await remote.expenses(tripId: tripId)
    }

    func createExpense(
        tripId: String,
        body: TripExpenseCreateRequest
    ) async throws -> TripExpense {
        try await remote.createExpense(tripId: tripId, body: body)
    }

    func createExpense(
        tripId: String,
        body: TripExpenseCreateRequest,
        idempotencyKey: String?
    ) async throws -> TripExpense {
        try await remote.createExpense(
            tripId: tripId,
            body: body,
            idempotencyKey: idempotencyKey
        )
    }

    func budgetSummary(tripId: String, baseCurrency: String?) async throws -> BudgetSummary {
        try await remote.budgetSummary(tripId: tripId, baseCurrency: baseCurrency)
    }

    func closeoutReadiness(tripId: String) async throws -> TripCloseoutReadiness {
        let readiness = try await remote.closeoutReadiness(tripId: tripId)
        indicator.markServedFromServer()
        return readiness
    }

    func localGuide(tripId: String) async throws -> LocalGuide {
        // 离线目录版指南体积小、随版本发布；直接走服务端，失败原样抛出。
        let guide = try await remote.localGuide(tripId: tripId)
        indicator.markServedFromServer()
        return guide
    }

    /// 结果复盘是只读聚合，直接走服务端。
    func tripOutcome(tripId: String) async throws -> TripOutcome {
        let outcome = try await remote.tripOutcome(tripId: tripId)
        indicator.markServedFromServer()
        return outcome
    }

    func expenseReports(tripId: String) async throws -> [ExpenseReport] {
        try await remote.expenseReports(tripId: tripId)
    }

    func createExpenseReport(tripId: String) async throws -> ExpenseReport {
        try await remote.createExpenseReport(tripId: tripId)
    }

    func submitExpenseReport(id: String) async throws -> ExpenseReport {
        try await remote.submitExpenseReport(id: id)
    }

    func entryAssessments(tripId: String) async throws -> [EntryAssessment] {
        try await remote.entryAssessments(tripId: tripId)
    }

    func refreshEntryAssessments(tripId: String) async throws -> [EntryAssessment] {
        try await remote.refreshEntryAssessments(tripId: tripId)
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
