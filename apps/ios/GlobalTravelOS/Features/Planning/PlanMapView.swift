import MapKit
import SwiftUI

/// Selectable, read-only map projection of a specific plan version.
/// Selecting a pin changes the inspection drawer only; it never mutates the plan.
struct PlanMapView: View {
    let plan: PlanVersionDetail
    var origin: Place?
    var title = "行程地图"
    var visits: [TripVisit] = []
    var customers: [Customer] = []
    var isPlanActive = false
    var isSubmittingChange = false
    var onCancelVisit: ((PlanStop) -> Void)?

    @State private var position: MapCameraPosition = .automatic
    @State private var selectedStopID: String?

    private struct DayRoute {
        var dayIndex: Int
        var date: String
        var coordinates: [CLLocationCoordinate2D]
        var stops: [PlanStop]
        var color: Color
    }

    private static let palette: [Color] = DS.ColorToken.routePalette

    private var planTimeZone: TimeZone {
        origin?.timeZone() ?? .current
    }

    private var dayRoutes: [DayRoute] {
        var routes: [DayRoute] = []
        let originCoordinate: CLLocationCoordinate2D? = {
            guard let origin, let lat = origin.latitude, let lon = origin.longitude else { return nil }
            return CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }()
        var previousCoordinate = originCoordinate
        for (dayIndex, day) in plan.dayList.enumerated() {
            var coordinates: [CLLocationCoordinate2D] = []
            if let start = previousCoordinate { coordinates.append(start) }
            let dayStops = day.stops.filter(\.hasCoordinates)
            for stop in dayStops {
                guard let lat = stop.latitude, let lon = stop.longitude else { continue }
                coordinates.append(CLLocationCoordinate2D(latitude: lat, longitude: lon))
            }
            if !dayStops.isEmpty {
                routes.append(
                    DayRoute(
                        dayIndex: dayIndex + 1,
                        date: day.date,
                        coordinates: coordinates,
                        stops: dayStops,
                        color: Self.palette[dayIndex % Self.palette.count]
                    )
                )
                if let last = coordinates.last { previousCoordinate = last }
            }
        }
        return routes
    }

    private var allStops: [PlanStop] {
        dayRoutes.flatMap(\.stops)
    }

    private var selectedStop: PlanStop? {
        guard let selectedStopID else { return nil }
        return allStops.first { $0.visitId == selectedStopID }
    }

    private var selectedDayRoute: DayRoute? {
        guard let selectedStop else { return nil }
        return dayRoutes.first { $0.stops.contains(where: { $0.visitId == selectedStop.visitId }) }
    }

    private var selectedVisit: TripVisit? {
        guard let selectedStop else { return nil }
        return visits.first { $0.id == selectedStop.visitId }
    }

    private var selectedCustomer: Customer? {
        guard let customerId = selectedVisit?.customerId else { return nil }
        return customers.first { $0.id == customerId }
    }

    private var legs: [PlanTravelLeg] {
        plan.legs ?? []
    }

    var body: some View {
        Group {
            if dayRoutes.isEmpty {
                ContentUnavailableView(
                    "没有可展示的坐标",
                    systemImage: DSIconCatalog.map,
                    description: Text("拜访地点缺少经纬度。请补齐地址坐标后重新生成计划，地图不会用虚构位置代替。")
                )
            } else {
                VStack(spacing: 0) {
                    Map(position: $position, selection: $selectedStopID) {
                        ForEach(dayRoutes, id: \.dayIndex) { route in
                            if route.coordinates.count >= 2 {
                                MapPolyline(coordinates: route.coordinates)
                                    .stroke(route.color, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            }
                            ForEach(route.stops) { stop in
                                if let lat = stop.latitude, let lon = stop.longitude {
                                    Marker(
                                        stop.displayName,
                                        coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon)
                                    )
                                    .tint(route.color)
                                    .tag(stop.visitId)
                                }
                            }
                        }
                    }
                    .mapStyle(.standard)
                    .mapControls {
                        MapCompass()
                        MapScaleView()
                    }
                    .accessibilityLabel("商务拜访路线地图")
                    if let selectedStop {
                        selectedStopDrawer(selectedStop)
                            .transition(.move(edge: .bottom).combined(with: .opacity))
                    }
                    routeLegend
                }
                .animation(.easeInOut(duration: DS.Motion.standard), value: selectedStopID)
            }
        }
        .navigationTitle(title)
        .onAppear { position = .automatic }
        .onChange(of: plan.id) { _, _ in
            selectedStopID = nil
            position = .automatic
        }
    }

