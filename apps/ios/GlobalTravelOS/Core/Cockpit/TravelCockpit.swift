import Foundation

/// 差旅驾驶舱（审计 5.1）：把「今日」从建议收件箱改造成决策面板。
///
/// 纯函数构建（不碰网络/时钟），输入行程、当前计划、签证案件与"现在"，
/// 输出今天要看的路线、下一站、风险与 7 天窗口——便于单测锁定语义。
struct TravelCockpit: Equatable {
    /// 今日时间轴上的一站（直接复用 `PlanStop`，标签格式化统一在模型层）。
    var trip: Trip?

    /// 今天（行程出发地时区的自然日）的停靠，按 sequence 升序。
    var todayStops: [PlanStop] = []

    /// 下一站：今天还没结束的那一站；今天没有则取后续行程日的第一站。
    var nextStop: PlanStop?

    /// 下一站不在今天时的日期说明（如「明天」「11月18日」）。
    var nextDayLabel: String?

    /// 建议离场时刻 = 下一站到达 − 路程时长。
    var leaveBy: Date?

    /// 行程进度/倒计时文案（D-2 出发 / 今天出发 / 第 3/5 天）。
    var countdownText: String?

    /// 当前所在位置或目的地，用于英雄卡副标题。
    var locationText: String?

    /// 展示时刻所用的时区（行程出发地时区；与规划引擎 timezoneHome 同口径）。
    var timeZoneIdentifier: String?

    /// 今天已排定的服务时长（分钟）。
    var todayServiceMinutes: Int = 0

    /// 需要今天处理的风险/待办（已按严重度排序）。
    var risks: [Risk] = []

    /// 未来 7 天内出发的其他行程（不含当前选中的行程）。
    var upcoming: [Trip] = []

    /// 当前计划（可能有版本但未激活）。
    var hasPlan: Bool = false

    /// 计划是否为激活状态（ACTIVE）。
    var planActive: Bool = false

    var hasData: Bool { trip != nil }

    /// 展示时刻用的时区对象（缺失时由调用方退回设备时区）。
    var timeZone: TimeZone? {
        guard let timeZoneIdentifier, let zone = TimeZone(identifier: timeZoneIdentifier) else {
            return nil
        }
        return zone
    }

    struct Risk: Equatable, Identifiable {
        enum Kind: String {
            case planMissing
            case planDraft
            case unplaced
            case validationFail
            case validationWarn
            case visaMissing
            case visaIncomplete
        }

        var kind: Kind
        var title: String
        var detail: String?
        /// HIGH / MEDIUM / LOW，与 `DS.ColorToken.severity` 同一口径。
        var severity: String

        var id: String { "\(kind.rawValue)|\(title)" }
    }
}

enum TravelCockpitBuilder {
    /// 选中的行程优先级：ACTIVE > 窗口内 > 尚未结束且最早的待出发行程。
    static func build(
        trips: [Trip],
        plan: PlanVersionDetail?,
        visaCases: [VisaCase],
        now: Date = Date(),
        calendar: Calendar = .current
    ) -> TravelCockpit {
        var cockpit = TravelCockpit()
        guard let trip = selectTrip(from: trips, now: now) else { return cockpit }
        cockpit.trip = trip

        let window = trip.window
        if let window {
            let daysToStart = calendar.dateComponents(
                [.day],
                from: calendar.startOfDay(for: now),
                to: calendar.startOfDay(for: window.start)
            ).day ?? 0
            if daysToStart > 0 {
                cockpit.countdownText = daysToStart == 1 ? "明天出发" : "D-\(daysToStart) 出发"
            } else if now <= window.end {
                let elapsed = calendar.dateComponents(
                    [.day],
                    from: calendar.startOfDay(for: window.start),
                    to: calendar.startOfDay(for: now)
                ).day ?? 0
                let day = max(1, elapsed + 1)
                cockpit.countdownText = "第 \(day)/\(max(window.dayCount, day)) 天"
            }
        }

        cockpit.locationText = locationText(trip: trip)
        cockpit.timeZoneIdentifier = trip.originPlace?.timezone

        if let plan {
            cockpit.hasPlan = true
            cockpit.planActive = plan.status == .active
            let todayKey = dayKey(now, calendar: calendar)
            let days = plan.days ?? []
            let today = days.first { $0.date == todayKey }
            cockpit.todayStops = (today?.stops ?? []).sorted { $0.sequence < $1.sequence }
            cockpit.todayServiceMinutes = cockpit.todayStops.reduce(0) { $0 + ($1.serviceMinutes ?? 0) }

            if let next = nextStop(
                todayStops: cockpit.todayStops, days: days, todayKey: todayKey,
                now: now, calendar: calendar
            ) {
                cockpit.nextStop = next.stop
                cockpit.nextDayLabel = next.dayLabel
                if let arrive = next.stop.arrive {
                    let lead = TimeInterval((next.stop.travelMinutes ?? 0) * 60)
                    cockpit.leaveBy = arrive.addingTimeInterval(-lead)
                }
            }
        }

        cockpit.risks = risks(trip: trip, plan: plan, visaCases: visaCases)
        cockpit.upcoming = upcomingTrips(from: trips, excluding: trip, now: now, calendar: calendar)
        return cockpit
    }

