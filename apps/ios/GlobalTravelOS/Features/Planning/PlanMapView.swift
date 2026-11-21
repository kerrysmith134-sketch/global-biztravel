import MapKit
import SwiftUI

/// 行程地图：按天绘制路线 polyline（出发地 → 当日停靠顺序），
/// 并在底部标注每段交通的时间/方式/数据来源（审计 5.3：不再只是点位图）。
struct PlanMapView: View {
    let plan: PlanVersionDetail
    var origin: Place?

    @State private var position: MapCameraPosition = .automatic
    @State private var selection: PlanStop?

    private struct DayRoute {
        var dayIndex: Int
        var date: String
        var coordinates: [CLLocationCoordinate2D]
        var stops: [PlanStop]
        var color: Color
    }

    private static let palette: [Color] = DS.ColorToken.routePalette

    /// 每天一条连续路径：出发地/上一段终点 → 当日各停靠（跳过缺坐标的点）。
    private var dayRoutes: [DayRoute] {
        var routes: [DayRoute] = []
        let originCoordinate: CLLocationCoordinate2D? = {
            guard let origin, let lat = origin.latitude, let lon = origin.longitude else { return nil }
            return CLLocationCoordinate2D(latitude: lat, longitude: lon)
        }()
        var previousCoordinate = originCoordinate
        for (dayIndex, day) in plan.days?.enumerated() ?? [].enumerated() {
            var coords: [CLLocationCoordinate2D] = []
            if let start = previousCoordinate { coords.append(start) }
            var dayStops: [PlanStop] = []
            for stop in day.stops where stop.hasCoordinates {
                guard let lat = stop.latitude, let lon = stop.longitude else { continue }
                coords.append(CLLocationCoordinate2D(latitude: lat, longitude: lon))
                dayStops.append(stop)
            }
            if !dayStops.isEmpty {
                routes.append(
                    DayRoute(
                        dayIndex: dayIndex + 1,
                        date: day.date,
                        coordinates: coords,
                        stops: dayStops,
                        color: Self.palette[dayIndex % Self.palette.count]
                    )
                )
                if let last = coords.last { previousCoordinate = last }
            }
        }
        return routes
    }

    private var legs: [PlanTravelLeg] {
        plan.legs ?? []
    }

    var body: some View {
        Group {
            if dayRoutes.isEmpty {
                ContentUnavailableView(
                    "没有可展示的坐标",
                    systemImage: "map",
                    description: Text("拜访地点缺少经纬度，先补全坐标再查看地图。")
                )
            } else {
                VStack(spacing: 0) {
                    Map(position: $position) {
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
                                }
                            }
                        }
                    }
                    .mapStyle(.standard)
                    routeLegend
                }
            }
        }
        .navigationTitle("行程地图")
        .onAppear { position = .automatic }
    }

    /// 底部图例：按天列出路线 + 每段交通的时长/方式/来源（数据质量显式标注）。
    private var routeLegend: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(dayRoutes, id: \.dayIndex) { route in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Circle().fill(route.color).frame(width: 8, height: 8)
                            Text("第 \(route.dayIndex) 天 · \(route.date)")
                                .font(.caption.weight(.semibold))
                        }
                        ForEach(route.stops) { stop in
                            Text("\(stop.sequence). \(stop.displayName) \(stopTimeLabel(stop))")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if let leg = legs.first(where: { $0.dayIndex == route.dayIndex - 1 }) {
                            Text("\(leg.mode) · \(leg.travelMinutes) 分钟 · \(sourceLabel(leg))")
                                .font(.caption2)
                                .foregroundStyle(leg.stale ? .orange : .secondary)
                        }
                    }
                    .padding(10)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
                }
            }
            .padding(10)
        }
        .background(.bar)
    }

    private func stopTimeLabel(_ stop: PlanStop) -> String {
        guard let arrive = stop.arrive else { return "" }
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: arrive)
    }

    /// 数据来源标注（审计 5.3 要求：城市中心点/直线估算不得冒充真实路线数据）。
    private func sourceLabel(_ leg: PlanTravelLeg) -> String {
        if leg.stale { return "供应商不可用·直线估算" }
        switch leg.source {
        case "osrm-road": return "OSRM 路网"
        case "haversine-estimate": return "直线估算"
        case let other: return other
        }
    }
}
