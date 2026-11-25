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

    private(set) var lastSyncError: String?
    private(set) var lastSyncAt: Date?

    var isPathAvailable: Bool {
        connectivityState.hasObservedPath && connectivityState.isPathAvailable
    }

    init(receiptVault: ReceiptVault, createExpense: @escaping ExpenseCreator) {
        self.receiptVault = receiptVault
        self.outbox = receiptVault.makeTripExpenseOutbox()
        self.createExpense = createExpense
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

    /// Called on initial path observation, subsequent reconnection, and app foreground.
    func handlePathUpdate(isPathAvailable: Bool) async {
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
        let tripIDs: [String]
        do {
            tripIDs = try outbox.pendingTripIDs()
        } catch {
            lastSyncError = error.localizedDescription
            return
        }
        for tripID in tripIDs {
            _ = await syncPendingExpenses(tripId: tripID)
        }
    }

    /// Manual retries and lifecycle/path-triggered retries share one in-flight task per Trip.
    /// The FIFO head is re-read after each successful request, so newly queued commands join
    /// the same drain without waiting for another app lifecycle event.
    func syncPendingExpenses(tripId: String) async -> Int {
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

    private func drain(tripId: String) async -> Int {
        var syncedCount = 0
        while true {
            let item: PendingTripExpense
            do {
                guard let next = try outbox.pending(tripId: tripId).first else {
                    break
                }
                item = next
            } catch {
                lastSyncError = error.localizedDescription
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
                try? outbox.recordFailure(
                    id: item.id,
                    message: String(error.localizedDescription.prefix(500))
                )
                lastSyncError = error.localizedDescription
                break
            }
        }
        if syncedCount > 0 {
            lastSyncAt = Date()
            lastSyncError = nil
        }
        return syncedCount
    }
}
