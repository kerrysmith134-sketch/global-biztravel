import Foundation

/// 计划变更操作类型（`POST /v1/trips/{id}/plan-changes` 的 `operation_type`）。
///
/// 语义由服务端 `plan_change_request_service._apply_change` 固定：变更先改**拜访输入**，
/// 再入队一次重求解，由 Worker 生成新计划版本。客户端不自行改计划。
enum PlanChangeOperation: String, Codable, CaseIterable, Identifiable {
    case cancelVisit = "CANCEL_VISIT"
    case changeVisitTime = "CHANGE_VISIT_TIME"
    case changeVisitDuration = "CHANGE_VISIT_DURATION"
    case addVisit = "ADD_VISIT"
    case removeVisit = "REMOVE_VISIT"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .cancelVisit: return "取消拜访"
        case .changeVisitTime: return "改拜访时间"
        case .changeVisitDuration: return "改停留时长"
        case .addVisit: return "新增拜访"
        case .removeVisit: return "删除拜访"
        }
    }
}

/// `POST /v1/trips/{id}/plan-changes` 请求体。
///
/// `basePlanVersionId` 必须是**当前生效**版本、`baseTripVersion` 必须是当前出行版本，
/// 否则服务端回 409（`PlanVersionConflict`）——这是「在过期视图上提变更」的护栏。
struct PlanChangeCreate: Codable, Equatable {
    var basePlanVersionId: String
    var baseTripVersion: Int
    var source: String
    var operationType: PlanChangeOperation
    /// 操作参数（`CANCEL_VISIT` 需要 `visit_id`；`CHANGE_VISIT_TIME` 另需新时间窗）。
    var payload: [String: String]
    var reason: String?

    init(
        basePlanVersionId: String,
        baseTripVersion: Int,
        operationType: PlanChangeOperation,
        payload: [String: String],
        source: String = "MAP",
        reason: String? = nil
    ) {
        self.basePlanVersionId = basePlanVersionId
        self.baseTripVersion = baseTripVersion
        self.source = source
        self.operationType = operationType
        self.payload = payload
        self.reason = reason
    }
}

/// `POST /v1/trips/{id}/plan-changes` 的 202 响应：变更已受理、重求解已入队。
struct PlanChangeAccepted: Codable, Equatable {
    var changeRequestId: String
    var status: String
    var optimizationJobId: String
    var tripVersion: Int

    var summary: String {
        "变更已受理（\(status)），正在生成新计划版本"
    }
}
