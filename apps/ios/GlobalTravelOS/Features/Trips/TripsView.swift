import SwiftUI

@MainActor
final class TripsViewModel: ObservableObject {
    @Published private(set) var trips: [Trip] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?
    @Published var showsCreate = false
    @Published private(set) var hasMore = false
    @Published private(set) var lastTrip: Trip?

    private var cursor: String?
    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let page = try await environment.tripListUseCase.loadFirstPage()
            trips = environment.tripListUseCase.activeTrips(from: page.items)
            cursor = page.nextCursor
            hasMore = cursor != nil
            if let lastId = environment.settings.lastTripId {
                lastTrip = try? await environment.tripRepository.trip(id: lastId)
            }
            environment.refreshOfflineSummary()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func loadMore() async {
        guard let cursor else { return }
        do {
            let page = try await environment.tripListUseCase.loadNextPage(after: cursor)
            var seen = Set(trips.map(\.id))
            for trip in environment.tripListUseCase.activeTrips(from: page.items) where !seen.contains(trip.id) {
                seen.insert(trip.id)
                trips.append(trip)
            }
            self.cursor = page.nextCursor
            hasMore = page.nextCursor != nil
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func create(name: String, countryCode: String, start: Date, end: Date) async -> Bool {
        do {
            let trip = try await environment.createTripUseCase.create(
                name: name,
                purpose: "商务拜访",
                originName: "上海总部",
                countryCode: countryCode,
                timezone: "Asia/Shanghai",
                latitude: 31.2304,
                longitude: 121.4737,
                start: start,
                end: end
            )
            // 列表按窗口开始时间倒序分页；刚建的近期出行可能落在下一页。先把它放到可见位置，
            // 而不是让用户创建成功后看不到自己刚建的出行。
            trips.removeAll { $0.id == trip.id }
            trips.insert(trip, at: 0)
            return true
        } catch {
            errorMessage = environment.errorMessage(error)
            return false
        }
    }
}

struct TripsView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TripsViewModel

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: TripsViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            List {
                if let error = viewModel.errorMessage {
                    Section { ErrorBanner(message: error) }
                }
                if let lastTrip = viewModel.lastTrip {
                    Section("上次出行") {
                        NavigationLink {
                            TripDetailView(tripId: lastTrip.id, environment: environment)
                        } label: {
                            TripRow(trip: lastTrip)
                        }
                    }
                }

                Section {
                    ForEach(viewModel.trips) { trip in
                        NavigationLink {
                            TripDetailView(tripId: trip.id, environment: environment)
                        } label: {
                            TripRow(trip: trip)
                        }
                    }
                    if viewModel.trips.isEmpty && !viewModel.isLoading {
                        ContentUnavailableView(
                            "还没有出行",
                            systemImage: "airplane.departure",
                            description: Text("点右上角新建一次出行，再导入客户或添加拜访。")
                        )
                    }
                    if viewModel.hasMore {
                        Button("加载更多") {
                            Task { await viewModel.loadMore() }
                        }
                    }
                }
            }
            .navigationTitle("出行")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        viewModel.showsCreate = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("新建出行")
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
            .sheet(isPresented: $viewModel.showsCreate) {
                CreateTripSheet { name, country, start, end in
                    await viewModel.create(
                        name: name,
                        countryCode: country,
                        start: start,
                        end: end
                    )
                }
            }
        }
    }
}

private struct TripRow: View {
    let trip: Trip

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(trip.name).font(.headline)
                Spacer()
                Text(trip.statusLabel)
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(.quaternary, in: Capsule())
            }
            Text(trip.windowLabel)
                .font(.subheadline)
                .foregroundStyle(.secondary)
            Text(trip.stats?.displayText ?? "客户 0 · 拜访 0 · 计划版本 0")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}

private struct CreateTripSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onCreate: (String, String, Date, Date) async -> Bool

    @State private var name = ""
    @State private var countryCode = "DE"
    @State private var start = Date().addingTimeInterval(86_400 * 7)
    @State private var end = Date().addingTimeInterval(86_400 * 10)
    @State private var saving = false

    var body: some View {
        NavigationStack {
            Form {
                Section("基本信息") {
                    TextField("出行名称", text: $name)
                    TextField("目的国代码（如 DE / SG）", text: $countryCode)
                        .textInputAutocapitalization(.characters)
                }
                Section("时间窗口") {
                    DatePicker("开始", selection: $start, displayedComponents: .date)
                    DatePicker("结束", selection: $end, displayedComponents: .date)
                }
                Section {
                    Text("出发地默认使用上海总部坐标（31.2304, 121.4737）。新建后可再调整。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("新建出行")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("取消") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("创建") {
                        saving = true
                        Task {
                            let ok = await onCreate(
                                name,
                                countryCode.uppercased(),
                                start,
                                end
                            )
                            saving = false
                            if ok { dismiss() }
                        }
                    }
                    .disabled(saving || name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
        }
    }
}
