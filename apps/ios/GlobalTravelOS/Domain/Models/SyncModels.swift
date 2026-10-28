import Foundation

/// `entity:"visit_record|customer|plan_stop"`.
enum SyncEntity: String, Codable, CaseIterable, Identifiable {
    case visitRecord = "visit_record"
    case customer = "customer"
    case planStop = "plan_stop"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .visitRecord: return "拜访记录"
        case .customer: return "客户资料"
        case .planStop: return "计划停靠"
        }
    }

    /// Design doc §10.5 conflict matrix: visit records use last-writer-wins with a conflict copy,
    /// plan stops are rejected and recorded as a conflict, customer fields merge per field.
    var conflictPolicyLabel: String {
        switch self {
        case .visitRecord: return "后写覆盖并保留冲突副本"
        case .customer: return "字段级合并"
        case .planStop: return "拒绝写入并记录冲突"
        }
    }
}

enum SyncOperation: String, Codable, CaseIterable, Identifiable {
    case upsert
    case delete

    var id: String { rawValue }

    var label: String {
        switch self {
        case .upsert: return "写入"
        case .delete: return "删除"
        }
    }
}

/// `status:"applied|conflict|rejected"` — the per-item verdict the offline queue renders one row at
/// a time (design doc §10.5).
enum SyncItemStatus: String, Codable, Equatable, Identifiable {
    case applied
    case conflict
    case rejected
    case unknown

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = SyncItemStatus(rawValue: raw) ?? .unknown
    }

    var id: String { rawValue }

    var label: String {
        switch self {
        case .applied: return "已应用"
        case .conflict: return "冲突"
        case .rejected: return "被拒绝"
        case .unknown: return "未知"
        }
    }

    var glyph: String {
        switch self {
        case .applied: return "checkmark.circle.fill"
        case .conflict: return "arrow.triangle.branch"
        case .rejected: return "hand.raised.fill"
        case .unknown: return "questionmark.circle"
        }
    }

    /// `applied` is the only verdict that clears an item out of the outbox.
    var clearsQueue: Bool { self == .applied }
}

/// `items:[{item_id, entity, operation, payload, client_updated_at, base_version}]`.
struct SyncItem: Codable, Equatable, Identifiable {
    var itemId: String
    var entity: SyncEntity
    var operation: SyncOperation
    var payload: [String: JSONValue]
    var clientUpdatedAt: Date
    var baseVersion: Int?

    init(
        itemId: String,
        entity: SyncEntity,
        operation: SyncOperation = .upsert,
        payload: [String: JSONValue] = [:],
        clientUpdatedAt: Date = Date(),
        baseVersion: Int? = nil
    ) {
        self.itemId = itemId
        self.entity = entity
        self.operation = operation
        self.payload = payload
        self.clientUpdatedAt = clientUpdatedAt
        self.baseVersion = baseVersion
    }

    var id: String { itemId }

    /// A missing `payload` is tolerated so a partially specified item still decodes instead of
    /// taking down the whole sync screen.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.itemId = try container.decode(String.self, forKey: .itemId)
        self.entity = try container.decode(SyncEntity.self, forKey: .entity)
        self.operation = try container.decodeIfPresent(SyncOperation.self, forKey: .operation) ?? .upsert
        self.payload = try container.decodeIfPresent([String: JSONValue].self, forKey: .payload) ?? [:]
        self.clientUpdatedAt = try container.decodeIfPresent(Date.self, forKey: .clientUpdatedAt) ?? Date()
        self.baseVersion = try container.decodeIfPresent(Int.self, forKey: .baseVersion)
    }

    private enum CodingKeys: String, CodingKey {
        case itemId
        case entity
        case operation
        case payload
        case clientUpdatedAt
        case baseVersion
    }

    /// Convenience constructor for the most common outbox entry: an offline visit record.
    ///
    /// `visit_id` is added to the payload because `POST /v1/sync:push` cannot infer which visit a
    /// note belongs to from `client_record_id` alone (offline-only drafts never hit the direct
    /// record endpoint first). Without it the server rejects the item instead of guessing.
    static func visitRecord(
        _ request: VisitRecordRequest,
        visitId: String,
        baseVersion: Int? = nil
    ) -> SyncItem {
        var payload = SyncPayloadBuilder.encode(request)
        payload["visit_id"] = .string(visitId)
        return SyncItem(
            itemId: request.clientRecordId,
            entity: .visitRecord,
            operation: .upsert,
            payload: payload,
            clientUpdatedAt: request.clientUpdatedAt,
            baseVersion: baseVersion
        )
    }

    var payloadSummary: String {
        guard !payload.isEmpty else { return "无 payload" }
        return payload.keys.sorted()
            .map { "\($0)=\(payload[$0]?.displayDescription ?? "")" }
            .joined(separator: ", ")
    }
}

