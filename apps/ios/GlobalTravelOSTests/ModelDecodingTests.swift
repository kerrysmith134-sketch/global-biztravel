import XCTest
@testable import GlobalTravelOS

final class ModelDecodingTests: XCTestCase {
    func testTripDecodesBackendFlatShape() throws {
        let json = """
        {
          "id": "t1",
          "name": "2026 Q4 欧洲多国商务出行",
          "purpose": "客户拜访",
          "status": "ACTIVE",
          "version": 3,
          "window_start": "2026-11-08T16:00:00+00:00",
          "window_end": "2026-11-15T16:00:00+00:00",
          "origin_place_id": "p1",
          "active_plan_version_id": null,
          "visit_count": 4,
          "created_at": "2026-10-28T10:00:00+00:00",
          "updated_at": "2026-10-28T10:00:00+00:00"
        }
        """
        let trip = try DateCoding.decode(Trip.self, fromJSON: json)
        XCTAssertEqual(trip.status, "ACTIVE")
        XCTAssertEqual(trip.version, 3)
        XCTAssertEqual(trip.stats?.visitCount, 4)
        XCTAssertNotNil(trip.window)
        XCTAssertEqual(trip.originLabel, "出发地已设置")
        XCTAssertEqual(trip.statusLabel, "进行中")
    }

    func testPlanDetailGroupsFlatStopsIntoDays() throws {
        let json = """
        {
          "id": "p1",
          "trip_id": "t1",
          "version_number": 2,
          "status": "DRAFT",
          "solver": "heuristic",
          "degraded": false,
          "objective_value": 12.5,
          "solution_status": "FEASIBLE",
          "cost_total": 120.5,
          "cost_currency": "EUR",
          "cost_unknown_count": 1,
          "validation": {"feasible": true, "checks": []},
          "unplaced": [],
          "created_at": "2026-11-01T10:00:00+00:00",
          "stops": [
            {"visit_id": "v1", "day_index": 0, "day_date": "2026-11-03", "sequence": 1,
             "arrive_at": "2026-11-03T09:00:00+01:00", "depart_at": "2026-11-03T10:00:00+01:00",
             "travel_minutes": 30, "service_minutes": 60, "distance_km": 12.5,
             "cost_amount": 20.0, "cost_currency": "EUR",
             "travel_source": "stub-provider", "stale": false,
             "place_id": "p1", "place_name": "Berlin HQ", "city": "Berlin",
             "country_code": "DE", "latitude": 52.52, "longitude": 13.405},
            {"visit_id": "v2", "day_index": 0, "day_date": "2026-11-03", "sequence": 2,
             "arrive_at": "2026-11-03T11:00:00+01:00", "depart_at": "2026-11-03T12:00:00+01:00",
             "travel_minutes": 15, "service_minutes": 45},
            {"visit_id": "v3", "day_index": 1, "day_date": "2026-11-04", "sequence": 1,
             "arrive_at": "2026-11-04T09:00:00+01:00", "depart_at": "2026-11-04T10:00:00+01:00",
             "travel_minutes": 40, "service_minutes": 60}
          ],
          "legs": [
            {"id": "l1", "from_stop_id": null, "to_stop_id": "s1", "day_index": 0,
             "mode": "AIR", "travel_minutes": 840, "distance_km": 8000.0,
             "cost_amount": 500.0, "cost_currency": "EUR",
             "source": "stub-provider", "stale": false}
          ]
        }
        """
        let plan = try DateCoding.decode(PlanVersionDetail.self, fromJSON: json)
        XCTAssertEqual(plan.feasible, true)
        XCTAssertEqual(plan.dayList.count, 2)
        XCTAssertEqual(plan.dayList[0].date, "2026-11-03")
        XCTAssertEqual(plan.dayList[0].stops.map(\.visitId), ["v1", "v2"])
        XCTAssertEqual(plan.displayStopCount, 3)
        XCTAssertNotNil(plan.dayList[0].stops[0].arrive)
        XCTAssertEqual(plan.solutionStatus, "FEASIBLE")
        XCTAssertEqual(plan.costTotal, 120.5)
        XCTAssertEqual(plan.costUnknownCount, 1)
        XCTAssertEqual(plan.legs?.count, 1)
        XCTAssertEqual(plan.legs?.first?.mode, "AIR")
        XCTAssertEqual(plan.dayList[0].stops[0].placeName, "Berlin HQ")
        XCTAssertEqual(plan.dayList[0].stops[0].costAmount, 20.0)
    }

