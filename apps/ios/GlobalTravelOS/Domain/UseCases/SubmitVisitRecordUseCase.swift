import Foundation

/// What happened to a visit record the operator just filled in.
///
/// The three outcomes are exactly the three things the workspace can tell the operator:
/// it reached the server, it is safely queued for the next sync, or the server took it but
/// flagged a conflict that a human still has to look at.
enum VisitSubmissionResult: Equatable {
    case uploaded(VisitRecord)
    case queued(reason: String)
    case conflict(serverVersion: Int?, reason: String?)

    var isPersistedLocally: Bool {
        switch self {
        case .uploaded, .queued, .conflict: return true
        }
    }

    var headline: String {
        switch self {
        case .uploaded: return "已提交到服务端"
        case .queued(let reason): return "已离线保存（\(reason)）"
        case .conflict: return "已提交，但服务端判定冲突"
        }
    }

    var detail: String {
        switch self {
        case let .uploaded(record):
            return "记录 \(record.id) · \(record.outcomeLabel) · server v\(record.serverVersion ?? 0)"
        case .queued:
            return "记录已进入离线队列，联网后按逐项结果同步"
        case let .conflict(serverVersion, reason):
            var parts = ["服务端保留了另一个版本"]
            if let serverVersion { parts.append("server v\(serverVersion)") }
            if let reason, !reason.isEmpty { parts.append(reason) }
            return parts.joined(separator: " · ")
        }
    }

    var requiresAttention: Bool {
        if case .conflict = self { return true }
        return false
    }
}

/// Writes a visit record from the field.
///
/// Offline-first by construction (design doc §10.5): the record is *always* written to the local
/// outbox first, and only then is the network attempted. A dropped connection therefore never
/// loses an operator's note, and the per-item verdict from either path is what the UI shows.
final class SubmitVisitRecordUseCase {
    private let repository: VisitRepository
    private let store: OfflineStore

    init(repository: VisitRepository, store: OfflineStore) {
        self.repository = repository
        self.store = store
    }

    /// - Parameters:
    ///   - draft: the record being submitted; its `clientRecordId` doubles as the idempotency key.
    ///   - networkAvailable: set by the view model from the last request outcome / health probe.
    func submit(
        _ draft: VisitDraft,
        networkAvailable: Bool,
        now: Date = Date()
    ) async throws -> VisitSubmissionResult {
        let request = draft.makeRequest(clientUpdatedAt: now)
        let item = SyncItem.visitRecord(request, visitId: draft.visitId)
        try store.enqueue(item, now: now)

        guard networkAvailable else {
            return .queued(reason: "设备当前离线")
        }

        do {
            let record = try await repository.submitRecord(visitId: draft.visitId, request: request)
            if let conflict = record.conflict {
                // The server accepted the write but kept its own version — the item stays in the
                // outbox with the conflict attached so it is visible until a human resolves it.
                try store.record(
                    SyncItemResult(
                        itemId: item.itemId,
                        entity: .visitRecord,
                        status: .conflict,
                        serverVersion: record.serverVersion,
                        conflict: conflict
                    ),
                    forItemId: item.itemId,
                    now: now
                )
                try store.removeDraft(clientRecordId: draft.clientRecordId)
                return .conflict(serverVersion: record.serverVersion, reason: conflict.reason)
            }

            try store.removeAll(itemIds: [item.itemId])
            try store.removeDraft(clientRecordId: draft.clientRecordId)
            return .uploaded(record)
        } catch let error as APIError where error.isRetryable {
            try store.markAttempt(itemId: item.itemId, error: error.message, now: now)
            return .queued(reason: error.message)
        } catch {
            // A non-retryable failure (bad payload, 4xx) keeps the item queued too — the operator's
            // notes are never thrown away — but the error is surfaced so it can be fixed.
            try store.markAttempt(itemId: item.itemId, error: error.localizedDescription, now: now)
            throw error
        }
    }

    /// Queue a record without trying the network at all.
    @discardableResult
    func enqueueOffline(_ draft: VisitDraft, now: Date = Date()) throws -> OutboxItem {
        try store.enqueue(
            SyncItem.visitRecord(draft.makeRequest(clientUpdatedAt: now), visitId: draft.visitId),
            now: now
        )
    }

    func pendingCount() -> Int {
        store.pendingCount()
    }

    func drafts() -> [VisitDraft] {
        store.drafts()
    }
}