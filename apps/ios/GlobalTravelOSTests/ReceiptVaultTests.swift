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

    func testSQLiteQueryPropagatesStepFailuresInsteadOfReturningPartialResults() throws {
        let database = try LocalDatabase.open(
            path: temporaryDirectory.appendingPathComponent("query-errors.sqlite3")
        )

        XCTAssertThrowsError(
            try database.store.query(
                "SELECT abs(CAST('-9223372036854775808' AS INTEGER))"
            ) { $0.int(0) }
        )
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
            "Concurrent receipt capture failed: \(failures.messages.joined(separator: "; "))"
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

    func testTripExpenseOutboxSerializesConcurrentFailureUpdatesAcrossDatabaseHandles() throws {
        let path = temporaryDirectory.appendingPathComponent("receipt-vault.sqlite3")
        let initialDatabase = try LocalDatabase.open(path: path)
        let outbox = TripExpenseOutbox(database: initialDatabase)
        let request = TripExpenseCreateRequest(
            category: .meals,
            amount: Decimal(string: "42.50") ?? .zero,
            currency: "EUR",
            occurredOn: "2026-11-24",
            note: "Concurrent failure counter",
            receiptAssetId: nil
        )
        let queued = try outbox.enqueue(
            tripId: "trip-concurrent-failures",
            body: request,
            localReceiptAssetId: nil
        )
        let failures = MigrationFailureCollector()

        DispatchQueue.concurrentPerform(iterations: 8) { index in
            do {
                let database = try LocalDatabase.open(path: path)
                let independentHandle = TripExpenseOutbox(database: database)
                try independentHandle.recordFailure(
                    id: queued.id,
                    message: "Concurrent network failure \(index)"
                )
            } catch {
                failures.append(String(describing: error))
            }
        }

        XCTAssertTrue(
            failures.messages.isEmpty,
            "Concurrent outbox updates failed: \(failures.messages.joined(separator: "; "))"
        )
        let reopened = try LocalDatabase.open(path: path)
        let restored = try XCTUnwrap(
            TripExpenseOutbox(database: reopened).pending(tripId: queued.tripId).first
        )
        XCTAssertEqual(restored.id, queued.id)
        XCTAssertEqual(restored.attempts, 8, "Concurrent increments must not be lost")
        XCTAssertNotNil(restored.lastError)
        XCTAssertTrue(restored.lastError?.hasPrefix("Concurrent network failure ") == true)
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


    func testExpenseOutboxReplaysCommittedWriteAfterResponseLossAcrossRelaunch() async throws {
        let path = temporaryDirectory.appendingPathComponent("receipt-vault.sqlite3")
        let database = try LocalDatabase.open(path: path)
        let outbox = TripExpenseOutbox(database: database)
        let body = TripExpenseCreateRequest(
            category: .meals,
            amount: Decimal(string: "42.50") ?? .zero,
            currency: "EUR",
            occurredOn: "2026-11-24",
            note: "Response-loss idempotency",
            receiptAssetId: nil
        )
        let queued = try outbox.enqueue(
            tripId: "trip-response-loss",
            body: body,
            localReceiptAssetId: nil
        )

        CommitThenDropExpenseURLProtocol.server.reset()
        let sessionConfiguration = URLSessionConfiguration.ephemeral
        sessionConfiguration.protocolClasses = [CommitThenDropExpenseURLProtocol.self]
        let session = URLSession(configuration: sessionConfiguration)
        defer { session.invalidateAndCancel() }

        let apiConfiguration = APIConfiguration(
            baseURL: URL(string: "https://expense.test")!,
            orgId: "org-test",
            userId: "user-test",
            deviceId: "device-test",
            apiToken: nil
        )
        let repository = RemoteTripRepository(client: APIClient(
            configuration: apiConfiguration,
            session: session
        ))

        var firstAttemptFailed = false
        do {
            _ = try await repository.createExpense(
                tripId: queued.tripId,
                body: queued.body,
                idempotencyKey: queued.id
            )
        } catch {
            firstAttemptFailed = true
            try outbox.recordFailure(id: queued.id, message: "Server committed; response was lost")
        }
        XCTAssertTrue(firstAttemptFailed, "The first response must be dropped after the mock server commits")

        // A fresh SQLite handle models app termination/relaunch. The retry must recover
        // the original command UUID rather than generate a new idempotency key.
        let relaunchedDatabase = try LocalDatabase.open(path: path)
        let relaunchedOutbox = TripExpenseOutbox(database: relaunchedDatabase)
        let restored = try XCTUnwrap(relaunchedOutbox.pending(tripId: queued.tripId).first)
        XCTAssertEqual(restored.id, queued.id)
        XCTAssertEqual(restored.attempts, 1)
        XCTAssertEqual(restored.lastError, "Server committed; response was lost")

        let created = try await repository.createExpense(
            tripId: restored.tripId,
            body: restored.body,
            idempotencyKey: restored.id
        )
        XCTAssertEqual(created.id, "expense-server-1")
        XCTAssertEqual(created.tripId, queued.tripId)
        try relaunchedOutbox.remove(id: restored.id)
        XCTAssertTrue(try relaunchedOutbox.pending(tripId: queued.tripId).isEmpty)

        let snapshot = CommitThenDropExpenseURLProtocol.server.snapshot()
        XCTAssertEqual(snapshot.sideEffectCount, 1, "An idempotent replay must not create a second expense")
        XCTAssertEqual(snapshot.idempotencyKeys, [queued.id, queued.id])
        XCTAssertEqual(snapshot.requestBodies.count, 2)
        XCTAssertEqual(snapshot.requestBodies[0], snapshot.requestBodies[1])
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

private final class CommitThenDropExpenseURLProtocol: URLProtocol {
    static let server = CommitThenDropExpenseServer()

    override class func canInit(with request: URLRequest) -> Bool {
        request.url?.host == "expense.test"
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        let result = Self.server.handle(request)
        if result.dropResponse {
            client?.urlProtocol(self, didFailWithError: URLError(.networkConnectionLost))
            return
        }

        guard let url = request.url,
              let response = HTTPURLResponse(
                  url: url,
                  statusCode: 201,
                  httpVersion: "HTTP/1.1",
                  headerFields: ["Content-Type": "application/json"]
              ) else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }

        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: result.responseBody)
        client?.urlProtocolDidFinishLoading(self)
    }

    override func stopLoading() {}
}

private final class CommitThenDropExpenseServer: @unchecked Sendable {
    struct Snapshot {
        let sideEffectCount: Int
        let idempotencyKeys: [String]
        let requestBodies: [Data]
    }

    private let lock = NSLock()
    private var responsesByKey: [String: Data] = [:]
    private var idempotencyKeys: [String] = []
    private var requestBodies: [Data] = []
    private var sideEffectCount = 0
    private var droppedInitialResponse = false

    func reset() {
        lock.lock()
        defer { lock.unlock() }
        responsesByKey = [:]
        idempotencyKeys = []
        requestBodies = []
        sideEffectCount = 0
        droppedInitialResponse = false
    }

    func handle(_ request: URLRequest) -> (responseBody: Data, dropResponse: Bool) {
        lock.lock()
        defer { lock.unlock() }

        let key = request.value(forHTTPHeaderField: "Idempotency-Key") ?? "<missing>"
        idempotencyKeys.append(key)
        requestBodies.append(request.httpBody ?? Data())

        if let replay = responsesByKey[key] {
            return (replay, false)
        }

        sideEffectCount += 1
        let response = Data(
            """
            {
              "id": "expense-server-1",
              "trip_id": "trip-response-loss",
              "category": "MEALS",
              "amount": "42.50",
              "currency": "EUR"
            }
            """.utf8
        )
        responsesByKey[key] = response

        guard !droppedInitialResponse else {
            return (response, false)
        }
        droppedInitialResponse = true
        return (response, true)
    }

    func snapshot() -> Snapshot {
        lock.lock()
        defer { lock.unlock() }
        return Snapshot(
            sideEffectCount: sideEffectCount,
            idempotencyKeys: idempotencyKeys,
            requestBodies: requestBodies
        )
    }
}

