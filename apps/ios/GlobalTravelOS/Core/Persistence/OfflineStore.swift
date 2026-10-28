import Foundation

/// Snapshot of everything the client keeps on disk while offline.
struct OfflineState: Codable, Equatable {
    var schemaVersion: Int
    var items: [OutboxItem]
    var drafts: [VisitDraft]
    var lastSyncAt: Date?

    static let empty = OfflineState(schemaVersion: 1, items: [], drafts: [], lastSyncAt: nil)
}

/// Offline-first queue backed by a single JSON file — no CoreData, no SwiftData.
///
/// `OfflineStore` intentionally depends on `OutboxItem` / `VisitDraft` / `VisitOutcome` from the
/// Domain layer: the queue is the client-side implementation of the design doc §10.5 per-item sync
/// result, so its persisted shape *is* a domain shape. The dependency direction
/// (Core → Domain) is one-way and documented in `docs/architecture/ios-client.md`.
final class OfflineStore {
    private let fileURL: URL
    private let lock = NSLock()
    private var state: OfflineState

    init(fileURL: URL) {
        self.fileURL = fileURL
        self.state = OfflineStore.read(from: fileURL) ?? .empty
    }

    convenience init(directory: URL, fileName: String = "offline-store.json") {
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: nil
        )
        self.init(fileURL: directory.appendingPathComponent(fileName))
    }

    static func defaultDirectory() -> URL {
        let base = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first ?? FileManager.default.temporaryDirectory
        return base.appendingPathComponent("GlobalTravelOS", isDirectory: true)
    }

    var filePath: String { fileURL.path }

    // MARK: - Reading

    func currentItems() -> [OutboxItem] {
        lock.lock()
        defer { lock.unlock() }
        return state.items
    }

    func item(id: String) -> OutboxItem? {
        lock.lock()
        defer { lock.unlock() }
        return state.items.first { $0.item.itemId == id }
    }

    func pendingCount() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return state.items.count
    }

    func pendingItemIds() -> [String] {
        lock.lock()
        defer { lock.unlock() }
        return state.items.map { $0.item.itemId }
    }

    func drafts() -> [VisitDraft] {
        lock.lock()
        defer { lock.unlock() }
        return state.drafts
    }

    func lastSyncAt() -> Date? {
        lock.lock()
        defer { lock.unlock() }
        return state.lastSyncAt
    }

    // MARK: - Queue mutation

    /// Enqueues a sync item. Re-enqueuing the same `itemId` replaces the payload and resets the
    /// item's terminal status while keeping its first `enqueuedAt`.
    @discardableResult
    func enqueue(_ item: SyncItem, now: Date = Date()) throws -> OutboxItem {
        lock.lock()
        defer { lock.unlock() }
        if let index = state.items.firstIndex(where: { $0.item.itemId == item.itemId }) {
            var existing = state.items[index]
            existing.item = item
            existing.lastError = nil
            existing.lastStatus = nil
            existing.conflict = nil
            existing.serverVersion = nil
            existing.updatedAt = now
            state.items[index] = existing
            try persistLocked()
            return existing
        }
        let queued = OutboxItem(
            item: item,
            attempts: 0,
            lastError: nil,
            lastStatus: nil,
            serverVersion: nil,
            conflict: nil,
            enqueuedAt: now,
            updatedAt: now
        )
        state.items.append(queued)
        try persistLocked()
        return queued
    }

    /// Applies one per-item server verdict. `applied` dequeues; `conflict` / `rejected` stay in the
    /// queue with the server's verdict attached so the UI can show it item by item.
    @discardableResult
    func record(_ result: SyncItemResult, forItemId itemId: String, now: Date = Date()) throws -> OutboxItem? {
        lock.lock()
        defer { lock.unlock() }
        guard let index = state.items.firstIndex(where: { $0.item.itemId == itemId }) else { return nil }

        if result.status == .applied {
            state.items.remove(at: index)
            try persistLocked()
            return nil
        }

        var existing = state.items[index]
        existing.lastStatus = result.status
        existing.serverVersion = result.serverVersion
        existing.conflict = result.conflict
        existing.lastError = result.conflict?.reason
        existing.updatedAt = now
        state.items[index] = existing
        try persistLocked()
        return existing
    }

    @discardableResult
    func markAttempt(itemId: String, error: String?, now: Date = Date()) throws -> OutboxItem? {
        lock.lock()
        defer { lock.unlock() }
        guard let index = state.items.firstIndex(where: { $0.item.itemId == itemId }) else { return nil }
        var existing = state.items[index]
        existing.attempts += 1
        existing.lastError = error
        existing.updatedAt = now
        state.items[index] = existing
        try persistLocked()
        return existing
    }

    /// Removes items the server accepted, in one write.
    @discardableResult
    func removeAll(itemIds: [String]) throws -> Int {
        lock.lock()
        defer { lock.unlock() }
        guard !itemIds.isEmpty else { return 0 }
        let doomed = Set(itemIds)
        let before = state.items.count
        state.items.removeAll { doomed.contains($0.item.itemId) }
        let removed = before - state.items.count
        if removed > 0 { try persistLocked() }
        return removed
    }

    /// Manual discard of a conflicting/rejected item from the offline queue UI.
    @discardableResult
    func remove(itemId: String) throws -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let before = state.items.count
        state.items.removeAll { $0.item.itemId == itemId }
        guard state.items.count != before else { return false }
        try persistLocked()
        return true
    }

    func setLastSyncAt(_ date: Date) throws {
        lock.lock()
        defer { lock.unlock() }
        state.lastSyncAt = date
        try persistLocked()
    }

    // MARK: - Drafts

    @discardableResult
    func saveDraft(_ draft: VisitDraft) throws -> VisitDraft {
        lock.lock()
        defer { lock.unlock() }
        if let index = state.drafts.firstIndex(where: { $0.clientRecordId == draft.clientRecordId }) {
            state.drafts[index] = draft
        } else {
            state.drafts.append(draft)
        }
        try persistLocked()
        return draft
    }

    @discardableResult
    func removeDraft(clientRecordId: String) throws -> Bool {
        lock.lock()
        defer { lock.unlock() }
        let before = state.drafts.count
        state.drafts.removeAll { $0.clientRecordId == clientRecordId }
        guard state.drafts.count != before else { return false }
        try persistLocked()
        return true
    }

    func clear() throws {
        lock.lock()
        defer { lock.unlock() }
        state = .empty
        try persistLocked()
    }

    /// Forces a reload from disk. Used by tests to prove the queue round-trips through the file.
    func reload() {
        lock.lock()
        defer { lock.unlock() }
        state = OfflineStore.read(from: fileURL) ?? .empty
    }

    // MARK: - Private

    private static func read(from url: URL) -> OfflineState? {
        guard let data = try? Data(contentsOf: url), !data.isEmpty else { return nil }
        return try? DateCoding.decode(OfflineState.self, from: data)
    }

    private func persistLocked() throws {
        let data = try DateCoding.encode(state)
        try data.write(to: fileURL, options: .atomic)
    }
}

