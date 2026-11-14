import XCTest

/// 本地模式存储与仓储测试（P1，ADR-0036）。
/// 每个用例独立临时库文件，互不污染。
@testable import GlobalTravelOS

final class LocalStoreTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var database: LocalDatabase!

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("localstore-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
        database = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("test.sqlite3")
        )
    }

    override func tearDownWithError() throws {
        database = nil
        try? FileManager.default.removeItem(at: temporaryDirectory)
    }

    // MARK: - 迁移与基础操作

    func testMigrationsAreIdempotent() throws {
        // 重复 open 同一路径：迁移只应用一次，不报错
        let reopened = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("test.sqlite3")
        )
        let count = try reopened.count(table: "schema_migrations")
        XCTAssertGreaterThanOrEqual(count, 3)
        let again = try reopened.count(table: "schema_migrations")
        XCTAssertEqual(again, count)
    }

    func testKeyValueRoundTrip() throws {
        try database.setKeyValue("probe", value: "值-1")
        XCTAssertEqual(database.keyValue("probe"), "值-1")
        try database.setKeyValue("probe", value: "值-2")
        XCTAssertEqual(database.keyValue("probe"), "值-2")
    }

    // MARK: - LocalTripRepository：创建（Place+Trip 原子编排）

    private func makeRepository() -> LocalTripRepository {
        LocalTripRepository(database: database)
    }

    private func makeRequest(name: String) -> CreateTripRequest {
        CreateTripRequest(
            name: name,
            purpose: "客户拜访",
            originPlace: PlaceRef(
                name: "上海浦东",
                countryCode: "cn",
                timezone: "Asia/Shanghai",
                latitude: 31.23,
                longitude: 121.47
            ),
            window: TripWindow(
                start: Date(timeIntervalSinceNow: 86_400),
                end: Date(timeIntervalSinceNow: 86_400 * 3)
            )
        )
    }

    func testCreateTripWritesPlaceAndTripAtomically() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "演示行程"))

        XCTAssertEqual(trip.status, "DRAFT")
        XCTAssertEqual(trip.version, 1)
        XCTAssertEqual(trip.originPlace?.countryCode, "CN") // 归一化为大写
        XCTAssertNotNil(trip.window)

        let places = try database.loadPayloads(table: "places")
        XCTAssertEqual(places.count, 1)
        let place = try JSONDecoder().decode(Place.self, from: places[0])
        XCTAssertEqual(place.id, trip.originPlaceId)
    }

    func testListTripsIsOrderedByUpdatedAtDescAndPaginates() async throws {
        let repository = makeRepository()
        for index in 1...3 {
            _ = try await repository.createTrip(makeRequest(name: "行程-\(index)"))
            // createTrip 时间戳同秒内可能相同：显式错开 updated_at
            try database.run(
                "UPDATE trips SET updated_at = ? WHERE name = ?",
                bindings: [
                    SQLTimestamp.basics.string(from: Date(timeIntervalSinceNow: Double(index) * 60)),
                    "行程-\(index)",
                ]
            )
        }
        let first = try await repository.listTrips(page: PageRequest(limit: 2))
        XCTAssertEqual(first.items.count, 2)
        XCTAssertEqual(first.items.first?.name, "行程-3") // 最新优先
        XCTAssertNotNil(first.nextCursor)

        let second = try await repository.listTrips(page: PageRequest(limit: 2, cursor: first.nextCursor))
        XCTAssertEqual(second.items.count, 1)
        XCTAssertNil(second.nextCursor)
        XCTAssertEqual(second.items.first?.name, "行程-1")
    }

    // MARK: - hold/resume/complete 状态机与版本令牌

    func testHoldResumeCompleteStateMachine() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "状态机行程"))

        // DRAFT 不能 hold（仅 ACTIVE）
        do {
            _ = try await repository.holdTrip(id: trip.id, version: trip.version)
            XCTFail("DRAFT 行程不应可挂起")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "TRIP_NOT_HOLDABLE")
        }

        // DRAFT resume 直接回 DRAFT（activePlan 为空）——服务端语义按状态机拒绝，这里锁本地行为
        do {
            _ = try await repository.resumeTrip(id: trip.id, version: trip.version)
            XCTFail("非 HOLD 行程不应可恢复")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "TRIP_NOT_RESUMABLE")
        }

        // 直接把行程置 ACTIVE（模拟已激活计划）再走 hold→resume
        var activated = trip
        activated.status = "ACTIVE"
        activated.activePlanVersionId = "plan-1"
        activated.version += 1
        try database.upsert(
            table: "trips",
            id: activated.id,
            columns: [
                "name": activated.name,
                "status": "ACTIVE",
                "version": activated.version,
                "active_plan_id": "plan-1",
                "updated_at": SQLTimestamp.basics.string(from: Date()),
            ],
            payload: try DateCoding.encoder().encode(activated)
        )

        let held = try await repository.holdTrip(id: trip.id, version: activated.version)
        XCTAssertEqual(held.status, "HOLD")
        XCTAssertEqual(held.version, activated.version + 1)

        // 诊断：resume 前 payload 里 activePlanVersionId 是否存在
        let checkPayload = try database.loadPayloads(table: "trips", where: "id = ?", bindings: [trip.id])
        let checked = try DateCoding.decoder().decode(Trip.self, from: checkPayload[0])
        XCTAssertEqual(
            checked.activePlanVersionId, "plan-1",
            "hold 后 payload 应保留 activePlanVersionId；payload=\(String(decoding: checkPayload[0], as: UTF8.self))"
        )
        let resumed = try await repository.resumeTrip(id: trip.id, version: held.version)
        XCTAssertEqual(resumed.status, "ACTIVE") // 有激活计划 ⇒ 回 ACTIVE
        let completed = try await repository.completeTrip(id: trip.id, version: resumed.version)
        XCTAssertEqual(completed.status, "COMPLETED")
    }

    func testVersionConflictOnStaleWrite() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "并发令牌行程"))
        // 用过期版本写
        do {
            _ = try await repository.holdTrip(id: trip.id, version: trip.version + 5)
            XCTFail("过期版本应当冲突")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "VERSION_CONFLICT")
        }
    }

    func testTripNotFoundShapesServerError() async throws {
        let repository = makeRepository()
        do {
            _ = try await repository.trip(id: "00000000-0000-4000-8000-999999999999")
            XCTFail("应抛 TRIP_NOT_FOUND")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "TRIP_NOT_FOUND")
        }
    }

    // MARK: - Pending 方法显式占位（P4/P7 补齐）

    func testPendingMethodsThrowInsteadOfFaking() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "pending 行程"))

        // budgetSummary 已在 P4 本地化：无费用时返回空摘要（不是 pending）。
        let budget = try await repository.budgetSummary(tripId: trip.id, baseCurrency: "EUR")
        XCTAssertEqual(budget.budgetCurrency, "EUR")
        XCTAssertEqual(budget.actualByCurrency?.count ?? 0, 0)
        // 诊断断言保留：localGuide 仍待 P7 实现。
        do {
            _ = try await repository.localGuide(tripId: trip.id)
            XCTFail("localGuide 应显式未实现（P7 前）")
        } catch is LocalStorePendingError {
            // 预期
        }
        do {
            _ = try await repository.localGuide(tripId: trip.id)
            XCTFail("localGuide 应显式未实现")
        } catch is LocalStorePendingError {
            // 预期
        }
    }
}
