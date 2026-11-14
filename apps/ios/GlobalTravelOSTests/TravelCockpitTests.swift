import XCTest

/// 差旅驾驶舱（审计 5.1）构建器的语义测试：行程选择、今日时间轴、下一站/离场时刻、
/// 风险聚合与 7 天窗口。纯函数，无网络/时钟依赖。
@testable import GlobalTravelOS

final class TravelCockpitTests: XCTestCase {
    private let timeZone = TimeZone(identifier: "Asia/Shanghai")!

    private var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        return calendar
    }

    private func date(_ day: Int, _ hour: Int, _ minute: Int = 0) -> Date {
        calendar.date(from: DateComponents(year: 2026, month: 11, day: day, hour: hour, minute: minute))!
    }

    private func trip(
        id: String = "trip-1",
        name: String = "柏林—阿姆斯特丹客户周",
        status: String = "DRAFT",
        start: Date? = nil,
        end: Date? = nil,
        countries: [String] = ["DE", "NL"],
        activePlanId: String? = nil
    ) -> Trip {
        Trip(
            id: id,
            name: name,
            status: status,
            version: 1,
            window: TripWindow(start: start ?? date(17, 8), end: end ?? date(21, 20)),
            originPlace: Place(
                name: "上海总部", countryCode: "CN", timezone: "Asia/Shanghai",
                latitude: 31.2304, longitude: 121.4737, city: "上海"
            ),
            activePlanVersionId: activePlanId,
            countries: countries
        )
    }

    private func plan(
        status: PlanStatus = .active,
        days: [PlanDay] = [],
        unplaced: [UnplacedVisit] = [],
        validation: ValidationReport? = nil
    ) -> PlanVersionDetail {
        PlanVersionDetail(
            id: "plan-1",
            tripId: "trip-1",
            versionNumber: 1,
            status: status,
            feasible: true,
            validation: validation,
            unplaced: unplaced,
            days: days
        )
    }

    private func stop(
        _ sequence: Int,
        name: String = "大众总部",
        arrive: Date,
        depart: Date,
        travel: Int = 840,
        service: Int = 90
    ) -> PlanStop {
        PlanStop(
            visitId: "visit-\(sequence)",
            sequence: sequence,
            placeId: "place-\(sequence)",
            placeName: name,
            city: "Berlin",
            countryCode: "DE",
            arrive: arrive,
            depart: depart,
            travelMinutes: travel,
            serviceMinutes: service
        )
    }

    // MARK: - 行程选择

    func testActiveTripWinsOverEarlierDraft() {
        let active = trip(id: "active", status: "ACTIVE", start: date(17, 8), end: date(21, 20))
        let draft = trip(id: "draft", status: "DRAFT", start: date(16, 8), end: date(18, 20))

        let cockpit = TravelCockpitBuilder.build(
            trips: [draft, active], plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertEqual(cockpit.trip?.id, "active")
    }

    func testEarliestUpcomingTripChosenWhenNoneActive() {
        let later = trip(id: "later", start: date(20, 8), end: date(24, 20))
        let sooner = trip(id: "sooner", start: date(16, 8), end: date(18, 20))

        let cockpit = TravelCockpitBuilder.build(
            trips: [later, sooner], plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertEqual(cockpit.trip?.id, "sooner")
    }

    func testOnlyEndedTripsYieldEmptyCockpit() {
        let finished = trip(status: "COMPLETED", start: date(1, 8), end: date(3, 20))

        let cockpit = TravelCockpitBuilder.build(
            trips: [finished], plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertNil(cockpit.trip)
        XCTAssertFalse(cockpit.hasData)
    }

    // MARK: - 倒计时

    func testCountdownBeforeDeparture() {
        let cockpit = TravelCockpitBuilder.build(
            trips: [trip(start: date(17, 8), end: date(21, 20))],
            plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )
        XCTAssertEqual(cockpit.countdownText, "D-2 出发")
    }

    func testDayProgressDuringTrip() {
        let cockpit = TravelCockpitBuilder.build(
            trips: [trip(status: "ACTIVE", start: date(14, 8), end: date(18, 20))],
            plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )
        XCTAssertEqual(cockpit.countdownText, "第 2/5 天")
    }

    // MARK: - 今日时间轴与下一站

    func testTodayStopsAndNextStopWithLeaveBy() {
        let departed = stop(1, name: "上午客户", arrive: date(15, 9), depart: date(15, 10, 30), travel: 120)
        let upcoming = stop(2, name: "下午客户", arrive: date(15, 15), depart: date(15, 16, 30), travel: 60)
        let tomorrow = PlanDay(date: "2026-11-16", stops: [
            stop(1, name: "明日客户", arrive: date(16, 9), depart: date(16, 10), travel: 30)
        ])
        let plan = plan(days: [
            PlanDay(date: "2026-11-15", stops: [upcoming, departed]),
            tomorrow,
        ])

        let cockpit = TravelCockpitBuilder.build(
            trips: [trip(status: "ACTIVE", start: date(14, 8), end: date(18, 20))],
            plan: plan, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertEqual(cockpit.todayStops.map(\.visitId), ["visit-1", "visit-2"])
        XCTAssertEqual(cockpit.todayServiceMinutes, 180)
        XCTAssertEqual(cockpit.nextStop?.visitId, "visit-2")
        XCTAssertNil(cockpit.nextDayLabel)
        // 离场时刻 = 到达 15:00 − 路程 60 分钟
        XCTAssertEqual(cockpit.leaveBy, date(15, 14))
    }

    func testNextStopRollsToTomorrowWhenTodayIsDone() {
        let done = stop(1, arrive: date(15, 9), depart: date(15, 10), travel: 60)
        let plan = plan(days: [
            PlanDay(date: "2026-11-15", stops: [done]),
            PlanDay(date: "2026-11-16", stops: [
                stop(1, name: "明日客户", arrive: date(16, 9), depart: date(16, 10), travel: 30)
            ]),
        ])

        let cockpit = TravelCockpitBuilder.build(
            trips: [trip(status: "ACTIVE", start: date(14, 8), end: date(18, 20))],
            plan: plan, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertEqual(cockpit.nextStop?.placeName, "明日客户")
        XCTAssertEqual(cockpit.nextDayLabel, "明天")
        XCTAssertEqual(cockpit.leaveBy, date(16, 8, 30))
    }

    /// 计划日键与停靠到达时刻可能跨天（引擎按出发地时区切日）：下一站说明以到达时刻为准。
    func testNextStopLabelUsesArrivalDateNotPlanDayKey() {
        let plan = plan(days: [
            PlanDay(date: "2026-11-15", stops: [
                stop(1, name: "柏林客户", arrive: date(17, 22), depart: date(17, 23, 30), travel: 840)
            ])
        ])

        let cockpit = TravelCockpitBuilder.build(
            trips: [trip(start: date(17, 8), end: date(21, 20))],
            plan: plan, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertEqual(cockpit.nextStop?.placeName, "柏林客户")
        XCTAssertEqual(cockpit.nextDayLabel, "11月17日")
        XCTAssertEqual(cockpit.leaveBy, date(17, 8))
    }

    func testNoStopsTodayStillShowsTodayAsEmpty() {
        let plan = plan(days: [
            PlanDay(date: "2026-11-16", stops: [
                stop(1, arrive: date(16, 9), depart: date(16, 10), travel: 30)
            ])
        ])

        let cockpit = TravelCockpitBuilder.build(
            trips: [trip(start: date(16, 8), end: date(18, 20))],
            plan: plan, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertTrue(cockpit.todayStops.isEmpty)
        XCTAssertEqual(cockpit.nextStop?.sequence, 1)
        XCTAssertEqual(cockpit.nextDayLabel, "明天")
    }

    // MARK: - 风险

    func testMissingPlanIsHighRisk() {
        let cockpit = TravelCockpitBuilder.build(
            trips: [trip()], plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        let first = cockpit.risks.first
        XCTAssertEqual(first?.kind, .planMissing)
        XCTAssertEqual(first?.severity, "HIGH")
    }

    func testUnplacedAndValidationRisksOrderedBySeverity() {
        let validation = ValidationReport(
            feasible: true,
            checks: [ValidationCheck(rule: "TIME_WINDOW", status: .warn, detail: "到达略晚")],
            violations: []
        )
        let plan = plan(
            status: .draft,
            unplaced: [UnplacedVisit(visitId: "v9", placeName: "慕尼黑客户", reason: "time_window")],
            validation: validation
        )

        let cockpit = TravelCockpitBuilder.build(
            trips: [trip()], plan: plan, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        let kinds = cockpit.risks.map(\.kind)
        XCTAssertTrue(kinds.contains(.unplaced))
        XCTAssertTrue(kinds.contains(.validationWarn))
        XCTAssertTrue(kinds.contains(.planDraft))
        // 高风险排在前：unplaced 先于 warning / draft。
        let highIndex = kinds.firstIndex(of: .unplaced)!
        let warnIndex = kinds.firstIndex(of: .validationWarn)!
        let draftIndex = kinds.firstIndex(of: .planDraft)!
        XCTAssertLessThan(highIndex, warnIndex)
        XCTAssertLessThan(warnIndex, draftIndex)
        // 三个风险都指向同一行程，标题包含可读文案而不是原始 rule id。
        XCTAssertTrue(cockpit.risks.contains { $0.title.contains("未排入计划") })
        XCTAssertTrue(cockpit.risks.contains { $0.title.contains("拜访时间窗") })
    }

    func testVisaRisksMissingAndIncomplete() {
        let missing = TravelCockpitBuilder.build(
            trips: [trip()], plan: plan(), visaCases: [],
            now: date(15, 12), calendar: calendar
        )
        let missingRisk = missing.risks.first { $0.kind == .visaMissing }
        XCTAssertEqual(missingRisk?.severity, "MEDIUM")
        XCTAssertEqual(missingRisk?.title, "未创建签证案件：DE、NL")

        let preparing = VisaCase(
            id: "case-1", tripId: "trip-1", countryCode: "DE", status: .preparing,
            checklist: [
                VisaChecklistItem(key: "passport", label: "护照", status: "READY", note: nil),
                VisaChecklistItem(key: "photo", label: "照片", status: "MISSING", note: nil),
            ],
            notes: nil, passportExpiresAt: nil, confidence: nil,
            submittedAt: nil, decisionAt: nil, createdAt: nil, updatedAt: nil
        )
        let approved = VisaCase(
            id: "case-2", tripId: "trip-1", countryCode: "NL", status: .approved,
            checklist: nil, notes: nil, passportExpiresAt: nil, confidence: nil,
            submittedAt: nil, decisionAt: nil, createdAt: nil, updatedAt: nil
        )
        let incomplete = TravelCockpitBuilder.build(
            trips: [trip()], plan: plan(), visaCases: [preparing, approved],
            now: date(15, 12), calendar: calendar
        )
        let incompleteRisks = incomplete.risks.filter { $0.kind == .visaIncomplete }
        XCTAssertEqual(incompleteRisks.count, 1)
        XCTAssertEqual(incompleteRisks.first?.title, "签证材料待补：DE")
        XCTAssertEqual(incompleteRisks.first?.detail, "准备中 · 清单 1/2")
        XCTAssertFalse(incomplete.risks.contains { $0.kind == .visaMissing })
    }

    // MARK: - 未来 7 天

    func testUpcomingWithinSevenDaysExcludingCurrentAndFinished() {
        let current = trip(id: "current", start: date(16, 8), end: date(18, 20))
        let inThreeDays = trip(id: "soon", start: date(18, 8), end: date(20, 20))
        let inTenDays = trip(id: "far", start: date(25, 8), end: date(27, 20))
        let cancelled = trip(id: "cancelled", status: "CANCELLED", start: date(17, 8), end: date(17, 20))

        let cockpit = TravelCockpitBuilder.build(
            trips: [current, inThreeDays, inTenDays, cancelled],
            plan: nil, visaCases: [],
            now: date(15, 12), calendar: calendar
        )

        XCTAssertEqual(cockpit.upcoming.map(\.id), ["soon"])
    }
}
