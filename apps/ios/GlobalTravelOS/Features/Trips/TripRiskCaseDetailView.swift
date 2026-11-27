import SwiftUI

@MainActor
final class TripRiskCaseDetailViewModel: ObservableObject {
    @Published private(set) var riskCase: TripRiskCase
    @Published private(set) var events: [TripRiskCaseEvent] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isCaseFresh = false
    @Published private(set) var isTransitioning = false
    @Published private(set) var hasEarlierEvents = false
    @Published var errorMessage: String?

    private let environment: AppEnvironment
    private var refreshGeneration = 0

    init(riskCase: TripRiskCase, environment: AppEnvironment) {
        self.riskCase = riskCase
        self.environment = environment
    }

    func refresh() async {
        guard !isTransitioning else { return }
        refreshGeneration += 1
        let generation = refreshGeneration
        isLoading = true
        isCaseFresh = false
        errorMessage = nil
        defer {
            if generation == refreshGeneration {
                isLoading = false
            }
        }
        do {
            let page = try await environment.tripRiskRepository.cases(
                tripId: riskCase.tripId,
                status: nil,
                limit: 200
            )
            guard generation == refreshGeneration else { return }
            guard let latest = page.items.first(where: { $0.id == riskCase.id }) else {
                events = []
                hasEarlierEvents = false
                errorMessage = "该风险事项已不在当前行程的服务端记录中，请返回工作台重新加载"
                return
            }
            riskCase = latest
            isCaseFresh = true
            do {
                let loadedEvents = try await environment.tripRiskRepository.events(
                    tripId: riskCase.tripId,
                    caseId: riskCase.id,
                    limit: 200
                )
                guard generation == refreshGeneration else { return }
                let orderedEvents = TripRiskCaseEvent.orderedByCaseVersion(loadedEvents)
                events = Array(orderedEvents.suffix(200))
                hasEarlierEvents = orderedEvents.count == 200
                    && (orderedEvents.first?.caseVersion ?? 1) > 1
            } catch {
                guard generation == refreshGeneration else { return }
                events = []
                hasEarlierEvents = false
                errorMessage = environment.errorMessage(error)
            }
        } catch {
            guard generation == refreshGeneration else { return }
            events = []
            hasEarlierEvents = false
            errorMessage = environment.errorMessage(error)
        }
    }

    func transition(_ request: TripRiskTransitionRequest) async throws -> TripRiskCase {
        guard isCaseFresh, !isTransitioning else {
            throw RepositoryError.requiresNetwork("风险状态尚未成功刷新，或另一项处置正在提交")
        }
        isTransitioning = true
        defer { isTransitioning = false }
        let outcome = try await environment.tripRiskRepository.transition(
            tripId: riskCase.tripId,
            caseId: riskCase.id,
            request: request
        )
        riskCase = outcome.caseItem
        isCaseFresh = true
        let orderedEvents = TripRiskCaseEvent.orderedByCaseVersion(events + [outcome.event])
        if orderedEvents.count > 200 {
            hasEarlierEvents = true
            events = Array(orderedEvents.suffix(200))
        } else {
            events = orderedEvents
        }
        return outcome.caseItem
    }
}

