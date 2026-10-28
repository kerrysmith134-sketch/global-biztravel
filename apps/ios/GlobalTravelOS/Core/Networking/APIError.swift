import Foundation

/// Errors surfaced by the wire layer. `serverError` carries the decoded unified error envelope,
/// which is what the UI switches on for `PLAN_VERSION_CONFLICT` / `PRECONDITION_FAILED` /
/// `PLAN_NOT_FEASIBLE`.
enum APIError: Error, Equatable {
    case invalidURL(String)
    case transport(String)
    case invalidResponse(String)
    case serverError(code: String, message: String, traceId: String?, details: [String: JSONValue])
    case unexpectedStatus(Int)
    case notConfigured(String)

    /// Machine-readable code from the error envelope, when the server sent one.
    var errorCode: String? {
        if case let .serverError(code, _, _, _) = self { return code }
        return nil
    }

    /// The HTTP status the backend documents for this error code.
    /// `serverError` does not carry the status code, so it is re-derived here.
    var httpStatusHint: Int? {
        switch self {
        case .unexpectedStatus(let status):
            return status
        case .serverError(let code, _, _, _):
            switch code {
            case "PLAN_VERSION_CONFLICT": return 409
            case "PRECONDITION_FAILED": return 412
            case "PLAN_NOT_FEASIBLE": return 422
            case "UNAUTHENTICATED", "UNAUTHORIZED": return 401
            case "FORBIDDEN": return 403
            case "NOT_FOUND": return 404
            case "VALIDATION_ERROR": return 422
            case "RATE_LIMITED": return 429
            default: return nil
            }
        case .invalidURL, .transport, .invalidResponse, .notConfigured:
            return nil
        }
    }

    var isVersionConflict: Bool { errorCode == "PLAN_VERSION_CONFLICT" }
    var isPreconditionFailed: Bool { errorCode == "PRECONDITION_FAILED" }
    var isPlanNotFeasible: Bool { errorCode == "PLAN_NOT_FEASIBLE" }

    /// Only transport/5xx-ish failures may be retried blindly; 409/412/422 must go back to the user.
    var isRetryable: Bool {
        switch self {
        case .transport, .invalidResponse:
            return true
        case .unexpectedStatus(let status):
            return status >= 500
        case .serverError:
            return false
        case .invalidURL, .notConfigured:
            return false
        }
    }

    var message: String {
        switch self {
        case .invalidURL(let detail):
            return "请求地址无效：\(detail)"
        case .transport(let detail):
            return "网络不可用：\(detail)"
        case .invalidResponse(let detail):
            return "服务端响应无法解析：\(detail)"
        case .serverError(let code, let message, _, _):
            return "服务端错误 \(code)：\(message)"
        case .unexpectedStatus(let status):
            return "服务端返回了非预期状态码 \(status)"
        case .notConfigured(let detail):
            return "客户端未配置：\(detail)"
        }
    }
}

extension APIError: LocalizedError {
    var errorDescription: String? { message }
}
