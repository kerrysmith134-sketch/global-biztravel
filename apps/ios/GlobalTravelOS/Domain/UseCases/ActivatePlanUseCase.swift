import Foundation

/// Activation-specific failure vocabulary.
///
/// The three documented failure modes mean very different things to the operator, so they get
/// distinct cases instead of being flattened into one API error:
/// - 409 `PLAN_VERSION_CONFLICT` — someone else activated a plan first; refresh and re-choose.
/// - 412 `PRECONDITION_FAILED` — our `If-Match` version is stale; refresh and retry.
/// - 422 `PLAN_NOT_FEASIBLE` — the plan is not activatable at all; re-solve instead.
enum PlanActivationError: Error, Equatable {
    case versionConflict(message: String, traceId: String?)
    case preconditionFailed(message: String, traceId: String?)
    case planNotFeasible(message: String, traceId: String?)
    case transport(message: String)
    case unexpected(message: String)

    /// The trip version moved under us — the UI must reload the trip before offering activation
    /// again.
    var requiresVersionRefresh: Bool {
        switch self {
        case .versionConflict, .preconditionFailed: return true
        case .planNotFeasible, .transport, .unexpected: return false
        }
    }

    var requiresReplan: Bool {
        if case .planNotFeasible = self { return true }
        return false
    }

    var isConflict: Bool {
        if case .versionConflict = self { return true }
        return false
    }

    var traceId: String? {
        switch self {
        case let .versionConflict(_, traceId): return traceId
        case let .preconditionFailed(_, traceId): return traceId
        case let .planNotFeasible(_, traceId): return traceId
        case .transport, .unexpected: return nil
        }
    }

    var message: String {
        switch self {
        case let .versionConflict(message, _):
            return "计划版本冲突：另一个会话已激活了更新的计划。\(message)"
        case let .preconditionFailed(message, _):
            return "激活被拒绝：本地缓存的行程版本已过期。\(message)"
        case let .planNotFeasible(message, _):
            return "该计划不可行，无法激活。\(message)"
        case let .transport(message):
            return "激活请求未送达：\(message)"
        case let .unexpected(message):
            return "激活失败：\(message)"
        }
    }

    var headline: String {
        switch self {
        case .versionConflict: return "版本冲突（409）"
        case .preconditionFailed: return "前置条件失败（412）"
        case .planNotFeasible: return "计划不可行（422）"
        case .transport: return "网络不可用"
        case .unexpected: return "服务端未预期错误"
        }
    }

    /// What the UI offers as the next step.
    var recoveryHint: String {
        switch self {
        case .versionConflict:
            return "已为你重新拉取行程版本，请确认后再激活。"
        case .preconditionFailed:
            return "已刷新行程版本，请再次点击激活。"
        case .planNotFeasible:
            return "请重新生成计划，或先处理未安排项与校验失败项。"
        case .transport:
            return "检查网络后重试；激活请求带幂等键，重复提交是安全的。"
        case .unexpected:
            return "可重试一次；若持续失败请查看服务端 trace。"
        }
    }
}

/// Activates a plan version against the trip's current version (`If-Match`) and translates the
/// documented failures into `PlanActivationError`.
final class ActivatePlanUseCase {
    private let planRepository: PlanRepository
    private let tripRepository: TripRepository

    init(planRepository: PlanRepository, tripRepository: TripRepository) {
        self.planRepository = planRepository
        self.tripRepository = tripRepository
    }

    /// Activates `planId` using `tripVersion` as the optimistic-concurrency token.
    func activate(tripId: String, planId: String, tripVersion: Int) async throws -> PlanActivation {
        do {
            return try await planRepository.activatePlan(
                tripId: tripId,
                planId: planId,
                tripVersion: tripVersion
            )
        } catch let error as APIError {
            throw ActivatePlanUseCase.map(error)
        }
    }

    /// Re-reads the trip so the UI can pick up a version that moved while it was holding a plan.
    func refreshTripVersion(tripId: String) async throws -> Trip {
        try await tripRepository.trip(id: tripId)
    }

    /// Activate-and-refresh in one step: on a version conflict the caller still gets the fresh
    /// trip, so the screen can re-enable the button with the correct `If-Match` value.
    func activateAndRefresh(
        tripId: String,
        planId: String,
        tripVersion: Int
    ) async -> (activation: PlanActivation?, error: PlanActivationError?, refreshedTrip: Trip?) {
        do {
            let activation = try await activate(
                tripId: tripId,
                planId: planId,
                tripVersion: tripVersion
            )
            let trip = try? await refreshTripVersion(tripId: tripId)
            return (activation, nil, trip)
        } catch let error as PlanActivationError {
            let trip = error.requiresVersionRefresh ? try? await refreshTripVersion(tripId: tripId) : nil
            return (nil, error, trip)
        } catch {
            return (nil, .unexpected(message: error.localizedDescription), nil)
        }
    }

    // MARK: - Error mapping

    static func map(_ error: APIError) -> PlanActivationError {
        switch error {
        case let .serverError(code, message, traceId, _):
            switch code {
            case "PLAN_VERSION_CONFLICT":
                return .versionConflict(message: message, traceId: traceId)
            case "PRECONDITION_FAILED":
                return .preconditionFailed(message: message, traceId: traceId)
            case "PLAN_NOT_FEASIBLE":
                return .planNotFeasible(message: message, traceId: traceId)
            default:
                return .unexpected(message: message)
            }
        case .unexpectedStatus(let status):
            switch status {
            case 409: return .versionConflict(message: "HTTP 409", traceId: nil)
            case 412: return .preconditionFailed(message: "HTTP 412", traceId: nil)
            case 422: return .planNotFeasible(message: "HTTP 422", traceId: nil)
            default: return .unexpected(message: "HTTP \(status)")
            }
        case let .transport(message):
            return .transport(message: message)
        case let .invalidURL(detail), let .invalidResponse(detail):
            return .unexpected(message: detail)
        case let .notConfigured(detail):
            return .unexpected(message: detail)
        }
    }
}