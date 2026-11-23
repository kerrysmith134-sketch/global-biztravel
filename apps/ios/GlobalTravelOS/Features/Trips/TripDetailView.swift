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

    /// 分域加载错误（审计七·额外可靠性）：空区块必须能区分"确实没有"与"加载失败"。
    @Published private(set) var sectionErrors: [String: String] = [:]

    private func capture<T>(_ work: () async throws -> T) async -> Result<T, Error> {
        do { return .success(try await work()) } catch { return .failure(error) }
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        sectionErrors = [:]
        defer { isLoading = false }

        // 并行加载 + 逐域收集错误（此前串行 8 个请求且 try? 静默吞错）。
        async let tripResult = capture { try await environment.tripRepository.trip(id: tripId) }
        async let visitsResult = capture {
            try await environment.visitRepository.listVisits(tripId: tripId)
        }
        async let plansResult = capture {
            try await environment.planRepository.listPlans(
                tripId: tripId, page: PageRequest(limit: 50, cursor: nil)
            ).items
        }
        async let visaResult = capture {
            try await environment.tripRepository.visaCases(tripId: tripId)
        }
        async let assessmentsResult = capture {
            try await environment.tripRepository.entryAssessments(tripId: tripId)
        }
        async let expensesResult = capture {
            try await environment.tripRepository.expenses(tripId: tripId)
        }
        async let reportsResult = capture {
            try await environment.tripRepository.expenseReports(tripId: tripId)
        }

        switch await tripResult {
        case .success(let loaded):
            trip = loaded
            environment.rememberTrip(tripId)
        case .failure(let error):
            errorMessage = environment.errorMessage(error)
        }
        switch await visitsResult {
        case .success(let value): visits = value
        case .failure(let error): sectionErrors["visits"] = environment.errorMessage(error)
        }
        switch await plansResult {
        case .success(let value): plans = value
        case .failure(let error): sectionErrors["plans"] = environment.errorMessage(error)
        }
        switch await visaResult {
        case .success(let value): visaCases = value
        case .failure(let error): sectionErrors["visa"] = environment.errorMessage(error)
        }
        switch await assessmentsResult {
        case .success(let value): entryAssessments = value
        case .failure(let error): sectionErrors["visa"] = environment.errorMessage(error)
        }
        switch await expensesResult {
        case .success(let value): expenses = value
        case .failure(let error): sectionErrors["expenses"] = environment.errorMessage(error)
        }
        switch await reportsResult {
        case .success(let value): expenseReports = value
        case .failure(let error): sectionErrors["reports"] = environment.errorMessage(error)
        }
        // 预算单独加载（依赖 trip.currency，本就在 trip 之后执行）。
        do {
            budget = try await environment.tripRepository.budgetSummary(
                tripId: tripId,
                baseCurrency: trip?.currency
            )
        } catch {
            sectionErrors["budget"] = environment.errorMessage(error)
        }
        environment.refreshOfflineSummary()
        if !environment.isLocalMode, let receiptVault = environment.receiptVault {
            Task {
                await receiptVault.retryPendingUploads(tripId: tripId)
            }
        }
    }

    /// 某区块的加载错误（nil 表示该域加载成功或无错误）。
    func sectionError(_ key: String) -> String? {
        sectionErrors[key]
    }

    func storeReceiptDraft(_ draft: ReceiptDraft) throws -> LocalReceiptAsset {
        guard let receiptVault = environment.receiptVault else {
            throw ReceiptVaultError.originalFileUnavailable
        }
        return try receiptVault.capture(tripId: tripId, draft: draft)
    }

    func addExpense(
        category: TripExpenseCategory,
        amountText: String,
        currency: String,
        occurredOn: Date,
        note: String,
        localReceiptAssetId: String?
    ) async -> Bool {
        let normalized = amountText.replacingOccurrences(of: ",", with: ".")
        guard let amount = Decimal(string: normalized), amount > 0 else {
            errorMessage = "费用金额必须是大于 0 的数字"
            return false
        }

        var receiptAssetId: String?
        if let localReceiptAssetId {
            guard let receiptVault = environment.receiptVault else {
                errorMessage = "本地票据仓库不可用，费用未保存"
                return false
            }
            do {
                let asset = try await receiptVault.prepareForExpense(assetId: localReceiptAssetId)
                receiptAssetId = asset.expenseReceiptAssetId
            } catch {
                errorMessage = "\(environment.errorMessage(error))。原始文件已保留在本机；联网后可重试。"
                return false
            }
        }

        let body = TripExpenseCreateRequest(
            category: category,
            amount: amount,
            currency: currency.uppercased(),
            occurredOn: ISO8601.dayString(from: occurredOn),
            note: note.trimmingCharacters(in: .whitespacesAndNewlines),
            receiptAssetId: receiptAssetId
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

/// 行程详情分区（审计 5.2：515 行超长聚合页 → 5 个工作任务分区）。
enum TripSection: String, CaseIterable, Identifiable {
    case overview, schedule, plan, money, visa

    var id: String { rawValue }

    var title: String {
        switch self {
        case .overview: return "概览"
        case .schedule: return "日程"
        case .plan: return "计划"
        case .money: return "费用"
        case .visa: return "签证"
        }
    }
}

struct TripDetailView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: TripDetailViewModel
    @State private var selectedVisit: TripVisit?
    @State private var briefingVisit: TripVisit?
    @State private var showsImport = false
    @State private var showsAddVisit = false
    @State private var showsExpense = false
    @State private var showsAssistant = false
    @State private var section: TripSection = .overview

    init(tripId: String, environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: TripDetailViewModel(tripId: tripId, environment: environment)
        )
    }

    var body: some View {
        List {
            Section {
                Picker("分区", selection: $section) {
                    ForEach(TripSection.allCases) { item in
                        Text(item.title).tag(item)
                    }
                }
                .pickerStyle(.segmented)
            }
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
            if section == .overview {
                    Section("概览") {
                        LabeledContent("状态", value: trip.statusLabel)
                        LabeledContent("时间窗口", value: trip.windowLabel)
                        LabeledContent("出发地", value: trip.originLabel)
                        LabeledContent("版本", value: "v\(trip.version)")
                    }
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

                if section == .overview {
                    Section("客户导入") {
                        Button {
                            showsImport = true
                        } label: {
                            Label("导入 Excel 客户名单", systemImage: "doc.badge.plus")
                        }
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
            }
            if section == .schedule {
                Section("拜访") {
                    if let error = viewModel.sectionError("visits") {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
                    Button {
                        showsAddVisit = true
                    } label: {
                        Label("新增拜访（新建客户地点）", systemImage: "person.badge.plus")
                    }
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

            }
            if section == .plan {
                Section("计划版本") {
                    if let error = viewModel.sectionError("plans") {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
                    if viewModel.plans.isEmpty {
                        Text("还没有计划版本。").foregroundStyle(.secondary)
                    }
                    ForEach(Array(viewModel.plans.enumerated()), id: \.element.id) { index, plan in
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
                        // AT-008：有更早版本时给出「与上一版比较」入口（列表最新在前）。
                        if index + 1 < viewModel.plans.count {
                            NavigationLink {
                                PlanDiffView(
                                    tripId: viewModel.tripId,
                                    planId: plan.id,
                                    against: viewModel.plans[index + 1].id,
                                    environment: environment
                                )
                            } label: {
                                Label("与上一版比较", systemImage: "arrow.left.arrow.right")
                                    .font(.caption)
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

            }
            if section == .money {
                if let budget = viewModel.budget {
                    Section("费用与预算") {
                    if let error = viewModel.sectionError("expenses") ?? viewModel.sectionError("budget") {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
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
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text(expense.categoryLabel)
                                    Spacer()
                                    Text(expense.amountLabel)
                                        .font(.caption.monospacedDigit())
                                }
                                if expense.receiptAssetId != nil {
                                    Label(
                                        environment.isLocalMode ? "原件保存在本机" : "原件已上传",
                                        systemImage: "paperclip"
                                    )
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                } else {
                                    Label("未附原始票据", systemImage: "paperclip")
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
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
                    if let error = viewModel.sectionError("reports") {
                        Text(error).font(.caption).foregroundStyle(.red)
                    }
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

            }
            if section == .visa {
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
        .sheet(isPresented: $showsAddVisit) {
            VisitCreateSheet(tripId: viewModel.tripId, environment: environment) {
                await viewModel.load()
            }
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
            ) { category, amount, currency, date, note, receiptDraft in
                await viewModel.addExpense(
                    category: category,
                    amountText: amount,
                    currency: currency,
                    occurredOn: date,
                    note: note,
                    receiptDraft: receiptDraft
                )
            }
        }
    }
}