    // MARK: - 选择行程

    static func selectTrip(from trips: [Trip], now: Date) -> Trip? {
        let active = trips.first { ($0.status ?? "").uppercased() == "ACTIVE" }
        if let active { return active }

        let candidates = trips.filter { trip in
            let status = (trip.status ?? "").uppercased()
            guard ["DRAFT", "PLANNING", "READY", ""].contains(status) else { return false }
            guard let end = trip.window?.end else { return false }
            return end >= now
        }
        return candidates.min {
            ($0.window?.start ?? .distantFuture) < ($1.window?.start ?? .distantFuture)
        }
    }

    // MARK: - 下一站

    private static func nextStop(
        todayStops: [PlanStop],
        days: [PlanDay],
        todayKey: String,
        now: Date,
        calendar: Calendar
    ) -> (stop: PlanStop, dayLabel: String?)? {
        // 今天还没结束的那一站（按离开时间判断；缺离开时间退回到达时间）。
        if let stop = todayStops.first(where: { stop in
            guard let anchor = stop.depart ?? stop.arrive else { return false }
            return anchor > now
        }) {
            // 计划日键与到达时刻可能跨天：说明文字仍按到达时刻取日。
            return (stop, dayLabel(for: stop.arrive, todayKey: todayKey, calendar: calendar))
        }
        // 今天已全部结束：取后续行程日的第一站。
        let later = days.filter { $0.date > todayKey }.sorted { $0.date < $1.date }
        for day in later {
            guard let stop = day.stops.min(by: { $0.sequence < $1.sequence }) else { continue }
            return (stop, dayLabel(for: stop.arrive, todayKey: todayKey, calendar: calendar))
        }
        return nil
    }

    /// 下一站不在今天时的说明：按**停靠的真实到达时刻**取日（计划日键可能因引擎按
    /// 出发地时区切日而与到达时刻跨天，直接用日键会误导"明天"）。
    private static func dayLabel(
        for date: Date?, todayKey: String, calendar: Calendar
    ) -> String? {
        guard let date else { return nil }
        let targetKey = dayKey(date, calendar: calendar)
        guard targetKey > todayKey,
              let today = ISO8601.day(from: todayKey),
              let target = ISO8601.day(from: targetKey) else { return nil }
        let delta = calendar.dateComponents([.day], from: today, to: target).day ?? 0
        if delta == 1 { return "明天" }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "M月d日"
        return formatter.string(from: date)
    }

    // MARK: - 风险与待办