    private func selectedStopDrawer(_ stop: PlanStop) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                    Text(String(format: "%02d", stop.sequence))
                        .font(.subheadline.weight(.bold).monospacedDigit())
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(DS.ColorToken.brand, in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 3) {
                        Text(stop.displayName)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .lineLimit(2)
                        Text([stop.city, stop.countryCode].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Spacer(minLength: 2)
                    Button {
                        selectedStopID = nil
                    } label: {
                        Image(systemName: "xmark")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .frame(width: 32, height: 32)
                            .background(DS.ColorToken.surfaceSecondary, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("关闭拜访详情")
                }

                if let customer = selectedCustomer {
                    Label(customer.name, systemImage: "building.2")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    if let industry = customer.industry, !industry.isEmpty {
                        Text(industry)
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }

                if let selectedVisit {
                    HStack(spacing: DS.Spacing.xs) {
                        DSBadge(text: selectedVisit.statusLabel, color: DS.ColorToken.status(selectedVisit.status ?? ""))
                        if let priority = selectedVisit.priority {
                            DSBadge(text: "优先级 \(priority)", color: DS.ColorToken.priority("P\(priority)"))
                        }
                        if let customer = selectedCustomer {
                            DSBadge(text: customer.tierLabel, color: DS.ColorToken.tier(customer.tier))
                        }
                    }
                    if let objective = selectedVisit.objective, !objective.isEmpty {
                        Text(objective)
                            .font(.footnote)
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if selectedVisit.timeWindowStart != nil || selectedVisit.timeWindowEnd != nil {
                        Label(selectedVisit.timeWindowLabel, systemImage: "clock")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }

                HStack(spacing: DS.Spacing.md) {
                    if let day = selectedDayRoute?.date {
                        Text(day)
                    }
                    Label(stop.arrive.map { time($0) } ?? "到达时间未知", systemImage: "clock")
                    if let depart = stop.depart {
                        Text("离开 \(time(depart))")
                    }
                    Spacer(minLength: 0)
                    if let minutes = stop.serviceMinutes {
                        Text("停留 \(duration(minutes))")
                    }
                }
                .font(.caption)
                .foregroundStyle(DS.ColorToken.textSecondary)

                HStack(alignment: .center, spacing: DS.Spacing.sm) {
                    if let travel = stop.travelMinutes {
                        Label("路程 \(duration(travel))", systemImage: "car")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if stop.distanceKm != nil {
                        Text(stop.distanceLabel)
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if stop.travelIsStale {
                        DSDataQualityTag(kind: .stale, text: "交通数据需复核")
                    } else if let source = stop.travelSource {
                        DSDataQualityTag(kind: source == "osrm-road" ? .live : .estimate,
                                         text: source == "osrm-road" ? "路网路线" : "路线估算")
                    }
                    Spacer(minLength: 0)
                    if let cost = stop.costAmount {
                        Text("\(stop.costCurrency ?? "") \(String(format: "%.2f", cost))")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(DS.ColorToken.textPrimary)
                    }
                }

                if isPlanActive, let onCancelVisit {
                    Divider()
                    Button(role: .destructive) {
                        onCancelVisit(stop)
                    } label: {
                        Label(
                            isSubmittingChange ? "正在提交变更…" : "取消此拜访并重新优化路线",
                            systemImage: "arrow.trianglehead.2.clockwise"
                        )
                        .font(.caption.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 42)
                    }
                    .disabled(isSubmittingChange)
                    .accessibilityHint("提交前会要求再次确认，服务端将生成新的计划版本")
                }
            }

    private var routeLegend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: DS.Spacing.sm) {
                ForEach(dayRoutes, id: \.dayIndex) { route in
                    Button {
                        selectedStopID = route.stops.first?.visitId
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack(spacing: 6) {
                                Circle().fill(route.color).frame(width: 8, height: 8)
                                Text("第 \(route.dayIndex) 天 · \(route.date)")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(DS.ColorToken.textPrimary)
                            }
                            Text("\(route.stops.count) 次拜访 · \(dayDuration(route))")
                                .font(.caption2)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                            if let leg = legs.first(where: { $0.dayIndex == route.dayIndex - 1 }) {
                                Text("\(leg.mode) · \(duration(leg.travelMinutes)) · \(sourceLabel(leg))")
                                    .font(.caption2)
                                    .foregroundStyle(leg.stale ? DS.ColorToken.warning : DS.ColorToken.textSecondary)
                            }
                        }
                        .padding(DS.Spacing.md)
                        .frame(minWidth: 155, alignment: .leading)
                        .background(DS.ColorToken.surface, in: RoundedRectangle(cornerRadius: DS.Radius.control))
                        .overlay(
                            RoundedRectangle(cornerRadius: DS.Radius.control)
                                .stroke(DS.ColorToken.border, lineWidth: 0.8)
                        )
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("第 \(route.dayIndex) 天，\(route.stops.count) 次拜访，选择首个拜访点")
                }
            }
            .padding(.horizontal, DS.Spacing.sm)
            .padding(.vertical, DS.Spacing.sm)
        }
        .background(DS.ColorToken.surfaceSecondary)
    }

    private func dayDuration(_ route: DayRoute) -> String {
        duration(route.stops.reduce(0) { $0 + ($1.serviceMinutes ?? 0) })
    }

    private func time(_ date: Date) -> String {
        ISO8601.timeDisplay(from: date, timeZone: planTimeZone)
    }

    private func duration(_ minutes: Int) -> String {
        Distance.formatDuration(minutes: minutes)
    }

    private func sourceLabel(_ leg: PlanTravelLeg) -> String {
        if leg.stale { return "供应商数据过期" }
        switch leg.source {
        case "osrm-road": return "路网"
        case "haversine-estimate": return "直线估算"
        case let other: return other
        }
    }
}
