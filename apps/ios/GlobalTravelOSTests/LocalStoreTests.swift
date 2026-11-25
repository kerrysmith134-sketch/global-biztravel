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
        XCTAssertGreaterThanOrEqual(count, 6)
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

    func testPartialUpsertUpdatesExistingDocumentWithoutReinsertingRequiredColumns() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "部分字段更新"))
        var updatedTrip = trip
        updatedTrip.status = "COMPLETED"

        try database.transaction {
            try database.upsert(
                table: "trips",
                id: trip.id,
                columns: ["status": "COMPLETED"],
                payload: try DateCoding.encode(updatedTrip)
            )
        }

        let persisted = try await repository.trip(id: trip.id)
        XCTAssertEqual(persisted.status, "COMPLETED")
        XCTAssertEqual(persisted.name, trip.name)
        let scalarColumns = try database.store.query(
            "SELECT name, updated_at FROM trips WHERE id = ?",
            bindings: [trip.id]
        ) { ($0.string(0), $0.string(1)) }.first
        XCTAssertEqual(scalarColumns?.0, trip.name)
        XCTAssertFalse(scalarColumns?.1.isEmpty ?? true)
    }

    func testNestedTransactionSavepointRollsBackOnlyInnerChanges() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "嵌套事务回滚"))
        var outerTrip = trip
        outerTrip.status = "ACTIVE"

        try database.transaction {
            try database.upsert(
                table: "trips",
                id: trip.id,
                columns: ["status": "ACTIVE"],
                payload: try DateCoding.encode(outerTrip)
            )

            do {
                try database.transaction {
                    var innerTrip = outerTrip
                    innerTrip.status = "COMPLETED"
                    try database.upsert(
                        table: "trips",
                        id: trip.id,
                        columns: ["status": "COMPLETED"],
                        payload: try DateCoding.encode(innerTrip)
                    )
                    throw NSError(domain: "LocalStoreTests", code: 1)
                }
            } catch {
                // The caller intentionally recovers; the failed savepoint must still roll back.
            }
        }

        let persisted = try await repository.trip(id: trip.id)
        XCTAssertEqual(persisted.status, "ACTIVE")
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

    func testLocalCustomerPlacesPersistAndEnforceSinglePrimaryAddress() async throws {
        let repository = LocalCustomerRepository(database: database)
        let customer = try await repository.createCustomer(
            CustomerCreateRequest(name: "Local Customer", tier: "A")
        )
        let headOffice = Place(
            id: "place-local-hq",
            name: "Head Office",
            countryCode: "DE",
            latitude: 52.5200,
            longitude: 13.4050,
            city: "Berlin",
            address: "Industriestrasse 12"
        )
        let factory = Place(
            id: "place-local-factory",
            name: "Factory",
            countryCode: "DE",
            latitude: 51.4416,
            longitude: 5.4697,
            city: "Eindhoven"
        )
        for place in [headOffice, factory] {
            try database.upsert(
                table: "places",
                id: place.id ?? "",
                columns: ["name": place.name, "city": place.city],
                payload: try DateCoding.encode(place)
            )
        }

        let firstLink = try await repository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: headOffice.id ?? "",
                label: "总部",
                isPrimary: true
            )
        )
        XCTAssertTrue(firstLink.isPrimary)
        XCTAssertTrue(firstLink.hasValidCoordinates)
        XCTAssertEqual(firstLink.geocodeStatus, "RESOLVED")
        XCTAssertNil(firstLink.geocodeSource) // 本地存储不伪造地理编码来源

        _ = try await repository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: factory.id ?? "",
                label: "工厂",
                isPrimary: true
            )
        )

        // Re-linking an existing pair updates it instead of creating a duplicate row.
        _ = try await repository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: factory.id ?? "",
                label: "生产基地",
                isPrimary: true
            )
        )

        let places = try await repository.customerPlaces(customerId: customer.id)
        XCTAssertEqual(places.count, 2)
        XCTAssertEqual(places.first?.placeId, factory.id)
        XCTAssertEqual(places.first?.label, "生产基地")
        XCTAssertTrue(places.first?.isPrimary == true)
        XCTAssertFalse(places.first(where: { $0.placeId == headOffice.id })?.isPrimary ?? true)

        let storedCount = try database.count(
            table: "customer_places",
            where: "customer_id = ?",
            bindings: [customer.id]
        )
        XCTAssertEqual(storedCount, 2)
    }

    func testCustomerAddressBookCreateSearchAndUnlinkKeepsSharedPlace() async throws {
        let repository = LocalCustomerRepository(database: database)
        let customer = try await repository.createCustomer(
            CustomerCreateRequest(name: "Address Book Customer", tier: "A")
        )
        let request = PlaceCreateRequest(
            name: "Rotterdam Factory",
            addressLine: "Harbor Road 8",
            city: "Rotterdam",
            region: "South Holland",
            countryCode: "NL",
            timezone: "Europe/Amsterdam",
            lat: 51.9244,
            lon: 4.4777
        )

        let created = try await repository.createAndLinkCustomerPlace(
            customerId: customer.id,
            place: request,
            label: "工厂",
            isPrimary: true
        )
        XCTAssertTrue(created.isPrimary)
        XCTAssertEqual(created.region, "South Holland")
        XCTAssertEqual(created.geocodeStatus, "RESOLVED")
        XCTAssertEqual(created.geocodeSource, "client")
        XCTAssertTrue(created.hasValidCoordinates)

        let catalog = try await repository.listPlaces(
            query: "Rotterdam",
            countryCode: "NL",
            page: PageRequest(limit: 20)
        )
        XCTAssertEqual(catalog.items.count, 1)
        XCTAssertEqual(catalog.items.first?.id, created.placeId)
        XCTAssertEqual(catalog.items.first?.displayAddress, "Harbor Road 8 · Rotterdam · South Holland · NL")

        try await repository.unlinkCustomerPlace(customerId: customer.id, placeId: created.placeId)
        let remainingLinks = try await repository.customerPlaces(customerId: customer.id)
        XCTAssertTrue(remainingLinks.isEmpty)

        // Removing a customer relation must not delete the organization-level shared place.
        let retained = try await repository.listPlaces(
            query: "Rotterdam",
            countryCode: "NL",
            page: PageRequest(limit: 20)
        )
        XCTAssertEqual(retained.items.map(\.id), [created.placeId])
    }

    func testLocalGlobalCustomerMapAggregatesAndFiltersAddresses() async throws {
        let repository = LocalCustomerRepository(database: database)
        let customerA = try await repository.createCustomer(
            CustomerCreateRequest(name: "Map Customer A", tier: "A", countryCode: "DE", industry: "Energy")
        )
        let customerB = try await repository.createCustomer(
            CustomerCreateRequest(name: "Map Customer B", tier: "C", countryCode: "NL")
        )
        let berlin = Place(
            id: "map-berlin-hq", name: "Berlin HQ", countryCode: "DE",
            latitude: 52.52, longitude: 13.405, city: "Berlin",
            address: "Industriestrasse 12", region: "Berlin",
            geocodeStatus: "RESOLVED", geocodeSource: "client"
        )
        let rotterdam = Place(
            id: "map-rotterdam-factory", name: "Rotterdam Factory",
            countryCode: "NL", city: "Rotterdam", geocodeStatus: "MISSING"
        )
        let hamburg = Place(
            id: "map-hamburg-office", name: "Hamburg Office", countryCode: "DE",
            latitude: 53.5511, longitude: 9.9937, city: "Hamburg",
            geocodeStatus: "RESOLVED", geocodeSource: "client"
        )
        for place in [berlin, rotterdam, hamburg] {
            try database.upsert(
                table: "places", id: place.id ?? "",
                columns: ["name": place.name, "city": place.city],
                payload: try DateCoding.encode(place)
            )
        }
        _ = try await repository.linkCustomerPlace(
            customerId: customerA.id,
            request: CustomerPlaceLinkRequest(placeId: berlin.id ?? "", label: "总部", isPrimary: true)
        )
        _ = try await repository.linkCustomerPlace(
            customerId: customerA.id,
            request: CustomerPlaceLinkRequest(placeId: rotterdam.id ?? "", label: "工厂", isPrimary: false)
        )
        _ = try await repository.linkCustomerPlace(
            customerId: customerA.id,
            request: CustomerPlaceLinkRequest(placeId: hamburg.id ?? "", label: "区域办公室", isPrimary: false)
        )
        _ = try await repository.linkCustomerPlace(
            customerId: customerB.id,
            request: CustomerPlaceLinkRequest(placeId: berlin.id ?? "", label: "合作办公室", isPrimary: true)
        )

        let all = try await repository.listGlobalCustomerMapLocations(
            filters: .all, page: PageRequest(limit: 50)
        )
        XCTAssertEqual(all.items.count, 4)
        XCTAssertEqual(all.summary.customersWithLocations, 2)
        XCTAssertEqual(all.summary.customersWithoutLocations, 0)
        XCTAssertEqual(all.summary.locationsMapped, 3)
        XCTAssertEqual(all.summary.locationsMissingCoordinates, 1)
        let germany = try XCTUnwrap(all.summary.countryStats.first { $0.countryCode == "DE" })
        XCTAssertEqual(germany.customersTotal, 2)
        XCTAssertEqual(germany.locationsTotal, 3)
        XCTAssertEqual(germany.locationsMapped, 3)
        XCTAssertEqual(germany.tierACustomers, 1)
        XCTAssertEqual(germany.tierCCustomers, 1)

        let aTier = try await repository.listGlobalCustomerMapLocations(
            filters: GlobalCustomerMapFilters(tier: "A"), page: PageRequest(limit: 50)
        )
        XCTAssertEqual(aTier.items.count, 3)
        XCTAssertTrue(aTier.items.allSatisfy { $0.customer.tier == "A" })

        let missing = try await repository.listGlobalCustomerMapLocations(
            filters: GlobalCustomerMapFilters(geocodeStatus: "MISSING"), page: PageRequest(limit: 50)
        )
        XCTAssertEqual(missing.items.map(\.place.id), ["map-rotterdam-factory"])
    }

    func testAddingExistingCustomerAddressToTripReusesPlace() async throws {
        let trip = try await makeRepository().createTrip(makeRequest(name: "地图客户加入 Mission"))
        let customerRepository = LocalCustomerRepository(database: database)
        let customer = try await customerRepository.createCustomer(
            CustomerCreateRequest(name: "Map Mission Customer", tier: "A")
        )
        let place = Place(
            id: "map-mission-existing-place",
            name: "Customer Berlin HQ",
            countryCode: "DE",
            latitude: 52.5200,
            longitude: 13.4050,
            city: "Berlin",
            address: "Industriestrasse 12",
            geocodeStatus: "RESOLVED",
            geocodeSource: "client"
        )
        try database.upsert(
            table: "places",
            id: place.id ?? "",
            columns: ["name": place.name, "city": place.city],
            payload: try DateCoding.encode(place)
        )
        _ = try await customerRepository.linkCustomerPlace(
            customerId: customer.id,
            request: CustomerPlaceLinkRequest(
                placeId: place.id ?? "",
                label: "总部",
                isPrimary: true
            )
        )
        let placesBefore = try database.count(table: "places")

        let visit = try await LocalVisitRepository(database: database).createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                placeId: place.id,
                customerId: customer.id,
                title: "客户现场沟通",
                serviceMinutes: 90,
                priority: 1,
                movementMode: "GROUND"
            )
        )

        XCTAssertEqual(visit.customerId, customer.id)
        XCTAssertEqual(visit.placeId, place.id)
        XCTAssertEqual(try database.count(table: "places"), placesBefore)
        let storedVisits = try await LocalVisitRepository(database: database).listVisits(tripId: trip.id)
        XCTAssertEqual(storedVisits.first?.placeId, place.id)
        XCTAssertEqual(storedVisits.first?.customerId, customer.id)
    }

    func testLocalVisitKeepsCRMCustomerAssociation() async throws {
        let trip = try await makeRepository().createTrip(makeRequest(name: "CRM 关联行程"))
        let customer = try await LocalCustomerRepository(database: database).createCustomer(
            CustomerCreateRequest(name: "Visit Customer")
        )
        let visit = try await LocalVisitRepository(database: database).createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                customerId: customer.id,
                placeName: "Customer Site",
                city: "Berlin",
                countryCode: "DE",
                latitude: 52.52,
                longitude: 13.405,
                title: "Customer review"
            )
        )

        let stored = try await LocalVisitRepository(database: database).listVisits(tripId: trip.id)
        XCTAssertEqual(stored.first?.id, visit.id)
        XCTAssertEqual(stored.first?.customerId, customer.id)
        XCTAssertNotNil(stored.first?.placeId)
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

        // localGuide 已在 P7 本地化：返回指南（空拜访时国家有目录条目、带实时源缺口标注）。
        let guide = try await repository.localGuide(tripId: trip.id)
        XCTAssertEqual(guide.tripId, trip.id)
        XCTAssertTrue(guide.gapList.contains { $0.code == "LIVE_POI_NOT_CONNECTED" })

        // 签证案件已本地化（P7）：按目的地国家创建 NEW 案件 + 材料清单。
        let cases = try await repository.createVisaCases(tripId: trip.id, countries: ["DE"])
        XCTAssertEqual(cases.count, 1)
        XCTAssertEqual(cases.first?.status, .new)
        XCTAssertEqual(cases.first?.checklistItems.count, 7)
        // 幂等：再次创建不新增
        let again = try await repository.createVisaCases(tripId: trip.id, countries: ["DE"])
        XCTAssertEqual(again.first?.id, cases.first?.id)
        // 非法状态迁移必须拒绝（NEW → APPROVED 直跳）
        do {
            _ = try await repository.updateVisaCase(id: cases.first!.id, status: .approved)
            XCTFail("NEW→APPROVED 应被状态机拒绝")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "CASE_TRANSITION_INVALID")
        }

        let preparing = try await repository.updateVisaCase(
            id: cases.first!.id,
            status: .preparing
        )
        XCTAssertEqual(preparing.status, .preparing)
        let submitted = try await repository.updateVisaCase(
            id: cases.first!.id,
            status: .submitted
        )
        XCTAssertEqual(submitted.status, .submitted)
        XCTAssertNotNil(submitted.submittedAt)
    }

    func testLocalVisaReminderApprovalCreatesCompleteCasesAndIsIdempotent() async throws {
        var request = makeRequest(name: "签证提醒执行")
        request.countries = ["DE", "FR"]
        let trip = try await makeRepository().createTrip(request)
        let suggestionId = try seedLocalVisaReminderSuggestion(tripId: trip.id)
        let proactive = LocalProactiveRepository(database: database)

        let approved = try await proactive.approve(suggestionId: suggestionId)
        XCTAssertEqual(approved.status, "EXECUTED")

        let cases = try await makeRepository().visaCases(tripId: trip.id)
        XCTAssertEqual(cases.map(\.countryCode), ["DE", "FR"])
        XCTAssertTrue(cases.allSatisfy { $0.status == .new && $0.checklistItems.count == 7 })

        let payloads = try database.loadPayloads(
            table: "proactive_suggestions",
            where: "id = ?",
            bindings: [suggestionId]
        )
        let object = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: XCTUnwrap(payloads.first)) as? [String: Any]
        )
        let execution = try XCTUnwrap(object["execution_result"] as? [String: Any])
        let recordedIds = try XCTUnwrap(execution["visa_cases"] as? [String])
        XCTAssertEqual(Set(recordedIds), Set(cases.map(\.id)))

        let repeated = try await proactive.approve(suggestionId: suggestionId)
        XCTAssertEqual(repeated.status, "EXECUTED")
        let casesAfterRetry = try await makeRepository().visaCases(tripId: trip.id)
        XCTAssertEqual(casesAfterRetry.map(\.id), cases.map(\.id))
    }

    func testLocalVisaReminderApprovalRollsBackWhenTripIsMissing() async throws {
        let suggestionId = try seedLocalVisaReminderSuggestion(tripId: "missing-local-trip")
        let proactive = LocalProactiveRepository(database: database)

        do {
            _ = try await proactive.approve(suggestionId: suggestionId)
            XCTFail("缺少行程时不得把签证提醒标记为已执行")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "NOT_FOUND")
        }

        let payloads = try database.loadPayloads(
            table: "proactive_suggestions",
            where: "id = ?",
            bindings: [suggestionId]
        )
        let object = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: XCTUnwrap(payloads.first)) as? [String: Any]
        )
        XCTAssertEqual(object["status"] as? String, "PENDING")
        XCTAssertEqual(
            try database.count(
                table: "visa_cases",
                where: "trip_id = ?",
                bindings: ["missing-local-trip"]
            ),
            0
        )
    }

    private func seedLocalVisaReminderSuggestion(tripId: String) throws -> String {
        let id = UUID().uuidString
        let now = SQLTimestamp.basics.string(from: Date())
        let payload: [String: Any] = [
            "id": id,
            "trip_id": tripId,
            "suggestion_type": "VISA_REQUIREMENT_REMINDER",
            "status": "PENDING",
        ]
        try database.upsert(
            table: "proactive_suggestions",
            id: id,
            columns: [
                "trip_id": tripId,
                "type": "VISA_REQUIREMENT_REMINDER",
                "status": "PENDING",
                "push_status": "WITHHELD",
                "dedupe_key": "local-visa-reminder-\(id)",
                "severity": "MEDIUM",
                "created_at": now,
            ],
            payload: try JSONSerialization.data(withJSONObject: payload)
        )
        return id
    }

    func testLocalProactiveScanPersistsTriggerAndDeduplicatesRepeatedCandidates() async throws {
        var request = makeRequest(name: "本地主动触发审计")
        request.countries = ["DE"]
        let trip = try await makeRepository().createTrip(request)
        let now = Date()
        let config = ProactiveConfig(dailySuggestionCap: 0)

        let first = LocalProactiveEngine.scan(
            database: database,
            userId: "local-user",
            config: config,
            now: now
        )
        XCTAssertEqual(first.triggers, 1)
        XCTAssertEqual(first.suggestions, 1)
        XCTAssertTrue(first.errors.isEmpty)

        let triggerRows = try database.store.query(
            """
            SELECT rule_id, dedupe_key, status
            FROM proactive_triggers
            WHERE trip_id = ?
            """,
            bindings: [trip.id]
        ) { ($0.string(0), $0.string(1), $0.string(2)) }
        XCTAssertEqual(triggerRows.count, 1)
        XCTAssertEqual(triggerRows.first?.0, "visa_check")
        XCTAssertEqual(triggerRows.first?.2, "PROPOSED")
        XCTAssertEqual(
            try database.count(
                table: "proactive_suggestions",
                where: "trip_id = ?",
                bindings: [trip.id]
            ),
            1
        )

        let second = LocalProactiveEngine.scan(
            database: database,
            userId: "local-user",
            config: config,
            now: now
        )
        XCTAssertEqual(second.triggers, 1)
        XCTAssertEqual(second.deduped, 1)
        XCTAssertEqual(second.suggestions, 0)
        XCTAssertEqual(
            try database.count(
                table: "proactive_triggers",
                where: "trip_id = ?",
                bindings: [trip.id]
            ),
            1
        )
        XCTAssertEqual(
            try database.count(
                table: "proactive_suggestions",
                where: "trip_id = ?",
                bindings: [trip.id]
            ),
            1
        )
    }

    func testLocalProactiveDailyCapUsesPersistedPushTimestampColumn() async throws {
        var request = makeRequest(name: "每日推送上限")
        request.countries = ["DE"]
        let trip = try await makeRepository().createTrip(request)
        let now = Date()
        let existingId = UUID().uuidString
        let existingPayload: [String: Any] = [
            "id": existingId,
            "trip_id": "another-local-trip",
            "suggestion_type": "VISA_REQUIREMENT_REMINDER",
            "status": "PENDING",
            "push_status": "PUSHED",
            "created_at": SQLTimestamp.basics.string(from: now),
        ]
        try database.upsert(
            table: "proactive_suggestions",
            id: existingId,
            columns: [
                "trip_id": "another-local-trip",
                "type": "VISA_REQUIREMENT_REMINDER",
                "status": "PENDING",
                "push_status": "PUSHED",
                "dedupe_key": "existing-push",
                "severity": "MEDIUM",
                "created_at": SQLTimestamp.basics.string(from: now),
            ],
            payload: try JSONSerialization.data(withJSONObject: existingPayload)
        )

        let config = ProactiveConfig(dailySuggestionCap: 1)
        XCTAssertTrue(
            try LocalProactiveEngine.dailyCapReached(
                database: database,
                now: now,
                cap: config.dailySuggestionCap
            )
        )
        let candidate = try XCTUnwrap(
            LocalProactiveEngine.visaCandidate(trip, config: config, now: now)
        )
        var report = ProactiveScanReport()
        LocalProactiveEngine.record(
            candidate,
            trip: trip,
            userId: "local-user",
            database: database,
            config: config,
            now: now,
            report: &report
        )

        XCTAssertEqual(report.suggestions, 1)
        XCTAssertEqual(report.withheld, 1)
        let payloads = try database.loadPayloads(
            table: "proactive_suggestions",
            where: "trip_id = ?",
            bindings: [trip.id]
        )
        let payload = try XCTUnwrap(payloads.first)
        let object = try XCTUnwrap(
            try JSONSerialization.jsonObject(with: payload) as? [String: Any]
        )
        XCTAssertEqual(object["push_status"] as? String, "WITHHELD")
        XCTAssertEqual(object["push_reason"] as? String, "DAILY_CAP")
    }

    // MARK: - P7 聚合（复盘/简报/版本比较）

    func testLocalCloseoutReadinessUsesPersistedVisitsExpensesAndReportSnapshots() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "本地结账评估"))
        let visitRepository = LocalVisitRepository(database: database)
        let visit = try await visitRepository.createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                placeName: "Berlin Buyer",
                city: "Berlin",
                countryCode: "DE",
                title: "合同条款复核"
            )
        )
        _ = try await repository.createExpense(
            tripId: trip.id,
            body: TripExpenseCreateRequest(
                category: .meals,
                amount: Decimal(string: "34.20")!,
                currency: "EUR",
                occurredOn: ISO8601.dayString(from: Date()),
                note: "客户会议午餐",
                receiptAssetId: nil
            )
        )

        let initial = try await repository.closeoutReadiness(tripId: trip.id)
        XCTAssertEqual(initial.status, .blocked)
        XCTAssertEqual(initial.summary.blockerCount, 2)
        XCTAssertEqual(initial.summary.warningCount, 1)
        XCTAssertEqual(initial.summary.expensesNotInReportCount, 1)
        XCTAssertEqual(initial.summary.expensesWithoutReceiptCount, 1)
        XCTAssertTrue(initial.blockers.contains { $0.code == "VISIT_NOT_CLOSED" })
        XCTAssertTrue(initial.blockers.contains { $0.code == "EXPENSE_NOT_IN_REPORT" })
        XCTAssertEqual(initial.totalsByCurrency.first?.currency, "EUR")
        XCTAssertEqual(initial.totalsByCurrency.first?.amount, 34.2)

        let report = try await repository.createExpenseReport(tripId: trip.id)
        XCTAssertEqual(report.itemList.count, 1)
        let persistedExpenses = try await repository.expenses(tripId: trip.id)
        XCTAssertEqual(report.itemList.first?.expenseId, persistedExpenses.first?.id)
        let listedReports = try await repository.expenseReports(tripId: trip.id)
        XCTAssertEqual(listedReports.first?.id, report.id)
        let submittedReport = try await repository.submitExpenseReport(id: report.id)
        XCTAssertEqual(submittedReport.status, .submitted)
        let reloadedReport = try await repository.expenseReports(tripId: trip.id)
        XCTAssertEqual(reloadedReport.first?.status, .submitted)

        var completedVisit = visit
        completedVisit.status = "DONE"
        try repository.saveVisit(completedVisit)
        _ = try await visitRepository.submitRecord(
            visitId: visit.id,
            request: VisitRecordRequest(
                clientRecordId: VisitRecordRequest.newClientRecordId(),
                outcome: .met,
                notes: "合同条款已确认"
            )
        )

        let resolved = try await repository.closeoutReadiness(tripId: trip.id)
        XCTAssertEqual(resolved.status, .reportSubmitted)
        XCTAssertEqual(resolved.summary.blockerCount, 0)
        XCTAssertEqual(resolved.summary.warningCount, 1)
        XCTAssertEqual(resolved.summary.visitOutcomeCount, 1)
        XCTAssertEqual(resolved.summary.expensesInReportCount, 1)
        XCTAssertEqual(resolved.warnings.first?.code, "RECEIPT_MISSING")

        let persistedTrip = try await repository.trip(id: trip.id)
        XCTAssertEqual(persistedTrip.version, trip.version, "只读结账评估不得改写 Trip 版本")
    }

    func testLocalTripOutcomeAggregatesLocalFacts() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "复盘行程"))

        // 直接写入一条拜访 + 一条现场记录（聚合读取本地事实）。
        let visit = TripVisit(
            id: UUID().uuidString, tripId: trip.id, placeId: trip.originPlaceId,
            title: "客户拜访", objective: nil, serviceMinutes: 60,
            timeWindowStart: nil, timeWindowEnd: nil, priority: 1,
            movementMode: "AIR", status: "COMPLETED", createdAt: Date()
        )
        try database.upsert(
            table: "visits", id: visit.id,
            columns: ["trip_id": trip.id, "place_id": visit.placeId, "title": "客户拜访", "status": "COMPLETED"],
            payload: try DateCoding.encode(visit)
        )
        try database.upsert(
            table: "visit_records", id: UUID().uuidString,
            columns: ["visit_id": visit.id, "client_record_id": "rec-1"],
            payload: try JSONSerialization.data(withJSONObject: ["outcome": "MET", "notes": "确认续约"])
        )

        let outcome = try await repository.tripOutcome(tripId: trip.id)
        XCTAssertEqual(outcome.visits?["COMPLETED"], 1)
        XCTAssertEqual(outcome.recordOutcomes?["MET"], 1)
        XCTAssertEqual(outcome.visitLabels?["COMPLETED"], "已完成")
    }

    func testLocalPlanDiffDetectsChanges() async throws {
        let repository = makeRepository()
        let trip = try await repository.createTrip(makeRequest(name: "diff 行程"))

        func plan(id: String, version: Int, stops: [(String, Int, Int)]) -> PlanVersionDetail {
            let days = stops.map { visitId, day, seq in
                PlanDay(
                    date: "2026-11-20",
                    stops: [
                        PlanStop(
                            visitId: visitId, sequence: seq, placeId: nil, placeName: nil,
                            city: nil, countryCode: nil, latitude: nil, longitude: nil,
                            arrive: Date(timeIntervalSince1970: Double(day * 3600 + seq * 60)),
                            depart: Date(timeIntervalSince1970: Double(day * 3600 + seq * 60 + 1800)),
                            travelMinutes: 30, distanceKm: nil
                        )
                    ]
                )
            }
            return PlanVersionDetail(
                id: id, tripId: trip.id, versionNumber: version, status: .draft,
                solver: "heuristic", feasible: true, degraded: false, objectiveValue: 100,
                problemHash: "hash", createdAt: Date(), validation: nil, unplaced: nil,
                days: days, legs: nil, solutionStatus: "FEASIBLE",
                costTotal: nil, costCurrency: nil, costUnknownCount: nil, policyViolations: nil
            )
        }

        let baseId = UUID().uuidString
        let targetId = UUID().uuidString
        try database.upsert(
            table: "plan_versions", id: baseId,
            columns: ["trip_id": trip.id, "version": 1, "status": "DRAFT", "created_at": "2026-11-20T00:00:00Z"],
            payload: try DateCoding.encode(plan(id: baseId, version: 1, stops: [("v1", 1, 1), ("v2", 1, 2)]))
        )
        try database.upsert(
            table: "plan_versions", id: targetId,
            columns: ["trip_id": trip.id, "version": 2, "status": "DRAFT", "created_at": "2026-11-21T00:00:00Z"],
            payload: try DateCoding.encode(plan(id: targetId, version: 2, stops: [("v2", 1, 1), ("v3", 2, 1)]))
        )

        let planRepository = LocalPlanRepository(database: database)
        let diff = try await planRepository.planDiff(tripId: trip.id, planId: targetId, against: baseId)
        let changes = Dictionary(uniqueKeysWithValues: diff.stopList.map { ($0.visitId, $0.change) })
        XCTAssertEqual(changes["v1"], .removed)
        XCTAssertEqual(changes["v2"], .moved)   // 日/序号变化
        XCTAssertEqual(changes["v3"], .added)
        XCTAssertEqual(diff.summary?.added, 1)
        XCTAssertEqual(diff.summary?.removed, 1)
        XCTAssertEqual(diff.summary?.moved, 1)
    }

    // MARK: - H2.12：本地拜访跟进闭环


    func testLocalVisitFollowUpsRequireDecisionAndAppearInActionCenter() async throws {
        let (visit, repository) = try await makeLocalFollowUpFixture()
        let reopened = try await exerciseLocalFollowUpLifecycle(
            repository: repository,
            visit: visit
        )
        let center = try await LocalActionCenterRepository(database: database)
            .actionCenter(tripId: visit.tripId)
        let preparation = try XCTUnwrap(
            center.sections.first { $0.key == "PREPARATION" }
        )
        XCTAssertTrue(preparation.items.contains {
            $0.kind == "VISIT_FOLLOW_UP" && $0.entityId == reopened.id && $0.status == "OPEN"
        })
    }

    private func makeLocalFollowUpFixture() async throws -> (
        visit: TripVisit,
        repository: LocalVisitRepository
    ) {
        let trip = try await makeRepository().createTrip(makeRequest(name: "本地跟进闭环"))
        let repository = LocalVisitRepository(database: database)
        let visit = try await repository.createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                placeName: "Berlin Buyer",
                city: "Berlin",
                countryCode: "DE",
                title: "商业条款复核",
                serviceMinutes: 60
            )
        )
        _ = try await repository.submitRecord(
            visitId: visit.id,
            request: VisitRecordRequest(
                clientRecordId: VisitRecordRequest.newClientRecordId(),
                outcome: .met,
                notes: "客户要求补充交付和质保材料。",
                followUpTasks: [
                    FollowUpTask(title: "发送工厂产能证明", dueDate: "2026-12-01"),
                    FollowUpTask(title: "确认延长质保条件", dueDate: "2026-12-03")
                ]
            )
        )
        return (visit, repository)
    }

    private func exerciseLocalFollowUpLifecycle(
        repository: LocalVisitRepository,
        visit: TripVisit
    ) async throws -> VisitFollowUp {
        let initial = try await repository.followUps(visitId: visit.id)
        XCTAssertTrue(initial.items.isEmpty)
        XCTAssertEqual(Set(initial.suggestions.map(\.title)), [
            "发送工厂产能证明", "确认延长质保条件"
        ])
        let acceptSuggestion = try XCTUnwrap(
            initial.suggestions.first { $0.title == "发送工厂产能证明" }
        )
        let dismissSuggestion = try XCTUnwrap(
            initial.suggestions.first { $0.title == "确认延长质保条件" }
        )
        let accepted = try await repository.decideFollowUp(
            visitId: visit.id, request: .accept(acceptSuggestion)
        )
        let dismissed = try await repository.decideFollowUp(
            visitId: visit.id, request: .dismiss(dismissSuggestion)
        )
        XCTAssertEqual(accepted.status, "OPEN")
        XCTAssertEqual(dismissed.status, "DISMISSED")
        XCTAssertEqual(accepted.events.map(\.eventType), ["ACCEPTED"])
        XCTAssertEqual(dismissed.events.map(\.eventType), ["DISMISSED"])

        let afterDecision = try await repository.followUps(visitId: visit.id)
        XCTAssertTrue(afterDecision.suggestions.isEmpty)
        XCTAssertEqual(afterDecision.items.count, 2)

        let started = try await repository.updateFollowUp(
            id: accepted.id,
            request: VisitFollowUpUpdateRequest(status: "IN_PROGRESS")
        )
        XCTAssertEqual(started.status, "IN_PROGRESS")
        let completed = try await repository.updateFollowUp(
            id: accepted.id,
            request: VisitFollowUpUpdateRequest(status: "COMPLETED")
        )
        XCTAssertEqual(completed.status, "COMPLETED")
        XCTAssertNotNil(completed.completedAt)

        do {
            _ = try await repository.updateFollowUp(
                id: accepted.id,
                request: VisitFollowUpUpdateRequest(status: "OPEN")
            )
            XCTFail("重新打开已完成事项必须填写原因")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "VALIDATION_ERROR")
        }

        let reopened = try await repository.updateFollowUp(
            id: accepted.id,
            request: VisitFollowUpUpdateRequest(
                status: "OPEN",
                note: "客户要求调整报价后再确认交付计划。"
            )
        )
        XCTAssertEqual(reopened.status, "OPEN")
        XCTAssertEqual(reopened.events.map(\.eventType), [
            "ACCEPTED", "STARTED", "COMPLETED", "REOPENED"
        ])
        XCTAssertEqual(reopened.events.last?.note, "客户要求调整报价后再确认交付计划。")
        return reopened
    }

}