/// `POST /v1/sync:push` request body.
struct SyncPushRequest: Codable, Equatable {
    var deviceId: String
    var items: [SyncItem]

    init(deviceId: String, items: [SyncItem]) {
        self.deviceId = deviceId
        self.items = items
    }

    var isEmpty: Bool { items.isEmpty }
}

/// `results:[{item_id, entity, status, server_version, conflict}]`.
struct SyncItemResult: Codable, Equatable, Identifiable {
    var itemId: String
    var entity: SyncEntity?
    var status: SyncItemStatus
    var serverVersion: Int?
    var conflict: ConflictRecord?

    init(
        itemId: String,
        entity: SyncEntity? = nil,
        status: SyncItemStatus,
        serverVersion: Int? = nil,
        conflict: ConflictRecord? = nil
    ) {
        self.itemId = itemId
        self.entity = entity
        self.status = status
        self.serverVersion = serverVersion
        self.conflict = conflict
    }

    var id: String { itemId }

    var statusLabel: String { status.label }

    var detailText: String {
        var parts: [String] = []
        if let entity { parts.append(entity.label) }
        parts.append(status.label)
        if let serverVersion { parts.append("server v\(serverVersion)") }
        if let conflict { parts.append(conflict.reasonLabel) }
        return parts.joined(separator: " · ")
    }
}

/// `summary:{applied, conflict, rejected}`.
struct SyncSummary: Codable, Equatable {
    var applied: Int?
    var conflict: Int?
    var rejected: Int?

    init(applied: Int? = nil, conflict: Int? = nil, rejected: Int? = nil) {
        self.applied = applied
        self.conflict = conflict
        self.rejected = rejected
    }

    var appliedCount: Int { applied ?? 0 }
    var conflictCount: Int { conflict ?? 0 }
    var rejectedCount: Int { rejected ?? 0 }
    var totalCount: Int { appliedCount + conflictCount + rejectedCount }

    var displayText: String {
        "已应用 \(appliedCount) · 冲突 \(conflictCount) · 拒绝 \(rejectedCount)"
    }
}

/// `POST /v1/sync:push` response.
struct SyncPushResult: Codable, Equatable {
    var results: [SyncItemResult]
    var summary: SyncSummary?

    init(results: [SyncItemResult] = [], summary: SyncSummary? = nil) {
        self.results = results
        self.summary = summary
    }

    var resultList: [SyncItemResult] { results }

    /// Recomputed from the per-item results so the UI can trust it even when the server omits
    /// `summary`.
    var computedSummary: SyncSummary {
        SyncSummary(
            applied: results.filter { $0.status == .applied }.count,
            conflict: results.filter { $0.status == .conflict }.count,
            rejected: results.filter { $0.status == .rejected }.count
        )
    }

    var effectiveSummary: SyncSummary { summary ?? computedSummary }

    func result(forItemId itemId: String) -> SyncItemResult? {
        results.first { $0.itemId == itemId }
    }

