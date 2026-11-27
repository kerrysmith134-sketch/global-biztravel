import SwiftUI

@MainActor
final class TripRiskWorkspaceViewModel: ObservableObject {
    @Published private(set) var cases: [TripRiskCase] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isReconciling = false
    @Published var errorMessage: String?
    @Published var statusMessage: String?

    private let tripId: String
    private let environment: AppEnvironment
    private var loadGeneration = 0

    init(tripId: String, environment: AppEnvironment) {
        self.tripId = tripId
        self.environment = environment
    }

    func load() async {
        loadGeneration += 1
        let generation = loadGeneration
        isLoading = true
        errorMessage = nil
        defer {
            if generation == loadGeneration {
                isLoading = false
            }
        }
        do {
            let page = try await environment.tripRiskRepository.cases(
                tripId: tripId,
                status: nil,
                limit: 200
            )
            guard generation == loadGeneration else { return }
            cases = page.items
        } catch {
            guard generation == loadGeneration else { return }
            cases = []
            errorMessage = environment.errorMessage(error)
        }
    }

    func reconcile() async {
        guard !isReconciling else { return }
        isReconciling = true
        statusMessage = nil
        errorMessage = nil
        defer { isReconciling = false }
        do {
            let report = try await environment.tripRiskRepository.reconcile(tripId: tripId)
            statusMessage = report.errors.isEmpty
                ? "来源核对完成 · " + report.summary
                : "来源核对完成，但有 \(report.errors.count) 项未能处理"
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func replace(_ updated: TripRiskCase) {
        guard let index = cases.firstIndex(where: { $0.id == updated.id }) else {
            cases.insert(updated, at: 0)
            return
        }
        cases[index] = updated
    }
}

private enum TripRiskFilter: String, CaseIterable, Identifiable {
    case active = "待处理"
    case all = "全部"
    case closed = "已结束"

    var id: String { rawValue }
}

struct TripRiskWorkspaceView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TripRiskWorkspaceViewModel
    @State private var filter: TripRiskFilter = .active
    @State private var selectedCase: TripRiskCase?

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: TripRiskWorkspaceViewModel(tripId: tripId, environment: environment)
        )
    }

    private var activeCases: [TripRiskCase] {
        viewModel.cases.filter { !$0.isTerminal }
    }

    private var criticalCases: Int {
        activeCases.filter { ["CRITICAL", "HIGH"].contains($0.severity.uppercased()) }.count
    }

    private var visibleCases: [TripRiskCase] {
        switch filter {
        case .active: return activeCases
        case .all: return viewModel.cases
        case .closed: return viewModel.cases.filter(\.isTerminal)
        }
    }

    var body: some View {
        List {
            Section {
                HStack(alignment: .center, spacing: DS.Spacing.lg) {
                    metric("待处理", value: activeCases.count, tint: DS.ColorToken.textPrimary)
                    metric("高风险", value: criticalCases, tint: criticalCases > 0 ? DS.ColorToken.danger : DS.ColorToken.textSecondary)
                    metric("已结束", value: viewModel.cases.filter(\.isTerminal).count, tint: DS.ColorToken.success)
                }
                .padding(.vertical, DS.Spacing.xs)
            }

            Section {
                Picker("风险筛选", selection: $filter) {
                    ForEach(TripRiskFilter.allCases) { item in
                        Text(item.rawValue).tag(item)
                    }
                }
                .pickerStyle(.segmented)
            }

            if let message = viewModel.statusMessage {
                Section {
                    Label(message, systemImage: "checkmark.circle")
                        .font(.footnote)
                        .foregroundStyle(DS.ColorToken.success)
                }
            }

            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }

            Section {
                if visibleCases.isEmpty {
                    ContentUnavailableView(
                        filter == .closed ? "暂无已结束事项" : "暂无风险事项",
                        systemImage: "checkmark.shield",
                        description: Text(
                            filter == .closed
                                ? "风险关闭后会保留在这里，供后续追溯。"
                                : "来源核对后，航班、预约、拜访跟进等风险会汇总到这里。"
                        )
                    )
                } else {
                    ForEach(visibleCases) { riskCase in
                        Button {
                            selectedCase = riskCase
                        } label: {
                            TripRiskCaseRow(riskCase: riskCase)
                        }
                        .buttonStyle(.plain)
                    }
                }
            } header: {
                HStack {
                    Text("风险与恢复事项")
                    Spacer()
                    Text("\(visibleCases.count)")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
            } footer: {
                Text("状态以服务端风险聚合记录为准。这里的人工操作只记录处置状态，不会自动联系客户、改签或修改行程计划。")
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("风险与恢复")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await viewModel.reconcile() }
                } label: {
                    if viewModel.isReconciling {
                        ProgressView()
                    } else {
                        Label("核对来源", systemImage: "arrow.triangle.2.circlepath")
                    }
                }
                .disabled(viewModel.isReconciling || viewModel.isLoading)
                .accessibilityHint("从主动建议与人工跟进记录刷新行程风险")
            }
        }
        .refreshable { await viewModel.load() }
        .task { await viewModel.load() }
        .sheet(item: $selectedCase) { riskCase in
            TripRiskCaseDetailView(
                riskCase: riskCase,
                environment: environment
            ) { updated in
                viewModel.replace(updated)
            }
        }
    }

    private func metric(_ title: String, value: Int, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value, format: .number)
                .font(.title2.weight(.semibold).monospacedDigit())
                .foregroundStyle(tint)
            Text(title)
                .font(.caption)
                .foregroundStyle(DS.ColorToken.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}

private struct TripRiskCaseRow: View {
    let riskCase: TripRiskCase

    private var severityColor: Color {
        switch riskCase.severity.uppercased() {
        case "CRITICAL": return DS.ColorToken.danger
        case "HIGH": return DS.ColorToken.warning
        case "MEDIUM": return DS.ColorToken.info
        default: return DS.ColorToken.textSecondary
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: DS.Spacing.sm) {
            HStack(alignment: .top, spacing: DS.Spacing.sm) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(severityColor)
                    .frame(width: 3, height: 42)
                VStack(alignment: .leading, spacing: 4) {
                    Text(riskCase.title)
                        .font(.headline)
                        .foregroundStyle(DS.ColorToken.textPrimary)
                        .multilineTextAlignment(.leading)
                    Text("\(riskCase.riskTypeLabel) · \(riskCase.severityLabel)风险")
                        .font(.caption)
                        .foregroundStyle(DS.ColorToken.textSecondary)
                }
                Spacer(minLength: 4)
                DSBadge(text: riskCase.statusLabel, color: severityColor)
            }

            if !riskCase.summary.isEmpty {
                Text(riskCase.summary)
                    .font(.subheadline)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
            }

            HStack(spacing: DS.Spacing.sm) {
                Label(riskCase.nextReviewLabel, systemImage: "clock")
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                Spacer()
                Text("v\(riskCase.version)")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(DS.ColorToken.textSecondary)
            }
        }
        .padding(.vertical, DS.Spacing.xs)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}
