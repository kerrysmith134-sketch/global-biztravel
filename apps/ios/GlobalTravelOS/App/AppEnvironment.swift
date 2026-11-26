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

    private(set) var directory: URL
    let settingsStore: AppSettingsStore
    let indicator: OfflineIndicator
    private(set) var offlineStore: OfflineStore
    private(set) var localDatabase: LocalDatabase?

    private(set) var apiClient: APIClient
    private(set) var receiptVault: ReceiptVault?
    private(set) var tripRepository: TripRepository
    private(set) var planRepository: PlanRepository
    private(set) var visitRepository: VisitRepository
    private(set) var proactiveRepository: ProactiveRepository
    private(set) var memoryRepository: MemoryRepository
    private(set) var actionCenterRepository: ActionCenterRepository
    private(set) var notificationRepository: NotificationRepository
    private(set) var assistantRepository: AssistantRepository
    private(set) var researchRepository: ResearchRepository
    private(set) var customerRepository: CustomerRepository

    private(set) var tripListUseCase: TripListUseCase
    private(set) var createTripUseCase: CreateTripUseCase
    private(set) var generatePlanUseCase: GeneratePlanUseCase
    private(set) var activatePlanUseCase: ActivatePlanUseCase
    private(set) var submitVisitRecordUseCase: SubmitVisitRecordUseCase
    private(set) var syncPushUseCase: SyncPushUseCase

    init() {
        // 设置文件是全局的；缓存/离线队列按账户（org+user）分区（P0-1：
        // 身份切换不得复用其他账户的缓存或队列）。
        let settingsRoot = AppSettingsStore.defaultDirectory()
        let store = AppSettingsStore(directory: settingsRoot)
        var loaded = store.load()
        let directory = Self.accountDirectory(root: settingsRoot, settings: loaded)

        // UI 测试用启动参数覆盖身份/地址：不写回磁盘，避免测试污染用户设置。
        let overrides = ProcessInfo.processInfo.environment
        if let value = overrides["GBT_UI_TEST_BASE_URL"], !value.isEmpty {
            loaded.baseURLString = value
            // UI 冒烟全部针对真实后端：地址覆盖隐含云端模式（否则默认 local 会让用例
            // 连到一个空的本地库，断言"后端种子数据"必然失败）。
            loaded.runMode = "cloud"
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
        let receiptVault = Self.makeReceiptVault(
            directory: directory, client: client, uploadsEnabled: loaded.runMode != "local"
        )
        self.receiptVault = receiptVault
        self.localDatabase = nil

        let bundle = Self.makeBundle(
            settings: loaded, client: client, directory: directory,
            offlineStore: offline, indicator: indicator, receiptVault: receiptVault
        )
        self.tripRepository = bundle.trips
        self.planRepository = bundle.plans
        self.visitRepository = bundle.visits
        self.proactiveRepository = bundle.proactive
        self.memoryRepository = bundle.memory
        self.actionCenterRepository = bundle.actionCenter
        self.notificationRepository = bundle.notifications
        self.assistantRepository = bundle.assistant
        self.researchRepository = bundle.research
        self.customerRepository = bundle.customers
        self.tripListUseCase = bundle.tripList
        self.createTripUseCase = bundle.createTrip
        self.generatePlanUseCase = bundle.generatePlan
        self.activatePlanUseCase = bundle.activatePlan
        self.submitVisitRecordUseCase = bundle.submitRecord
        self.syncPushUseCase = bundle.syncPush

        if loaded.runMode == "local" {
            // 本地模式的库引用必须在启动时就挂上：设置页与自动示例数据都依赖它。
            localDatabase = Self.localDatabase(from: bundle)
        }
        refreshOfflineSummary()
    }

    /// updateSettings 重建时套用新 bundle（init 场景直接内联赋值以满足两段式）。
    private func apply(_ bundle: RepositoryBundle) {
        tripRepository = bundle.trips
        planRepository = bundle.plans
        visitRepository = bundle.visits
        proactiveRepository = bundle.proactive
        memoryRepository = bundle.memory
        actionCenterRepository = bundle.actionCenter
        notificationRepository = bundle.notifications
        assistantRepository = bundle.assistant
        researchRepository = bundle.research
        customerRepository = bundle.customers
        tripListUseCase = bundle.tripList
        createTripUseCase = bundle.createTrip
        generatePlanUseCase = bundle.generatePlan
        activatePlanUseCase = bundle.activatePlan
        submitVisitRecordUseCase = bundle.submitRecord
        syncPushUseCase = bundle.syncPush
        refreshOfflineSummary()
    }

    /// 账户级数据目录：cloud 模式按 org+user 隔离；local 模式独立单用户目录。
    static func accountDirectory(root: URL, settings: AppSettings) -> URL {
        if settings.runMode == "local" {
            return root.appendingPathComponent("local", isDirectory: true)
        }
        let fingerprint = "\(settings.orgId)_\(settings.userId)"
            .replacingOccurrences(of: "/", with: "_")
        return root.appendingPathComponent("accounts/\(fingerprint)", isDirectory: true)
    }

    /// 组合根：按运行模式构造仓储（ADR-0036）。
    /// cloud = 远端 + offline read-through；local = SQLite 本地仓储（阶段推进中，
    /// 未实现的方法显式抛 LocalStorePendingError）。static：init 两段式约束下不借 self。
    private struct RepositoryBundle {
        let trips: TripRepository
        let plans: PlanRepository
        let visits: VisitRepository
        let proactive: ProactiveRepository
        let memory: MemoryRepository
        let actionCenter: ActionCenterRepository
        let notifications: NotificationRepository
        let assistant: AssistantRepository
        let research: ResearchRepository
        let customers: CustomerRepository
        let tripList: TripListUseCase
        let createTrip: CreateTripUseCase
        let generatePlan: GeneratePlanUseCase
        let activatePlan: ActivatePlanUseCase
        let submitRecord: SubmitVisitRecordUseCase
        let syncPush: SyncPushUseCase
    }

    private static func makeBundle(
        settings: AppSettings,
        client: APIClient,
        directory: URL,
        offlineStore: OfflineStore,
        indicator: OfflineIndicator,
        receiptVault: ReceiptVault?
    ) -> RepositoryBundle {
        if settings.runMode == "local" {
            do {
                let database = try LocalDatabase.open(path: SQLiteStore.defaultPath())
                let trips = LocalTripRepository(database: database, receiptVault: receiptVault)
                let plans = LocalPlanRepository(database: database)
                let visits = LocalVisitRepository(database: database)
                return RepositoryBundle(
                    trips: trips,
                    plans: plans,
                    visits: visits,
                    proactive: LocalProactiveRepository(database: database),
                    memory: LocalMemoryRepository(database: database),
                    actionCenter: LocalActionCenterRepository(database: database),
                    notifications: RemoteNotificationRepository(client: client),
                    assistant: RemoteAssistantRepository(client: client),
                    research: RemoteResearchRepository(client: client),
                    customers: LocalCustomerRepository(database: database),
                    tripList: TripListUseCase(repository: trips),
                    createTrip: CreateTripUseCase(repository: trips),
                    generatePlan: GeneratePlanUseCase(repository: plans),
                    activatePlan: ActivatePlanUseCase(planRepository: plans, tripRepository: trips),
                    submitRecord: SubmitVisitRecordUseCase(repository: visits, store: offlineStore),
                    syncPush: SyncPushUseCase(store: offlineStore, transport: LocalSyncTransport())
                )
            } catch {
                // 本地库打不开（磁盘损坏等）：回退云端，不让 App 起不来。
            }
        }
        let remoteTrips = RemoteTripRepository(client: client)
        let remotePlans = RemotePlanRepository(client: client)
        let remoteVisits = RemoteVisitRepository(client: client)
        let trips = OfflineTripRepository(remote: remoteTrips, directory: directory, indicator: indicator)
        let plans = OfflinePlanRepository(remote: remotePlans, directory: directory, indicator: indicator)
        let visits = OfflineVisitRepository(remote: remoteVisits, directory: directory, indicator: indicator)
        return RepositoryBundle(
            trips: trips,
            plans: plans,
            visits: visits,
            proactive: OfflineProactiveRepository(
                remote: RemoteProactiveRepository(client: client),
                directory: directory,
                indicator: indicator
            ),
            memory: OfflineMemoryRepository(
                remote: RemoteMemoryRepository(client: client),
                directory: directory,
                indicator: indicator
            ),
            actionCenter: RemoteActionCenterRepository(client: client),
            notifications: RemoteNotificationRepository(client: client),
            assistant: RemoteAssistantRepository(client: client),
            research: RemoteResearchRepository(client: client),
            customers: RemoteCustomerRepository(client: client),
            tripList: TripListUseCase(repository: trips),
            createTrip: CreateTripUseCase(repository: trips),
            generatePlan: GeneratePlanUseCase(repository: plans),
            activatePlan: ActivatePlanUseCase(planRepository: plans, tripRepository: trips),
            submitRecord: SubmitVisitRecordUseCase(repository: visits, store: offlineStore),
            syncPush: SyncPushUseCase(store: offlineStore, transport: RemoteSyncTransport(client: client))
        )
    }

    private static func makeReceiptVault(
        directory: URL,
        client: APIClient,
        uploadsEnabled: Bool
    ) -> ReceiptVault? {
        let vaultDirectory = directory.appendingPathComponent("receipts", isDirectory: true)
        return try? ReceiptVault.open(
            directory: vaultDirectory,
            client: client,
            uploadsEnabled: uploadsEnabled
        )
    }

    /// 本地模式（ADR-0036）：仓储全部落在 App 内 SQLite。
    var isLocalMode: Bool { settings.runMode == "local" }

    func refreshOfflineSummary() {
        // 本地模式没有服务端可"回退"，缓存指示器语义不适用：固定显示本地口径。
        offlineSummary = isLocalMode ? "本地模式（数据在设备内）" : indicator.summary
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
        // Rebuild everything that carries the old identity / mode（P0-1：目录随身份切换）。
        let client = APIClient(configuration: next.apiConfiguration)
        apiClient = client
        directory = Self.accountDirectory(root: AppSettingsStore.defaultDirectory(), settings: next)
        receiptVault = Self.makeReceiptVault(
            directory: directory, client: client, uploadsEnabled: next.runMode != "local"
        )
        offlineStore = OfflineStore(directory: directory)
        let bundle = Self.makeBundle(
            settings: next, client: client, directory: directory,
            offlineStore: offlineStore, indicator: indicator, receiptVault: receiptVault
        )
        if next.runMode == "local" {
            // 重建时补挂本地库引用（bundle 内部持有，这里只留强引用）。
            localDatabase = Self.localDatabase(from: bundle)
        } else {
            localDatabase = nil
        }
        apply(bundle)
    }

    private static func localDatabase(from bundle: RepositoryBundle) -> LocalDatabase? {
        (bundle.trips as? LocalTripRepository)?.database
            ?? (bundle.plans as? LocalPlanRepository)?.database
    }

    /// 本地模式首次启动：空库自动载入示例数据（只做一次，用户清空后不再注入）。
    func seedDemoIfNeeded() async {
        guard isLocalMode, let database = localDatabase else { return }
        let flagKey = "local-demo-auto-seeded"
        guard !UserDefaults.standard.bool(forKey: flagKey) else { return }
        let existing = (try? database.count(table: "trips")) ?? 0
        guard existing == 0 else {
            UserDefaults.standard.set(true, forKey: flagKey)
            return
        }
        do {
            _ = try await LocalDemoSeeder.seed(environment: self, generatePlan: false)
            UserDefaults.standard.set(true, forKey: flagKey)
        } catch {
            // 自动注入失败不阻断首屏：设置页仍可手动载入，错误已在下一轮启动重试。
        }
    }

    func errorMessage(_ error: Error) -> String {
        if let apiError = error as? APIError { return apiError.message }
        if let localized = error as? LocalizedError, let description = localized.errorDescription {
            return description
        }
        return error.localizedDescription
    }
}
