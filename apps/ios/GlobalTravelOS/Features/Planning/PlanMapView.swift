import MapKit
import SwiftUI

/// 激活计划的只读地图视图：每个有坐标的停靠一个 Marker。
struct PlanMapView: View {
    let plan: PlanVersionDetail
    @State private var position: MapCameraPosition = .automatic

    private var stops: [PlanStop] {
        plan.orderedStops.map(\.stop).filter(\.hasCoordinates)
    }

    var body: some View {
        Group {
            if stops.isEmpty {
                ContentUnavailableView(
                    "没有可展示的坐标",
                    systemImage: "map",
                    description: Text("拜访地点缺少经纬度，先补全坐标再查看地图。")
                )
            } else {
                Map(position: $position) {
                    ForEach(stops) { stop in
                        if let latitude = stop.latitude, let longitude = stop.longitude {
                            Marker(
                                stop.displayName,
                                coordinate: CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
                            )
                            .tint(.blue)
                        }
                    }
                }
                .mapStyle(.standard)
            }
        }
        .navigationTitle("行程地图")
        .onAppear { position = .automatic }
    }
}
