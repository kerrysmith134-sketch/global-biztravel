import SwiftUI

@MainActor
final class DestinationBriefViewModel: ObservableObject {
    @Published private(set) var trips: [Trip] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let environment: AppEnvironment

    init(environment: AppEnvironment) {
        self.environment = environment
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let page = try await environment.tripRepository.listTrips(page: PageRequest(limit: 50))
            trips = page.items
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

/// Brief hub groups destination guidance by business trip, preserving the existing
/// country/city guide and its explicit data-gap disclosures.
struct DestinationBriefView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: DestinationBriefViewModel

    init(environment: AppEnvironment) {
        _viewModel = StateObject(wrappedValue: DestinationBriefViewModel(environment: environment))
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.md) {
                    intro
                    if let error = viewModel.errorMessage {
                        DSErrorState(message: error) {
                            Task { await viewModel.load() }
                        }
                    }
                    if viewModel.isLoading && viewModel.trips.isEmpty {
                        DSLoadingState(message: "正在载入目的地资料…")
                    } else if viewModel.trips.isEmpty {
                        DSEmptyState(
                            title: "暂无目的地简报",
                            message: "创建一段商务行程并填写目的国家/城市后，相关入境与当地指南会在这里集中展示。",
                            systemImage: DSIconCatalog.brief
                        )
                    } else {
                        ForEach(viewModel.trips) { trip in
                            briefCard(for: trip)
                        }
                    }
                }
                .padding(DS.Spacing.lg)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("目的地简报")
            .navigationBarTitleDisplayMode(.large)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await viewModel.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("刷新目的地简报")
                }
            }
            .task { await viewModel.load() }
            .refreshable { await viewModel.load() }
        }
    }

    private var intro: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                Label("Destination Intelligence", systemImage: DSIconCatalog.brief)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.brand)
                Text("出发前先理解市场与当地环境")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text("从目的国家与城市进入签证、入境、商务文化和当地生活指南。离线目录会显示数据缺口；实时天气、安全与交通状态仍需对应数据源验证。")
                    .font(.footnote)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func briefCard(for trip: Trip) -> some View {
        NavigationLink {
            LocalGuideView(tripId: trip.id, environment: environment)
        } label: {
            DSCard {
                VStack(alignment: .leading, spacing: DS.Spacing.md) {
                    HStack(alignment: .top) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(trip.name)
                                .font(.headline)
                                .foregroundStyle(DS.ColorToken.textPrimary)
                            Text(trip.windowLabel)
                                .font(.caption)
                                .foregroundStyle(DS.ColorToken.textSecondary)
                        }
                        Spacer()
                        Image(systemName: "arrow.up.right")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.brand)
                    }
                    let countries = trip.countries ?? []
                    if countries.isEmpty {
                        Text("尚未填写目的国家")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.warning)
                    } else {
                        HStack(spacing: 6) {
                            ForEach(countries, id: \.self) { country in
                                DSBadge(text: country, color: DS.ColorToken.brand)
                            }
                        }
                    }
                    Label("打开国家与城市指南", systemImage: "globe")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(DS.ColorToken.brand)
                }
            }
        }
        .buttonStyle(.plain)
    }
}
