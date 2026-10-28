import SwiftUI

/// 激活计划的日历视图：按天列出停靠与时间。
struct PlanCalendarView: View {
    let plan: PlanVersionDetail

    var body: some View {
        List {
            if plan.dayList.isEmpty {
                ContentUnavailableView(
                    "计划没有可展示的日程",
                    systemImage: "calendar",
                    description: Text("重新生成计划后再查看日历。")
                )
            }
            ForEach(plan.dayList) { day in
                Section(day.displayDate) {
                    ForEach(day.stops) { stop in
                        HStack(alignment: .top, spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(stop.arriveLabel).font(.caption.monospacedDigit())
                                Text(stop.departLabel)
                                    .font(.caption2.monospacedDigit())
                                    .foregroundStyle(.secondary)
                            }
                            VStack(alignment: .leading, spacing: 2) {
                                Text(stop.displayName).font(.body)
                                Text("行程 \(stop.travelLabel) · 停留 \(stop.serviceMinutes ?? 0) 分钟")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("行程日历")
    }
}
