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
            _ = try await vault.prepareForExpense(tripId: "trip-cloud", draft: draft)
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

    private func pngFixture() -> Data {
        Data([
            0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
            0x00, 0x00, 0x00, 0x00
        ])
    }
}