    func testProactiveFeedDecodes() throws {
        let json = """
        {
          "items": [
            {
              "id": "s1",
              "trip_id": "t1",
              "suggestion_type": "VISA_REQUIREMENT_REMINDER",
              "status": "PENDING",
              "title": "出发前签证与证件核验",
              "rationale": "出行将于约 48 小时后开始。",
              "evidence": [{"kind": "TRIP_WINDOW", "ref": "trip:t1@v1"}],
              "proposed_action": {"kind": "PREPARE_CHECKLIST", "tool": "visa.prepare_checklist"},
              "confidence": 0.85,
              "requires_approval": true,
              "severity": "HIGH",
              "dedupe_key": "visa_check:2026-11-03",
              "created_at": "2026-10-28T10:00:00+00:00"
            }
          ],
          "daily_cap": {"used": 1, "cap": 3},
          "next_cursor": null
        }
        """
        let feed = try DateCoding.decode(SuggestionFeed.self, fromJSON: json)
        XCTAssertEqual(feed.items.count, 1)
        let suggestion = try XCTUnwrap(feed.items.first)
        XCTAssertTrue(suggestion.isActionable)
        XCTAssertTrue(suggestion.needsHumanApproval)
        XCTAssertEqual(suggestion.evidenceList.count, 1)
        XCTAssertEqual(feed.dailyCap?.used, 1)
        XCTAssertEqual(feed.cap.remaining, 2)
    }

    func testSyncPushResultDecodesPerItemVerdicts() throws {
        let json = """
        {
          "results": [
            {"item_id": "r1", "entity": "visit_record", "status": "applied", "server_version": 1},
            {"item_id": "r2", "entity": "plan_stop", "status": "rejected",
             "conflict": {"reason": "PLAN_IMMUTABLE", "server": null, "client": {"message": "immutable"}}}
          ],
          "summary": {"applied": 1, "conflict": 0, "rejected": 1}
        }
        """
        let result = try DateCoding.decode(SyncPushResult.self, fromJSON: json)
        XCTAssertEqual(result.effectiveSummary.appliedCount, 1)
        XCTAssertEqual(result.effectiveSummary.rejectedCount, 1)
        XCTAssertEqual(result.rejected.count, 1)
        XCTAssertEqual(result.rejected.first?.conflict?.reason, "PLAN_IMMUTABLE")
    }

    func testTripMemoryDecodes() throws {
        let json = """
        {
          "trip_id": "t1",
          "summaries": [{
            "id": "m1", "kind": "TRIP_REVIEW", "summary": "完成 1 个拜访",
            "decisions": ["激活计划 v1"], "follow_ups": ["提交续约方案"],
            "lessons": ["规划被否决（TIME_WINDOW_INFEASIBLE）"],
            "valid_from": "2026-10-28T10:00:00+00:00", "confidence": 0.85
          }],
          "procedure": [{
            "attempt_id": "a1", "outcome": "PLAN_REJECTED",
            "reason": "时间窗冲突", "created_at": "2026-10-28T09:00:00+00:00"
          }]
        }
        """
        let memory = try DateCoding.decode(TripMemory.self, fromJSON: json)
        XCTAssertEqual(memory.summaryList.count, 1)
        XCTAssertEqual(memory.followUpCount, 1)
        XCTAssertEqual(memory.failedAttempts.count, 1)
    }