/// Tiny "last known good response" cache used by the offline-wrapping repositories.
final class JSONFileCache<Value: Codable> {
    private let fileURL: URL
    private let lock = NSLock()
    private var value: Value?
    private var didLoad = false

    init(fileURL: URL) {
        self.fileURL = fileURL
    }

    convenience init(directory: URL, fileName: String) {
        try? FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: nil
        )
        self.init(fileURL: directory.appendingPathComponent(fileName))
    }

    func load() -> Value? {
        lock.lock()
        defer { lock.unlock() }
        if !didLoad {
            didLoad = true
            if let data = try? Data(contentsOf: fileURL), !data.isEmpty {
                value = try? DateCoding.decode(Value.self, from: data)
            }
        }
        return value
    }

    func save(_ newValue: Value) {
        lock.lock()
        defer { lock.unlock() }
        value = newValue
        didLoad = true
        guard let data = try? DateCoding.encode(newValue) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }

    func clear() {
        lock.lock()
        defer { lock.unlock() }
        value = nil
        didLoad = true
        try? FileManager.default.removeItem(at: fileURL)
    }

    /// Test seam: drop the in-memory copy so the next `load()` re-reads the file.
    func unload() {
        lock.lock()
        defer { lock.unlock() }
        value = nil
        didLoad = false
    }
}