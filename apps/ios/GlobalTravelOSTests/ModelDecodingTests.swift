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
        XCTAssertEqual(trip.statusLabel, "ACTIVE")
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
          "validation": {"feasible": true, "checks": []},
          "unplaced": [],
          "created_at": "2026-11-01T10:00:00+00:00",
          "stops": [
            {"visit_id": "v1", "day_index": 0, "day_date": "2026-11-03", "sequence": 1,
             "arrive_at": "2026-11-03T09:00:00+01:00", "depart_at": "2026-11-03T10:00:00+01:00",
             "travel_minutes": 30, "service_minutes": 60},
            {"visit_id": "v2", "day_index": 0, "day_date": "2026-11-03", "sequence": 2,
             "arrive_at": "2026-11-03T11:00:00+01:00", "depart_at": "2026-11-03T12:00:00+01:00",
             "travel_minutes": 15, "service_minutes": 45},
            {"visit_id": "v3", "day_index": 1, "day_date": "2026-11-04", "sequence": 1,
             "arrive_at": "2026-11-04T09:00:00+01:00", "depart_at": "2026-11-04T10:00:00+01:00",
             "travel_minutes": 40, "service_minutes": 60}
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
