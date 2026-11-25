import XCTest
@testable import GlobalTravelOS

final class LocalSpreadsheetImportTests: XCTestCase {
    private var temporaryDirectory: URL!
    private var database: LocalDatabase!

    // A tiny OOXML workbook containing one valid row, one duplicate and one blocking row.
    private let workbookBase64 = """
    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
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
        let trip = try await repository.createTrip(CreateTripRequest(
            name: "Excel 导入验收",
            purpose: "客户拜访",
            originPlace: PlaceRef(
                name: "Shanghai", countryCode: "CN", timezone: "Asia/Shanghai",
                latitude: 31.23, longitude: 121.47
            ),
            window: TripWindow(
                start: Date(timeIntervalSinceNow: 86_400),
                end: Date(timeIntervalSinceNow: 86_400 * 3)
            )
        ))
        let originalPlaceCount = try database.count(table: "places")
        let originalVersion = trip.version
        let workbook = try XCTUnwrap(Data(base64Encoded: workbookBase64, options: .ignoreUnknownCharacters))

        let preview = try await repository.parseImport(
            tripId: trip.id,
            fileData: workbook,
            filename: "customers.xlsx"
        )
        XCTAssertEqual(preview.status, "PARSED")
        XCTAssertEqual(preview.totalRows, 3)
        XCTAssertEqual(preview.validRows, 1)
        XCTAssertEqual(preview.duplicateRows, 1)
        XCTAssertEqual(preview.invalidRows, 1)
        XCTAssertEqual(preview.rowList.map(\.rowNumber), [2, 3, 4])
        XCTAssertEqual(preview.rowList[0].status, .valid)
        XCTAssertEqual(preview.rowList[1].status, .duplicate)
        XCTAssertEqual(preview.rowList[2].status, .invalid)
        XCTAssertEqual(preview.importableRowNumbers, [2])

        XCTAssertEqual(try database.count(table: "customers"), 0)
        XCTAssertEqual(try database.count(table: "places"), originalPlaceCount)
        XCTAssertEqual(try database.count(table: "visits"), 0)
        XCTAssertNotNil(database.keyValue("local-import:\(preview.importId)"))

        let confirmed = try await repository.confirmImport(
            tripId: trip.id,
            request: ConfirmImportRequest(importId: preview.importId, acceptedRowNumbers: [2, 4])
        )
        XCTAssertEqual(confirmed.status, "CONFIRMED")
        XCTAssertEqual(confirmed.acceptedRows, 1)
        XCTAssertEqual(confirmed.rejectedRows, 1)
        XCTAssertEqual(confirmed.createdCustomers, 1)
        XCTAssertEqual(confirmed.createdPlaces, 1)
        XCTAssertEqual(confirmed.createdVisits, 1)
        XCTAssertEqual(try database.count(table: "customers"), 1)
        XCTAssertEqual(try database.count(table: "places"), originalPlaceCount + 1)
        XCTAssertEqual(try database.count(table: "visits"), 1)
        XCTAssertEqual(try database.count(table: "customer_places"), 1)

        let visitPayloads = try database.loadPayloads(table: "visits", where: "trip_id = ?", bindings: [trip.id])
        let visitData = try XCTUnwrap(visitPayloads.first)
        let visit = try DateCoding.decoder().decode(TripVisit.self, from: visitData)
        XCTAssertEqual(visit.title, "拜访 Acme Battery GmbH")
        XCTAssertEqual(visit.serviceMinutes, 60)
        XCTAssertEqual(visit.priority, 1)
        XCTAssertEqual(visit.businessValue ?? 0, 150_000, accuracy: 0.001)
        XCTAssertEqual(visit.isImmovable, true)
        XCTAssertEqual(visit.movementMode, "GROUND")
        XCTAssertEqual(visit.status, "PLANNED")
        XCTAssertNotNil(visit.timeWindowStart)
        XCTAssertNotNil(visit.timeWindowEnd)
        let updatedTrip = try await repository.trip(id: trip.id)
        XCTAssertEqual(updatedTrip.version, originalVersion + 1)

        do {
            _ = try await repository.confirmImport(
                tripId: trip.id,
                request: ConfirmImportRequest(importId: preview.importId, acceptedRowNumbers: [2])
            )
            XCTFail("Confirmed batches must be replay-protected")
        } catch let error as APIError {
            XCTAssertEqual(error.errorCode, "IMPORT_ALREADY_CONFIRMED")
        }
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
