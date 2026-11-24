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

    func testLocalMigrationDDLAndVersionMarkerRollbackTogether() throws {
        let database = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("migration-atomic.sqlite3")
        )
        let migrationName = "test_atomic_failure"

        XCTAssertThrowsError(
            try database.applyMigration(
                name: migrationName,
                script: """
                CREATE TABLE migration_atomic_probe (id TEXT PRIMARY KEY);
                CREATE TABLE migration_atomic_probe (id TEXT PRIMARY KEY);
                """
            )
        )

        let createdTables = try database.store.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            bindings: ["migration_atomic_probe"]
        ) { $0.string(0) }
        let appliedMarkers = try database.store.query(
            "SELECT name FROM schema_migrations WHERE name = ?",
            bindings: [migrationName]
        ) { $0.string(0) }

        XCTAssertTrue(createdTables.isEmpty, "DDL before a failed statement must roll back")
        XCTAssertTrue(appliedMarkers.isEmpty, "A failed migration must not leave a success marker")
    }

    func testConcurrentOpenAppliesLocalMigrationsExactlyOnce() throws {
        let path = temporaryDirectory.appendingPathComponent("concurrent-migrations.sqlite3")
        let failures = MigrationFailureCollector()

        DispatchQueue.concurrentPerform(iterations: 8) { _ in
            do {
                let database = try LocalDatabase.open(path: path)
                let appliedCount = try database.count(table: "schema_migrations")
                let pendingExpenseColumns = try database.store.query(
                    "PRAGMA table_info(pending_trip_expenses)"
                ) { $0.string(1) }

                if appliedCount != 9 || !pendingExpenseColumns.contains("queue_order") {
                    failures.append(
                        "Expected 9 migrations and queue_order; got count=\(appliedCount), " +
                            "columns=\(pendingExpenseColumns)"
                    )
                }
            } catch {
                failures.append(String(describing: error))
            }
        }

        XCTAssertTrue(
            failures.messages.isEmpty,
            "Concurrent database open failed: \(failures.messages.joined(separator: "; "))"
        )
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

    func testConcurrentCaptureDeduplicatesSameReceiptAcrossDatabaseHandles() throws {
        let directory = temporaryDirectory!
        let failures = MigrationFailureCollector()
        let identifiers = MigrationFailureCollector()
        let original = pngFixture()

        DispatchQueue.concurrentPerform(iterations: 8) { _ in
            do {
                let vault = try ReceiptVault.open(
                    directory: directory,
                    client: nil,
                    uploadsEnabled: false
                )
                let asset = try vault.capture(
                    tripId: "trip-concurrent-capture",
                    draft: ReceiptDraft(filename: "same-receipt.png", data: original)
                )
                identifiers.append(asset.id)
            } catch {
                failures.append(String(describing: error))
            }
        }

        XCTAssertTrue(
            failures.messages.isEmpty,
            "Concurrent receipt capture failed: \\(failures.messages.joined(separator: "; "))"
        )
        XCTAssertEqual(Set(identifiers.messages).count, 1)
        let reopened = try ReceiptVault.open(
            directory: directory,
            client: nil,
            uploadsEnabled: false
        )
        XCTAssertEqual(try reopened.assets(tripId: "trip-concurrent-capture").count, 1)
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

private final class MigrationFailureCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []

    func append(_ message: String) {
        lock.lock()
        storage.append(message)
        lock.unlock()
    }

    var messages: [String] {
        lock.lock()
        defer { lock.unlock() }
        return storage
    }
}
