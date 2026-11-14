import SwiftUI

/// 行程日历：日时间轴（审计 5.4——不再是分组列表）。
///
/// 每天一条时间轴：左侧小时刻度、右侧按真实到达/停留比例定位的拜访卡片，
/// 大空档显式标注，交通段显示在卡片上方。默认展示设备时区的当地时间。
struct PlanCalendarView: View {
    let plan: PlanVersionDetail

    private let hourHeight: CGFloat = 44

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                if plan.dayList.isEmpty {
                    ContentUnavailableView(
                        "计划没有可展示的日程",
                        systemImage: "calendar",
                        description: Text("重新生成计划后再查看日历。")
                    )
                    .padding(.top, 40)
                }
                ForEach(plan.dayList) { day in
                    dayTimeline(day)
                }
            }
            .padding()
        }
        .navigationTitle("行程日历")
    }

    // MARK: - 单日时间轴

    private func dayTimeline(_ day: PlanDay) -> some View {
        let stops = day.stops
        let bounds = timeBounds(stops)
        let totalMinutes = max(bounds.end - bounds.start, 60)
        let height = CGFloat(totalMinutes) / 60 * hourHeight + 24

        return VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(day.displayDate)
                    .font(.headline)
                Spacer()
                Text("\(stops.count) 个停靠 · \(minutesLabel(totalMinutes))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            HStack(alignment: .top, spacing: 8) {
                // 左列：小时刻度
                VStack(alignment: .trailing, spacing: 0) {
                    ForEach(hourTicks(bounds), id: \.self) { minute in
                        Text(hourLabel(bounds.start + minute))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                            .frame(height: hourHeight, alignment: .top)
                            .offset(y: 0)
                    }
                }
                .frame(width: 44)

                // 右列：绝对定位的拜访卡片 + 空档
                GeometryReader { _ in
                    ZStack(alignment: .topLeading) {
                        ForEach(gaps(stops, bounds: bounds), id: \.startMinute) { gap in
                            gapView(gap)
                                .offset(y: CGFloat(gap.startMinute - bounds.start) / 60 * hourHeight)
                        }
                        ForEach(stops) { stop in
                            stopCard(stop, bounds: bounds)
                                .offset(
                                    y: CGFloat((minuteOf(stop.arrive, fallback: bounds.start) - bounds.start)) / 60 * hourHeight
                                )
                        }
                    }
                }
                .frame(height: height)
            }
        }
        .padding(12)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 14))
    }

    private func stopCard(_ stop: PlanStop, bounds: (start: Int, end: Int)) -> some View {
        let arrive = minuteOf(stop.arrive, fallback: bounds.start)
        let duration = max(stop.serviceMinutes ?? 60, 20)
        let height = CGFloat(duration) / 60 * hourHeight

        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text("\(stop.sequence)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 16, height: 16)
                    .background(Circle().fill(.blue))
                Text(stop.displayName)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
            }
            Text("\(stop.arriveLabel) – \(stop.departLabel)")
                .font(.caption2.monospacedDigit())
                .foregroundStyle(.secondary)
            if let travel = stop.travelMinutes, travel > 0 {
                Text("行程 \(stop.travelLabel)")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(6)
        .frame(maxWidth: .infinity, minHeight: max(height, 34), alignment: .topLeading)
        .background(.background.opacity(0.9), in: RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8).stroke(.blue.opacity(0.35), lineWidth: 1)
        )
        .offset(x: 0)
        .padding(.leading, 4)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("第 \(stop.sequence) 站 \(stop.displayName)，\(stop.arriveLabel) 到 \(stop.departLabel)")
        .accessibilityValue(arrive >= 0 ? "" : "")
    }

    private func gapView(_ gap: (startMinute: Int, minutes: Int)) -> some View {
        DSDataQualityTag(kind: .gap, text: "空档 \(minutesLabel(gap.minutes))")
            .padding(.vertical, 2)
            .padding(.horizontal, 6)
            .background(.quaternary.opacity(0.4), in: Capsule())
    }

    // MARK: - 时间计算（设备时区展示；分钟数以当日 00:00 为基准的本地偏移）

    private func minuteOf(_ date: Date?, fallback: Int = 0) -> Int {
        guard let date else { return fallback }
        let calendar = Calendar.current
        let components = calendar.dateComponents([.hour, .minute], from: date)
        return (components.hour ?? 0) * 60 + (components.minute ?? 0)
    }

    private func timeBounds(_ stops: [PlanStop]) -> (start: Int, end: Int) {
        let arrivals = stops.compactMap { stop in stop.arrive.map { minuteOf($0) } }
        let departs = stops.compactMap { stop in stop.depart.map { minuteOf($0) } }
        guard let first = arrivals.min(), let last = departs.max() else {
            return (8 * 60, 18 * 60)
        }
        let start = max(0, (first / 60 - 1) * 60)          // 向上一小时取整
        let end = min(24 * 60, ((last / 60) + 1) * 60)     // 向下一小时取整
        return (start, max(end, start + 60))
    }

    private func hourTicks(_ bounds: (start: Int, end: Int)) -> [Int] {
        var ticks: [Int] = []
        var minute = bounds.start
        while minute <= bounds.end {
            ticks.append(minute)
            minute += 60
        }
        return ticks
    }

    /// 相邻停靠之间 ≥ 90 分钟的空档显式标注（赶路/等待/缓冲）。
    private func gaps(_ stops: [PlanStop], bounds: (start: Int, end: Int)) -> [(startMinute: Int, minutes: Int)] {
        var result: [(startMinute: Int, minutes: Int)] = []
        var previousDepart: Int?
        for stop in stops {
            let arrive = minuteOf(stop.arrive, fallback: bounds.start)
            if let previous = previousDepart, arrive - previous >= 90 {
                result.append((startMinute: previous, minutes: arrive - previous))
            }
            previousDepart = minuteOf(stop.depart, fallback: bounds.start)
        }
        return result
    }

    private func hourLabel(_ minute: Int) -> String {
        String(format: "%02d:00", (minute / 60) % 24)
    }

    private func minutesLabel(_ minutes: Int) -> String {
        if minutes >= 60 {
            let hours = minutes / 60
            let rest = minutes % 60
            return rest == 0 ? "\(hours) 小时" : "\(hours) 小时 \(rest) 分"
        }
        return "\(minutes) 分钟"
    }
}
