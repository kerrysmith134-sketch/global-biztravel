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
            9
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

    func testTripExpenseOutboxPersistsStableKeyAndRetryMetadataAcrossReopen() throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: true
        )
        let outbox = vault.makeTripExpenseOutbox()
        let request = TripExpenseCreateRequest(
            category: .meals,
            amount: Decimal(string: "42.50") ?? .zero,
            currency: "EUR",
            occurredOn: "2026-11-24",
            note: "Dinner receipt",
            receiptAssetId: nil
        )
        let queued = try outbox.enqueue(
            tripId: "trip-offline-expense",
            body: request,
            localReceiptAssetId: "local-receipt-asset"
        )
        try outbox.recordFailure(id: queued.id, message: "Network unavailable")

        // A new SQLite handle simulates app termination and relaunch.
        let reopenedDatabase = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("receipt-vault.sqlite3")
        )
        let reopenedOutbox = TripExpenseOutbox(database: reopenedDatabase)
        let restored = try XCTUnwrap(reopenedOutbox.pending(tripId: queued.tripId).first)

        XCTAssertEqual(restored.id, queued.id)
        XCTAssertEqual(restored.body, request)
        XCTAssertEqual(restored.localReceiptAssetId, "local-receipt-asset")
        XCTAssertEqual(restored.attempts, 1)
        XCTAssertEqual(restored.lastError, "Network unavailable")

        try reopenedOutbox.remove(id: queued.id)
        XCTAssertTrue(try reopenedOutbox.pending(tripId: queued.tripId).isEmpty)
    }

    func testTripExpenseOutboxMaintainsFIFOOrderAcrossRapidEnqueuesAndReopen() throws {
        let vault = try ReceiptVault.open(
            directory: temporaryDirectory,
            client: nil,
            uploadsEnabled: true
        )
        let outbox = vault.makeTripExpenseOutbox()
        let tripId = "trip-fifo-expense"

        func request(_ amount: String) -> TripExpenseCreateRequest {
            TripExpenseCreateRequest(
                category: .meals,
                amount: Decimal(string: amount) ?? .zero,
                currency: "EUR",
                occurredOn: "2026-11-24",
                note: "FIFO order acceptance",
                receiptAssetId: nil
            )
        }

        let first = try outbox.enqueue(
            tripId: tripId,
            body: request("10.00"),
            localReceiptAssetId: nil
        )
        let second = try outbox.enqueue(
            tripId: tripId,
            body: request("20.00"),
            localReceiptAssetId: nil
        )
        let third = try outbox.enqueue(
            tripId: tripId,
            body: request("30.00"),
            localReceiptAssetId: nil
        )
        try outbox.recordFailure(id: second.id, message: "Temporary network failure")

        let expectedOrder = [first.id, second.id, third.id]
        XCTAssertEqual(try outbox.pending(tripId: tripId).map(\.id), expectedOrder)

        // A new database handle simulates termination/relaunch without changing FIFO positions.
        let reopenedDatabase = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("receipt-vault.sqlite3")
        )
        let reopenedOutbox = TripExpenseOutbox(database: reopenedDatabase)
        XCTAssertEqual(try reopenedOutbox.pending(tripId: tripId).map(\.id), expectedOrder)

        try reopenedOutbox.remove(id: second.id)
        let fourth = try reopenedOutbox.enqueue(
            tripId: tripId,
            body: request("40.00"),
            localReceiptAssetId: nil
        )
        XCTAssertEqual(
            try reopenedOutbox.pending(tripId: tripId).map(\.id),
            [first.id, third.id, fourth.id]
        )
    }

    private func pngFixture() -> Data {
        Data([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x00
        ])
    }
}
