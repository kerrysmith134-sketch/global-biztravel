import Foundation

/// LLM 直连服务（OpenAI-compatible /chat/completions，ADR-0036）。
/// Key 由设置页填写（只存本机）；未配置或调用失败走内置离线模板降级，
/// 功能不断——与 StubProvider 的离线实现同语义。
struct LLMService {
    var baseUrl: String
    var apiKey: String
    var model: String
    var temperature: Double = 0.0
    var maxTokens: Int = 1024

    struct Unavailable: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    // MARK: - 配置读取

    static func fromSettings(_ settings: AppSettings) -> LLMService? {
        guard let baseUrl = settings.llmBaseUrl, !baseUrl.isEmpty,
              let apiKey = settings.llmApiKey, !apiKey.isEmpty else { return nil }
        return LLMService(
            baseUrl: baseUrl.trimmingCharacters(in: .whitespaces),
            apiKey: apiKey,
            model: (settings.llmModel ?? "").isEmpty ? "gpt-4o-mini" : settings.llmModel!
        )
    }

    // MARK: - 调用

    /// 发起一次 JSON 模式调用；失败抛 Unavailable（调用方降级）。
    static func completeJson(
        service: LLMService,
        systemPrompt: String,
        userPayload: [String: Any]
    ) throws -> [String: Any] {
        guard let url = URL(string: service.baseUrl + "/chat/completions") else {
            throw Unavailable(message: "LLM Base URL 无效")
        }
        var request = URLRequest(url: url, timeoutInterval: 60)
        request.httpMethod = "POST"
        request.setValue("Bearer \(service.apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = [
            "model": service.model,
            "messages": [
                ["role": "system", "content": systemPrompt],
                ["role": "user", "content": userPayload],
            ],
            "temperature": service.temperature,
            "max_tokens": service.maxTokens,
            "response_format": ["type": "json_object"],
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try synchronouslyFetch(request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw Unavailable(
                message: "LLM 请求失败：HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1)"
            )
        }
        guard let envelope = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = envelope["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any],
              let content = message["content"] as? String else {
            throw Unavailable(message: "LLM 响应缺少 choices/message/content")
        }
        return try parseJsonContent(content)
    }

    /// 剥 ```json fence 后解析。
    static func parseJsonContent(_ content: String) throws -> [String: Any] {
        var text = content.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.hasPrefix("```") {
            text = text
                .replacingOccurrences(of: "```json", with: "")
                .replacingOccurrences(of: "```", with: "")
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        guard let object = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any] else {
            throw Unavailable(message: "LLM 输出不是 JSON 对象")
        }
        return object
    }

    private static func synchronouslyFetch(_ request: URLRequest) throws -> (Data, URLResponse?) {
        var result: (Data, URLResponse?)?
        var thrown: Error?
        let semaphore = DispatchSemaphore(value: 0)
        URLSession.shared.dataTask(with: request) { data, response, error in
            if let error { thrown = error } else { result = (data ?? Data(), response) }
            semaphore.signal()
        }.resume()
        semaphore.wait()
        if let thrown { throw Unavailable(message: "网络失败：\(thrown.localizedDescription)") }
        return result ?? (Data(), nil)
    }
}

// MARK: - 三 prompt 模板（原文平移自 app/llm/prompts.py）

enum LLMPrompts {
    /// meeting_minutes v2（服务实际使用版本）。
    static let meetingMinutesSystem = """
    你是企业差旅助手。根据现场拜访记录起草会议纪要，只输出 JSON。\
    summary 用 2-4 句客观描述，decisions 只写客户当场确认的结论（没有就留空数组），\
    follow_up_tasks 每条都必须有 title；insights 数组按 kind 分类（SUMMARY/CUSTOMER_NEED/\
    OPPORTUNITY_SIGNAL/DEAL_TERM/COMMITMENT/STAGE_CHANGE），非 SUMMARY 类必须标注 \
    requires_confirmation=true；不要编造未在输入中出现的事实。
    """

    /// trip_intake v2。
    static let tripIntakeSystem = """
    你是企业差旅助手的行程意图解析器。判断用户消息是否包含创建出差的请求，只输出 JSON。\
    has_trip_request 为是否包含创建出差的明确意图；提取 name/countries（ISO 两位码数组）\
    /days/start_date/end_date，缺什么不编造什么。today 供相对日期解析参考。
    """

    /// assistant_reply v1。
    static let assistantReplySystem = """
    你是企业差旅助手。根据消息与已知事实卡片用中文简短回答（2-4 句），只输出 JSON \
    {"reply": "..."}。不要编造事实；不知道就说建议查看对应页面。
    """
}

// MARK: - 离线降级（StubProvider 的确定性实现直译）

enum LLMDegrade {
    /// trip_creation_intent 关键词门（先于 LLM 使用）。
    static func tripCreationIntent(_ text: String) -> Bool {
        if text.contains("出差") { return true }
        let createVerbs = ["创建", "新建", "规划", "安排", "计划"]
        if text.contains("行程") && createVerbs.contains(where: text.contains) { return true }
        let lower = text.lowercased()
        if lower.contains("trip") && (lower.contains("create") || lower.contains("plan") || lower.contains("book")) {
            return true
        }
        return false
    }

    /// 纪要模板兜底（服务端 _fallback 同语义）。
    static func fallbackMinutes(notes: String, followUps: [String]) -> [String: Any] {
        let summary = notes.isEmpty ? "现场记录缺失，等待补充。" : String(notes.prefix(120))
        let tasks = followUps.isEmpty
            ? [["title": "补充跟进计划", "due_date": NSNull()]]
            : followUps.map { ["title": $0, "due_date": NSNull()] }
        return [
            "title": "会议纪要（离线模板）",
            "summary": summary,
            "decisions": [],
            "follow_up_tasks": tasks,
            "insights": [["kind": "SUMMARY", "text": summary, "requires_confirmation": false]],
        ]
    }

    /// 助手固定话术。
    static func fallbackAssistantReply() -> [String: Any] {
        ["reply": "助手暂不可用；请到行动中心或行程页查看权威状态。"]
    }

    /// trip_intake 离线解析（不做日期解析，只识别意图与国名）。
    static func fallbackTripIntake(_ text: String) -> [String: Any] {
        guard tripCreationIntent(text) else {
            return ["has_trip_request": false]
        }
        var countries: [String] = []
        let dictionary: [String: String] = [
            "德国": "DE", "法国": "FR", "荷兰": "NL", "英国": "GB", "美国": "US",
            "新加坡": "SG", "泰国": "TH", "日本": "JP", "阿联酋": "AE", "巴西": "BR",
        ]
        for (name, code) in dictionary where text.contains(name) {
            countries.append(code)
        }
        return ["has_trip_request": true, "countries": countries]
    }
}
