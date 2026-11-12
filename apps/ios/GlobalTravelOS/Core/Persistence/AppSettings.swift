import Foundation

/// Locally persisted identity + preferences. Authentication is deliberately "enter your Org/User
/// identity" rather than OAuth: the backend trusts `X-Org-Id` / `X-User-Id` in local development.
struct AppSettings: Codable, Equatable {
    var baseURLString: String
    var orgId: String
    var userId: String
    var deviceId: String
    var operatorName: String?
    var isAuthenticated: Bool
    var lastTripId: String?
    var prefersMiles: Bool
    /// 运行模式（ADR-0036）：cloud = 服务端后端；local = App 内 SQLite 本地模式。
    var runMode: String
    var llmBaseUrl: String?
    var llmApiKey: String?
    var llmModel: String?

    init(
        baseURLString: String,
        orgId: String,
        userId: String,
        deviceId: String,
        operatorName: String? = nil,
        isAuthenticated: Bool = false,
        lastTripId: String? = nil,
        prefersMiles: Bool = false,
        runMode: String = "cloud",
        llmBaseUrl: String? = nil,
        llmApiKey: String? = nil,
        llmModel: String? = nil
    ) {
        self.baseURLString = baseURLString
        self.orgId = orgId
        self.userId = userId
        self.deviceId = deviceId
        self.operatorName = operatorName
        self.isAuthenticated = isAuthenticated
        self.lastTripId = lastTripId
        self.prefersMiles = prefersMiles
        self.runMode = runMode
        self.llmBaseUrl = llmBaseUrl
        self.llmApiKey = llmApiKey
        self.llmModel = llmModel
    }

    static let standard = AppSettings(
        baseURLString: APIConfiguration.defaultBaseURLString,
        orgId: APIConfiguration.defaultOrgId,
        userId: APIConfiguration.defaultUserId,
        deviceId: UUID().uuidString.lowercased(),
        operatorName: nil,
        isAuthenticated: false,
        lastTripId: nil,
        prefersMiles: false,
        runMode: "cloud"
    )

    private enum CodingKeys: String, CodingKey {
        case baseURLString, orgId, userId, deviceId, operatorName
        case isAuthenticated, lastTripId, prefersMiles, runMode
        case llmBaseUrl, llmApiKey, llmModel
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        baseURLString = try container.decode(String.self, forKey: .baseURLString)
        orgId = try container.decode(String.self, forKey: .orgId)
        userId = try container.decode(String.self, forKey: .userId)
        deviceId = try container.decode(String.self, forKey: .deviceId)
        operatorName = try container.decodeIfPresent(String.self, forKey: .operatorName)
        isAuthenticated = try container.decodeIfPresent(Bool.self, forKey: .isAuthenticated) ?? false
        lastTripId = try container.decodeIfPresent(String.self, forKey: .lastTripId)
        prefersMiles = try container.decodeIfPresent(Bool.self, forKey: .prefersMiles) ?? false
        runMode = try container.decodeIfPresent(String.self, forKey: .runMode) ?? "cloud"
        llmBaseUrl = try container.decodeIfPresent(String.self, forKey: .llmBaseUrl)
        llmApiKey = try container.decodeIfPresent(String.self, forKey: .llmApiKey)
        llmModel = try container.decodeIfPresent(String.self, forKey: .llmModel)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(baseURLString, forKey: .baseURLString)
        try container.encode(orgId, forKey: .orgId)
        try container.encode(userId, forKey: .userId)
        try container.encode(deviceId, forKey: .deviceId)
        try container.encodeIfPresent(operatorName, forKey: .operatorName)
        try container.encode(isAuthenticated, forKey: .isAuthenticated)
        try container.encodeIfPresent(lastTripId, forKey: .lastTripId)
        try container.encode(prefersMiles, forKey: .prefersMiles)
        try container.encode(runMode, forKey: .runMode)
        try container.encodeIfPresent(llmBaseUrl, forKey: .llmBaseUrl)
        try container.encodeIfPresent(llmApiKey, forKey: .llmApiKey)
        try container.encodeIfPresent(llmModel, forKey: .llmModel)
    }

    var apiConfiguration: APIConfiguration {
        APIConfiguration.make(
            baseURLString: baseURLString,
            orgId: orgId,
            userId: userId,
            deviceId: deviceId
        )
    }

    var baseURL: URL? {
        URL(string: baseURLString)
    }

    /// Local-development defaults keep the app usable before anyone types an identity in.
    var isValid: Bool {
        !orgId.trimmingCharacters(in: .whitespaces).isEmpty
            && !userId.trimmingCharacters(in: .whitespaces).isEmpty
            && baseURL != nil
    }
}

/// Small JSON-file store for `AppSettings`, mirroring `OfflineStore` so both survive cold starts.
final class AppSettingsStore {
    private let fileURL: URL
    private let lock = NSLock()

    init(fileURL: URL) {
        self.fileURL = fileURL
    }

    convenience init(directory: URL, fileName: String = "app-settings.json") {
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

    func load() -> AppSettings {
        lock.lock()
        defer { lock.unlock() }
        guard let data = try? Data(contentsOf: fileURL),
              let settings = try? DateCoding.decode(AppSettings.self, from: data) else {
            return .standard
        }
        return settings
    }

    @discardableResult
    func save(_ settings: AppSettings) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let data = try? DateCoding.encode(settings) else { return false }
        do {
            try data.write(to: fileURL, options: .atomic)
            return true
        } catch {
            return false
        }
    }

    func reset() {
        _ = save(.standard)
    }
}