    func testCrmSyncJobDecodesPrepared() throws {
        let json = """
        {
          "id": "c1", "trip_id": "t1", "visit_id": "v1", "visit_record_id": "r1",
          "status": "PREPARED", "job_id": "j1",
          "result": {"external_id": "file:var/crm/org/r1.json", "detail": "待导入"},
          "error": null,
          "created_at": "2026-10-28T10:00:00+00:00",
          "updated_at": "2026-10-28T10:01:00+00:00"
        }
        """
        let job = try DateCoding.decode(CrmSyncJob.self, fromJSON: json)
        XCTAssertEqual(job.status, .prepared)
        XCTAssertEqual(job.status.label, "已生成待导入文件")
        XCTAssertEqual(job.externalId, "file:var/crm/org/r1.json")
        XCTAssertTrue(job.status.isTerminal)
    }

    func testVisitBriefingDecodesHistoryAndGaps() throws {
        let json = """
        {
          "visit": {
            "id": "v1", "trip_id": "t1", "place_id": "p1", "title": "续约沟通",
            "objective": "确认续约条款", "service_minutes": 90,
            "priority": 2, "business_value": "0", "movement_mode": "GROUND",
            "is_immovable": false, "status": "PLANNED"
          },
          "customer": {
            "id": "c1", "name": "ACME", "tier": "A", "status": "ACTIVE",
            "industry": "新能源", "primary_contact_name": "Maria",
            "primary_contact_email": "maria@example.com"
          },
          "place": {
            "id": "p1", "name": "柏林办公室", "city": "Berlin",
            "country_code": "DE", "timezone": "Europe/Berlin", "geocode_status": "RESOLVED"
          },
          "history": [{
            "visit_id": "v0", "trip_id": "t0", "trip_name": "上次出行",
            "date": "2026-08-01", "title": "上次拜访", "status": "DONE",
            "outcome": "MET", "notes": "关注交付周期"
          }],
          "follow_ups": [{
            "title": "补充产能证明", "due_date": "2026-11-15",
            "trip_id": "t0", "trip_name": "上次出行", "visit_id": "v0"
          }],
          "memories": [{
            "id": "m1", "kind": "CUSTOMER_NOTE", "summary": "重视交付确定性",
            "decisions": ["优先提供产能证明"], "follow_ups": [], "lessons": [],
            "confidence": 0.9, "valid_from": "2026-10-01T00:00:00+00:00"
          }],
          "data_gaps": [{
            "code": "CRM_OPPORTUNITY_NOT_CONNECTED",
            "message": "尚未连接 CRM"
          }]
        }
        """
        let briefing = try DateCoding.decode(VisitBriefing.self, fromJSON: json)
        XCTAssertEqual(briefing.objective, "确认续约条款")
        XCTAssertEqual(briefing.customer?.contactLine, "Maria · maria@example.com")
        XCTAssertEqual(briefing.historyList.first?.outcomeLabel, "已会面")
        XCTAssertEqual(briefing.followUpList.first?.title, "补充产能证明")
        XCTAssertEqual(briefing.memoryList.first?.decisions, ["优先提供产能证明"])
        XCTAssertEqual(briefing.gapList.first?.code, "CRM_OPPORTUNITY_NOT_CONNECTED")
    }

    func testActionCenterDecodesSectionsAndActions() throws {
        let json = """
        {
          "generated_at": "2026-10-28T10:00:00+00:00",
          "total": 2,
          "counts": {"DECISION": 1, "APPROVAL": 1},
          "sections": [{
            "key": "DECISION", "title": "需要我决定",
            "items": [{
              "category": "DECISION", "kind": "SUGGESTION", "entity_id": "s1",
              "title": "签证材料可能不足", "detail": "距离出发不足 10 个工作日",
              "status": "PENDING", "severity": "HIGH", "trip_id": "t1",
              "trip_name": "柏林客户拜访", "action_hint": "批准后由业务服务执行",
              "available_actions": ["APPROVE", "DISMISS", "OPEN_TRIP"],
              "created_at": "2026-10-28T09:00:00+00:00", "updated_at": null
            }]
          }]
        }
        """
        let center = try DateCoding.decode(ActionCenter.self, fromJSON: json)
        XCTAssertEqual(center.total, 2)
        XCTAssertEqual(center.actionableCount, 2)
        XCTAssertEqual(center.sectionList.first?.items.first?.tripLabel, "柏林客户拜访")
        XCTAssertTrue(center.sectionList.first?.items.first?.canApproveSuggestion == true)
    }

