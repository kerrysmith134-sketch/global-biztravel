import Foundation

/// Pushes the local outbox and applies the server's **per-item** verdict (design doc §10.5).
///
/// Nothing here throws: a sync round is a report, not a success/failure. A transport failure, a
/// partial batch and a per-item conflict all come back as a `SyncRunOutcome` the UI can render
/// row by row, and every item that was not explicitly applied stays in the queue for retry.
final class SyncPushUseCase {
    static let defaultBatchSize = 50

    private let store: OfflineStore
    private let transport: SyncTransport
    private let batchSize: Int

    init(
        store: OfflineStore,
        transport: SyncTransport,
        batchSize: Int = SyncPushUseCase.defaultBatchSize
    ) {
        self.store = store
        self.transport = transport
        self.batchSize = max(batchSize, 1)
    }

    // MARK: - Queue inspection

    func pendingItems() -> [OutboxItem] {
        store.currentItems()
    }

    func pendingCount() -> Int {
        store.pendingCount()
    }

    func pendingItemIds() -> [String] {
        store.pendingItemIds()
    }

    func itemDetail(itemId: String) -> OutboxItem? {
        store.item(id: itemId)
    }

    /// Items the server already refused or flagged — these need a human decision, not a retry.
    func itemsNeedingAttention() -> [OutboxItem] {
        store.currentItems().filter { $0.needsManualAttention }
    }

    func lastSyncAt() -> Date? {
        store.lastSyncAt()
    }

    // MARK: - Push

    /// Pushes up to `batchSize` queued items and folds the verdicts back into the queue.
    func pushPending(deviceId: String, now: Date = Date()) async -> SyncRunOutcome {
        let queued = Array(store.currentItems().prefix(batchSize))
        guard !queued.isEmpty else {
            return SyncRunOutcome(ranAt: now)
        }

        let request = SyncPushRequest(deviceId: deviceId, items: queued.map(\.item))

        let pushResult: SyncPushResult
        do {
            pushResult = try await transport.push(request)
        } catch {
            let message = (error as? APIError)?.message ?? error.localizedDescription
            for item in queued {
                try? store.markAttempt(itemId: item.itemId, error: message, now: now)
            }
            return SyncRunOutcome(
                attempted: queued.count,
                failed: queued.count,
                results: [],
                failures: Self.failureMap(for: queued, message: message),
                ranAt: now,
                errorMessage: message
            )
        }

        var applied = 0
        var conflict = 0
        var rejected = 0
        var failed = 0
        var results: [SyncItemResult] = []
        var failures: [String: String] = [:]
        var decided = Set<String>()

        for result in pushResult.resultList {
            decided.insert(result.itemId)
            results.append(result)
            switch result.status {
            case .applied:
                applied += 1
                try? store.record(result, forItemId: result.itemId, now: now)
            case .conflict:
                conflict += 1
                try? store.record(result, forItemId: result.itemId, now: now)
            case .rejected:
                rejected += 1
                try? store.record(result, forItemId: result.itemId, now: now)
            case .unknown:
                failed += 1
                failures[result.itemId] = "服务端返回了未知的逐项状态"
                try? store.markAttempt(itemId: result.itemId, error: "未知同步状态", now: now)
            }
        }

        // Items the server never spoke about are treated as undelivered, not as rejected: they stay
        // queued and are retried on the next round.
        for item in queued where !decided.contains(item.itemId) {
            failed += 1
            failures[item.itemId] = "服务端未返回该项结果"
            try? store.markAttempt(itemId: item.itemId, error: "服务端未返回该项结果", now: now)
        }

        if failed == 0 {
            try? store.setLastSyncAt(now)
        }

        return SyncRunOutcome(
            attempted: queued.count,
            applied: applied,
            conflict: conflict,
            rejected: rejected,
            failed: failed,
            results: results,
            failures: failures,
            ranAt: now,
            errorMessage: nil
        )
    }

    /// Retries everything still queued. Conflicting / rejected items are included, because the
    /// operator may have resolved them in the meantime; use `discard` to drop one permanently.
    func retryPending(deviceId: String, now: Date = Date()) async -> SyncRunOutcome {
        await pushPending(deviceId: deviceId, now: now)
    }

    func discard(itemId: String) throws -> Bool {
        try store.remove(itemId: itemId)
    }

    private static func failureMap(for items: [OutboxItem], message: String) -> [String: String] {
        var map: [String: String] = [:]
        for item in items {
            map[item.itemId] = message
        }
        return map
    }
}
