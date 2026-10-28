import SwiftUI

@MainActor
final class TripMemoryViewModel: ObservableObject {
    @Published private(set) var memory: TripMemory?
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
            memory = try await environment.memoryRepository.tripMemory(tripId: tripId)
            environment.refreshOfflineSummary()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct TripMemoryView: View {
    @StateObject private var viewModel: TripMemoryViewModel

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: TripMemoryViewModel(tripId: tripId, environment: environment)
        )
    }

    var body: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if let memory = viewModel.memory {
                if memory.isEmpty {
                    Text("这次出行还没有复盘记忆。行程完成并由 Worker 复盘后会出现。")
                        .foregroundStyle(.secondary)
                }
                ForEach(memory.summaryList) { summary in
                    Section(summary.kindLabel) {
                        Text(summary.summaryText)
                        if !summary.decisionList.isEmpty {
                            Subtopic(title: "决策", items: summary.decisionList)
                        }
                        if !summary.followUpList.isEmpty {
                            Subtopic(title: "待办", items: summary.followUpList)
                        }
                        if !summary.lessonList.isEmpty {
                            Subtopic(title: "教训", items: summary.lessonList)
                        }
                        Text("\(summary.confidenceLabel) · \(summary.validFromLabel)")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                if !memory.procedureList.isEmpty {
                    Section("过程记录") {
                        ForEach(memory.procedureByRecency) { entry in
                            VStack(alignment: .leading, spacing: 2) {
                                HStack {
                                    Text(entry.outcomeLabel).font(.subheadline)
                                    if entry.isFailureRecord {
                                        Image(systemName: "exclamationmark.triangle.fill")
                                            .foregroundStyle(.orange)
                                    }
                                }
                                Text(entry.reasonLabel)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text(entry.createdAtLabel)
                                    .font(.caption2)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                }
            } else if viewModel.isLoading {
                ProgressView("加载记忆…")
            }
        }
        .navigationTitle("行程记忆")
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
    }
}

private struct Subtopic: View {
    let title: String
    let items: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            ForEach(items, id: \.self) { item in
                Text("• \(item)").font(.footnote)
            }
        }
    }
}