struct TripRiskCaseDetailView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: TripRiskCaseDetailViewModel
    @State private var transitionTarget: TripRiskTransitionTarget?

    let onChanged: (TripRiskCase) -> Void

    init(
        riskCase: TripRiskCase,
        environment: AppEnvironment,
        onChanged: @escaping (TripRiskCase) -> Void
    ) {
        _viewModel = StateObject(
            wrappedValue: TripRiskCaseDetailViewModel(riskCase: riskCase, environment: environment)
        )
        self.onChanged = onChanged
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                        HStack {
                            DSBadge(text: viewModel.riskCase.severityLabel + "风险", color: severityColor)
                            DSBadge(text: viewModel.riskCase.statusLabel, color: DS.ColorToken.brand)
                        }
                        Text(viewModel.riskCase.title)
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text(viewModel.riskCase.summary)
                            .font(.subheadline)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                        Divider()
                        LabeledContent("风险类型", value: viewModel.riskCase.riskTypeLabel)
                        LabeledContent("首次发现", value: ISO8601.shortDisplay(from: viewModel.riskCase.firstSeenAt))
                        LabeledContent("最近观察", value: ISO8601.shortDisplay(from: viewModel.riskCase.lastObservedAt))
                        LabeledContent("下次复核", value: viewModel.riskCase.nextReviewAt.map { ISO8601.shortDisplay(from: $0) } ?? "未安排")
                        LabeledContent("当前版本", value: "v\(viewModel.riskCase.version)")
                    }
                    .padding(.vertical, DS.Spacing.xs)
                } header: {
                    Text("风险概况")
                }

                Section("来源记录") {
                    if viewModel.riskCase.sourceDescriptions.isEmpty {
                        Text("服务端未返回可展示的来源引用")
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    } else {
                        ForEach(Array(viewModel.riskCase.sourceDescriptions.enumerated()), id: \.offset) { item in
                            Label(item.element, systemImage: "arrow.up.right.square")
                                .font(.subheadline)
                        }
                    }
                }

                Section("证据引用") {
                    if viewModel.riskCase.evidenceDescriptions.isEmpty {
                        Text("暂无结构化证据引用")
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    } else {
                        ForEach(Array(viewModel.riskCase.evidenceDescriptions.enumerated()), id: \.offset) { item in
                            Label(item.element, systemImage: "doc.text.magnifyingglass")
                                .font(.subheadline)
                        }
                    }
                }

                if !viewModel.isCaseFresh {
                    Section {
                        Label(
                            "当前风险状态未成功刷新，暂不允许提交处置",
                            systemImage: "exclamationmark.arrow.trianglehead.2.clockwise.rotate.90"
                        )
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.warning)
                    }
                }

                if !viewModel.riskCase.isTerminal && viewModel.isCaseFresh {
                    Section {
                        ForEach(viewModel.riskCase.availableTransitions) { target in
                            Button {
                                transitionTarget = target
                            } label: {
                                HStack {
                                    Text(target.label)
                                    Spacer()
                                    Image(systemName: "chevron.right")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(DS.ColorToken.textSecondary)
                                }
                            }
                            .foregroundStyle(target == .dismissed ? DS.ColorToken.danger : DS.ColorToken.brand)
                        }
                    } header: {
                        Text("人工处置")
                    } footer: {
                        Text("处置动作采用乐观并发版本校验。关闭风险需要提供依据，服务端会再次核验来源状态。")
                    }
                }

                Section {
                    if viewModel.hasEarlierEvents {
                        Label(
                            "当前仅展示最近 200 条事件，较早记录未加载",
                            systemImage: "clock.arrow.circlepath"
                        )
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.warning)
                    }
                    if let error = viewModel.errorMessage {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(DS.ColorToken.danger)
                    }
                    if viewModel.events.isEmpty && !viewModel.isLoading {
                        Text("暂无状态事件")
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                    ForEach(viewModel.events) { event in
                        TripRiskEventRow(event: event)
                    }
                    if viewModel.isLoading {
                        ProgressView()
                    }
                } header: {
                    Text("审计时间线")
                } footer: {
                    Text("事件由服务端追加保存，版本号单调递增；此时间线用于追溯风险状态变化。")
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("风险详情")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await viewModel.refresh() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .accessibilityLabel("刷新事件")
                    .disabled(viewModel.isLoading || viewModel.isTransitioning)
                }
            }
            .task { await viewModel.refresh() }
            .sheet(item: $transitionTarget) { target in
                TripRiskTransitionSheet(
                    riskCase: viewModel.riskCase,
                    target: target
                ) { request in
                    let updated = try await viewModel.transition(request)
                    onChanged(updated)
                }
            }
        }
    }

    private var severityColor: Color {
        switch viewModel.riskCase.severity.uppercased() {
        case "CRITICAL": return DS.ColorToken.danger
        case "HIGH": return DS.ColorToken.warning
        case "MEDIUM": return DS.ColorToken.info
        default: return DS.ColorToken.textSecondary
        }
    }
}

private struct TripRiskEventRow: View {
    let event: TripRiskCaseEvent

    var body: some View {
        HStack(alignment: .top, spacing: DS.Spacing.md) {
            Circle()
                .fill(DS.ColorToken.brand)
                .frame(width: 8, height: 8)
                .padding(.top, 6)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(event.eventLabel)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(DS.ColorToken.textPrimary)
                    Spacer()
                    Text("v\(event.caseVersion)")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                if let note = event.note, !note.isEmpty {
                    Text(note)
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                Text("\(event.actorLabel) · \(ISO8601.shortDisplay(from: event.createdAt))")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
        .padding(.vertical, DS.Spacing.xs)
        .accessibilityElement(children: .combine)
    }
}