    var conflicted: [SyncItemResult] { results.filter { $0.status == .conflict } }
    var rejected: [SyncItemResult] { results.filter { $0.status == .rejected } }
}

/// One queued write, persisted inside `OfflineStore` between launches.
///
/// This is the client-side bookkeeping that makes §10.5 possible: the *item* is the payload the
/// server sees, and the extra fields record what the server said about it last time so a conflict
/// survives a cold start.
struct OutboxItem: Codable, Equatable, Identifiable {
    var item: SyncItem
    var attempts: Int
    var lastError: String?
    var lastStatus: SyncItemStatus?
    var serverVersion: Int?
    var conflict: ConflictRecord?
    var enqueuedAt: Date
    var updatedAt: Date

    var id: String { item.itemId }

    var itemId: String { item.itemId }

    var entity: SyncEntity { item.entity }

    var displayStatus: String {
        guard let lastStatus else { return "待同步" }
        return lastStatus.label
    }

    var needsManualAttention: Bool {
        lastStatus == .conflict || lastStatus == .rejected
    }

    var retryLabel: String { "重试 \(attempts) 次" }

    var conflictDetail: String? {
        guard let conflict else { return nil }
        var parts = [conflict.reasonLabel]
        if let server = conflict.server, !server.isEmpty {
            parts.append("服务端版本已保留")
        }
        if let client = conflict.client, !client.isEmpty {
            parts.append("本地版本待处理")
        }
        return parts.joined(separator: " · ")
    }
}

/// Aggregate verdict of one `sync:push` round, including items that never reached the server.
struct SyncRunOutcome: Codable, Equatable {
    var attempted: Int
    var applied: Int
    var conflict: Int
    var rejected: Int
    /// Items the transport itself failed on — still queued, retryable, not a server verdict.
    var failed: Int
    var results: [SyncItemResult]
    var failures: [String: String]
    var ranAt: Date
    var errorMessage: String?

    init(
        attempted: Int = 0,
        applied: Int = 0,
        conflict: Int = 0,
        rejected: Int = 0,
        failed: Int = 0,
        results: [SyncItemResult] = [],
        failures: [String: String] = [:],
        ranAt: Date = Date(),
        errorMessage: String? = nil
    ) {
        self.attempted = attempted
        self.applied = applied
        self.conflict = conflict
        self.rejected = rejected
        self.failed = failed
        self.results = results
        self.failures = failures
        self.ranAt = ranAt
        self.errorMessage = errorMessage
    }

    var summaryLine: String {
        "提交 \(attempted) 条 · 应用 \(applied) · 冲突 \(conflict) · 拒绝 \(rejected) · 未送达 \(failed)"
    }

    var hasItemsNeedingAttention: Bool { conflict > 0 || rejected > 0 }

    var isClean: Bool { applied == attempted && attempted > 0 }
}

/// Serialises a domain value into the snake_case payload the sync endpoint expects.
///
/// The domain value is encoded with `DateCoding.encoder()` (which converts Swift's camelCase
/// property names to the wire's `snake_case`) and then re-read with a *plain* `JSONDecoder()` so
/// the resulting dictionary keys are already the wire names — the request body therefore contains
/// `client_record_id`, not `clientRecordId`.
enum SyncPayloadBuilder {
    static func encode<T: Encodable>(_ value: T) -> [String: JSONValue] {
        guard let data = try? DateCoding.encode(value) else { return [:] }
        return (try? JSONDecoder().decode([String: JSONValue].self, from: data)) ?? [:]
    }

    static func visitRecordPayload(_ request: VisitRecordRequest) -> [String: JSONValue] {
        encode(request)
    }

    static func deletePayload(itemId: String, entity: SyncEntity) -> [String: JSONValue] {
        [
            "item_id": .string(itemId),
            "entity": .string(entity.rawValue),
        ]
    }
}