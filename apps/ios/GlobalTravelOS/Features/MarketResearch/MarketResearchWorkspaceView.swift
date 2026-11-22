import SwiftUI

@MainActor
final class MarketResearchWorkspaceListViewModel: ObservableObject {
    @Published private(set) var workspaces: [MarketWorkspace] = []
    @Published private(set) var isLoading = false
    @Published private(set) var isCreating = false
    @Published var errorMessage: String?

    let repository: ResearchRepository

    init(repository: ResearchRepository) {
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            let page = try await repository.listWorkspaces(page: PageRequest(limit: 50))
            workspaces = page.items
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func create(name: String, objective: String, context: String) async -> Bool {
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleanName.isEmpty else {
            errorMessage = "请填写工作空间名称"
            return false
        }
        isCreating = true
        defer { isCreating = false }
        do {
            let workspace = try await repository.createWorkspace(
                MarketWorkspaceCreateRequest(
                    name: cleanName,
                    objective: objective.nilIfBlank,
                    initialContext: context.nilIfBlank
                )
            )
            workspaces.insert(workspace, at: 0)
            errorMessage = nil
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }
}

struct MarketResearchWorkspaceView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: MarketResearchWorkspaceListViewModel
    @State private var showsCreate = false

    init(environment: AppEnvironment) {
        _viewModel = StateObject(
            wrappedValue: MarketResearchWorkspaceListViewModel(
                repository: environment.researchRepository
            )
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: DS.Spacing.md) {
                    intro
                    if let error = viewModel.errorMessage {
                        DSErrorState(message: error)
                    }
                    if viewModel.workspaces.isEmpty && !viewModel.isLoading {
                        emptyState
                    } else {
                        ForEach(viewModel.workspaces) { workspace in
                            NavigationLink {
                                MarketResearchWorkspaceDetailView(
                                    workspace: workspace,
                                    repository: repository
                                )
                            } label: {
                                MarketResearchWorkspaceCard(workspace: workspace)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
                .padding(DS.Spacing.lg)
            }
            .background(DS.ColorToken.canvas.ignoresSafeArea())
            .navigationTitle("研究工作区")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsCreate = true
                    } label: {
                        Image(systemName: "plus")
                    }
                    .accessibilityLabel("新建研究工作区")
                }
            }
            .refreshable { await viewModel.load() }
            .task { await viewModel.load() }
            .sheet(isPresented: $showsCreate) {
                MarketResearchWorkspaceCreateSheet { name, objective, context in
                    await viewModel.create(name: name, objective: objective, context: context)
                }
            }
        }
    }

    private var repository: ResearchRepository {
        // The view model and detail screens share the same app-owned repository.
        viewModel.repository
    }

    private var intro: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(spacing: DS.Spacing.sm) {
                    Image(systemName: "point.3.connected.trianglepath.dotted")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("从问题开始，逐步建立证据")
                            .font(DS.Typography.sectionTitle)
                            .foregroundStyle(DS.ColorToken.textPrimary)
                        Text("每个研究问题有自己的来源范围和审核状态。")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                    }
                }
                HStack(spacing: DS.Spacing.sm) {
                    DSDataQualityTag(kind: .estimate, text: "搜索来源需核验")
                    DSDataQualityTag(kind: .live, text: "答案需人工采纳")
                }
            }
        }
    }

    private var emptyState: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                Image(systemName: "square.stack.3d.up")
                    .font(.title2)
                    .foregroundStyle(DS.ColorToken.brand)
                Text("还没有研究工作区")
                    .font(DS.Typography.sectionTitle)
                    .foregroundStyle(DS.ColorToken.textPrimary)
                Text("创建一个工作区，整理研究目标和问题，再让系统按依赖关系推进证据与答案。")
                    .font(.subheadline)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                DSButton(title: "创建工作区", systemImage: "plus") {
                showsCreate = true
            }
            }
        }
    }
}

private struct MarketResearchWorkspaceCard: View {
    let workspace: MarketWorkspace

    var body: some View {
        DSCard {
            VStack(alignment: .leading, spacing: DS.Spacing.sm) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(workspace.name)
                            .font(.headline)
                            .foregroundStyle(DS.ColorToken.textPrimary)
                            .lineLimit(2)
                        Text(workspace.objective ?? "尚未补充研究目标")
                            .font(.caption)
                            .foregroundStyle(DS.ColorToken.textSecondary)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 8)
                    DSBadge(
                        text: workspace.statusLabel,
                        color: DS.ColorToken.status(workspace.status)
                    )
                }
                HStack(spacing: DS.Spacing.md) {
                    Label(
                        workspace.currentContextVersion.map { "上下文 v\($0)" } ?? "未采纳上下文",
                        systemImage: "doc.text"
                    )
                    .font(.caption2)
                    .foregroundStyle(DS.ColorToken.textSecondary)
                    Spacer()
                    Image(systemName: "arrow.up.right")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(DS.ColorToken.brand)
                }
            }
        }
    }
}
