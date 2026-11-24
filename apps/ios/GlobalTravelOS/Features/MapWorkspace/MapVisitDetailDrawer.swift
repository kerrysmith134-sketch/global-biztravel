import MapKit
import SwiftUI

private enum MapHandoff {
    static func open(name: String, latitude: Double, longitude: Double) {
        guard (-90.0...90.0).contains(latitude),
              (-180.0...180.0).contains(longitude) else { return }
        let coordinate = CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
        let item = MKMapItem(placemark: MKPlacemark(coordinate: coordinate))
        item.name = name
        // Open the destination only; do not force driving for rail/air legs.
        item.openInMaps()
    }
}

/// A focused inspection drawer for a selected stop on the route map.
struct MapVisitDetailDrawer: View {
    let stop: PlanStop
    let visit: TripVisit?
    let customer: Customer?
    let dayDate: String?
    let timeZone: TimeZone
    let isPlanActive: Bool
    let isSubmittingChange: Bool
    let onCancelVisit: ((PlanStop) -> Void)?
    let onClose: () -> Void

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                header
                customerContext
                visitContext
                timingContext
                travelContext
                mapAction
                cancellationAction
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .accessibilityElement(children: .contain)
    }

    private var header: some View {
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
                onClose()
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
    }

    private var customerContext: some View {
        Group {
            if let customer {
                VStack(alignment: .leading, spacing: 3) {
                    Label(customer.name, systemImage: "building.2")
                        .font(.caption.weight(.medium))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    if let industry = customer.industry, !industry.isEmpty {
                        Text(industry)
                            .font(.caption2)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var visitContext: some View {
        if let visit {
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                HStack(spacing: DS.Spacing.xs) {
                    DSBadge(text: visit.statusLabel, color: DS.ColorToken.status(visit.status ?? ""))
                    if let priority = visit.priority {
                        DSBadge(text: "优先级 \(priority)", color: DS.ColorToken.priority("P\(priority)"))
                    }
                    if let customer {
                        DSBadge(text: customer.tierLabel, color: DS.ColorToken.tier(customer.tier))
                    }
                }
                if let objective = visit.objective, !objective.isEmpty {
                    Text(objective)
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if visit.timeWindowStart != nil || visit.timeWindowEnd != nil {
                    Label(visit.timeWindowLabel, systemImage: "clock")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            }
        }
    }

    private var timingContext: some View {
        HStack(spacing: DS.Spacing.md) {
            if let dayDate { Text(dayDate) }
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
    }

    private var travelContext: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.xs) {
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
                Spacer(minLength: 0)
                if let cost = stop.costAmount {
                    Text("\(stop.costCurrency ?? "") \(String(format: "%.2f", cost))")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textPrimary)
                }
            }
            if stop.travelSource != nil || stop.travelIsStale {
                DSDataQualityTag(
                    kind: stop.travelIsStale ? .stale : .estimate,
                    text: stop.travelSourceLabel
                )
            }
        }
    }

    @ViewBuilder
    private var mapAction: some View {
        if stop.hasValidCoordinates,
           let latitude = stop.latitude,
           let longitude = stop.longitude {
            Button {
                MapHandoff.open(name: stop.displayName, latitude: latitude, longitude: longitude)
            } label: {
                Label("在 Apple 地图中查看", systemImage: "map")
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.bordered)
            .accessibilityIdentifier("map-visit-open-in-maps")
            .accessibilityHint("打开地点后，可在 Apple 地图中选择合适的导航方式")
        }
    }

    @ViewBuilder
    private var cancellationAction: some View {
        if isPlanActive, let onCancelVisit, visit?.isOpen ?? true {
            Divider()
            Button(role: .destructive) {
                onCancelVisit(stop)
            } label: {
                Label(
                    isSubmittingChange ? "正在处理计划变更…" : "取消此拜访并重新优化路线",
                    systemImage: "arrow.clockwise"
                )
                .font(.caption.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 42)
            }
            .disabled(isSubmittingChange)
            .accessibilityHint("提交前会要求再次确认，服务端将生成新的计划版本")
        }
    }

    private func time(_ date: Date) -> String {
        ISO8601.timeDisplay(from: date, timeZone: timeZone)
    }

    private func duration(_ minutes: Int) -> String {
        Distance.formatDuration(minutes: minutes)
    }
}

/// Inspection panel for a customer address pin. This is deliberately separate from a planned visit:
/// linking a customer to an address does not imply that a visit has been scheduled there.
struct MapCustomerPlaceDrawer: View {
    let location: CustomerMapLocation
    let onClose: () -> Void

    private var coordinateLabel: String? {
        guard location.place.hasValidCoordinates,
              let latitude = location.place.lat,
              let longitude = location.place.lon else { return nil }
        return String(format: "%.5f, %.5f", latitude, longitude)
    }

    var body: some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(alignment: .top, spacing: DS.Spacing.sm) {
                    Image(systemName: "building.2.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(DS.ColorToken.info, in: RoundedRectangle(cornerRadius: 10))
                    VStack(alignment: .leading, spacing: 3) {
                        Text(location.customer.name)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .lineLimit(2)
                        Text(location.place.name)
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    Spacer(minLength: 2)
                    Button(action: onClose) {
                        Image(systemName: "xmark")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .frame(width: 32, height: 32)
                            .background(DS.ColorToken.surfaceSecondary, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("关闭客户地址详情")
                }

                HStack(spacing: DS.Spacing.xs) {
                    DSBadge(text: location.customer.tierLabel, color: DS.ColorToken.tier(location.customer.tier))
                    if let label = location.place.label, !label.isEmpty {
                        DSBadge(text: label, color: DS.ColorToken.info)
                    }
                    if location.place.isPrimary {
                        DSBadge(text: "主地址", color: DS.ColorToken.success)
                    }
                }

                if location.place.displayAddress.isEmpty {
                    Text("尚未登记完整地址")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.warning)
                } else {
                    Text(location.place.displayAddress)
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if location.place.hasValidCoordinates,
                   let latitude = location.place.lat,
                   let longitude = location.place.lon {
                    Button {
                        MapHandoff.open(
                            name: "\(location.customer.name) · \(location.place.name)",
                            latitude: latitude,
                            longitude: longitude
                        )
                    } label: {
                        Label("在 Apple 地图中查看", systemImage: "map")
                            .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.bordered)
                    .accessibilityIdentifier("map-customer-open-in-maps")
                }

                Divider()
                VStack(alignment: .leading, spacing: 5) {
                    metadataRow("地理编码状态", location.place.geocodeStatus)
                    if let source = location.place.geocodeSource, !source.isEmpty {
                        metadataRow("坐标来源", source)
                    }
                    if let confidence = location.place.confidenceLabel {
                        metadataRow("来源可信度", confidence)
                    }
                    if let coordinateLabel {
                        metadataRow("坐标", coordinateLabel)
                    } else {
                        metadataRow("坐标", "缺失或无效")
                    }
                }

                Text("客户地址仅表示 CRM 中已关联的地点，不代表已纳入当前 Mission 的拜访计划。")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, DS.Spacing.sm)
        .padding(.vertical, DS.Spacing.xs)
        .accessibilityElement(children: .contain)
    }

    private func metadataRow(_ title: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: DS.Spacing.sm) {
            Text(title)
                .font(.caption2)
                .foregroundStyle(DS.ColorToken.textSecondary)
            Spacer(minLength: 4)
            Text(value)
                .font(.caption2.weight(.medium).monospacedDigit())
                .foregroundStyle(DS.ColorToken.textPrimary)
                .multilineTextAlignment(.trailing)
        }
    }
}
