import Foundation
import os

/// 本地模式示例数据（一键载入，仅 local 模式且空库时可用）。
/// 走与用户操作完全相同的仓储路径——不是直插数据库的假数据。
enum LocalDemoSeeder {
    private static let logger = Logger(subsystem: "com.anari.globaltravelos", category: "demo-seed")

    @MainActor
    struct Summary {
        var tripName: String
        var visitCount: Int
        var planCreated: Bool
    }

    @MainActor
    static func seed(environment: AppEnvironment, generatePlan: Bool = true) async throws -> Summary {
        logger.info("seed start mode=\(environment.settings.runMode, privacy: .public)")
        let now = Date()
        // 窗口按**出发地时区**整天构造（与引擎 timezoneHome 一致——用设备时区会差出几小时，
        // 洲际段直接排不进当天）。
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Asia/Shanghai") ?? .current
        // 洲际段 840+ 分钟：起点必须给整天，否则当天放不下。
        let startDay = calendar.date(byAdding: .day, value: 2, to: now) ?? now
        let endDay = calendar.date(byAdding: .day, value: 6, to: now) ?? now
        let windowStart = calendar.date(bySettingHour: 8, minute: 0, second: 0, of: startDay) ?? startDay
        let windowEnd = calendar.date(bySettingHour: 20, minute: 0, second: 0, of: endDay) ?? endDay

        let trip = try await environment.createTripUseCase.create(
            name: "示例：柏林—阿姆斯特丹客户周",
            purpose: "客户拜访与产线对接",
            originName: "上海总部",
            countryCode: "CN",
            timezone: "Asia/Shanghai",
            latitude: 31.2304,
            longitude: 121.4737,
            start: windowStart,
            end: windowEnd,
            destinationCountries: ["DE", "NL"]
        )

        let visits = [
            VisitCreateRequest(
                placeName: "大众汽车（柏林总部）", city: "Berlin", countryCode: "DE",
                latitude: 52.5208, longitude: 13.3702, timezone: "Europe/Berlin",
                title: "大众总部年度复审", serviceMinutes: 90, priority: 1,
                movementMode: "AIR"
            ),
            VisitCreateRequest(
                placeName: "ASML（费尔德霍芬园区）", city: "Eindhoven", countryCode: "NL",
                latitude: 51.4416, longitude: 5.4697, timezone: "Europe/Amsterdam",
                title: "ASML 产线对接", serviceMinutes: 90, priority: 1,
                movementMode: "AIR"
            ),
        ]
        for request in visits {
            _ = try await environment.visitRepository.createVisit(tripId: trip.id, request: request)
        }
        logger.info(
            "seed core data ready trip=\(trip.id, privacy: .public) visits=\(visits.count, privacy: .public)"
        )

        guard generatePlan else {
            logger.info("seed route optimization deferred until user requests a plan")
            return Summary(tripName: trip.name, visitCount: visits.count, planCreated: false)
        }

        var planCreated = false
        do {
            let generated = try await environment.planRepository.generatePlan(
                tripId: trip.id, request: GeneratePlanRequest()
            )
            let job = try await environment.planRepository.job(id: generated.jobId)
            if let planId = job.result?.planVersionId {
                _ = try await environment.planRepository.planDetail(tripId: trip.id, planId: planId)
                planCreated = true
            } else {
                logger.error("seed plan job has no plan id: \(String(describing: job.status), privacy: .public)")
            }
        } catch {
            logger.error("seed plan generation failed: \(String(describing: error), privacy: .public)")
        }

        return Summary(tripName: trip.name, visitCount: visits.count, planCreated: planCreated)
    }

    /// 仅当本地库没有任何行程时可载入（避免污染用户真实数据）。
    @MainActor
    static func canSeed(environment: AppEnvironment) async -> Bool {
        guard environment.settings.runMode == "local" else { return false }
        let page = try? await environment.tripRepository.listTrips(page: PageRequest(limit: 1))
        return (page?.items.isEmpty ?? true)
    }
}
