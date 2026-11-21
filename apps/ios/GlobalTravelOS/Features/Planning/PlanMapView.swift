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
    @State private var selectedDayIndex: Int?
    @State private var showsOpenVisitsOnly = false

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
        for (dayIndex, day) in plan.dayList.enumerated() {
            var coordinates: [CLLocationCoordinate2D] = []
            // The origin is a valid first-day anchor only. Never draw a synthetic
            // overnight transfer from yesterday's final stop to today's first stop.
            if dayIndex == 0, let originCoordinate { coordinates.append(originCoordinate) }
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
            }
        }
        return routes
    }

    private var visibleDayRoutes: [DayRoute] {
        guard let selectedDayIndex else { return dayRoutes }
        return dayRoutes.filter { $0.dayIndex == selectedDayIndex }
    }

    private var allStops: [PlanStop] {
        visibleDayRoutes.flatMap(\.stops).filter(isVisibleStop)
    }

    private func isVisibleStop(_ stop: PlanStop) -> Bool {
        guard showsOpenVisitsOnly else { return true }
        guard let visit = visits.first(where: { $0.id == stop.visitId }) else { return true }
        return visit.isOpen
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
                    dayFilter
                    Map(position: $position, selection: $selectedStopID) {
                        ForEach(visibleDayRoutes, id: \.dayIndex) { route in
                            if route.coordinates.count >= 2 {
                                MapPolyline(coordinates: route.coordinates)
                                    .stroke(route.color, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                            }
                            ForEach(route.stops.filter(isVisibleStop)) { stop in
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
            selectedDayIndex = nil
            position = .automatic
        }
    }

    /// Day filtering changes only the map projection, never the underlying plan inputs.
    private var dayFilter: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: DS.Spacing.xs) {
                filterChip(title: "全部", selected: selectedDayIndex == nil) {
                    selectedDayIndex = nil
                    selectedStopID = nil
                    position = .automatic
                }
                filterChip(title: "全部拜访", selected: !showsOpenVisitsOnly) {
                    showsOpenVisitsOnly = false
                    selectedStopID = nil
                }
                filterChip(title: "待执行", selected: showsOpenVisitsOnly) {
                    showsOpenVisitsOnly = true
                    selectedStopID = nil
                }
                ForEach(dayRoutes, id: .dayIndex) { route in
                    filterChip(
                        title: "第 \(route.dayIndex) 天",
                        selected: selectedDayIndex == route.dayIndex
                    ) {
                        selectedDayIndex = route.dayIndex
                        selectedStopID = nil
                        position = .automatic
                    }
                }
            }
            .padding(.horizontal, DS.Spacing.sm)
            .padding(.vertical, DS.Spacing.sm)
        }
        .background(DS.ColorToken.surfaceSecondary)
    }

    private func filterChip(title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(selected ? .white : DS.ColorToken.textSecondary)
                .padding(.horizontal, DS.Spacing.md)
                .frame(minHeight: 34)
                .background(
                    selected ? DS.ColorToken.brand : DS.ColorToken.surface,
                    in: Capsule()
                )
                .overlay(
                    Capsule().stroke(selected ? DS.ColorToken.brand : DS.ColorToken.border, lineWidth: 0.8)
                )
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func selectedStopDrawer(_ stop: PlanStop) -> some View {
        MapVisitDetailDrawer(
            stop: stop,
            visit: selectedVisit,
            customer: selectedCustomer,
            dayDate: selectedDayRoute?.date,
            timeZone: planTimeZone,
            isPlanActive: isPlanActive,
            isSubmittingChange: isSubmittingChange,
            onCancelVisit: onCancelVisit,
            onClose: { selectedStopID = nil }
        )
    }

    private var routeLegend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: DS.Spacing.sm) {
                ForEach(visibleDayRoutes, id: \.dayIndex) { route in
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
                            Text("\(route.stops.count) 次拜访 · 服务时长 \(dayDuration(route))")
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
