import SwiftUI

@MainActor
final class LocalGuideViewModel: ObservableObject {
    @Published private(set) var guide: LocalGuide?
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let tripId: String
    private let environment: AppEnvironment

    init(tripId: String, environment: AppEnvironment) {
        self.tripId = tripId
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            guide = try await environment.tripRepository.localGuide(tripId: tripId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

/// 当地生活与文化指南（PRD 方向五）：离线目录按出行国家与拜访城市组装。
struct LocalGuideView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: LocalGuideViewModel
    @Environment(\.dismiss) private var dismiss

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: LocalGuideViewModel(tripId: tripId, environment: environment)
        )
    }

    var body: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if let guide = viewModel.guide {
                ForEach(guide.countryList) { country in
                    Section("\(country.name)（\(country.countryCode)）") {
                        ForEach(country.sectionList) { section in
                            VStack(alignment: .leading, spacing: 4) {
                                Text(section.title)
                                    .font(.subheadline.weight(.semibold))
                                ForEach(section.itemList, id: \.self) { item in
                                    Text("• " + item)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                        ForEach(country.cityList) { city in
                            VStack(alignment: .leading, spacing: 4) {
                                Text("\(city.city) 贴士")
                                    .font(.subheadline.weight(.semibold))
                                ForEach(city.tipList, id: \.self) { tip in
                                    Text("• " + tip)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .padding(.vertical, 2)
                        }
                    }
                }
                if !guide.gapList.isEmpty {
                    Section("数据缺口") {
                        ForEach(guide.gapList) { gap in
                            Label(gap.message, systemImage: "exclamationmark.circle")
                                .font(.footnote)
                                .foregroundStyle(.orange)
                        }
                    }
                }
            } else if viewModel.isLoading {
                ProgressView("加载当地指南…")
            }
        }
        .navigationTitle("当地指南")
        .refreshable { await viewModel.load() }
        .task { await viewModel.load() }
    }
}