    func testBudgetSummaryDecodesStringDecimalsAndConversion() throws {
        let json = """
        {
          "budget_amount": "1000.00", "budget_currency": "EUR",
          "planned_cost_total": 300.0, "planned_cost_currency": "USD",
          "planned_cost_unknown_count": 0,
          "actual_by_currency": [{"currency": "EUR", "amount": 200.0, "count": 1}],
          "actual_total_in_budget_currency": "200.00",
          "variance_vs_budget": "800.00", "variance_vs_plan": null,
          "conversion_base_currency": "EUR",
          "converted_actual_total": "420.00",
          "converted_planned_cost_total": "272.73",
          "converted_variance_vs_budget": "580.00",
          "conversion_complete": true,
          "unconverted_currencies": [],
          "fx_conversions": [{
            "currency": "USD", "rate": 0.9090909090909091,
            "as_of": "2026-01-01T00:00:00+00:00", "source": "manual", "via_inverse": false
          }]
        }
        """
        let budget = try DateCoding.decode(BudgetSummary.self, fromJSON: json)
        XCTAssertEqual(budget.budgetAmount, Decimal(string: "1000.00"))
        XCTAssertEqual(budget.convertedActualTotal, Decimal(string: "420.00"))
        XCTAssertEqual(budget.conversionNote, "已按组织录入汇率折算到 EUR")
        XCTAssertEqual(budget.convertedActualLabel, "EUR 420")
        XCTAssertEqual(budget.fxConversions?.first?.source, "manual")
    }

    func testFinanceRowsDecodeStringAmounts() throws {
        let expenseJSON = """
        {"id": "e1", "trip_id": "t1", "category": "MEALS", "amount": "12.34",
         "currency": "EUR", "occurred_on": "2026-10-28", "note": null, "receipt_ref": null}
        """
        let expense = try DateCoding.decode(TripExpense.self, fromJSON: expenseJSON)
        XCTAssertEqual(expense.amount, Decimal(string: "12.34"))

        let itemJSON = """
        {"id": "i1", "report_id": "r1", "expense_id": "e1", "category": "MEALS",
         "amount": "12.34", "currency": "EUR", "occurred_on": "2026-10-28"}
        """
        let item = try DateCoding.decode(ExpenseReportItem.self, fromJSON: itemJSON)
        XCTAssertEqual(item.amount, Decimal(string: "12.34"))
    }

    func testMinutesDraftDecodes() throws {
        let json = """
        {
          "draft_id": "d1",
          "llm_invocation_id": "i1",
          "title": "客户 · 续约沟通会 会议纪要草稿",
          "summary": "与客户就续约进行了会面。",
          "decisions": ["确认续约意向"],
          "follow_up_tasks": [{"title": "提交续约报价", "due_date": "2026-11-15"}],
          "degraded": false
        }
        """
        let draft = try DateCoding.decode(MinutesDraft.self, fromJSON: json)
        XCTAssertEqual(draft.taskList.count, 1)
        XCTAssertEqual(draft.taskList.first?.title, "提交续约报价")
        XCTAssertEqual(draft.sourceLabel, "模型草稿")
    }

    func testOfflineQueueCarriesVisitIdForSync() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let store = OfflineStore(directory: directory)
        let request = VisitRecordRequest(
            clientRecordId: "record-1",
            outcome: .met,
            notes: "与客户 CTO 会面"
        )
        let item = SyncItem.visitRecord(request, visitId: "visit-1")
        _ = try store.enqueue(item)

        XCTAssertEqual(store.pendingCount(), 1)
        let queued = try XCTUnwrap(store.currentItems().first)
        guard case let .string(visitId)? = queued.item.payload["visit_id"] else {
            return XCTFail("payload 缺少 visit_id")
        }
        XCTAssertEqual(visitId, "visit-1")
        XCTAssertEqual(queued.item.entity, .visitRecord)
    }
}
