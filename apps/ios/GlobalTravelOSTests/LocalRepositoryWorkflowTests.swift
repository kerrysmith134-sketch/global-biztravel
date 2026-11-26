import XCTest
@testable import GlobalTravelOS

/// Local repository workflow regressions that need a real temporary SQLite database.
final class LocalRepositoryWorkflowTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var database: LocalDatabase!

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("local-workflows-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
        database = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("test.sqlite3")
        )
    }

    override func tearDownWithError() throws {
        database = nil
        try? FileManager.default.removeItem(at: temporaryDirectory)
    }

    private func makeTrip(_ name: String) async throws -> Trip {
        let now = Date()
        return try await LocalTripRepository(database: database).createTrip(
            CreateTripRequest(
                name: name,
                purpose: "本地仓储回归",
                originPlace: PlaceRef(
                    name: "上海浦东",
                    countryCode: "CN",
                    timezone: "Asia/Shanghai",
                    latitude: 31.23,
                    longitude: 121.47
                ),
                window: TripWindow(
                    start: now.addingTimeInterval(86_400),
                    end: now.addingTimeInterval(86_400 * 10)
                )
            )
        )
    }

    func testTripNotFoundShapesServerError() async throws {
        let repository = LocalTripRepository(database: database)
        do {
            _ = try await repository.trip(id: "00000000-0000-4000-8000-999999999999")
            XCTFail("应抛 TRIP_NOT_FOUND")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "TRIP_NOT_FOUND")
        }
    }

    func testLocalPlanGenerationJobIsReadableByReturnedIdentifier() async throws {
        let trip = try await makeTrip("本地计划任务")
        let visits = LocalVisitRepository(database: database)
        _ = try await visits.createVisit(
            tripId: trip.id,
            request: VisitCreateRequest(
                placeName: "Berlin Buyer",
                city: "Berlin",
                countryCode: "DE",
                latitude: 52.52,
                longitude: 13.405,
                timezone: "Europe/Berlin",
                title: "本地计划验收拜访",
                serviceMinutes: 60,
                priority: 1,
                movementMode: "AIR"
            )
        )

        let plans = LocalPlanRepository(database: database)
        let response = try await plans.generatePlan(
            tripId: trip.id,
            request: GeneratePlanRequest()
        )
        let job = try await plans.job(id: response.jobId)

        XCTAssertEqual(job.id, response.jobId)
        XCTAssertNotNil(job.result?.planVersionId)
        XCTAssertGreaterThanOrEqual(
            try database.count(
                table: "plan_versions",
                where: "trip_id = ?",
                bindings: [trip.id]
            ),
            1
        )
    }

    func testLocalPlanDiffDetectsChanges() async throws {
        let trip = try await makeTrip("diff 行程")

        func plan(id: String, version: Int, stops: [(String, Int, Int)]) -> PlanVersionDetail {
            let days = stops.map { visitId, day, sequence in
                PlanDay(
                    date: "2026-11-20",
                    stops: [
                        PlanStop(
                            visitId: visitId,
                            sequence: sequence,
                            placeId: nil,
                            placeName: nil,
                            city: nil,
                            countryCode: nil,
                            latitude: nil,
                            longitude: nil,
                            arrive: Date(timeIntervalSince1970: Double(day * 3600 + sequence * 60)),
                            depart: Date(timeIntervalSince1970: Double(day * 3600 + sequence * 60 + 1800)),
                            travelMinutes: 30,
                            distanceKm: nil
                        )
                    ]
                )
            }
            return PlanVersionDetail(
                id: id,
                tripId: trip.id,
                versionNumber: version,
                status: .draft,
                solver: "heuristic",
                feasible: true,
                degraded: false,
                objectiveValue: 100,
                problemHash: "hash",
                createdAt: Date(),
                validation: nil,
                unplaced: nil,
                days: days,
                legs: nil,
                solutionStatus: "FEASIBLE",
                costTotal: nil,
                costCurrency: nil,
                costUnknownCount: nil,
                policyViolations: nil
            )
        }

        let baseId = UUID().uuidString
        let targetId = UUID().uuidString
        try database.upsert(
            table: "plan_versions",
            id: baseId,
            columns: [
                "trip_id": trip.id,
                "version": 1,
                "status": "DRAFT",
                "created_at": "2026-11-20T00:00:00Z",
            ],
            payload: try DateCoding.encode(
                plan(id: baseId, version: 1, stops: [("v1", 1, 1), ("v2", 1, 2)])
            )
        )
        try database.upsert(
            table: "plan_versions",
            id: targetId,
            columns: [
                "trip_id": trip.id,
                "version": 2,
                "status": "DRAFT",
                "created_at": "2026-11-21T00:00:00Z",
            ],
            payload: try DateCoding.encode(
                plan(id: targetId, version: 2, stops: [("v2", 1, 1), ("v3", 2, 1)])
            )
        )

        let repository = LocalPlanRepository(database: database)
        let diff = try await repository.planDiff(
            tripId: trip.id,
            planId: targetId,
            against: baseId
        )
        let changes = Dictionary(uniqueKeysWithValues: diff.stopList.map { ($0.visitId, $0.change) })
        XCTAssertEqual(changes["v1"], .removed)
        XCTAssertEqual(changes["v2"], .moved)
        XCTAssertEqual(changes["v3"], .added)
        XCTAssertEqual(diff.summary?.added, 1)
        XCTAssertEqual(diff.summary?.removed, 1)
        XCTAssertEqual(diff.summary?.moved, 1)
    }

    func testLocalTripOutcomeAggregatesLocalFacts() async throws {
        let repository = LocalTripRepository(database: database)
        let trip = try await makeTrip("复盘行程")
        let visit = TripVisit(
            id: UUID().uuidString,
            tripId: trip.id,
            placeId: trip.originPlaceId,
            title: "客户拜访",
            objective: nil,
            serviceMinutes: 60,
            timeWindowStart: nil,
            timeWindowEnd: nil,
            priority: 1,
            movementMode: "AIR",
            status: "COMPLETED",
            createdAt: Date()
        )
        try database.upsert(
            table: "visits",
            id: visit.id,
            columns: [
                "trip_id": trip.id,
                "place_id": visit.placeId,
                "title": "客户拜访",
                "status": "COMPLETED",
            ],
            payload: try DateCoding.encode(visit)
        )
        try database.upsert(
            table: "visit_records",
            id: UUID().uuidString,
            columns: ["visit_id": visit.id, "client_record_id": "rec-1"],
            payload: try JSONSerialization.data(
                withJSONObject: ["outcome": "MET", "notes": "确认续约"]
            )
        )

        let outcome = try await repository.tripOutcome(tripId: trip.id)
        XCTAssertEqual(outcome.visits?["COMPLETED"], 1)
        XCTAssertEqual(outcome.recordOutcomes?["MET"], 1)
        XCTAssertEqual(outcome.visitLabels?["COMPLETED"], "已完成")
    }
}
