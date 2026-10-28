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

    static let standard = AppSettings(
        baseURLString: APIConfiguration.defaultBaseURLString,
        orgId: APIConfiguration.defaultOrgId,
        userId: APIConfiguration.defaultUserId,
        deviceId: UUID().uuidString.lowercased(),
        operatorName: nil,
        isAuthenticated: false,
        lastTripId: nil,
        prefersMiles: false
    )

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