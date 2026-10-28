import Foundation

/// CRM 同步状态。`PREPARED` 表示适配器已生成待导入文件，**不是**外部系统已写入。
enum CrmSyncStatus: String, Codable, CaseIterable, Identifiable {
    case queued = "QUEUED"
    case prepared = "PREPARED"
    case succeeded = "SUCCEEDED"
    case failed = "FAILED"
    case unknown = "UNKNOWN"

    var id: String { rawValue }

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = CrmSyncStatus(rawValue: raw) ?? .unknown
    }

    var label: String {
        switch self {
        case .queued: return "排队中"
        case .prepared: return "已生成待导入文件"
        case .succeeded: return "已同步到 CRM"
        case .failed: return "同步失败"
        case .unknown: return rawValue
        }
    }

    var isTerminal: Bool {
        self == .prepared || self == .succeeded || self == .failed
    }
}

/// `POST /v1/visit-records/{record_id}/crm-sync` 与 `GET /v1/crm-sync-jobs/{id}` 响应。
struct CrmSyncJob: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String
    var visitId: String
    var visitRecordId: String
    var status: CrmSyncStatus
    var jobId: String?
    var result: [String: JSONValue]?
    var error: String?
    var createdAt: Date?
    var updatedAt: Date?

    var externalId: String? {
        guard case let .string(value)? = result?["external_id"] else { return nil }
        return value
    }

    var detail: String? {
        guard case let .string(value)? = result?["detail"] else { return nil }
        return value
    }
}
