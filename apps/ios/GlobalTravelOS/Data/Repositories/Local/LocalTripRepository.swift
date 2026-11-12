import Foundation

/// 本地模式尚未实现的方法统一抛这个：UI 显示错误横幅，随阶段推进逐个替换。
struct LocalStorePendingError: LocalizedError {
    let method: String

    var errorDescription: String? {
        "本地模式暂未实现：\(method)（随本地版阶段推进补齐）"
    }
}

/// 本地模式仓储：SQLite document 表即真相，UI 协议零改动。
///
/// 实现范围随本地版阶段推进（ADR-0036）：
/// - P1 已本地化：trips/places/visits 读写、hold/resume/complete（版本令牌语义）
/// - P4/P7 逐步替换标 `pending` 的领域方法（费用/报销/预算/签证/导入/指南/复盘）
final class LocalTripRepository: TripRepository {
    let database: LocalDatabase

    init(database: LocalDatabase) {
        self.database = database
    }

    // MARK: - 编解码

    private static let encoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()

    private static let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }()

    private func encode<T: Encodable>(_ value: T) throws -> Data {
        try Self.encoder.encode(value)
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        try Self.decoder.decode(type, from: data)
    }

    // MARK: - 行程读写（P1 本地化）

    func listTrips(page: PageRequest) async throws -> Page<Trip> {
        let offset = page.cursor.flatMap { Int($0.replacingOccurrences(of: "offset:", with: "")) } ?? 0
        let payloads = try database.loadPayloads(
            table: "trips",
            order: "updated_at DESC",
            limit: page.limit + 1
        )
        var trips = try payloads.map { try decode(Trip.self, from: $0) }
        var next: String?
        if trips.count > page.limit {
            trips = Array(trips.dropLast())
            next = "offset:\(offset + page.limit)"
        }
        let skipped = Array(trips.dropFirst(offset))
        let windowed = Array(skipped.prefix(page.limit))
        return Page(items: windowed, nextCursor: next)
    }

    func trip(id: String) async throws -> Trip {
        let payloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [id])
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        return try decode(Trip.self, from: payload)
    }

    /// 乐观并发：version 不匹配即冲突（与 `If-Match` 同语义）。
    private func loadForWrite(id: String, expectedVersion: Int) throws -> Trip {
        let payloads = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [id])
        guard let payload = payloads.first else {
            throw APIError.serverError(code: "TRIP_NOT_FOUND", message: "出行不存在", traceId: nil, details: [:])
        }
        let trip = try decode(Trip.self, from: payload)
        guard trip.version == expectedVersion else {
            throw APIError.serverError(
                code: "VERSION_CONFLICT",
                message: "出行已被其他操作更新（本地版本 \(trip.version) ≠ 请求 \(expectedVersion)）",
                traceId: nil,
                details: [:]
            )
        }
        return trip
    }

    private func save(_ trip: Trip) throws {
        var updated = trip
        updated.updatedAt = Date()
        let columns: [String: Any?] = [
            "name": updated.name,
            "status": updated.status ?? "DRAFT",
            "version": updated.version,
            "window_start": updated.window.map { SQLTimestamp.basics.string(from: $0.start) },
            "window_end": updated.window.map { SQLTimestamp.basics.string(from: $0.end) },
            "active_plan_id": updated.activePlanVersionId,
            "updated_at": SQLTimestamp.basics.string(from: updated.updatedAt ?? Date()),
        ]
        try database.upsert(
            table: "trips",
            id: updated.id,
            columns: columns,
            payload: try encode(updated)
        )
    }

    func holdTrip(id: String, version: Int) async throws -> Trip {
        var trip = try loadForWrite(id: id, expectedVersion: version)
        guard trip.status == "ACTIVE" else {
            throw APIError.serverError(code: "TRIP_NOT_HOLDABLE", message: "仅进行中的出行可以挂起", traceId: nil, details: [:])
        }
        trip.status = "HOLD"
        trip.version += 1
        try save(trip)
        return trip
    }

    func resumeTrip(id: String, version: Int) async throws -> Trip {
        var trip = try loadForWrite(id: id, expectedVersion: version)
        guard trip.status == "HOLD" else {
            throw APIError.serverError(code: "TRIP_NOT_RESUMABLE", message: "仅挂起中的出行可以恢复", traceId: nil, details: [:])
        }
        // 服务端语义：按生效计划回到 ACTIVE/READY/DRAFT。
        trip.status = trip.activePlanVersionId != nil ? "ACTIVE" : "DRAFT"
        trip.version += 1
        try save(trip)
        return trip
    }

    func completeTrip(id: String, version: Int) async throws -> Trip {
        var trip = try loadForWrite(id: id, expectedVersion: version)
        guard trip.status == "ACTIVE" || trip.status == "HOLD" else {
            throw APIError.serverError(code: "TRIP_NOT_COMPLETABLE", message: "仅进行中/挂起的出行可以完成", traceId: nil, details: [:])
        }
        trip.status = "COMPLETED"
        trip.version += 1
        try save(trip)
        return trip
    }

    /// 本地编排：建 Place + 建 Trip 在同一事务里（远端是两次 HTTP，本地吸收为原子操作）。
    func createTrip(_ request: CreateTripRequest) async throws -> Trip {
        let origin = request.originPlace
        let placeId = UUID().uuidString
        let tripId = UUID().uuidString
        let now = Date()

        let place = Place(
            id: placeId,
            name: origin.name,
            countryCode: origin.countryCode.uppercased(),
            timezone: origin.timezone,
            latitude: origin.latitude,
            longitude: origin.longitude,
            city: origin.name
        )
        let trip = Trip(
            id: tripId,
            organizationId: "local",
            name: request.name,
            purpose: request.purpose,
            status: "DRAFT",
            version: 1,
            window: request.window,
            originPlace: place,
            originPlaceId: placeId,
            activePlanVersionId: nil,
            createdAt: now,
            updatedAt: now,
            activePlan: nil,
            stats: nil,
            currency: nil,
            countries: [origin.countryCode.uppercased()]
        )

        try database.transaction {
            let placeColumns: [String: Any?] = ["name": place.name, "city": place.city]
            try database.upsert(
                table: "places",
                id: placeId,
                columns: placeColumns,
                payload: try encode(place)
            )
            try save(trip)
            let visitStatus: String? = nil // 新行程无拜访
            _ = visitStatus
        }
        return trip
    }

    // MARK: - 拜访/地点查询（本地行程详情需要）

    func visits(tripId: String) throws -> [TripVisit] {
        let payloads = try database.loadPayloads(
            table: "visits",
            where: "trip_id = ?",
            bindings: [tripId],
            order: "created_at"
        )
        return try payloads.map { try decode(TripVisit.self, from: $0) }
    }

    func saveVisit(_ visit: TripVisit) throws {
        try database.upsert(
            table: "visits",
            id: visit.id,
            columns: [
                "trip_id": visit.tripId as Any?,
                "title": visit.title as Any?,
                "status": visit.status as Any?,
                "lat": nil as Any?,
                "lon": nil as Any?,
            ],
            payload: try encode(visit)
        )
    }

    // MARK: - P4/P7 逐个替换的领域方法（显式占位）

    func visaCases(tripId: String) async throws -> [VisaCase] {
        let payloads = try database.loadPayloads(
            table: "visa_cases", where: "trip_id = ?", bindings: [tripId], order: "created_at"
        )
        return try payloads.map { try decode(VisaCase.self, from: $0) }
    }

    func createVisaCases(tripId: String, countries: [String]?) async throws -> [VisaCase] {
        throw LocalStorePendingError(method: "createVisaCases")
    }

    func updateVisaCase(id: String, status: VisaCaseStatus) async throws -> VisaCase {
        throw LocalStorePendingError(method: "updateVisaCase")
    }

    func expenses(tripId: String) async throws -> [TripExpense] {
        let payloads = try database.loadPayloads(
            table: "trip_expenses", where: "trip_id = ?", bindings: [tripId], order: "occurred_on"
        )
        return try payloads.map { try decode(TripExpense.self, from: $0) }
    }

    func createExpense(tripId: String, body: TripExpenseCreateRequest) async throws -> TripExpense {
        throw LocalStorePendingError(method: "createExpense")
    }

    func budgetSummary(tripId: String, baseCurrency: String?) async throws -> BudgetSummary {
        throw LocalStorePendingError(method: "budgetSummary")
    }

    func expenseReports(tripId: String) async throws -> [ExpenseReport] {
        let payloads = try database.loadPayloads(
            table: "expense_reports", where: "trip_id = ?", bindings: [tripId], order: "created_at"
        )
        return try payloads.map { try decode(ExpenseReport.self, from: $0) }
    }

    func createExpenseReport(tripId: String) async throws -> ExpenseReport {
        throw LocalStorePendingError(method: "createExpenseReport")
    }

    func submitExpenseReport(id: String) async throws -> ExpenseReport {
        throw LocalStorePendingError(method: "submitExpenseReport")
    }

    func entryAssessments(tripId: String) async throws -> [EntryAssessment] {
        let payloads = try database.loadPayloads(
            table: "entry_assessments", where: "trip_id = ?", bindings: [tripId], order: "created_at"
        )
        return try payloads.map { try decode(EntryAssessment.self, from: $0) }
    }

    func refreshEntryAssessments(tripId: String) async throws -> [EntryAssessment] {
        throw LocalStorePendingError(method: "refreshEntryAssessments")
    }

    func localGuide(tripId: String) async throws -> LocalGuide {
        throw LocalStorePendingError(method: "localGuide")
    }

    func tripOutcome(tripId: String) async throws -> TripOutcome {
        throw LocalStorePendingError(method: "tripOutcome")
    }

    func parseImport(tripId: String, fileData: Data, filename: String) async throws -> ImportParseResult {
        throw LocalStorePendingError(method: "parseImport")
    }

    func confirmImport(tripId: String, request: ConfirmImportRequest) async throws -> ConfirmImportResult {
        throw LocalStorePendingError(method: "confirmImport")
    }
}
