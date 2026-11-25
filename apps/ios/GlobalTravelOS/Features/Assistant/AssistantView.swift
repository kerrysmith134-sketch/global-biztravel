import SwiftUI

@MainActor
final class AssistantViewModel: ObservableObject {
    @Published private(set) var conversations: [AssistantConversation] = []
    @Published var detail: AssistantConversationDetail?
    @Published private(set) var isLoading = false
    @Published private(set) var isSending = false
    @Published var draft = ""
    @Published var errorMessage: String?

    private let environment: AppEnvironment
    /// 当前行程上下文（PRD 6.1）：从行程详情进入时，新对话自动绑定该行程。
    let tripId: String?

    init(environment: AppEnvironment, tripId: String? = nil) {
        self.environment = environment
        self.tripId = tripId
    }

    func loadList() async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            conversations = try await environment.assistantRepository.conversations()
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func open(_ conversation: AssistantConversation) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            detail = try await environment.assistantRepository.detail(id: conversation.id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func startNew() async {
        errorMessage = nil
        do {
            let conversation = try await environment.assistantRepository.createConversation(tripId: tripId)
            conversations.insert(conversation, at: 0)
            detail = try await environment.assistantRepository.detail(id: conversation.id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    func runAction(action: String, subjectId: String) async {
        guard let conversation = detail?.conversation else { return }
        isSending = true
        errorMessage = nil
        defer { isSending = false }
        do {
            _ = try await environment.assistantRepository.runAction(
                conversationId: conversation.id,
                action: action,
                subjectId: subjectId
            )
            detail = try await environment.assistantRepository.detail(id: conversation.id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }


    func runTool(toolName: String) async {
        guard let conversation = detail?.conversation, let tripId = conversation.tripId else { return }
        isSending = true
        errorMessage = nil
        defer { isSending = false }

        let retryIdentity = toolRetryIdentity(
            conversationId: conversation.id,
            toolName: toolName,
            tripId: tripId
        )
        do {
            _ = try await environment.assistantRepository.runTool(
                conversationId: conversation.id,
                toolName: toolName,
                tripId: tripId,
                idempotencyKey: retryIdentity.token
            )
            // Clear only after the tool endpoint returns successfully. A lost response keeps
            // the key on disk so a manual retry or app relaunch replays the first response.
            detail = try await environment.assistantRepository.detail(id: conversation.id)
            // Keep the key until both execution and conversation refresh succeed. If the POST
            // committed but the follow-up GET fails, a retry must replay the same tool result.
            clearToolRetryIdentity(retryIdentity.storageKey)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }

    private func toolRetryIdentity(
        conversationId: String,
        toolName: String,
        tripId: String
    ) -> (storageKey: String, token: String) {
        let storageKey = "assistant.tool.retry.\(conversationId).\(toolName).\(tripId)"
        let defaults = UserDefaults.standard
        let tokenKey = "\(storageKey).token"
        let createdAtKey = "\(storageKey).created_at"
        let retentionWindow: TimeInterval = 23 * 60 * 60

        if let token = defaults.string(forKey: tokenKey),
           let createdAt = defaults.object(forKey: createdAtKey) as? Date {
            let age = Date().timeIntervalSince(createdAt)
            if age >= 0, age < retentionWindow {
                return (storageKey, token)
            }
        }

        let token = APIRequest.newIdempotencyKey()
        defaults.set(token, forKey: tokenKey)
        defaults.set(Date(), forKey: createdAtKey)
        return (storageKey, token)
    }

    private func clearToolRetryIdentity(_ storageKey: String) {
        UserDefaults.standard.removeObject(forKey: "\(storageKey).token")
        UserDefaults.standard.removeObject(forKey: "\(storageKey).created_at")
    }

    func send() async {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, let conversation = detail?.conversation else { return }
        isSending = true
        errorMessage = nil
        defer { isSending = false }
        do {
            _ = try await environment.assistantRepository.send(id: conversation.id, content: text)
            draft = ""
            detail = try await environment.assistantRepository.detail(id: conversation.id)
        } catch {
            errorMessage = environment.errorMessage(error)
        }
    }
}

/// AI 助手（PRD 页面 05 v1 客户端）：对话列表 + 消息流 + 事实卡片。
struct AssistantView: View {
    @EnvironmentObject private var environment: AppEnvironment
    @StateObject private var viewModel: AssistantViewModel
    @State private var showsResearchWorkspace = false
    @Environment(\.dismiss) private var dismiss

    init(environment: AppEnvironment, tripId: String? = nil) {
        _viewModel = StateObject(
            wrappedValue: AssistantViewModel(environment: environment, tripId: tripId)
        )
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.detail == nil {
                    conversationList
                } else {
                    chat
                }
            }
            .navigationTitle(
                viewModel.detail == nil ? "AI 助手" : (viewModel.detail?.conversation.displayTitle ?? "对话")
            )
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("关闭") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showsResearchWorkspace = true
                    } label: {
                        Image(systemName: "doc.text.magnifyingglass")
                    }
                    .accessibilityLabel("新建研究")
                }
                if viewModel.detail != nil {
                    ToolbarItem(placement: .navigationBarLeading) {
                        Button("列表") {
                            viewModel.detail = nil
                            Task { await viewModel.loadList() }
                        }
                    }
                    if let tripId = viewModel.detail?.conversation.tripId {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button {
                                Task {
                                    await viewModel.runAction(
                                        action: "generate_plan",
                                        subjectId: tripId
                                    )
                                }
                            } label: {
                                Image(systemName: "wand.and.stars")
                            }
                            .disabled(viewModel.isSending)
                            .accessibilityLabel("生成计划")
                        }
                    }
                } else {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button {
                            Task { await viewModel.startNew() }
                        } label: {
                            Image(systemName: "square.and.pencil")
                        }
                    }
                }
            }
            .task { await viewModel.loadList() }
            .sheet(isPresented: $showsResearchWorkspace) {
                ResearchWorkspaceView(
                    environment: environment,
                    tripId: viewModel.tripId ?? viewModel.detail?.conversation.tripId
                )
            }
        }
    }

