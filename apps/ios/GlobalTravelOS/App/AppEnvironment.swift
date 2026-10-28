import Foundation

/// One place that owns the client-side object graph.
///
/// The repositories are wrapped in their offline read-through variants and share a single
/// `OfflineIndicator`, so any screen can say "this is cached" without each feature re-implementing
/// fallback logic. Settings changes rebuild the graph: identity and base URL are part of every
/// request, so reusing a client across an identity switch would leak the old identity.
@MainActor
final class AppEnvironment: ObservableObject {
    @Published private(set) var settings: AppSettings
    @Published private(set) var offlineSummary: String = "数据来自服务端"

    let directory: URL
    let settingsStore: AppSettingsStore
    let indicator: OfflineIndicator
    let offlineStore: OfflineStore

    private(set) var apiClient: APIClient
    private(set) var tripRepository: TripRepository
    private(set) var planRepository: PlanRepository
    private(set) var visitRepository: VisitRepository
    private(set) var proactiveRepository: ProactiveRepository
    private(set) var memoryRepository: MemoryRepository
    private(set) var actionCenterRepository: ActionCenterRepository

    private(set) var tripListUseCase: TripListUseCase
    private(set) var createTripUseCase: CreateTripUseCase
    private(set) var generatePlanUseCase: GeneratePlanUseCase
    private(set) var activatePlanUseCase: ActivatePlanUseCase
    private(set) var submitVisitRecordUseCase: SubmitVisitRecordUseCase
    private(set) var syncPushUseCase: SyncPushUseCase

    init() {
        let directory = AppSettingsStore.defaultDirectory()
        let store = AppSettingsStore(directory: directory)
        var loaded = store.load()

        // UI 测试用启动参数覆盖身份/地址：不写回磁盘，避免测试污染用户设置。
        let overrides = ProcessInfo.processInfo.environment
        if let value = overrides["GBT_UI_TEST_BASE_URL"], !value.isEmpty {
            loaded.baseURLString = value
        }
        if let value = overrides["GBT_UI_TEST_ORG"], !value.isEmpty {
            loaded.orgId = value
        }
        if let value = overrides["GBT_UI_TEST_USER"], !value.isEmpty {
            loaded.userId = value
        }
        if let value = overrides["GBT_UI_TEST_LAST_TRIP"], !value.isEmpty {
            loaded.lastTripId = value
        }

        let indicator = OfflineIndicator()
        let offline = OfflineStore(directory: directory)
        let client = APIClient(configuration: loaded.apiConfiguration)

        self.directory = directory
        self.settingsStore = store
        self.settings = loaded
        self.indicator = indicator
        self.offlineStore = offline
        self.apiClient = client

        let remoteTrips = RemoteTripRepository(client: client)
        let remotePlans = RemotePlanRepository(client: client)
        let remoteVisits = RemoteVisitRepository(client: client)
        let remoteProactive = RemoteProactiveRepository(client: client)
        let remoteMemory = RemoteMemoryRepository(client: client)
        let remoteActionCenter = RemoteActionCenterRepository(client: client)

        let trips = OfflineTripRepository(remote: remoteTrips, directory: directory, indicator: indicator)
        let plans = OfflinePlanRepository(remote: remotePlans, directory: directory, indicator: indicator)
        let visits = OfflineVisitRepository(remote: remoteVisits, directory: directory, indicator: indicator)
        let proactive = OfflineProactiveRepository(
            remote: remoteProactive,
            directory: directory,
            indicator: indicator
        )
        let memory = OfflineMemoryRepository(remote: remoteMemory, directory: directory, indicator: indicator)

        self.tripRepository = trips
        self.planRepository = plans
        self.visitRepository = visits
        self.proactiveRepository = proactive
        self.memoryRepository = memory
        self.actionCenterRepository = remoteActionCenter

        self.tripListUseCase = TripListUseCase(repository: trips)
        self.createTripUseCase = CreateTripUseCase(repository: trips)
        self.generatePlanUseCase = GeneratePlanUseCase(repository: plans)
        self.activatePlanUseCase = ActivatePlanUseCase(planRepository: plans, tripRepository: trips)
        self.submitVisitRecordUseCase = SubmitVisitRecordUseCase(repository: visits, store: offline)
        self.syncPushUseCase = SyncPushUseCase(
            store: offline,
            transport: RemoteSyncTransport(client: client)
        )
    }

    func refreshOfflineSummary() {
        offlineSummary = indicator.summary
    }

    /// Remember the last opened trip without rebuilding the repositories.
    func rememberTrip(_ tripId: String) {
        guard settings.lastTripId != tripId else { return }
        var next = settings
        next.lastTripId = tripId
        settingsStore.save(next)
        settings = next
    }

    func updateSettings(_ mutate: (inout AppSettings) -> Void) {
        var next = settings
        mutate(&next)
        settingsStore.save(next)
        settings = next
        // Rebuild everything that carries the old identity.
        let client = APIClient(configuration: next.apiConfiguration)
        apiClient = client
        let trips = OfflineTripRepository(
            remote: RemoteTripRepository(client: client),
            directory: directory,
            indicator: indicator
        )
        let plans = OfflinePlanRepository(
            remote: RemotePlanRepository(client: client),
            directory: directory,
            indicator: indicator
        )
        let visits = OfflineVisitRepository(
            remote: RemoteVisitRepository(client: client),
            directory: directory,
            indicator: indicator
        )
        tripRepository = trips
        planRepository = plans
        visitRepository = visits
        proactiveRepository = OfflineProactiveRepository(
            remote: RemoteProactiveRepository(client: client),
            directory: directory,
            indicator: indicator
        )
        memoryRepository = OfflineMemoryRepository(
            remote: RemoteMemoryRepository(client: client),
            directory: directory,
            indicator: indicator
        )
        actionCenterRepository = RemoteActionCenterRepository(client: client)
        tripListUseCase = TripListUseCase(repository: trips)
        createTripUseCase = CreateTripUseCase(repository: trips)
        generatePlanUseCase = GeneratePlanUseCase(repository: plans)
        activatePlanUseCase = ActivatePlanUseCase(planRepository: plans, tripRepository: trips)
        submitVisitRecordUseCase = SubmitVisitRecordUseCase(repository: visits, store: offlineStore)
        syncPushUseCase = SyncPushUseCase(
            store: offlineStore,
            transport: RemoteSyncTransport(client: client)
        )
    }

    func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError { return apiError.message }
        if let localized = error as? LocalizedError, let description = localized.errorDescription {
            return description
        }
        return error.localizedDescription
    }
}
