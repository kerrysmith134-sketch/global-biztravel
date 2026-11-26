import XCTest
@testable import GlobalTravelOS

final class LocalSpreadsheetImportTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var database: LocalDatabase!

    // A tiny OOXML workbook containing one valid row, one duplicate and one blocking row.
    private let workbookBase64 = """
    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
    AQAANgcAAAAA

    """

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("local-import-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: temporaryDirectory, withIntermediateDirectories: true)
        database = try LocalDatabase.open(path: temporaryDirectory.appendingPathComponent("test.sqlite3"))
    }

    override func tearDownWithError() throws {
        database = nil
        try? FileManager.default.removeItem(at: temporaryDirectory)
    }

    func testTwoPhaseImportPersistsOnlyStagingRowsUntilConfirmation() async throws {
        let repository = LocalTripRepository(database: database)
        let trip = try await makeTestTrip(repository, name: "Excel 导入验收")
        let originalPlaceCount = try database.count(table: "places")
        let originalVersion = trip.version
        let preview = try await parseWorkbook(repository, tripId: trip.id)

        XCTAssertEqual(preview.status, "PARSED")
        XCTAssertEqual(preview.totalRows, 3)
        XCTAssertEqual(preview.validRows, 1)
        XCTAssertEqual(preview.duplicateRows, 1)
        XCTAssertEqual(preview.invalidRows, 1)
        XCTAssertEqual(preview.rowList.map(\.rowNumber), [2, 3, 4])
        XCTAssertEqual(preview.rowList[0].status, .valid)
        XCTAssertEqual(preview.rowList[1].status, .duplicate)
        XCTAssertTrue(preview.rowList[1].status.selectableForImport)
        XCTAssertEqual(preview.rowList[2].status, .invalid)
        XCTAssertFalse(preview.rowList[2].status.selectableForImport)
        XCTAssertEqual(preview.importableRowNumbers, [2])

        XCTAssertEqual(try database.count(table: "customers"), 0)
        XCTAssertEqual(try database.count(table: "places"), originalPlaceCount)
        XCTAssertEqual(try database.count(table: "visits"), 0)
        XCTAssertNotNil(database.keyValue("local-import:\(preview.importId)"))

        let confirmed = try await repository.confirmImport(
            tripId: trip.id,
            request: ConfirmImportRequest(importId: preview.importId, acceptedRowNumbers: [2, 3, 4])
        )
        XCTAssertEqual(confirmed.status, "CONFIRMED")
        XCTAssertEqual(confirmed.acceptedRows, 2)
        XCTAssertEqual(confirmed.rejectedRows, 1)
        XCTAssertEqual(confirmed.createdCustomers, 1)
        XCTAssertEqual(confirmed.reusedCustomers, 1)
        XCTAssertEqual((confirmed.createdPlaces ?? 0) + (confirmed.reusedPlaces ?? 0), 2)
        XCTAssertEqual(confirmed.createdVisits, 2)
        XCTAssertEqual(try database.count(table: "customers"), 1)
        XCTAssertEqual(
            try database.count(table: "places"),
            originalPlaceCount + (confirmed.createdPlaces ?? 0)
        )
        XCTAssertEqual(try database.count(table: "visits"), 2)
        XCTAssertGreaterThanOrEqual(try database.count(table: "customer_places"), 1)
        try assertConfirmedBatchWasScrubbed(preview.importId)
        try assertImportedVisit(tripId: trip.id)

        let updatedTrip = try await repository.trip(id: trip.id)
        XCTAssertEqual(updatedTrip.version, originalVersion + 1)
        let replay = await confirmationOutcome(
            repository,
            tripId: trip.id,
            request: ConfirmImportRequest(importId: preview.importId, acceptedRowNumbers: [2])
        )
        XCTAssertEqual(replay, "IMPORT_ALREADY_CONFIRMED")
    }

    func testRejectedOnlyConfirmationStillInvalidatesPlan() async throws {
        let repository = LocalTripRepository(database: database)
        let trip = try await makeTestTrip(repository, name: "拒绝行导入验收")
        let preview = try await parseWorkbook(
            repository,
            tripId: trip.id,
            filename: "customers-rejected-only.xlsx"
        )

        let result = try await repository.confirmImport(
            tripId: trip.id,
            request: ConfirmImportRequest(importId: preview.importId, acceptedRowNumbers: [4])
        )
        XCTAssertEqual(result.acceptedRows, 0)
        XCTAssertEqual(result.rejectedRows, 1)
        XCTAssertEqual(try database.count(table: "visits"), 0)

        let updatedTrip = try await repository.trip(id: trip.id)
        XCTAssertEqual(updatedTrip.version, trip.version + 1)
    }

    func testConcurrentImportConfirmationOnlyCommitsOnce() async throws {
        let repository = LocalTripRepository(database: database)
        let trip = try await makeTestTrip(repository, name: "并发导入验收")
        let preview = try await parseWorkbook(repository, tripId: trip.id)
        let request = ConfirmImportRequest(importId: preview.importId, acceptedRowNumbers: [2])

        async let first = confirmationOutcome(repository, tripId: trip.id, request: request)
        async let second = confirmationOutcome(repository, tripId: trip.id, request: request)
        let outcomes = await [first, second]

        XCTAssertEqual(outcomes.filter { $0 == "CONFIRMED" }.count, 1)
        XCTAssertEqual(outcomes.filter { $0 == "IMPORT_ALREADY_CONFIRMED" }.count, 1)
        XCTAssertEqual(try database.count(table: "customers"), 1)
        XCTAssertEqual(try database.count(table: "visits"), 1)
        let updatedTrip = try await repository.trip(id: trip.id)
        XCTAssertEqual(updatedTrip.version, trip.version + 1)
    }

    private func makeTestTrip(_ repository: LocalTripRepository, name: String) async throws -> Trip {
        try await repository.createTrip(CreateTripRequest(
            name: name,
            purpose: "客户拜访",
            originPlace: PlaceRef(
                name: "Shanghai",
                countryCode: "CN",
                timezone: "Asia/Shanghai",
                latitude: 31.23,
                longitude: 121.47
            ),
            window: TripWindow(
                start: Date(timeIntervalSinceNow: 86_400),
                end: Date(timeIntervalSinceNow: 86_400 * 3)
            )
        ))
    }

    private func parseWorkbook(
        _ repository: LocalTripRepository,
        tripId: String,
        filename: String = "customers.xlsx"
    ) async throws -> ImportParseResult {
        let workbook = try XCTUnwrap(
            Data(base64Encoded: workbookBase64, options: .ignoreUnknownCharacters)
        )
        return try await repository.parseImport(
            tripId: tripId,
            fileData: workbook,
            filename: filename
        )
    }

    private func confirmationOutcome(
        _ repository: LocalTripRepository,
        tripId: String,
        request: ConfirmImportRequest
    ) async -> String {
        do {
            let result = try await repository.confirmImport(tripId: tripId, request: request)
            return result.status ?? "UNKNOWN"
        } catch let error as APIError {
            return error.errorCode ?? "API_ERROR"
        } catch {
            return "UNEXPECTED_ERROR"
        }
    }

    private func assertConfirmedBatchWasScrubbed(_ importId: String) throws {
        let storedBatch = try XCTUnwrap(database.keyValue("local-import:\(importId)"))
        XCTAssertFalse(storedBatch.contains("Acme Battery GmbH"))
        let batchJSON = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(storedBatch.utf8)) as? [String: Any]
        )
        let storedResult = try XCTUnwrap(batchJSON["result"] as? [String: Any])
        XCTAssertEqual((storedResult["rows"] as? [Any])?.count, 0)
        let stagedRows = try XCTUnwrap(batchJSON["rows"] as? [[String: Any]])
        XCTAssertTrue(stagedRows.allSatisfy { $0["normalized"] == nil })
    }

    private func assertImportedVisit(tripId: String) throws {
        let visitPayloads = try database.loadPayloads(
            table: "visits",
            where: "trip_id = ?",
            bindings: [tripId]
        )
        let visits = try visitPayloads.map {
            try DateCoding.decoder().decode(TripVisit.self, from: $0)
        }
        let visit = try XCTUnwrap(visits.first { $0.title == "拜访 Acme Battery GmbH" })
        XCTAssertEqual(visit.serviceMinutes, 60)
        XCTAssertEqual(visit.priority, 1)
        XCTAssertEqual(visit.businessValue ?? 0, 150_000, accuracy: 0.001)
        XCTAssertEqual(visit.isImmovable, true)
        XCTAssertEqual(visit.movementMode, "GROUND")
        XCTAssertEqual(visit.status, "PLANNED")
        XCTAssertNotNil(visit.timeWindowStart)
        XCTAssertNotNil(visit.timeWindowEnd)
    }


    func testExpiredPreviewIsScrubbedAndCannotBeConfirmed() async throws {
        let repository = LocalTripRepository(database: database)
        let trip = try await makeTestTrip(repository, name: "过期导入预览")
        let preview = try await parseWorkbook(repository, tripId: trip.id)
        let storageKey = "local-import:\(preview.importId)"
        let raw = try XCTUnwrap(database.keyValue(storageKey))
        var payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any]
        )
        payload["expires_at"] = ISO8601.stringWithFractionalSeconds(
            from: Date(timeIntervalSince1970: 0)
        )
        let expiredData = try JSONSerialization.data(withJSONObject: payload)
        try database.setKeyValue(
            storageKey,
            value: try XCTUnwrap(String(data: expiredData, encoding: .utf8))
        )

        // This is also called at local-mode app startup, without requiring another import.
        try repository.purgeExpiredImportPreviews()

        do {
            _ = try await repository.confirmImport(
                tripId: trip.id,
                request: ConfirmImportRequest(
                    importId: preview.importId,
                    acceptedRowNumbers: [2]
                )
            )
            XCTFail("过期预览不得确认入库")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "IMPORT_EXPIRED")
        }

        let stored = try XCTUnwrap(database.keyValue(storageKey))
        XCTAssertFalse(stored.contains("Acme Battery GmbH"))
        let tombstone = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(stored.utf8)) as? [String: Any]
        )
        XCTAssertEqual(tombstone["status"] as? String, "EXPIRED")
        XCTAssertEqual(tombstone["filename"] as? String, "")
        let result = try XCTUnwrap(tombstone["result"] as? [String: Any])
        XCTAssertEqual((result["rows"] as? [Any])?.count, 0)
        XCTAssertNil(result["filename"])
        let rows = try XCTUnwrap(tombstone["rows"] as? [Any])
        XCTAssertEqual(rows.count, 0)

        XCTAssertEqual(try database.count(table: "customers"), 0)
        XCTAssertEqual(try database.count(table: "visits"), 0)
    }

    func testLegacyAndCorruptFilesAreRejectedBeforeStaging() async throws {
        let repository = LocalTripRepository(database: database)
        let trip = try await repository.createTrip(CreateTripRequest(
            name: "导入文件校验",
            purpose: "客户拜访",
            originPlace: PlaceRef(name: "Shanghai", countryCode: "CN", timezone: "Asia/Shanghai", latitude: 31.23, longitude: 121.47),
            window: TripWindow(start: Date(timeIntervalSinceNow: 86_400), end: Date(timeIntervalSinceNow: 172_800))
        ))
        do {
            _ = try await repository.parseImport(tripId: trip.id, fileData: Data([1]), filename: "legacy.xls")
            XCTFail("Legacy .xls must be rejected")
        } catch let error as APIError {
            XCTAssertNotNil(error.message as String?)
        }
        do {
            _ = try await repository.parseImport(tripId: trip.id, fileData: Data([1, 2]), filename: "broken.xlsx")
            XCTFail("Corrupt workbook must be rejected")
        } catch let error as APIError {
            XCTAssertEqual(try database.count(table: "customers"), 0)
            XCTAssertEqual(try database.count(table: "visits"), 0)
            XCTAssertNotNil(error.message as String?)
        }
    }
}
