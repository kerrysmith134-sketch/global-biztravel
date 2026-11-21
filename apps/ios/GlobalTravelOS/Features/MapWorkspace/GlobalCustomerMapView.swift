import MapKit
import SwiftUI

@MainActor
final class GlobalCustomerMapViewModel: ObservableObject {
    @Published private(set) var locations: [GlobalCustomerMapLocation] = []
    @Published private(set) var summary = GlobalCustomerMapSummary.empty
    @Published private(set) var isLoading = false
    @Published private(set) var errorMessage: String?
    @Published private(set) var loadWarning: String?
    @Published var searchQuery = ""
    @Published var tier = "ALL"
    @Published var country = "ALL"
    @Published var quality = "ALL"
    @Published var selectedID: String?
    @Published var mode = "map"

    private let repository: CustomerRepository
    private var loadToken: UUID?

    init(repository: CustomerRepository) { self.repository = repository }

    var filtered: [GlobalCustomerMapLocation] {
        let query = searchQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        return locations.filter { item in
            if tier != "ALL", item.customer.tier.uppercased() != tier { return false }
            if country != "ALL", item.place.countryCode?.uppercased() != country { return false }
            switch quality {
            case "MAPPED": if !item.place.hasValidCoordinates { return false }
            case "MISSING": if item.place.hasValidCoordinates { return false }
            case "FAILED": if item.place.geocodeStatus.uppercased() != "FAILED" { return false }
            default: break
            }
            guard !query.isEmpty else { return true }
            let values = [
                item.customer.name, item.customer.industry, item.place.name,
                item.place.addressLine, item.place.city, item.place.region, item.place.countryCode
            ].compactMap { $0 }
            return values.contains { $0.localizedCaseInsensitiveContains(query) }
        }
    }

    var countries: [String] {
        Set(locations.compactMap { $0.place.countryCode?.uppercased() }).sorted()
    }

    var selected: GlobalCustomerMapLocation? {
        guard let selectedID else { return nil }
        return filtered.first { $0.id == selectedID }
    }

    func load() async {
        let token = UUID()
        loadToken = token
        isLoading = true
        errorMessage = nil
        loadWarning = nil
        defer { if loadToken == token { isLoading = false } }

        var cursor: String?
        var all: [GlobalCustomerMapLocation] = []
        var aggregate = GlobalCustomerMapSummary.empty
        var pageCount = 0
        do {
            repeat {
                let result = try await repository.listGlobalCustomerMapLocations(
                    filters: .all,
                    page: PageRequest(limit: 200, cursor: cursor)
                )
                guard loadToken == token else { return }
                if pageCount == 0 { aggregate = result.summary }
                all.append(contentsOf: result.items)
                cursor = result.nextCursor
                pageCount += 1
            } while cursor != nil && pageCount < 100

            guard loadToken == token else { return }
            locations = all
            summary = aggregate
            if cursor != nil { loadWarning = "已加载前 20,000 个地点，请使用筛选缩小范围。" }
            if let selectedID, !all.contains(where: { $0.id == selectedID }) {
                self.selectedID = nil
            }
        } catch {
            guard loadToken == token else { return }
            errorMessage = error.localizedDescription
        }
    }
}

