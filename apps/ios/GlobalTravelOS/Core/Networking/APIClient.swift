import Foundation

enum HTTPMethod: String {
    case get = "GET"
    case post = "POST"
    case put = "PUT"
    case patch = "PATCH"
    case delete = "DELETE"
}

/// Identity + base URL the client sends on every request.
struct APIConfiguration: Equatable {
    var baseURL: URL
    var orgId: String
    var userId: String
    var deviceId: String

    /// TestFlight 测试包默认直连开发机后端（与设置页可覆盖；生产包必须改回 https 域名）。
    /// Mac 的局域网 IP 变化时需同步更新此处（`ipconfig getifaddr en0`）。
    static let defaultBaseURLString = "http://10.128.29.211:8080"
    static let defaultOrgId = "11111111-1111-1111-1111-111111111111"
    static let defaultUserId = "22222222-2222-2222-2222-222222222222"

    static func make(
        baseURLString: String,
        orgId: String,
        userId: String,
        deviceId: String
    ) -> APIConfiguration {
        let url = URL(string: baseURLString)
            ?? URL(string: defaultBaseURLString)
            ?? URL(fileURLWithPath: "/")
        return APIConfiguration(baseURL: url, orgId: orgId, userId: userId, deviceId: deviceId)
    }
}

/// A single wire call. Everything the transport needs to build the `URLRequest` lives here so
/// endpoints stay declarative and the client stays the only place that touches URLSession.
struct APIRequest {
    var method: HTTPMethod = .get
    var path: String
    var queryItems: [URLQueryItem] = []
    var body: Data? = nil
    var contentType: String? = nil
    var additionalHeaders: [String: String] = [:]
    /// Sent as `Idempotency-Key` for every write operation.
    var idempotencyKey: String? = nil
    /// Sent as `If-Match` — plan activation requires the current trip version.
    var ifMatch: String? = nil

    init(
        method: HTTPMethod = .get,
        path: String,
        queryItems: [URLQueryItem] = [],
        body: Data? = nil,
        contentType: String? = nil,
        additionalHeaders: [String: String] = [:],
        idempotencyKey: String? = nil,
        ifMatch: String? = nil
    ) {
        self.method = method
        self.path = path
        self.queryItems = queryItems
        self.body = body
        self.contentType = contentType
        self.additionalHeaders = additionalHeaders
        self.idempotencyKey = idempotencyKey
        self.ifMatch = ifMatch
    }

    /// Idempotency keys the client generates are always lowercase UUIDs so they sort/compare
    /// predictably in logs and in the offline queue.
    static func newIdempotencyKey() -> String {
        UUID().uuidString.lowercased()
    }
}

final class APIClient {
    var configuration: APIConfiguration
    private let session: URLSession

    init(configuration: APIConfiguration, session: URLSession = .shared) {
        self.configuration = configuration
        self.session = session
    }

    // MARK: - Request building

    func makeURLRequest(_ request: APIRequest) throws -> URLRequest {
        guard var components = URLComponents(
            url: configuration.baseURL,
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidURL(configuration.baseURL.absoluteString)
        }

        var path = request.path
        if !path.hasPrefix("/") { path = "/" + path }
        let basePath = components.path.hasSuffix("/") ? String(components.path.dropLast()) : components.path
        components.path = basePath + path
        if !request.queryItems.isEmpty {
            components.queryItems = request.queryItems
        }

        guard let url = components.url else {
            throw APIError.invalidURL(configuration.baseURL.absoluteString + path)
        }

        var urlRequest = URLRequest(url: url)
        urlRequest.httpMethod = request.method.rawValue
        urlRequest.timeoutInterval = 30
        urlRequest.setValue("application/json", forHTTPHeaderField: "Accept")
        urlRequest.setValue(configuration.orgId, forHTTPHeaderField: "X-Org-Id")
        urlRequest.setValue(configuration.userId, forHTTPHeaderField: "X-User-Id")

        if let body = request.body {
            urlRequest.httpBody = body
            urlRequest.setValue(request.contentType ?? "application/json", forHTTPHeaderField: "Content-Type")
        }

        for (field, value) in request.additionalHeaders {
            urlRequest.setValue(value, forHTTPHeaderField: field)
        }

        if let key = request.idempotencyKey {
            urlRequest.setValue(key, forHTTPHeaderField: "Idempotency-Key")
        }

        if let version = request.ifMatch {
            urlRequest.setValue(version, forHTTPHeaderField: "If-Match")
        }

        return urlRequest
    }

    // MARK: - Sending

    func sendData(_ request: APIRequest) async throws -> Data {
        let urlRequest = try makeURLRequest(request)
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: urlRequest)
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.transport(error.localizedDescription)
        }

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse("响应不是 HTTP 响应")
        }
        guard (200 ..< 300).contains(http.statusCode) else {
            throw APIClient.decodeError(data: data, statusCode: http.statusCode)
        }
        return data
    }

    func send<T: Decodable>(_ request: APIRequest, as type: T.Type) async throws -> T {
        let data = try await sendData(request)
        guard !data.isEmpty else {
            throw APIError.invalidResponse("响应体为空，无法解析 \(T.self)")
        }
        do {
            return try DateCoding.decode(T.self, from: data)
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.invalidResponse("无法解析 \(T.self)：\(error)")
        }
    }

    /// For endpoints whose 2xx response body the client never reads.
    func sendWithoutResponse(_ request: APIRequest) async throws {
        _ = try await sendData(request)
    }

    // MARK: - Error envelope

    /// Turns any non-2xx body into `.serverError` when it matches the unified envelope, and falls
    /// back to `.unexpectedStatus` when it does not (proxies, HTML error pages, empty bodies).
    static func decodeError(data: Data, statusCode: Int) -> APIError {
        if !data.isEmpty, let envelope = try? DateCoding.decode(APIErrorEnvelope.self, from: data) {
            let body = envelope.error
            return .serverError(
                code: body.code,
                message: body.message.isEmpty ? "HTTP \(statusCode)" : body.message,
                traceId: body.traceId,
                details: body.details
            )
        }
        if !data.isEmpty, let envelope = try? JSONDecoder().decode(APIErrorEnvelope.self, from: data) {
            let body = envelope.error
            return .serverError(
                code: body.code,
                message: body.message.isEmpty ? "HTTP \(statusCode)" : body.message,
                traceId: body.traceId,
                details: body.details
            )
        }
        return .unexpectedStatus(statusCode)
    }
}
