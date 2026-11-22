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

    // ---- 第二批：只验证"契约样本可解码"，字段级断言交给各模型的独立测试 ----

    func testPlanVersionContractSampleDecodes() throws {
        _ = try decode(PlanVersion.self, ContractSamples.planVersion)
    }

    func testCustomerContractSampleDecodes() throws {
        _ = try decode(Customer.self, ContractSamples.customer)
    }

    func testPlaceContractSampleDecodes() throws {
        _ = try decode(Place.self, ContractSamples.place)
    }

    func testVisaCaseContractSampleDecodes() throws {
        _ = try decode(VisaCase.self, ContractSamples.visaCase)
    }

    func testEntryAssessmentContractSampleDecodes() throws {
        _ = try decode(EntryAssessment.self, ContractSamples.entryAssessment)
    }

    func testTripExpenseContractSampleDecodes() throws {
        _ = try decode(TripExpense.self, ContractSamples.tripExpense)
    }

    func testExpenseReportContractSampleDecodes() throws {
        _ = try decode(ExpenseReport.self, ContractSamples.expenseReport)
    }

    func testImportParseResultContractSampleDecodes() throws {
        _ = try decode(ImportParseResult.self, ContractSamples.importParseResult)
    }

    func testSyncSummaryContractSampleDecodes() throws {
        _ = try decode(SyncSummary.self, ContractSamples.syncSummary)
    }

    func testVisitRecordContractSampleDecodes() throws {
        _ = try decode(VisitRecord.self, ContractSamples.visitRecord)
    }

    func testTravelerProfileContractSampleDecodes() throws {
        _ = try decode(TravelerProfile.self, ContractSamples.travelerProfile)
    }

    func testAppNotificationContractSampleDecodes() throws {
        _ = try decode(AppNotification.self, ContractSamples.appNotification)
    }

    func testNotificationPreferenceContractSampleDecodes() throws {
        _ = try decode(NotificationPreferenceRead.self, ContractSamples.notificationPreferenceRead)
    }

    func testMarketEvidenceIntelligenceContractSampleDecodes() throws {
        let value = try decode(
            MarketEvidenceIntelligenceReport.self,
            ContractSamples.marketEvidenceIntelligenceReport
        )
        XCTAssertEqual(value.totalEvidenceCount, 1)
        XCTAssertEqual(value.items.first?.originLabel, "内容重复")
    }

    func testMarketSourceVerificationPlanContractSampleDecodes() throws {
        let value = try decode(
            MarketSourceVerificationPlan.self,
            ContractSamples.marketSourceVerificationPlan
        )
        XCTAssertEqual(value.workspaceId, "11111111-1111-1111-1111-111111111111")
        XCTAssertEqual(value.actions.first?.priority, "HIGH")
    }

    func testMarketSourceVerificationPlanApplicationContractSampleDecodes() throws {
        let value = try decode(
            MarketSourceVerificationPlanApplication.self,
            ContractSamples.marketSourceVerificationPlanApplication
        )
        XCTAssertFalse(value.planDigest.isEmpty)
        XCTAssertEqual(value.tasks.first?.questionStatus, "OPEN")
    }

    func testJobResultContractSampleDecodes() throws {
        _ = try decode(JobResult.self, ContractSamples.jobResult)
    }

    func testPlanDiffContractSampleDecodes() throws {
        let value = try decode(PlanDiff.self, ContractSamples.planDiff)
        XCTAssertFalse(value.basePlanVersionId.isEmpty)
        XCTAssertFalse(value.targetPlanVersionId.isEmpty)
    }
}
