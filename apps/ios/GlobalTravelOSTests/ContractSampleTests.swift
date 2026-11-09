import XCTest
@testable import GlobalTravelOS

/// S6 契约漂移门禁：用 `contracts/openapi.json` 生成的样本解码 iOS 领域模型。
///
/// 与 `ModelDecodingTests` 的分工：那里用手写 JSON 验证"已知字段能解"，这里用**契约生成的样本**
/// 验证"iOS 模型与后端 schema 对得上"——后端改了字段名/类型/枚举，样本随之变化，这里立刻失败。
/// 样本由 `scripts/gen_ios_contract_samples.py` 生成（`make ios-contract-samples`）。
final class ContractSampleTests: XCTestCase {
    private func decode<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        try DateCoding.decoder().decode(T.self, from: Data(json.utf8))
    }

    func testTripContractSampleDecodes() throws {
        let value = try decode(Trip.self, ContractSamples.trip)
        XCTAssertFalse(value.id.isEmpty)
        XCTAssertFalse(value.name.isEmpty)
    }

    func testPlanVersionDetailContractSampleDecodes() throws {
        // 计划详情把停靠按天分组（stops 在 PlanDay 里，不在版本顶层）。
        let value = try decode(PlanVersionDetail.self, ContractSamples.planVersionDetail)
        XCTAssertFalse(value.id.isEmpty)
    }

    func testVisitBriefingContractSampleDecodes() throws {
        _ = try decode(VisitBriefing.self, ContractSamples.visitBriefing)
    }

    func testBudgetSummaryContractSampleDecodes() throws {
        _ = try decode(BudgetSummary.self, ContractSamples.budgetSummary)
    }

    func testActionCenterContractSampleDecodes() throws {
        let value = try decode(ActionCenter.self, ContractSamples.actionCenter)
        let counted = (value.counts ?? [:]).values.reduce(0, +)
        XCTAssertEqual(value.total, counted)
    }

    func testNotificationFeedContractSampleDecodes() throws {
        _ = try decode(NotificationFeed.self, ContractSamples.notificationFeed)
    }

    func testCrmSyncJobContractSampleDecodes() throws {
        let value = try decode(CrmSyncJob.self, ContractSamples.crmSyncJob)
        XCTAssertFalse(value.id.isEmpty)
    }

    func testTripMemoryContractSampleDecodes() throws {
        _ = try decode(TripMemory.self, ContractSamples.tripMemory)
    }

    func testMinutesDraftContractSampleDecodes() throws {
        _ = try decode(MinutesDraft.self, ContractSamples.minutesDraft)
    }

    func testTripOutcomeContractSampleDecodes() throws {
        _ = try decode(TripOutcome.self, ContractSamples.tripOutcome)
    }

    func testLocalGuideContractSampleDecodes() throws {
        _ = try decode(LocalGuide.self, ContractSamples.localGuide)
    }

    func testAssistantConversationDetailContractSampleDecodes() throws {
        _ = try decode(AssistantConversationDetail.self, ContractSamples.assistantConversationDetail)
    }

    func testSuggestionFeedContractSampleDecodes() throws {
        _ = try decode(SuggestionFeed.self, ContractSamples.suggestionFeed)
    }
}
