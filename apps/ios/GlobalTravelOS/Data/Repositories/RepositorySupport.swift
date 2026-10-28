import Foundation

/// Shared state for the offline-wrapping repositories.
///
/// The offline wrappers fall back to the last known good response, and the UI needs to say so —
/// a silently stale trip list is worse than an explicit “离线缓存” banner. One indicator instance
/// is shared by every wrapper so any screen can ask whether *anything* on display is cached.
final class OfflineIndicator {
    private let lock = NSLock()
    private var servingCache = false
    private var messages: [String: Date] = [:]

    func markServedFromServer() {
        lock.lock()
        defer { lock.unlock() }
        servingCache = false
        messages.removeAll()
    }

    func markServedFromCache(_ source: String) {
        lock.lock()
        defer { lock.unlock() }
        servingCache = true
        messages[source] = Date()
    }

    func reset() {
        lock.lock()
        defer { lock.unlock() }
        servingCache = false
        messages.removeAll()
    }

    var isServingCachedData: Bool {
        lock.lock()
        defer { lock.unlock() }
        return servingCache
    }

    var cachedSources: [String] {
        lock.lock()
        defer { lock.unlock() }
        return messages.keys.sorted()
    }

    var summary: String {
        lock.lock()
        defer { lock.unlock() }
        guard servingCache, !messages.isEmpty else { return "数据来自服务端" }
        let sources = messages.keys.sorted().joined(separator: "、")
        guard let latest = messages.values.max() else { return "离线缓存：" + sources }
        return "离线缓存：\(sources) · 更新于 \(ISO8601.shortDisplay(from: latest))"
    }
}

/// Failures raised by the repository layer itself rather than by the wire.
enum RepositoryError: Error, Equatable, LocalizedError {
    case noCachedData(String)
    case requiresNetwork(String)
    case decoding(String)

    var errorDescription: String? { message }

    var message: String {
        switch self {
        case .noCachedData(let detail):
            return "本地没有可用缓存：\(detail)"
        case .requiresNetwork(let detail):
            return "该操作需要联网：\(detail)"
        case .decoding(let detail):
            return "数据解析失败：\(detail)"
        }
    }

    var isRecoverableOffline: Bool {
        if case .noCachedData = self { return true }
        return false
    }
}

/// Shared helper so each remote repository expresses "call, decode, surface APIError" the same way.
enum RepositoryCall {
    static func fetch<T: Decodable>(_ client: APIClient, _ request: APIRequest, as type: T.Type) async throws -> T {
        try await client.send(request, as: type)
    }

    static func fetchThrowing<T: Decodable>(
        _ client: APIClient,
        _ request: @autoclosure () throws -> APIRequest,
        as type: T.Type
    ) async throws -> T {
        try await client.send(try request(), as: type)
    }
}
