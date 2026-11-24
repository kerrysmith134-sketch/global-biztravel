import MapKit
import SwiftUI

/// Read-only map projection: planned visits when a plan exists, CRM addresses independently.
/// Selecting a pin changes the inspection drawer only; it never mutates the plan.
struct PlanMapView: View {
    let plan: PlanVersionDetail?
    var origin: Place?
    var title = "行程地图"
    var visits: [TripVisit] = []
    var customers: [Customer] = []
    var customerLocations: [CustomerMapLocation] = []
    var customerPlacesMessage: String?
    var isLoadingCustomerPlaces = false
    var isPlanActive = false
    var isSubmittingChange = false
    var onCancelVisit: ((PlanStop) -> Void)?

    @State private var position: MapCameraPosition = .automatic
    @State private var selectedStopID: String?
    @State private var selectedCustomerPlaceID: String?
    @State private var showsCustomerLayer = true
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
            guard let origin, let lat = origin.latitude, let lon = origin.longitude,
                  (-90.0...90.0).contains(lat), (-180.0...180.0).contains(lon) else { return nil }
            return CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }()
        guard let plan else { return [] }
        for (dayIndex, day) in plan.dayList.enumerated() {
            var coordinates: [CLLocationCoordinate2D] = []
            // The origin is a valid first-day anchor only. Never draw a synthetic
            // overnight transfer from yesterday's final stop to today's first stop.
            if dayIndex == 0, let originCoordinate { coordinates.append(originCoordinate) }
            let dayStops = day.stops.filter(\.hasValidCoordinates)
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
        plan?.legs ?? []
    }

    private var mappedCustomerLocations: [CustomerMapLocation] {
        customerLocations.filter { $0.place.hasValidCoordinates }
    }

    private var selectedCustomerLocation: CustomerMapLocation? {
        guard let selectedCustomerPlaceID else { return nil }
        return customerLocations.first { $0.id == selectedCustomerPlaceID }
    }

    var body: some View {
        Group {
            if dayRoutes.isEmpty && mappedCustomerLocations.isEmpty && !isLoadingCustomerPlaces {
                VStack(spacing: DS.Spacing.sm) {
                    ContentUnavailableView(
                        "没有可展示的地图坐标",
                        systemImage: DSIconCatalog.map,
                        description: Text("拜访地点和客户地址都没有可用的有效坐标。地图不会用城市中心点或虚构位置代替。")
                    )
                    if let customerPlacesMessage {
                        Text(customerPlacesMessage)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, DS.Spacing.lg)
                    }
                    if !customerLocations.isEmpty {
                        let invalidCount = customerLocations.filter {
                            !$0.place.hasValidCoordinates
                        }.count
                        Text("已读取客户地址 \(customerLocations.count) 个；其中 \(invalidCount) 个缺少有效坐标。")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.warning)
                    }
                }
            } else {
                VStack(spacing: 0) {
                    dayFilter
                    if !dayRoutes.isEmpty { routeAccuracyNotice }
                    customerPlacesNotice
                    Map(position: $position, selection: $selectedStopID) {
                        ForEach(visibleDayRoutes, id: \.dayIndex) { route in
                            if route.coordinates.count >= 2 {
                                MapPolyline(coordinates: route.coordinates)
                                    .stroke(route.color, style: StrokeStyle(lineWidth: 2.5, lineCap: .round, dash: [7, 5]))
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
                        if showsCustomerLayer {
                            ForEach(mappedCustomerLocations) { location in
                                if let latitude = location.place.lat, let longitude = location.place.lon {
                                    Annotation(
                                        location.customer.name,
                                        coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude),
                                        anchor: .bottom
                                    ) {
                                        Button {
                                            selectedStopID = nil
                                            selectedCustomerPlaceID = location.id
                                        } label: {
                                            VStack(spacing: 2) {
                                                Image(systemName: "building.2.fill")
                                                    .font(.system(size: 13, weight: .semibold))
                                                    .foregroundStyle(.white)
                                                    .frame(width: 32, height: 32)
                                                    .background(DS.ColorToken.info, in: Circle())
                                                    .overlay(Circle().stroke(DS.ColorToken.surface, lineWidth: 2))
                                                    .shadow(color: .black.opacity(0.16), radius: 4, y: 2)
                                                if location.place.isPrimary {
                                                    Text("主地址")
                                                        .font(.system(size: 9, weight: .semibold))
                                                        .foregroundStyle(DS.ColorToken.textPrimary)
                                                        .padding(.horizontal, 5)
                                                        .padding(.vertical, 2)
                                                        .background(DS.ColorToken.surface, in: Capsule())
                                                }
                                            }
                                        }
                                        .buttonStyle(.plain)
                                        .accessibilityLabel("\(location.customer.name)，\(location.place.label ?? location.place.name)")
                                    }
                                }
                            }
                        }
                    }
                    .mapStyle(.standard)
                    .mapControls {
                        MapCompass()
                        MapScaleView()
                    }
                    .accessibilityLabel(dayRoutes.isEmpty ? "客户地址地图" : "商务拜访点位顺序示意地图；虚线不代表真实道路导航路线")
                    if let selectedStop {
                        selectedStopDrawer(selectedStop)
                            .transition(.move(edge: .bottom).combined(with: .opacity))
                    } else if let selectedCustomerLocation {
                        MapCustomerPlaceDrawer(location: selectedCustomerLocation) {
                            selectedCustomerPlaceID = nil
                        }
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                    }
                    if !visibleDayRoutes.isEmpty { routeLegend }
                }
                .animation(.easeInOut(duration: DS.Motion.standard), value: selectedStopID)
                .animation(.easeInOut(duration: DS.Motion.standard), value: selectedCustomerPlaceID)
            }
        }
        .navigationTitle(title)
        .onAppear { position = .automatic }
        .onChange(of: customerLocations.map(\.id)) { oldIDs, newIDs in
            if oldIDs.isEmpty && !newIDs.isEmpty { position = .automatic }
        }
        .onChange(of: selectedStopID) { _, newValue in
            if newValue != nil { selectedCustomerPlaceID = nil }
        }
        .onChange(of: plan?.id) { _, _ in
            selectedStopID = nil
            selectedCustomerPlaceID = nil
            selectedDayIndex = nil
            position = .automatic
        }
    }

    @ViewBuilder
    private var customerPlacesNotice: some View {
        if !customerLocations.isEmpty || customerPlacesMessage != nil || isLoadingCustomerPlaces {
            let mappedCount = mappedCustomerLocations.count
            HStack(alignment: .top, spacing: DS.Spacing.sm) {
                Image(systemName: "building.2")
                    .foregroundStyle(DS.ColorToken.info)
                VStack(alignment: .leading, spacing: 2) {
                    Text("客户地址图层 · \(mappedCount)/\(customerLocations.count) 个地址有有效坐标")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    if isLoadingCustomerPlaces {
                        Text("正在同步当前 Mission 的客户地址…")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    if let customerPlacesMessage {
                        Text(customerPlacesMessage)
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        Text("客户地址独立于计划拜访点显示；主地址标记不代表该地点已安排拜访。")
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, DS.Spacing.md)
            .padding(.vertical, DS.Spacing.sm)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.ColorToken.info.opacity(0.06))
        }
    }

    /// The current plan contract carries leg mode/source and stop coordinates, but no route geometry.
    /// A dashed connector is therefore explicitly presented as an order schematic, never navigation.
    private var routeAccuracyNotice: some View {
        HStack(alignment: .top, spacing: DS.Spacing.sm) {
            Image(systemName: "info.circle")
                .foregroundStyle(DS.ColorToken.info)
            VStack(alignment: .leading, spacing: 2) {
                Text("拜访顺序示意")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text("虚线仅连接计划点位，不代表真实道路、铁路或航班轨迹；交通方式与耗时以计划数据卡片为准。")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, DS.Spacing.md)
        .padding(.vertical, DS.Spacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DS.ColorToken.info.opacity(0.07))
    }

    /// Day filtering changes only the map projection, never the underlying plan inputs.
    private var dayFilter: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: DS.Spacing.xs) {
                if !dayRoutes.isEmpty {
                    filterChip(title: "全部日期", selected: selectedDayIndex == nil) {
                        selectedDayIndex = nil
                        selectedStopID = nil
                        selectedCustomerPlaceID = nil
                        position = .automatic
                    }
                    filterChip(title: "全部拜访", selected: !showsOpenVisitsOnly) {
                        showsOpenVisitsOnly = false
                        selectedStopID = nil
                        selectedCustomerPlaceID = nil
                    }
                    filterChip(title: "待执行", selected: showsOpenVisitsOnly) {
                        showsOpenVisitsOnly = true
                        selectedStopID = nil
                        selectedCustomerPlaceID = nil
                    }
                    ForEach(dayRoutes, id: \.dayIndex) { route in
                        filterChip(
                            title: "第 \(route.dayIndex) 天",
                            selected: selectedDayIndex == route.dayIndex
                        ) {
                            selectedDayIndex = route.dayIndex
                            selectedStopID = nil
                            selectedCustomerPlaceID = nil
                            position = .automatic
                        }
                    }
                }
                if !customerLocations.isEmpty {
                    filterChip(title: "客户地址 · \(mappedCustomerLocations.count)", selected: showsCustomerLayer) {
                        showsCustomerLayer.toggle()
                        selectedCustomerPlaceID = nil
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
        leg.sourceLabel
    }
}