struct GlobalCustomerMapView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var model: GlobalCustomerMapViewModel

    init(environment: AppEnvironment) {
        _model = StateObject(wrappedValue: GlobalCustomerMapViewModel(repository: environment.customerRepository))
    }

    var body: some View {
        VStack(spacing: 0) {
            if model.isLoading && model.locations.isEmpty {
                DSLoadingState(message: "正在读取组织客户地点…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let error = model.errorMessage, model.locations.isEmpty {
                DSErrorState(message: error) { Task { await model.load() } }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                metrics
                filters
                modeSelector
                if model.mode == "map" { mapPanel } else { listPanel }
            }
        }
        .background(DS.ColorToken.canvas.ignoresSafeArea())
        .navigationTitle("全球客户地图")
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $model.searchQuery, prompt: "搜索客户、地址或城市")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { Task { await model.load() } } label: { Image(systemName: "arrow.clockwise") }
                    .accessibilityLabel("刷新全球客户地图")
            }
        }
        .task { await model.load() }
        .refreshable { await model.load() }
    }

    private var metrics: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: DS.Spacing.sm) {
                metric("有地址客户", model.summary.customersWithLocations, "building.2")
                metric("客户地点", model.summary.locationsTotal, "mappin.and.ellipse")
                metric("已定位", model.summary.locationsMapped, "location.fill")
                metric("缺坐标", model.summary.locationsMissingCoordinates, "mappin.slash")
                metric("无地址客户", model.summary.customersWithoutLocations, "building.2.crop.circle")
            }
            .padding(.horizontal, DS.Spacing.lg)
            .padding(.vertical, DS.Spacing.sm)
        }
    }

    private func metric(_ title: String, _ count: Int, _ symbol: String) -> some View {
        DSCard(padding: DS.Spacing.sm) {
            VStack(alignment: .leading, spacing: 4) {
                Label(title, systemImage: symbol)
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                Text(count.formatted())
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(DS.ColorToken.textPrimary)
            }
            .frame(width: 92, alignment: .leading)
        }
    }

    private var filters: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: DS.Spacing.xs) {
                tierFilter("全部", "ALL")
                tierFilter("A 级", "A")
                tierFilter("B 级", "B")
                tierFilter("C 级", "C")
                Menu {
                    Button("全部国家") { model.country = "ALL" }
                    ForEach(model.countries, id: \.self) { value in
                        Button(value) { model.country = value }
                    }
                } label: {
                    filterPill(model.country == "ALL" ? "国家" : model.country, model.country != "ALL")
                }
                Menu {
                    Button("所有地址") { model.quality = "ALL" }
                    Button("已定位") { model.quality = "MAPPED" }
                    Button("缺少坐标") { model.quality = "MISSING" }
                    Button("编码失败") { model.quality = "FAILED" }
                } label: { filterPill(qualityTitle, model.quality != "ALL") }
            }
            .padding(.horizontal, DS.Spacing.lg)
        }
        .padding(.vertical, DS.Spacing.xs)
    }

    private var qualityTitle: String {
        switch model.quality {
        case "MAPPED": return "已定位"
        case "MISSING": return "缺坐标"
        case "FAILED": return "编码失败"
        default: return "地址质量"
        }
    }

    private func tierFilter(_ title: String, _ value: String) -> some View {
        Button { model.tier = value } label: { filterPill(title, model.tier == value) }
            .buttonStyle(.plain)
    }

    private func filterPill(_ title: String, _ selected: Bool) -> some View {
        Text(title)
            .font(.caption.weight(.semibold))
            .foregroundStyle(selected ? DS.ColorToken.brand : DS.ColorToken.textSecondary)
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            .background(selected ? DS.ColorToken.brandSubtle : DS.ColorToken.surface, in: Capsule())
            .overlay(Capsule().stroke(DS.ColorToken.border, lineWidth: 0.8))
    }

    private var modeSelector: some View {
        HStack {
            Text("\(model.filtered.count.formatted()) 个匹配地点")
                .font(.caption.weight(.medium))
                .foregroundStyle(DS.ColorToken.textSecondary)
            Spacer()
            Picker("视图", selection: $model.mode) {
                Label("地图", systemImage: "map").tag("map")
                Label("列表", systemImage: "list.bullet").tag("list")
            }
            .pickerStyle(.segmented)
            .frame(width: 145)
        }
        .padding(.horizontal, DS.Spacing.lg)
        .padding(.vertical, DS.Spacing.sm)
    }

    private var mapPanel: some View {
        Group {
            if model.filtered.isEmpty {
                DSEmptyState(
                    title: "没有匹配的客户地点",
                    message: "调整筛选条件，或先在客户档案中补充企业地址。",
                    systemImage: DSIconCatalog.map
                )
            } else {
                GlobalCustomerMapKitView(locations: model.filtered, selectedID: $model.selectedID)
                    .overlay(alignment: .bottom) {
                        if let selected = model.selected {
                            selectedCard(selected).padding(DS.Spacing.md)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: DS.Radius.card))
                    .overlay(RoundedRectangle(cornerRadius: DS.Radius.card).stroke(DS.ColorToken.border, lineWidth: 0.8))
                    .padding(.horizontal, DS.Spacing.md)
                    .padding(.bottom, DS.Spacing.md)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var listPanel: some View {
        List {
            ForEach(countryGroups, id: \.key) { group in
                Section(group.key) {
                    ForEach(group.value) { item in
                        Button {
                            model.selectedID = item.id
                            model.mode = "map"
                        } label: {
                            locationRow(item)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("在地图上定位 \(item.customer.name)，\(item.place.name)")
                    }
                }
            }
            if let warning = model.loadWarning {
                Text(warning).font(.caption).foregroundStyle(DS.ColorToken.warning)
            }
        }
        .listStyle(.insetGrouped)
    }

    private var countryGroups: [(key: String, value: [GlobalCustomerMapLocation])] {
        let groups = Dictionary(grouping: model.filtered) { $0.place.countryCode ?? "国家未知" }
        return groups.keys.sorted().map { ($0, groups[$0] ?? []) }
    }

    private func locationRow(_ item: GlobalCustomerMapLocation) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(item.customer.name).font(.subheadline.weight(.semibold))
                DSBadge(text: item.customer.tier, color: DS.ColorToken.tier(item.customer.tier))
                if item.place.isPrimary { DSBadge(text: "主地址", color: DS.ColorToken.success) }
            }
            Text([item.place.label, item.place.name].compactMap { $0 }.joined(separator: " · "))
                .font(.caption.weight(.medium))
            if !item.place.displayAddress.isEmpty {
                Text(item.place.displayAddress).font(.caption).foregroundStyle(DS.ColorToken.textSecondary)
            }
            HStack(spacing: 10) {
                Label(item.place.hasValidCoordinates ? "坐标可用" : "缺少坐标",
                      systemImage: item.place.hasValidCoordinates ? "location.fill" : "mappin.slash")
                if let source = item.place.geocodeSource { Text("来源：\(source)") }
                if let confidence = item.place.confidenceLabel { Text(confidence) }
            }
            .font(.caption2)
            .foregroundStyle(DS.ColorToken.textSecondary)
        }
    }

    private func selectedCard(_ item: GlobalCustomerMapLocation) -> some View {
        DSCard(padding: DS.Spacing.md) {
            VStack(alignment: .leading, spacing: DS.Spacing.xs) {
                HStack {
                    Text(item.customer.name).font(.subheadline.weight(.semibold))
                    DSBadge(text: item.customer.tier, color: DS.ColorToken.tier(item.customer.tier))
                    Spacer()
                    Button { model.selectedID = nil } label: {
                        Image(systemName: "xmark.circle.fill").foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
                Text([item.place.label, item.place.name].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption.weight(.medium))
                Text(item.place.displayAddress.isEmpty ? "地址信息不完整" : item.place.displayAddress)
                    .font(.caption)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                NavigationLink {
                    CustomerDetailView(customer: domainCustomer(item.customer))
                } label: {
                    Label("打开客户档案", systemImage: "arrow.up.right").font(.caption.weight(.semibold))
                }
            }
        }
    }

    private func domainCustomer(_ item: GlobalMapCustomer) -> Customer {
        Customer(
            id: item.id, name: item.name, tier: item.tier,
            countryCode: item.countryCode, industry: item.industry, status: item.status
        )
    }
}

private final class GlobalCustomerPin: NSObject, MKAnnotation {
    let location: GlobalCustomerMapLocation
    @objc dynamic var coordinate: CLLocationCoordinate2D

    init(location: GlobalCustomerMapLocation) {
        self.location = location
        coordinate = CLLocationCoordinate2D(latitude: location.place.lat ?? 0, longitude: location.place.lon ?? 0)
        super.init()
    }

    var title: String? { location.customer.name }
    var subtitle: String? { location.place.displayAddress }
}

struct GlobalCustomerMapKitView: UIViewRepresentable {
    var locations: [GlobalCustomerMapLocation]
    @Binding var selectedID: String?

    func makeCoordinator() -> Coordinator { Coordinator { selectedID = $0 } }

    func makeUIView(context: Context) -> MKMapView {
        let map = MKMapView(frame: .zero)
        map.delegate = context.coordinator
        map.showsCompass = true
        map.showsScale = true
        map.isRotateEnabled = false
        map.register(MKMarkerAnnotationView.self, forAnnotationViewWithReuseIdentifier: Coordinator.pinID)
        map.register(MKMarkerAnnotationView.self, forAnnotationViewWithReuseIdentifier: Coordinator.clusterID)
        return map
    }

    func updateUIView(_ map: MKMapView, context: Context) {
        context.coordinator.onSelect = { selectedID = $0 }
        let valid = locations.filter { $0.place.hasValidCoordinates }
        let signature = valid.map(\.id).sorted().joined(separator: "|")
        if signature != context.coordinator.signature {
            context.coordinator.signature = signature
            map.removeAnnotations(map.annotations.compactMap { $0 as? GlobalCustomerPin })
            let pins = valid.map(GlobalCustomerPin.init(location:))
            map.addAnnotations(pins)
            if !pins.isEmpty { map.showAnnotations(pins, animated: context.coordinator.didSetRegion) }
            context.coordinator.didSetRegion = true
        }
        guard let selectedID else {
            if let annotation = map.selectedAnnotations.first {
                map.deselectAnnotation(annotation, animated: true)
            }
            context.coordinator.selectedID = nil
            return
        }
        guard context.coordinator.selectedID != selectedID,
              let pin = map.annotations.compactMap({ $0 as? GlobalCustomerPin })
                .first(where: { $0.location.id == selectedID }) else { return }
        context.coordinator.selectedID = selectedID
        map.selectAnnotation(pin, animated: true)
        map.setCenter(pin.coordinate, animated: true)
    }

    final class Coordinator: NSObject, MKMapViewDelegate {
        static let pinID = "global-customer-pin"
        static let clusterID = "global-customer-cluster"
        var onSelect: (String?) -> Void
        var signature = ""
        var selectedID: String?
        var didSetRegion = false

        init(onSelect: @escaping (String?) -> Void) { self.onSelect = onSelect }

        func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
            if let cluster = annotation as? MKClusterAnnotation {
                let view = mapView.dequeueReusableAnnotationView(withIdentifier: Self.clusterID, for: cluster)
                    as? MKMarkerAnnotationView
                view?.clusteringIdentifier = nil
                view?.markerTintColor = UIColor(DS.ColorToken.brand)
                view?.glyphText = "\(cluster.memberAnnotations.count)"
                view?.canShowCallout = false
                return view
            }
            guard let pin = annotation as? GlobalCustomerPin else { return nil }
            let view = mapView.dequeueReusableAnnotationView(withIdentifier: Self.pinID, for: pin)
                as? MKMarkerAnnotationView
            view?.clusteringIdentifier = "customer-location-cluster"
            view?.markerTintColor = UIColor(DS.ColorToken.tier(pin.location.customer.tier))
            view?.glyphImage = UIImage(systemName: "building.2.fill")
            view?.displayPriority = .defaultHigh
            view?.canShowCallout = true
            return view
        }

        func mapView(_ mapView: MKMapView, didSelect view: MKAnnotationView) {
            guard let pin = view.annotation as? GlobalCustomerPin else { return }
            selectedID = pin.location.id
            onSelect(pin.location.id)
        }
    }
}
