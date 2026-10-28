import SwiftUI

@MainActor
final class VisitBriefingViewModel: ObservableObject {
    @Published private(set) var briefing: VisitBriefing?
    @Published private(set) var isLoading = false
    @Published private(set) var saving = false
    @Published var objective = ""
    @Published var errorMessage: String?
    @Published var savedMessage: String?

    private let visitId: String
    private let environment: AppEnvironment

    init(visitId: String, environment: AppEnvironment) {
        self.visitId = visitId
        self.environment = environment
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let loaded = try await environment.visitRepository.briefing(visitId: visitId)
            briefing = loaded
            objective = loaded.objective ?? ""
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func saveObjective() async {
        saving = true
        errorMessage = nil
        savedMessage = nil
        defer { saving = false }
        do {
            let trimmed = objective.trimmingCharacters(in: .whitespacesAndNewlines)
            let visit = try await environment.visitRepository.updateObjective(
                visitId: visitId,
                objective: trimmed.isEmpty ? nil : trimmed
            )
            if var current = briefing {
                current.visit = visit
                briefing = current
            }
            objective = visit.objective ?? ""
            savedMessage = "拜访目标已保存"
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

struct VisitBriefingView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: VisitBriefingViewModel
    @Environment(\.dismiss) private var dismiss

    init(visitId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: VisitBriefingViewModel(visitId: visitId, environment: environment)
        )
    }

    var body: some View {
        NavigationStack {
            Form {
                if let error = viewModel.errorMessage {
                    Section { ErrorBanner(message: error) }
                }

                Section {
                    TextField("例如：确认续约条款并拿到采购时间表", text: $viewModel.objective, axis: .vertical)
                        .lineLimit(2 ... 5)
                    Button {
                        Task { await viewModel.saveObjective() }
                    } label: {
                        Label(viewModel.saving ? "保存中…" : "保存拜访目标", systemImage: "target")
                    }
                    .disabled(viewModel.saving)
                    if let saved = viewModel.savedMessage {
                        Text(saved).font(.caption).foregroundStyle(.green)
                    }
                } header: {
                    Text("本次拜访目标")
                } footer: {
                    Text("目标会随简报展示；空值表示尚未设定。")
                }

                if let customer = viewModel.briefing?.customer {
                    Section("客户") {
                        LabeledContent("名称", value: customer.name)
                        LabeledContent("等级", value: customer.tierLabel)
                        if let industry = customer.industry, !industry.isEmpty {
                            LabeledContent("行业", value: industry)
                        }
                        LabeledContent("状态", value: customer.status ?? "—")
                        if let contact = customer.contactLine {
                            LabeledContent("联系人", value: contact)
                        }
                        if let externalRef = customer.externalRef, !externalRef.isEmpty {
                            LabeledContent("CRM 编号", value: externalRef)
                        }
                        if let notes = customer.notes, !notes.isEmpty {
                            Text(notes).font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }

                if let place = viewModel.briefing?.place {
                    Section("地点") {
                        LabeledContent("地点", value: place.name)
                        if !place.displayRegion.isEmpty {
                            LabeledContent("城市", value: place.displayRegion)
                        }
                        if let address = place.addressLine, !address.isEmpty {
                            LabeledContent("地址", value: address)
                        }
                        if let timezone = place.timezone, !timezone.isEmpty {
                            LabeledContent("时区", value: timezone)
                        }
                    }
                }

                if let briefing = viewModel.briefing {
                    Section("历史拜访") {
                        if briefing.historyList.isEmpty {
                            Text("暂无同客户的历史拜访记录。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(briefing.historyList) { item in
                            VStack(alignment: .leading, spacing: 3) {
                                HStack {
                                    Text(item.title ?? item.tripName).font(.subheadline.weight(.semibold))
                                    Spacer()
                                    Text(item.outcomeLabel).font(.caption).foregroundStyle(.secondary)
                                }
                                Text("\(item.tripName) · \(item.dateLabel)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                if let notes = item.notes, !notes.isEmpty {
                                    Text(notes).font(.footnote)
                                }
                            }
                        }
                    }

                    Section("历史跟进项") {
                        if briefing.followUpList.isEmpty {
                            Text("没有从历史记录中提取到跟进项。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(briefing.followUpList) { item in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.title).font(.subheadline)
                                Text("\(item.tripName) · \(item.displayText)")
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }

                    Section("复盘记忆") {
                        if briefing.memoryList.isEmpty {
                            Text("还没有该客户的复盘记忆。")
                                .font(.footnote)
                                .foregroundStyle(.secondary)
                        }
                        ForEach(briefing.memoryList) { memory in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(memory.summary).font(.subheadline)
                                ForEach(memory.decisions ?? [], id: \.self) { decision in
                                    Text("决策 · \(decision)").font(.caption).foregroundStyle(.secondary)
                                }
                                ForEach(memory.followUps ?? [], id: \.self) { followUp in
                                    Text("跟进 · \(followUp)").font(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                    }

                    if !briefing.gapList.isEmpty {
                        Section("数据缺口") {
                            ForEach(briefing.gapList) { gap in
                                Label(gap.message, systemImage: "exclamationmark.circle")
                                    .font(.footnote)
                                    .foregroundStyle(.orange)
                            }
                        }
                    }
                } else if viewModel.isLoading {
                    ProgressView("加载简报…")
                }
            }
            .navigationTitle("拜访简报")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        Task { await viewModel.load() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .disabled(viewModel.isLoading)
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
        }
    }
}
