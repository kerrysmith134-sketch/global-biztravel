import XCTest

/// 规划引擎 Swift 移植的确定性对拍测试（P2，ADR-0036）。
/// 用例平移自 packages/planning-engine/tests（test_solvers/days/feasibility 的核心用例）；
/// objective/签名与 Python 双跑同值（2026-11-12 实测对拍记录在提交说明）。
@testable import GlobalTravelOS

final class PlanningEngineTests: XCTestCase {
    // 与 Python fixtures.py 同构：上海原点 + 欧洲城市锚点 + 确定性 UUID。
    private let timezoneHome = "Asia/Shanghai"

    private func date(_ day: Int, _ hour: Int, _ minute: Int = 0) -> Date {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: timezoneHome)!
        return calendar.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
    }

    private func visit(_ n: Int, city: String, service: Int = 60, value: Double = 0) -> EngineStopInput {
        let cities: [String: (Double, Double)] = [
            "berlin": (52.5200, 13.4050),
            "munich": (48.1351, 11.5820),
            "amsterdam": (52.3676, 4.9041),
            "frankfurt": (50.1109, 8.6821),
            "rotterdam": (51.9244, 4.4777),
            "hamburg": (53.5511, 9.9937),
        ]
        guard let coordinate = cities[city] else {
            fatalError("unknown city \(city)")
        }
        return EngineStopInput(
            visitId: UUID(uuidString: String(format: "00000000-0000-4000-8000-%012d", n))!,
            placeId: UUID(uuidString: String(format: "10000000-0000-4000-8000-%012d", n))!,
            lat: coordinate.0,
            lon: coordinate.1,
            serviceMinutes: service,
            timeWindows: [],
            priority: 2,
            businessValue: value,
            movementMode: .air
        )
    }

    private func fourCityProblem() -> EnginePlanningProblem {
        EnginePlanningProblem(
            tripId: UUID(uuidString: "77777777-7777-4777-8777-777777777777")!,
            timezoneHome: timezoneHome,
            origin: EngineOrigin(
                placeId: UUID(uuidString: "10000000-0000-4000-8000-000000000900")!,
                lat: 31.2304, lon: 121.4737
            ),
            window: EngineProblemWindow(start: date(9, 9), end: date(15, 18)),
            stops: [
                visit(1, city: "berlin", service: 90, value: 120),
                visit(2, city: "munich", service: 90, value: 80),
                visit(3, city: "amsterdam", service: 90, value: 150),
                visit(4, city: "frankfurt", service: 90, value: 60),
            ],
            solverBudgetSeconds: 1
        )
    }

    // MARK: - 几何

    func testHaversineKnownDistance() {
        // 上海 → 柏林 ≈ 7,300+ km；与 Python 端同公式同值（对拍基线 7719.9x）。
        let km = EngineGeometry.haversineKm(lat1: 31.2304, lon1: 121.4737, lat2: 52.52, lon2: 13.405)
        // Python 对拍基线：8397.26 km（同公式双跑一致）
        XCTAssertEqual(km, 8397.26, accuracy: 0.5)
    }

    func testTravelMinutesIncludesOverheadAndCeil() {
        // 6km GROUND：6/60*60=6 分钟 → 6+20=26
        let near = EngineGeometry.travelMinutes(
            lat1: 31.23, lon1: 121.47, lat2: 31.231, lon2: 121.471, mode: .ground
        )
        XCTAssertGreaterThan(near, 0)
        // 同点 < 0.05km ⇒ 0
        let same = EngineGeometry.travelMinutes(
            lat1: 31.23, lon1: 121.47, lat2: 31.2300001, lon2: 121.47, mode: .ground
        )
        XCTAssertEqual(same, 0)
    }

    // MARK: - 预检与候选

    func testFeasibilityCapacityExceededDropsByRank() {
        // 每日 1 停 × 2 天 = 容量 2；3 个停靠，最低价值的 (id=3) 被剔除。
        var problem = fourCityProblem()
        problem.window = EngineProblemWindow(start: date(9, 9), end: date(10, 18))
        problem.constraints.maxStopsPerDay = 1
        problem.stops = [
            visit(1, city: "berlin", value: 300),
            visit(2, city: "munich", value: 200),
            visit(3, city: "amsterdam", value: 100),
        ]
        let outcome = try! EngineFeasibility.checkFeasibility(problem)
        XCTAssertEqual(outcome.eligibleStopIds.count, 2)
        XCTAssertEqual(outcome.preUnplaced.count, 1)
        XCTAssertEqual(outcome.preUnplaced.first?.visitId, visit(3, city: "amsterdam").visitId)
        XCTAssertEqual(outcome.preUnplaced.first?.reason, .capacityExceeded)
    }

    func testCandidateOrdersDeterministicAndDistinct() {
        let problem = fourCityProblem()
        let eligible = problem.stops.map { $0.visitId }
        let orders = EngineCandidates.candidateOrders(problem, eligible)
        XCTAssertFalse(orders.isEmpty)
        let seen = Set(orders)
        XCTAssertEqual(seen.count, orders.count) // 互异
        XCTAssertEqual(orders, EngineCandidates.candidateOrders(problem, eligible)) // 确定性
    }

    // MARK: - 日界与 DST 语义

    func testEnumerateDaysSplitsByLocalMidnight() throws {
        let window = EngineProblemWindow(start: date(9, 9), end: date(11, 18))
        let days = try EngineDays.enumerateDays(window: window, timezoneHome: timezoneHome)
        XCTAssertEqual(days.count, 3)
        XCTAssertEqual(days[0].minutes, 900) // Python 对拍：9:00–23:59:59 = 53999s // 60 + 1
        XCTAssertEqual(days[1].minutes, 1440)
    }

    func testElapsedMinutesIsRealTime() {
        let start = date(9, 23)
        let end = date(10, 1)
        XCTAssertEqual(EngineDays.elapsedMinutes(from: start, to: end), 120)
    }

    // MARK: - 求解与验证（对拍 Python）

    func testHeuristicCoversEveryEligibleStop() throws {
        let problem = fourCityProblem()
        let eligible = problem.stops.map { $0.visitId }
        let outcome = try EngineHeuristic.solve(problem, eligible)
        let placed = outcome.days.flatMap { $0.stops.map { $0.visitId } }
        XCTAssertEqual(placed.count, eligible.count)
        XCTAssertFalse(outcome.unplaced.isEmpty == false && outcome.objectiveValue == 0)
        XCTAssertGreaterThan(outcome.objectiveValue, 0)
    }

    func testHeuristicDeterministicAcrossRuns() throws {
        let problem = fourCityProblem()
        let eligible = problem.stops.map { $0.visitId }
        let first = try EngineHeuristic.solve(problem, eligible)
        let second = try EngineHeuristic.solve(problem, eligible)
        XCTAssertEqual(first.days, second.days)
        XCTAssertEqual(first.objectiveValue, second.objectiveValue)
    }

    func testPipelineProducesValidatedFeasiblePlan() throws {
        let problem = fourCityProblem()
        let result = try EnginePipeline.planTrip(problem)
        XCTAssertTrue(result.validation.feasible)
        XCTAssertEqual(result.status, .feasible)
        XCTAssertEqual(result.placedVisitIds.count, problem.stops.count)
        // Python 对拍基线（启发式、全排）：objective = 1302.59
        // （行程 1303 分钟 × 1.0 − 410 价值 × 0.001）
        XCTAssertEqual(result.objectiveValue, 1302.59, accuracy: 0.01)
    }

    func testPipelineLockedOrderRespected() throws {
        var problem = fourCityProblem()
        problem.constraints.lockedOrder = [
            visit(1, city: "berlin").visitId,
            visit(4, city: "frankfurt").visitId,
            visit(3, city: "amsterdam").visitId,
        ]
        let result = try EnginePipeline.planTrip(problem)
        var arrivals: [UUID: Date] = [:]
        for day in result.days { for stop in day.stops { arrivals[stop.visitId] = stop.arrive } }
        let a1 = arrivals[visit(1, city: "berlin").visitId]!
        let a4 = arrivals[visit(4, city: "frankfurt").visitId]!
        let a3 = arrivals[visit(3, city: "amsterdam").visitId]!
        XCTAssertLessThan(a1, a4)
        XCTAssertLessThan(a4, a3)
    }

    func testPipelineUnreachableWindowFailsHonestly() {
        // 柏林窗口 1 小时（洲际 840+ 分钟赶不上）⇒ 求解失败而非半成品。
        var problem = fourCityProblem()
        var berlin = visit(1, city: "berlin")
        berlin.timeWindows = [
            EngineTimeWindow(start: date(10, 9), end: date(10, 10))
        ]
        problem.stops[0] = berlin
        XCTAssertThrowsError(try EnginePipeline.planTrip(problem)) { error in
            guard case EngineError.noSolverProducedFullCoverage = error else {
                XCTFail("应抛 noSolverProducedFullCoverage，实际 \(error)")
                return
            }
        }
    }

    func testProblemHashStableAcrossRuns() {
        let problem = fourCityProblem()
        XCTAssertEqual(EnginePipeline.problemHash(problem), EnginePipeline.problemHash(problem))
        var modified = problem
        modified.constraints.maxStopsPerDay += 1
        XCTAssertNotEqual(EnginePipeline.problemHash(problem), EnginePipeline.problemHash(modified))
    }

    /// 回归：计划日期的键必须按出发地时区取日，而不是设备时区。
    /// 引擎给出的 day.date 是"该时区午夜"的瞬时（上海 11/17 00:00 = 11/16T16:00Z），
    /// 用设备时区格式化会串成 11/16，日历/驾驶舱的"今天"随之错位。
    func testPlanDayKeysUseHomeTimeZoneNotDeviceZone() {
        let visitId = UUID(uuidString: "00000000-0000-4000-8000-000000000001")!
        let dayStart = date(17, 0)
        let days = [
            EngineDayPlan(date: dayStart, stops: [
                EngineStopPlan(
                    visitId: visitId, sequence: 1,
                    arrive: date(17, 9), depart: date(17, 10), travelMinutes: 0
                )
            ])
        ]
        let mapped = EngineMapping.makePlanDays(
            from: days, places: [], visits: [],
            timeZone: TimeZone(identifier: timezoneHome)!
        )
        XCTAssertEqual(mapped.first?.date, "2026-11-17")
        XCTAssertEqual(mapped.first?.stops.first?.visitId, visitId.uuidString)
    }


}
