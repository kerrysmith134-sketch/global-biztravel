import Foundation

struct AssistantConversation: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String?
    var title: String?
    var createdAt: Date?
    var updatedAt: Date?

    var displayTitle: String {
        let value = title?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return value.isEmpty ? "新对话" : value
    }
}

struct AssistantCard: Codable, Equatable, Identifiable {
    var type: String
    var title: String
    var detail: String
    var entityId: String?
    var link: String?
    var toolName: String?
    var toolArguments: AssistantToolExecuteArguments?

    var id: String { "\(type):\(entityId ?? title)" }
}

struct AssistantMessage: Codable, Equatable, Identifiable {
    var id: String
    var role: String
    var content: String
    var cards: [AssistantCard]?
    var llmInvocationId: String?
    var createdAt: Date?

    var cardList: [AssistantCard] { cards ?? [] }
    var isFromUser: Bool { role.uppercased() == "USER" }
}

struct AssistantConversationDetail: Codable, Equatable {
    var conversation: AssistantConversation
    var messages: [AssistantMessage]?
    var messageList: [AssistantMessage] { messages ?? [] }
}

struct AssistantMessageCreate: Codable, Equatable {
    var content: String
}


struct AssistantToolExecuteArguments: Codable, Equatable {
    var tripId: String? = nil
    var visitId: String? = nil
    var tone: String? = nil

    enum CodingKeys: String, CodingKey {
        case tripId = "trip_id"
        case visitId = "visit_id"
        case tone
    }
}

struct AssistantToolExecuteCreate: Codable, Equatable {
    var toolName: String
    var arguments: AssistantToolExecuteArguments

    enum CodingKeys: String, CodingKey {
        case toolName = "tool_name"
        case arguments
    }
}
