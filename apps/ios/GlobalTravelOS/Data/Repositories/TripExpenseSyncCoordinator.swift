import Foundation
import Network

/// Small deterministic reducer for NWPathMonitor updates. The first online observation is
/// actionable too: it recovers commands left pending when the previous process was terminated.
struct TripExpenseConnectivityState {
    private(set) var hasObservedPath = false
    private(set) var isPathAvailable = false

    mutating func update(isPathAvailable newValue: Bool) -> Bool {
        let shouldDrain = newValue && (!hasObservedPath || !isPathAvailable)
        hasObservedPath = true
        isPathAvailable = newValue
        return shouldDrain
    }
}

/// Owns cloud-mode expense replay for the whole account, not a Trip detail view. A durable
/// command therefore continues to recover when the traveler leaves the Trip screen, changes
/// tabs, or relaunches the app. Per-Trip work is serialized and every write reuses its persisted
/// idempotency key.
@MainActor
final class TripExpenseSyncCoordinator {
    typealias ExpenseCreator = (
        _ tripId: String,
        _ body: TripExpenseCreateRequest,
        _ idempotencyKey: String
    ) async throws -> TripExpense

    private let outbox: TripExpenseOutbox
    private let receiptVault: ReceiptVault
    private let createExpense: ExpenseCreator
    private let pathMonitor = NWPathMonitor()
    private let pathMonitorQueue = DispatchQueue(
        label: "com.anari.globaltravelos.account-expense-sync"
    )
    private var connectivityState = TripExpenseConnectivityState()
    private var inFlight: [String: Task<Int, Never>] = [:]
    private var syncErrorsByTrip: [String: String] = [:]
    private var outboxReadError: String?
    private var isStopped = false

    private(set) var lastSyncError: String?
    private(set) var lastSyncAt: Date?

    var isPathAvailable: Bool {
        connectivityState.hasObservedPath && connectivityState.isPathAvailable
    }

    init(
        receiptVault: ReceiptVault,
        startMonitoring: Bool = true,
        createExpense: @escaping ExpenseCreator
    ) {
        self.receiptVault = receiptVault
        self.outbox = receiptVault.makeTripExpenseOutbox()
        self.createExpense = createExpense
        guard startMonitoring else { return }
        pathMonitor.pathUpdateHandler = { [weak self] path in
            let available = path.status == .satisfied
            Task { @MainActor [weak self] in
                guard let self else { return }
                await self.handlePathUpdate(isPathAvailable: available)
            }
        }
        pathMonitor.start(queue: pathMonitorQueue)
    }

    deinit {
        pathMonitor.cancel()
    }

    /// Stops account-scoped observation and cancels in-flight requests before the app switches
    /// identity or run mode. Durable commands are intentionally left in SQLite for that account.
    func shutdown() {
        guard !isStopped else { return }
        isStopped = true
        pathMonitor.cancel()
        inFlight.values.forEach { $0.cancel() }
        inFlight.removeAll()
    }

    /// Called on initial path observation, subsequent reconnection, and app foreground.
    func handlePathUpdate(isPathAvailable: Bool) async {
        guard !isStopped else { return }
        if connectivityState.update(isPathAvailable: isPathAvailable) {
            await syncAllPending()
        }
    }

    func applicationDidBecomeActive() async {
        guard isPathAvailable else { return }
        await syncAllPending()
    }

    /// Enumerates all Trips in this account's local outbox, including Trips that are not
    /// represented in the current navigation stack.
    func syncAllPending() async {
        guard !isStopped else { return }
        let tripIDs: [String]
        do {
            tripIDs = try outbox.pendingTripIDs()
        } catch {
            outboxReadError = error.localizedDescription
            refreshLastSyncError()
            return
        }
        outboxReadError = nil
        refreshLastSyncError()
        for tripID in tripIDs {
            let syncedCount = await syncPendingExpenses(tripId: tripID)
            let pendingCount = (try? outbox.pending(tripId: tripID).count) ?? 0
            NotificationCenter.default.post(
                name: .tripExpenseSyncDidChange,
                object: self,
                userInfo: [
                    "tripId": tripID,
                    "syncedCount": syncedCount,
                    "pendingCount": pendingCount
                ]
            )
        }
    }

    /// Manual retries and lifecycle/path-triggered retries share one in-flight task per Trip.
    /// The FIFO head is re-read after each successful request, so newly queued commands join
    /// the same drain without waiting for another app lifecycle event.
    func syncPendingExpenses(tripId: String) async -> Int {
        guard !isStopped else { return 0 }
        if let task = inFlight[tripId] {
            return await task.value
        }

        let task = Task { [weak self] in
            guard let self else { return 0 }
            return await self.drain(tripId: tripId)
        }
        inFlight[tripId] = task
        let syncedCount = await task.value
        inFlight[tripId] = nil
        return syncedCount
    }

    private func refreshLastSyncError() {
        // A successful Trip must not erase another Trip's unresolved sync failure.
        lastSyncError = outboxReadError
            ?? syncErrorsByTrip.keys.sorted().compactMap { syncErrorsByTrip[$0] }.first
    }

    private func drain(tripId: String) async -> Int {
        var syncedCount = 0
        var encounteredFailure: String?
        while !Task.isCancelled {
            let item: PendingTripExpense
            do {
                guard let next = try outbox.pending(tripId: tripId).first else {
                    break
                }
                item = next
            } catch {
                encounteredFailure = error.localizedDescription
                syncErrorsByTrip[tripId] = error.localizedDescription
                break
            }

            do {
                var request = item.body
                if let localReceiptAssetId = item.localReceiptAssetId {
                    let asset = try await receiptVault.prepareForExpense(assetId: localReceiptAssetId)
                    guard let serverAssetId = asset.serverAssetId else {
                        throw ReceiptVaultError.serverAssetNotConfirmed
                    }
                    request.receiptAssetId = serverAssetId
                }
                _ = try await createExpense(tripId, request, item.id)
                try outbox.remove(id: item.id)
                syncedCount += 1
            } catch {
                guard !Task.isCancelled else { break }
                encounteredFailure = error.localizedDescription
                try? outbox.recordFailure(
                    id: item.id,
                    message: String(error.localizedDescription.prefix(500))
                )
                break
            }
        }

        if let encounteredFailure {
            syncErrorsByTrip[tripId] = encounteredFailure
        } else {
            let hasPendingCommands = (try? outbox.pending(tripId: tripId).isEmpty) == false
            if !hasPendingCommands {
                syncErrorsByTrip.removeValue(forKey: tripId)
            }
        }
        refreshLastSyncError()

        if syncedCount > 0 {
            lastSyncAt = Date()
        }
        return syncedCount
    }
}


extension Notification.Name {
    static let tripExpenseSyncDidChange = Notification.Name(
        "com.anari.globaltravelos.trip-expense-sync-did-change"
    )
}
