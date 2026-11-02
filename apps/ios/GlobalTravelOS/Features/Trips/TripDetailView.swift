import SwiftUI

@MainActor
final class TripDetailViewModel: ObservableObject {
    @Published private(set) var trip: Trip?
    @Published private(set) var visits: [TripVisit] = []
    @Published private(set) var plans: [PlanVersion] = []
    @Published private(set) var visaCases: [VisaCase] = []
    @Published private(set) var entryAssessments: [EntryAssessment] = []
    @Published private(set) var expenses: [TripExpense] = []
    @Published private(set) var budget: BudgetSummary?
    @Published private(set) var expenseReports: [ExpenseReport] = []
    @Published private(set) var isLoading = false
    @Published private(set) var generating = false
    @Published var errorMessage: String?
    @Published var statusMessage: String?

    let tripId: String
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
            let loaded = try await environment.tripRepository.trip(id: tripId)
            trip = loaded
            environment.rememberTrip(tripId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
        visits = (try? await environment.visitRepository.listVisits(tripId: tripId)) ?? visits
        plans = (try? await environment.planRepository.listPlans(
            tripId: tripId,
            page: PageRequest(limit: 50, cursor: nil)
        ).items) ?? plans
        visaCases = (try? await environment.tripRepository.visaCases(tripId: tripId)) ?? visaCases
        entryAssessments = (try? await environment.tripRepository.entryAssessments(tripId: tripId))
            ?? entryAssessments
        expenses = (try? await environment.tripRepository.expenses(tripId: tripId)) ?? expenses
        do {
            // 预算失败要显式暴露：try? 静默吞掉会让"预算区永远不出现"无法诊断。
            budget = try await environment.tripRepository.budgetSummary(
                tripId: tripId,
                baseCurrency: trip?.currency
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
        expenseReports = (try? await environment.tripRepository.expenseReports(tripId: tripId))
            ?? expenseReports
        environment.refreshOfflineSummary()
    }

    func addExpense(
        category: TripExpenseCategory,
        amountText: String,
        currency: String,
        occurredOn: Date,
        note: String
    ) async -> Bool {
        let normalized = amountText.replacingOccurrences(of: ",", with: ".")
        guard let amount = Decimal(string: normalized), amount > 0 else {
            errorMessage = "费用金额必须是大于 0 的数字"
            return false
        }
        let body = TripExpenseCreateRequest(
            category: category,
            amount: amount,
            currency: currency.uppercased(),
            occurredOn: ISO8601.dayString(from: occurredOn),
            note: note.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        do {
            _ = try await environment.tripRepository.createExpense(tripId: tripId, body: body)
            await load()
            return true
        } catch {
            errorMessage = environment.errorMessage(error)
            return false
        }
    }

    func createExpenseReport() async {
        errorMessage = nil
        do {
            let report = try await environment.tripRepository.createExpenseReport(tripId: tripId)
            expenseReports.insert(report, at: 0)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func submitExpenseReport(_ report: ExpenseReport) async {
        errorMessage = nil
        do {
            _ = try await environment.tripRepository.submitExpenseReport(id: report.id)
            expenseReports = try await environment.tripRepository.expenseReports(tripId: tripId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func createVisaCases() async {
        errorMessage = nil
        do {
            visaCases = try await environment.tripRepository.createVisaCases(
                tripId: tripId,
                countries: nil
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func refreshAssessments() async {
        errorMessage = nil
        do {
            entryAssessments = try await environment.tripRepository.refreshEntryAssessments(
                tripId: tripId
            )
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func advanceVisaCase(_ visaCase: VisaCase, to status: VisaCaseStatus) async {
        errorMessage = nil
        do {
            _ = try await environment.tripRepository.updateVisaCase(id: visaCase.id, status: status)
            visaCases = try await environment.tripRepository.visaCases(tripId: tripId)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func hold() async {
        await transition { try await environment.tripRepository.holdTrip(id: tripId, version: $0) }
    }

    func resume() async {
        await transition { try await environment.tripRepository.resumeTrip(id: tripId, version: $0) }
    }

    func complete() async {
        await transition { try await environment.tripRepository.completeTrip(id: tripId, version: $0) }
    }

    private func transition(
        _ action: (Int) async throws -> Trip
    ) async {
        guard let current = trip else { return }
        errorMessage = nil
        do {
            _ = try await action(current.version)
            statusMessage = "状态已更新"
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func generatePlan() async {
        generating = true
        errorMessage = nil
        statusMessage = "正在排队求解…"
        defer { generating = false }
        do {
            let outcome = try await environment.generatePlanUseCase.generate(
                tripId: tripId,
                onProgress: { job in
                    Task { @MainActor in
                        self.statusMessage = "求解中 · \(job.statusLabel) · 尝试 \(job.attempts ?? 0) 次"
                    }
                }
            )
            statusMessage = outcome.message
            await load()
        } catch {
            errorMessage = environment.errorMessage(error)
            statusMessage = nil
        }
    }
}

struct TripDetailView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TripDetailViewModel
    @State private var selectedVisit: TripVisit?
    @State private var briefingVisit: TripVisit?
    @State private var showsImport = false
    @State private var showsExpense = false
    @State private var showsAssistant = false

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: TripDetailViewModel(tripId: tripId, environment: environment)
        )
    }

    var body: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if let status = viewModel.statusMessage {
                Section {
                    Label(status, systemImage: "gearshape.2")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }

            if let trip = viewModel.trip {
                Section("概览") {
                    LabeledContent("状态", value: trip.statusLabel)
                    LabeledContent("时间窗口", value: trip.windowLabel)
                    LabeledContent("出发地", value: trip.originLabel)
                    LabeledContent("版本", value: "v\(trip.version)")
                }
            }

            if let trip = viewModel.trip {
                Section("出行状态") {
                    let status = (trip.status ?? "").uppercased()
                    if ["DRAFT", "PLANNING", "READY", "ACTIVE"].contains(status) {
                        Button(role: .destructive) {
                            Task { await viewModel.hold() }
                        } label: {
                            Label("暂停出行", systemImage: "pause.circle")
                        }
                    }
                    if status == "HOLD" {
                        Button {
                            Task { await viewModel.resume() }
                        } label: {
                            Label("恢复出行", systemImage: "play.circle")
                        }
                    }
                    if status != "COMPLETED" && status != "CANCELLED" {
                        Button {
                            Task { await viewModel.complete() }
                        } label: {
                            Label("标记完成", systemImage: "checkmark.circle")
                        }
                    }
                }
            }

            Section("签证与入境") {
                if viewModel.visaCases.isEmpty {
                    Text("还没有签证案件。创建后会按目的国生成材料清单。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.visaCases) { item in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(item.countryCode).font(.headline)
                            Text(item.status.label)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            Spacer()
                            Text(item.checklistLabel)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                            if !item.status.nextStatuses.isEmpty {
                                Menu("推进") {
                                    ForEach(item.status.nextStatuses) { next in
                                        Button(next.label) {
                                            Task { await viewModel.advanceVisaCase(item, to: next) }
                                        }
                                    }
                                }
                                .font(.caption)
                            }
                        }
                        if let notes = item.notes, !notes.isEmpty {
                            Text(notes).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                HStack {
                    Button {
                        Task { await viewModel.createVisaCases() }
                    } label: {
                        Label("创建签证案件", systemImage: "doc.badge.plus")
                    }
                    Spacer()
                    Button {
                        Task { await viewModel.refreshAssessments() }
                    } label: {
                        Label("刷新入境评估", systemImage: "arrow.clockwise")
                    }
                }
                ForEach(viewModel.entryAssessments) { assessment in
                    VStack(alignment: .leading, spacing: 3) {
                        Text("\(assessment.countryCode) · \(assessment.sourceLabel)")
                            .font(.caption.weight(.semibold))
                        Text(assessment.summary).font(.caption)
                        Text("\(assessment.confidenceLabel) · 需人工复核")
                            .font(.caption2)
                            .foregroundStyle(.orange)
                    }
                }
            }

            if let budget = viewModel.budget {
                Section("费用与预算") {
                    LabeledContent("预算", value: budget.budgetLabel)
                    LabeledContent("计划估算", value: budget.plannedLabel)
                    LabeledContent("实际费用", value: budget.actualLabel)
                    if let converted = budget.convertedActualLabel {
                        LabeledContent("折算实际", value: converted)
                    }
                    LabeledContent("预算差额", value: budget.varianceLabel)
                    if let note = budget.conversionNote {
                        Text(note)
                            .font(.caption2)
                            .foregroundStyle(budget.conversionComplete == true ? Color.secondary : Color.orange)
                    }
                    ForEach(viewModel.expenses) { expense in
                        HStack {
                            Text(expense.categoryLabel)
                            Spacer()
                            Text(expense.amountLabel)
                                .font(.caption.monospacedDigit())
                        }
                    }
                    Button {
                        showsExpense = true
                    } label: {
                        Label("记一笔费用", systemImage: "plus.circle")
                    }
                }
            }

            Section("报销单") {
                if viewModel.expenseReports.isEmpty {
                    Text("还没有报销单。可从当前费用记录生成草稿。")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.expenseReports) { report in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(report.status.label) · \(report.itemList.count) 项")
                                .font(.subheadline)
                            Text(report.totalsLabel)
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if report.canSubmit {
                            Button("提交") {
                                Task { await viewModel.submitExpenseReport(report) }
                            }
                            .font(.caption)
                        }
                    }
                }
                Button {
                    Task { await viewModel.createExpenseReport() }
                } label: {
                    Label("从费用记录生成报销单", systemImage: "doc.text")
                }
            }

            Section("客户导入") {
                Button {
                    showsImport = true
                } label: {
                    Label("导入 Excel 客户名单", systemImage: "doc.badge.plus")
                }
            }

            Section("拜访") {
                if viewModel.visits.isEmpty {
                    Text("还没有拜访。导入客户或新增拜访后再生成计划。")
                        .foregroundStyle(.secondary)
                }
                ForEach(viewModel.visits) { visit in
                    HStack {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(visit.displayName).font(.body)
                            Text("\(visit.statusLabel) · \(visit.timeWindowLabel)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("简报") { briefingVisit = visit }
                            .buttonStyle(.bordered)
                        Button("记录") { selectedVisit = visit }
                            .buttonStyle(.bordered)
                    }
                }
            }

            Section("计划版本") {
                if viewModel.plans.isEmpty {
                    Text("还没有计划版本。").foregroundStyle(.secondary)
                }
                ForEach(viewModel.plans) { plan in
                    NavigationLink {
                        PlanDetailView(
                            tripId: viewModel.tripId,
                            planId: plan.id,
                            tripVersion: viewModel.trip?.version ?? 1,
                            environment: environment
                        )
                    } label: {
                        VStack(alignment: .leading, spacing: 3) {
                            HStack {
                                Text(plan.versionLabel).font(.headline)
                                Spacer()
                                Text(plan.status?.label ?? "草稿")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Text("\(plan.feasibilityLabel) · \(plan.stopCountLabel)")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Button {
                    Task { await viewModel.generatePlan() }
                } label: {
                    Label(
                        viewModel.generating ? "求解中…" : "生成新计划",
                        systemImage: "wand.and.stars"
                    )
                }
                .disabled(viewModel.generating)
            }

            Section("当地与记忆") {
                NavigationLink {
                    TripOutcomeView(tripId: viewModel.tripId, environment: environment)
                } label: {
                    Label("结果复盘", systemImage: "chart.bar.doc.horizontal")
                }
                NavigationLink {
                    LocalGuideView(tripId: viewModel.tripId, environment: environment)
                } label: {
                    Label("当地生活指南", systemImage: "map")
                }
                NavigationLink {
                    TripMemoryView(tripId: viewModel.tripId, environment: environment)
                } label: {
                    Label("查看复盘记忆", systemImage: "brain.head.profile")
                }
            }
        }
        .navigationTitle(viewModel.trip?.name ?? "出行详情")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    showsAssistant = true
                } label: {
                    Image(systemName: "bubble.left.and.text.bubble.right")
                }
                .accessibilityLabel("问助手")
            }
        }
        .refreshable { await viewModel.load() }
        .task { await viewModel.load() }
        .sheet(item: $selectedVisit) { visit in
            VisitRecordSheet(visit: visit)
        }
        .sheet(item: $briefingVisit) { visit in
            VisitBriefingView(visitId: visit.id, environment: environment)
        }
        .sheet(isPresented: $showsImport) {
            ImportSheet(tripId: viewModel.tripId) {
                await viewModel.load()
            }
        }
        .sheet(isPresented: $showsAssistant) {
            AssistantView(environment: environment, tripId: viewModel.tripId)
        }
        .sheet(isPresented: $showsExpense) {
            ExpenseEntrySheet(
                defaultCurrency: viewModel.trip?.currency ?? viewModel.budget?.budgetCurrency ?? "EUR"
            ) { category, amount, currency, date, note in
                await viewModel.addExpense(
                    category: category,
                    amountText: amount,
                    currency: currency,
                    occurredOn: date,
                    note: note
                )
            }
        }
    }
}
