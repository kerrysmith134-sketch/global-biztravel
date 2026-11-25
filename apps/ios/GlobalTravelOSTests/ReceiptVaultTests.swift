import XCTest
@testable import GlobalTravelOS

final class ReceiptVaultTests: XCTestCase {
    private var temporaryDirectory: URL!

    override func setUpWithError() throws {
        temporaryDirectory = FileManager.default.temporaryDirectory
            .appendingPathComponent("receipt-vault-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(
            at: temporaryDirectory,
            withIntermediateDirectories: true
        )
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: temporaryDirectory)
    }

    func testLocalReceiptPersistsExactOriginalAndPrivateMetadata() throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: false
        )
        let original = pngFixture()
        let asset = try vault.capture(
            tripId: "trip-local-1",
            draft: ReceiptDraft(filename: "../../invoice.png", data: original)
        )

        XCTAssertEqual(asset.tripId, "trip-local-1")
        XCTAssertEqual(asset.mimeType, "image/png")
        XCTAssertEqual(asset.byteLength, original.count)
        XCTAssertEqual(asset.uploadState, .localOnly)
        XCTAssertNil(asset.serverAssetId)
        XCTAssertEqual(asset.originalFilename, "invoice.png")
        XCTAssertEqual(try vault.originalData(assetId: asset.id), original)
        XCTAssertEqual(try vault.assets(tripId: "trip-local-1").map(\.id), [asset.id])

        // Binary data lives in the private file vault; SQLite only stores an opaque relative key.
        XCTAssertFalse(asset.storageKey.hasPrefix("/"))
        XCTAssertFalse(asset.storageKey.contains(".."))
        let metadataDatabase = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("receipt-vault.sqlite3")
        )
        XCTAssertGreaterThanOrEqual(
            try metadataDatabase.count(table: "schema_migrations"),
            7
        )
    }

    func testSameOriginalIsDeduplicatedPerTripAndNeverAcrossTrips() throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: false
        )
        let draft = ReceiptDraft(filename: "receipt.png", data: pngFixture())

        let first = try vault.capture(tripId: "trip-a", draft: draft)
        let repeated = try vault.capture(tripId: "trip-a", draft: draft)
        let otherTrip = try vault.capture(tripId: "trip-b", draft: draft)

        XCTAssertEqual(first.id, repeated.id)
        XCTAssertNotEqual(first.id, otherTrip.id)
        XCTAssertEqual(try vault.assets(tripId: "trip-a").count, 1)
        XCTAssertEqual(try vault.assets(tripId: "trip-b").count, 1)
    }

    func testLocalExpenseReportPinsVerifiedReceiptMetadata() async throws {
        let mainDatabase = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("trip-store.sqlite3")
        )
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory.appendingPathComponent("receipt-vault"),
            client: nil,
            uploadsEnabled: false
        )
        let repository = LocalTripRepository(database: mainDatabase, receiptVault: vault)
        let trip = try await repository.createTrip(
            CreateTripRequest(
                name: "报销票据快照",
                purpose: "客户拜访",
                originPlace: PlaceRef(
                    name: "Berlin",
                    countryCode: "DE",
                    timezone: "Europe/Berlin",
                    latitude: 52.52,
                    longitude: 13.405
                ),
                window: TripWindow(
                    start: Date(timeIntervalSinceNow: 86_400),
                    end: Date(timeIntervalSinceNow: 172_800)
                )
            )
        )
        let original = pngFixture()
        let asset = try vault.capture(
            tripId: trip.id,
            draft: ReceiptDraft(filename: "hotel-invoice.png", data: original)
        )
        _ = try await repository.createExpense(
            tripId: trip.id,
            body: TripExpenseCreateRequest(
                category: .lodging,
                amount: Decimal(string: "120.00")!,
                currency: "EUR",
                occurredOn: ISO8601.dayString(from: Date()),
                note: "客户现场住宿",
                receiptAssetId: asset.id
            )
        )

        let report = try await repository.createExpenseReport(tripId: trip.id)
        let item = try XCTUnwrap(report.itemList.first)
        XCTAssertEqual(item.receiptAssetId, asset.id)
        XCTAssertEqual(item.receiptSha256, asset.sha256)
        XCTAssertEqual(item.receiptOriginalFilename, "hotel-invoice.png")
        XCTAssertEqual(item.receiptMimeType, "image/png")
        XCTAssertEqual(item.receiptByteLength, original.count)

        let reloaded = try await repository.expenseReports(tripId: trip.id)
        let persistedItem = try XCTUnwrap(reloaded.first?.itemList.first)
        XCTAssertEqual(persistedItem, item)
    }

    func testLocalExpenseReportRejectsCorruptedReceiptBeforeSnapshot() async throws {
        let mainDatabase = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("trip-store.sqlite3")
        )
        let vaultDirectory = temporaryDirectory.appendingPathComponent("receipt-vault", isDirectory: true)
        let vault = try ReceiptVault.open(
            directory: vaultDirectory,
            client: nil,
            uploadsEnabled: false
        )
        let repository = LocalTripRepository(database: mainDatabase, receiptVault: vault)
        let trip = try await repository.createTrip(
            CreateTripRequest(
                name: "票据损坏阻断快照",
                purpose: "客户拜访",
                originPlace: PlaceRef(
                    name: "Berlin",
                    countryCode: "DE",
                    timezone: "Europe/Berlin",
                    latitude: 52.52,
                    longitude: 13.405
                ),
                window: TripWindow(
                    start: Date(timeIntervalSinceNow: 86_400),
                    end: Date(timeIntervalSinceNow: 172_800)
                )
            )
        )
        let asset = try vault.capture(
            tripId: trip.id,
            draft: ReceiptDraft(filename: "receipt.png", data: pngFixture())
        )
        _ = try await repository.createExpense(
            tripId: trip.id,
            body: TripExpenseCreateRequest(
                category: .meals,
                amount: Decimal(string: "24.00")!,
                currency: "EUR",
                occurredOn: ISO8601.dayString(from: Date()),
                note: "客户午餐",
                receiptAssetId: asset.id
            )
        )
        let originalURL = vaultDirectory
            .appendingPathComponent("receipt-vault", isDirectory: true)
            .appendingPathComponent(asset.storageKey)
        try Data("damaged".utf8).write(to: originalURL, options: .atomic)

        do {
            _ = try await repository.createExpenseReport(tripId: trip.id)
            XCTFail("原始票据损坏时不得创建报销证据快照")
        } catch {
            XCTAssertEqual(error as? ReceiptVaultError, .integrityCheckFailed)
        }
        XCTAssertEqual(
            try mainDatabase.count(
                table: "expense_reports",
                where: "trip_id = ?",
                bindings: [trip.id]
            ),
            0
        )
    }

    func testReceiptVerificationRequiresOwningTrip() throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: false
        )
        let asset = try vault.capture(
            tripId: "trip-owner",
            draft: ReceiptDraft(filename: "receipt.png", data: pngFixture())
        )

        XCTAssertNoThrow(try vault.verifyOriginal(assetId: asset.id, tripId: "trip-owner"))
        XCTAssertThrowsError(
            try vault.verifyOriginal(assetId: asset.id, tripId: "another-trip")
        ) { error in
            XCTAssertEqual(error as? ReceiptVaultError, .assetTripMismatch)
        }
    }

    func testUnsupportedAndEmptyFilesAreRejectedBeforePersistence() throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: false
        )

        XCTAssertThrowsError(
            try vault.capture(
                tripId: "trip-a",
                draft: ReceiptDraft(filename: "fake.jpg", data: Data("not an image".utf8))
            )
        )
        XCTAssertThrowsError(
            try vault.capture(
                tripId: "trip-a",
                draft: ReceiptDraft(filename: "empty.png", data: Data())
            )
        )
        XCTAssertTrue(try vault.assets(tripId: "trip-a").isEmpty)
    }

    func testCloudUploadFailureKeepsOriginalInPendingState() async throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: true
        )
        let draft = ReceiptDraft(filename: "receipt.png", data: pngFixture())
        let localAsset = try vault.capture(tripId: "trip-cloud", draft: draft)

        XCTAssertEqual(localAsset.uploadState, .pendingUpload)
        XCTAssertNil(localAsset.expenseReceiptAssetId)

        do {
            _ = try await vault.prepareForExpense(assetId: localAsset.id)
            XCTFail("Cloud expense attachment must require a server-issued Receipt Asset ID")
        } catch {
            XCTAssertEqual(error as? ReceiptVaultError, .uploadUnavailable)
        }

        let afterFailure = try XCTUnwrap(vault.assets(tripId: "trip-cloud").first)
        XCTAssertEqual(afterFailure.id, localAsset.id)
        XCTAssertEqual(afterFailure.uploadState, .pendingUpload)
        XCTAssertNil(afterFailure.serverAssetId)
        XCTAssertNotNil(afterFailure.lastError)
        XCTAssertEqual(try vault.originalData(assetId: localAsset.id), draft.data)
    }

    func testLocalCloseoutFlagsCorruptedOriginalReceipt() async throws {
        let database = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("trip-local.sqlite3")
        )
        let vaultDirectory = temporaryDirectory.appendingPathComponent("vault-store", isDirectory: true)
        let vault = try ReceiptVault.open(
            directory: vaultDirectory,
            client: nil,
            uploadsEnabled: false
        )
        let repository = LocalTripRepository(database: database, receiptVault: vault)
        let trip = try await repository.createTrip(
            CreateTripRequest(
                name: "票据完整性校验",
                purpose: "客户拜访",
                originPlace: PlaceRef(
                    name: "Berlin",
                    countryCode: "DE",
                    timezone: "Europe/Berlin",
                    latitude: 52.52,
                    longitude: 13.405
                ),
                window: TripWindow(
                    start: Date(timeIntervalSinceNow: 86_400),
                    end: Date(timeIntervalSinceNow: 172_800)
                )
            )
        )
        let asset = try vault.capture(
            tripId: trip.id,
            draft: ReceiptDraft(filename: "receipt.png", data: pngFixture())
        )
        let expense = try await repository.createExpense(
            tripId: trip.id,
            body: TripExpenseCreateRequest(
                category: .meals,
                amount: Decimal(string: "12.50")!,
                currency: "EUR",
                occurredOn: ISO8601.dayString(from: Date()),
                note: "客户午餐",
                receiptAssetId: asset.expenseReceiptAssetId
            )
        )
        XCTAssertEqual(expense.receiptAssetId, asset.id)

        let valid = try await repository.closeoutReadiness(tripId: trip.id)
        XCTAssertEqual(valid.summary.receiptAssetUnavailableCount, 0)
        XCTAssertEqual(valid.summary.expensesWithoutReceiptCount, 0)

        let originalURL = vaultDirectory
            .appendingPathComponent("receipt-vault", isDirectory: true)
            .appendingPathComponent(asset.storageKey)
        try Data("corrupted-original".utf8).write(to: originalURL, options: .atomic)

        let invalid = try await repository.closeoutReadiness(tripId: trip.id)
        XCTAssertEqual(invalid.summary.receiptAssetUnavailableCount, 1)
        XCTAssertTrue(invalid.warnings.contains { $0.code == "RECEIPT_UNAVAILABLE" })
    }


    func testReceiptOCRModelsDecodeCandidatesAndEncodeExplicitReview() throws {
        let payload = Data("""
        {
          "id": "33333333-3333-4333-8333-333333333333",
          "receipt_asset_id": "44444444-4444-4444-8444-444444444444",
          "agent_run_id": "55555555-5555-4555-8555-555555555555",
          "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "engine": "tesseract",
          "engine_version": "tesseract 5.4.1",
          "status": "SUCCEEDED",
          "candidates": {
            "merchant_hint": "Berlin Hotel",
            "amount_candidates": ["EUR 128.50", "128.50"],
            "date_candidates": ["2026-11-25"],
            "text_length": 238
          },
          "started_at": "2026-11-25T08:00:00Z",
          "finished_at": "2026-11-25T08:00:01Z",
          "created_at": "2026-11-25T08:00:00Z",
          "review": null
        }
        """.utf8)

        let run = try DateCoding.decode(ReceiptOcrRun.self, from: payload)
        XCTAssertEqual(run.status, "SUCCEEDED")
        XCTAssertEqual(run.candidates.merchantHint, "Berlin Hotel")
        XCTAssertEqual(run.candidates.amountCandidates, ["EUR 128.50", "128.50"])
        XCTAssertEqual(run.candidates.dateCandidates, ["2026-11-25"])
        XCTAssertNil(run.review)

        let request = ReceiptOcrReviewRequest(
            decision: "CONFIRMED",
            merchant: "Berlin Hotel",
            amount: Decimal(string: "128.50"),
            currency: "EUR",
            occurredOn: "2026-11-25"
        )
        let encoded = try DateCoding.encode(request)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: encoded) as? [String: Any])
        XCTAssertEqual(object["decision"] as? String, "CONFIRMED")
        XCTAssertEqual(object["merchant"] as? String, "Berlin Hotel")
        XCTAssertEqual(object["currency"] as? String, "EUR")
        XCTAssertEqual(object["occurred_on"] as? String, "2026-11-25")
        XCTAssertNotNil(object["amount"])
    }

    func testFailedReceiptOCRRunCanDecodeEmptyCandidatePayload() throws {
        let payload = Data("""
        {
          "id": "33333333-3333-4333-8333-333333333333",
          "receipt_asset_id": "44444444-4444-4444-8444-444444444444",
          "agent_run_id": "55555555-5555-4555-8555-555555555555",
          "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "engine": "tesseract",
          "engine_version": "unavailable",
          "status": "FAILED",
          "candidates": {},
          "error": "tesseract unavailable",
          "started_at": "2026-11-25T08:00:00Z",
          "finished_at": "2026-11-25T08:00:01Z",
          "created_at": "2026-11-25T08:00:00Z",
          "review": null
        }
        """.utf8)

        let run = try DateCoding.decode(ReceiptOcrRun.self, from: payload)
        XCTAssertEqual(run.status, "FAILED")
        XCTAssertTrue(run.candidates.amountCandidates.isEmpty)
        XCTAssertTrue(run.candidates.dateCandidates.isEmpty)
        XCTAssertEqual(run.error, "tesseract unavailable")
    }

    private func pngFixture() -> Data {
        Data([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x00
        ])
    }
}