    private var conversationList: some View {
        List {
            if let error = viewModel.errorMessage {
                Section { ErrorBanner(message: error) }
            }
            if viewModel.conversations.isEmpty && !viewModel.isLoading {
                ContentUnavailableView(
                    "还没有对话",
                    systemImage: "bubble.left.and.text.bubble.right",
                    description: Text("点右上角新建对话，助手会基于行程与行动中心的真实状态回答。")
                )
            }
            ForEach(viewModel.conversations) { conversation in
                Button {
                    Task { await viewModel.open(conversation) }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(conversation.displayTitle).font(.body)
                        if let updated = conversation.updatedAt {
                            Text(ISO8601.shortDisplay(from: updated))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
    }

    private var chat: some View {
        VStack(spacing: 0) {
            if let messages = viewModel.detail?.messageList {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(messages) { message in
                            MessageBubble(
                                message: message,
                                environment: environment,
                                runAction: { action, subject in
                                    await viewModel.runAction(action: action, subjectId: subject)
                                },
                                runTool: { toolName in
                                    await viewModel.runTool(toolName: toolName)
                                },
                                isSending: viewModel.isSending
                            )
                        }
                        if let error = viewModel.errorMessage {
                            ErrorBanner(message: error)
                        }
                    }
                    .padding()
                }
            } else if viewModel.isLoading {
                ProgressView("加载对话…")
            }
            Divider()
            HStack(alignment: .bottom) {
                TextField("问行程状态、待办、建议…", text: $viewModel.draft, axis: .vertical)
                    .lineLimit(1 ... 4)
                Button {
                    Task { await viewModel.send() }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(
                    viewModel.isSending
                        || viewModel.draft.trimmingCharacters(in: .whitespaces).isEmpty
                )
            }
            .padding()
        }
    }
}

private struct MessageBubble: View {
    let message: AssistantMessage
    let environment: AppEnvironment
    let runAction: (String, String) async -> Void
    let runTool: (String) async -> Void
    let isSending: Bool

    var body: some View {
        VStack(alignment: message.isFromUser ? .trailing : .leading, spacing: 4) {
            Text(message.content)
                .padding(10)
                .background(
                    message.isFromUser
                        ? Color.accentColor.opacity(0.18)
                        : Color(.secondarySystemBackground),
                    in: RoundedRectangle(cornerRadius: 12)
                )
            ForEach(message.cardList) { card in
                if let link = card.link,
                   ["trip", "trip_plan", "trip_visa"].contains(link),
                   let tripId = card.entityId {
                    // 工具结果打开其权威业务分区；普通行程卡默认进入概览。
                    NavigationLink {
                        TripDetailView(
                            tripId: tripId,
                            environment: environment,
                            initialSection: link == "trip_visa"
                                ? .visa
                                : link == "trip_plan" ? .plan : .overview
                        )
                    } label: {
                        cardFace(card)
                    }
                } else {
                    cardFace(card)
                    if card.link == "tool",
                       let toolName = card.entityId,
                       ["trip.get_plan", "visa.get_checklist", "visa.prepare_checklist"].contains(toolName) {
                        Button {
                            Task { await runTool(toolName) }
                        } label: {
                            Label(
                                toolName == "visa.get_checklist"
                                    ? "查看签证清单"
                                    : toolName == "visa.prepare_checklist"
                                        ? "创建签证清单"
                                        : "读取计划",
                                systemImage: toolName == "visa.get_checklist"
                                    ? "doc.text"
                                    : toolName == "visa.prepare_checklist"
                                        ? "doc.badge.plus"
                                        : "doc.text.magnifyingglass"
                            )
                            .font(.caption)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(isSending)
                    }
                    if card.link == "trip_proposal", let subject = card.entityId {
                        // 提案卡是待确认动作：点按才创建草稿行程（服务端二次校验 + 幂等）。
                        Button {
                            Task { await runAction("create_trip_draft", subject) }
                        } label: {
                            Label("创建出差草稿", systemImage: "airplane.departure")
                                .font(.caption)
                        }
                        .buttonStyle(.borderedProminent)
                    }
                    if card.link == "suggestion", let subject = card.entityId {
                        HStack(spacing: 8) {
                            Button {
                                Task { await runAction("approve_suggestion", subject) }
                            } label: {
                                Label("批准", systemImage: "checkmark.circle.fill").font(.caption)
                            }
                            .buttonStyle(.borderedProminent)
                            Button {
                                Task { await runAction("dismiss_suggestion", subject) }
                            } label: {
                                Label("驳回", systemImage: "hand.raised").font(.caption)
                            }
                            .buttonStyle(.bordered)
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: message.isFromUser ? .trailing : .leading)
    }

    private func cardFace(_ card: AssistantCard) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(card.title).font(.caption.weight(.semibold))
            Text(card.detail).font(.caption2).foregroundStyle(.secondary)
        }
        .padding(8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 8))
    }
}
