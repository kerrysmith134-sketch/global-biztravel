import Foundation

protocol AssistantRepository {
    func conversations() async throws -> [AssistantConversation]
    func createConversation(tripId: String?) async throws -> AssistantConversation
    func detail(id: String) async throws -> AssistantConversationDetail
    func send(id: String, content: String) async throws -> AssistantMessage
    func deleteConversation(id: String) async throws
    func runAction(conversationId: String, action: String, subjectId: String) async throws -> AssistantMessage
    func runTool(conversationId: String, toolName: String, arguments: AssistantToolExecuteArguments) async throws -> AssistantMessage
}

/// 助手对话是会话式数据：直接读写服务端，不做离线回退（发送失败原样抛出，可重试）。
final class RemoteAssistantRepository: AssistantRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func conversations() async throws -> [AssistantConversation] {
        try await client.send(Endpoints.listAssistantConversations(), as: [AssistantConversation].self)
    }

    func createConversation(tripId: String?) async throws -> AssistantConversation {
        var body: [String: String] = [:]
        body["title"] = ""
        if let tripId { body["trip_id"] = tripId }
        return try await client.send(
            try Endpoints.createAssistantConversation(body: body),
            as: AssistantConversation.self
        )
    }

    func detail(id: String) async throws -> AssistantConversationDetail {
        try await client.send(Endpoints.assistantConversation(id: id), as: AssistantConversationDetail.self)
    }

    func send(id: String, content: String) async throws -> AssistantMessage {
        try await client.send(
            try Endpoints.postAssistantMessage(id: id, content: content),
            as: AssistantMessage.self
        )
    }

    func deleteConversation(id: String) async throws {
        try await client.sendWithoutResponse(Endpoints.deleteAssistantConversation(id: id))
    }

    func runAction(conversationId: String, action: String, subjectId: String) async throws -> AssistantMessage {
        try await client.send(
            try Endpoints.assistantAction(
                conversationId: conversationId,
                action: action,
                subjectId: subjectId
            ),
            as: AssistantMessage.self
        )
    }

    func runTool(
        conversationId: String,
        toolName: String,
        arguments: AssistantToolExecuteArguments
    ) async throws -> AssistantMessage {
        try await client.send(
            try Endpoints.assistantTool(
                conversationId: conversationId,
                toolName: toolName,
                arguments: arguments
            ),
            as: AssistantMessage.self
        )
    }
}