    private static func risks(
        trip: Trip,
        plan: PlanVersionDetail?,
        visaCases: [VisaCase]
    ) -> [TravelCockpit.Risk] {
        var risks: [TravelCockpit.Risk] = []

        if let plan {
            if plan.status != .active {
                risks.append(.init(
                    kind: .planDraft,
                    title: "计划尚未激活",
                    detail: "激活后才会按此版本执行并触发提醒",
                    severity: "LOW"
                ))
            }
            let unplaced = plan.unplaced?.count ?? 0
            if unplaced > 0 {
                risks.append(.init(
                    kind: .unplaced,
                    title: "\(unplaced) 个拜访未排入计划",
                    detail: "检查时间窗、坐标与交通时长后重新求解",
                    severity: "HIGH"
                ))
            }
            if let report = plan.validation {
                for check in report.failingChecks.prefix(3) {
                    risks.append(.init(
                        kind: .validationFail,
                        title: "校验未通过：\(ruleLabel(check.rule))",
                        detail: check.detail,
                        severity: "HIGH"
                    ))
                }
                for check in report.warningChecks.prefix(2) {
                    risks.append(.init(
                        kind: .validationWarn,
                        title: "校验警告：\(ruleLabel(check.rule))",
                        detail: check.detail,
                        severity: "MEDIUM"
                    ))
                }
                for violation in report.violationList.prefix(2) {
                    risks.append(.init(
                        kind: .validationFail,
                        title: "行程违规：\(ruleLabel(violation.rule))",
                        detail: violation.detail,
                        severity: "HIGH"
                    ))
                }
            }
        } else {
            risks.append(.init(
                kind: .planMissing,
                title: "尚未生成计划",
                detail: "生成计划后才能看到今天的路线与下一站",
                severity: "HIGH"
            ))
        }

        let countries = (trip.countries ?? []).map { $0.uppercased() }.filter { !$0.isEmpty }
        if !countries.isEmpty {
            if visaCases.isEmpty {
                risks.append(.init(
                    kind: .visaMissing,
                    title: "未创建签证案件：\(countries.joined(separator: "、"))",
                    detail: "按目的国生成材料清单，逐项核对后再提交",
                    severity: "MEDIUM"
                ))
            } else {
                for item in visaCases where item.status == .new || item.status == .preparing {
                    risks.append(.init(
                        kind: .visaIncomplete,
                        title: "签证材料待补：\(item.countryCode)",
                        detail: "\(item.status.label) · \(item.checklistLabel)",
                        severity: "MEDIUM"
                    ))
                }
            }
        }

        let order = ["HIGH": 0, "MEDIUM": 1, "LOW": 2]
        return risks.sorted { order[$0.severity, default: 3] < order[$1.severity, default: 3] }
    }

    private static func ruleLabel(_ rule: String) -> String {
        switch rule.uppercased() {
        case "STOP_COVERAGE": return "拜访覆盖"
        case "NO_DUPLICATE_STOP": return "重复停靠"
        case "DAY_WITHIN_WINDOW": return "日窗口"
        case "TIME_WINDOW": return "拜访时间窗"
        case "SERVICE_DURATION": return "服务时长"
        case "TRAVEL_TIME_CONSISTENCY": return "交通时长一致性"
        case "MAX_STOPS_PER_DAY": return "每日停靠上限"
        case "LOCKED_ORDER": return "固定顺序"
        case "OBJECTIVE_RECOMPUTED": return "目标值复算"
        default: return rule
        }
    }

    // MARK: - 未来 7 天

    private static func upcomingTrips(
        from trips: [Trip],
        excluding current: Trip,
        now: Date,
        calendar: Calendar
    ) -> [Trip] {
        guard let horizon = calendar.date(byAdding: .day, value: 7, to: now) else { return [] }
        return trips
            .filter { $0.id != current.id }
            .filter { trip in
                guard let start = trip.window?.start else { return false }
                let status = (trip.status ?? "").uppercased()
                guard !["COMPLETED", "CANCELLED"].contains(status) else { return false }
                return start > now && start <= horizon
            }
            .sorted { ($0.window?.start ?? .distantFuture) < ($1.window?.start ?? .distantFuture) }
    }

    // MARK: - 辅助

    private static func locationText(trip: Trip) -> String? {
        let countries = (trip.countries ?? []).map { $0.uppercased() }.filter { !$0.isEmpty }
        guard let location = trip.originPlace?.name else {
            return countries.isEmpty ? nil : countries.joined(separator: "、")
        }
        return countries.isEmpty ? location : "\(location) → \(countries.joined(separator: "、"))"
    }

    private static func dayKey(_ date: Date, calendar: Calendar) -> String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }
}
