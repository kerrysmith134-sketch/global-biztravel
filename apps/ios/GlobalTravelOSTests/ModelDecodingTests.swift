import XCTest
@testable import GlobalTravelOS

// swiftlint:disable:next type_body_length
final class ModelDecodingTests: XCTestCase {
    func testTripPlaceDecodesNumericAndStringGeocodeConfidence() throws {
        let numericJSON = """
        {
          "id": "place-numeric",
          "name": "Berlin HQ",
          "country_code": "DE",
          "latitude": 52.52,
          "longitude": 13.405,
          "geocode_status": "RESOLVED",
          "geocode_source": "client",
          "geocode_confidence": 0.98
        }
        """
        let numeric = try DateCoding.decode(Place.self, fromJSON: numericJSON)
        XCTAssertEqual(numeric.geocodeConfidence, Decimal(string: "0.98"))
        XCTAssertEqual(numeric.geocodeSource, "client")

        let stringJSON = """
        {
          "id": "place-string",
          "name": "Amsterdam Office",
          "geocode_confidence": "0.82"
        }
        """
        let string = try DateCoding.decode(Place.self, fromJSON: stringJSON)
        XCTAssertEqual(string.geocodeConfidence, Decimal(string: "0.82"))
    }

    func testGlobalCustomerMapResponseDecodesNestedCustomerPlaceAndSummary() throws {
        let json = """
        {
          "items": [{
            "customer": {
              "id": "customer-1", "name": "ACME Energy", "tier": "A",
              "country_code": "DE", "industry": "Energy", "status": "ACTIVE"
            },
            "place": {
              "id": "place-1", "label": "Headquarters", "is_primary": true,
              "name": "Berlin HQ", "address_line": "Industriestrasse 12",
              "city": "Berlin", "region": "Berlin", "country_code": "DE",
              "timezone": "Europe/Berlin", "lat": 52.52, "lon": 13.405,
              "geocode_status": "RESOLVED", "geocode_source": "client",
              "geocode_confidence": "0.98"
            }
          }],
          "summary": {
            "customers_with_locations": 1, "customers_without_locations": 2,
            "locations_total": 3, "locations_mapped": 2,
            "locations_missing_coordinates": 1, "countries": {"DE": 2, "NL": 1},
            "country_stats": [{
              "country_code": "DE", "customers_total": 2,
              "tier_a_customers": 1, "tier_b_customers": 0, "tier_c_customers": 1,
              "active_customers": 2, "prospect_customers": 0, "inactive_customers": 0,
              "locations_total": 2, "locations_mapped": 2
            }]
          },
          "next_cursor": "200"
        }
        """
        let value = try DateCoding.decode(GlobalCustomerMapResponse.self, fromJSON: json)
        XCTAssertEqual(value.items.count, 1)
        XCTAssertEqual(value.items[0].customer.name, "ACME Energy")
        XCTAssertEqual(value.items[0].place.displayAddress, "Industriestrasse 12 · Berlin · DE")
        XCTAssertTrue(value.items[0].place.hasValidCoordinates)
        XCTAssertEqual(value.items[0].place.geocodeConfidence, Decimal(string: "0.98"))
        XCTAssertEqual(value.summary.locationsMissingCoordinates, 1)
        XCTAssertEqual(value.summary.countries["DE"], 2)
        XCTAssertEqual(value.summary.countryStats.first?.customersTotal, 2)
        XCTAssertEqual(value.summary.countryStats.first?.tierACustomers, 1)
        XCTAssertEqual(value.summary.countryStats.first?.locationsTotal, 2)
        XCTAssertEqual(value.nextCursor, "200")
    }

    func testCustomerPlaceDecodesAddressAndGeocodeProvenance() throws {
        let json = """
        {
          "place_id": "place-1",
          "label": "工厂",
          "is_primary": true,
          "name": "ACME Berlin Factory",
          "address_line": "Industriestrasse 12",
          "city": "Berlin",
          "region": "Berlin",
          "country_code": "DE",
          "lat": 52.5200,
          "lon": 13.4050,
          "geocode_status": "RESOLVED",
          "geocode_source": "google-maps",
          "geocode_confidence": "0.82"
        }
        """
        let place = try DateCoding.decode(CustomerPlace.self, fromJSON: json)

        XCTAssertEqual(place.placeId, "place-1")
        XCTAssertEqual(place.label, "工厂")
        XCTAssertTrue(place.isPrimary)
        XCTAssertEqual(place.displayAddress, "Industriestrasse 12 · Berlin · DE")
        XCTAssertTrue(place.hasValidCoordinates)
        XCTAssertEqual(place.geocodeStatus, "RESOLVED")
        XCTAssertEqual(place.geocodeSource, "google-maps")
        XCTAssertEqual(place.geocodeConfidence, Decimal(string: "0.82"))
        XCTAssertEqual(place.confidenceLabel, "82%")
    }

    func testCustomerPlaceRejectsOutOfRangeCoordinatesForMap() throws {
        let json = """
        {
          "place_id": "place-2",
          "name": "Invalid location",
          "geocode_status": "FAILED",
          "lat": 95.0,
          "lon": 200.0
        }
        """
        let place = try DateCoding.decode(CustomerPlace.self, fromJSON: json)
        XCTAssertFalse(place.hasValidCoordinates)
        XCTAssertEqual(place.displayAddress, "")
    }

    func testCustomerPlaceLinkRequestUsesBackendSnakeCaseAndIdempotency() throws {
        let request = try Endpoints.linkCustomerPlace(
            customerId: "customer-1",
            request: CustomerPlaceLinkRequest(
                placeId: "place-1",
                label: "拜访地址",
                isPrimary: false
            )
        )
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/v1/customers/customer-1/places")
        XCTAssertNotNil(request.idempotencyKey)

        let data = try XCTUnwrap(request.body)
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(payload["place_id"] as? String, "place-1")
        XCTAssertEqual(payload["label"] as? String, "拜访地址")
        XCTAssertEqual(payload["is_primary"] as? Bool, false)
    }

    func testCreateVisitEndpointCarriesIdempotencyKey() throws {
        let request = try Endpoints.createVisit(
            tripId: "trip-1",
            body: ["place_id": "place-1", "title": "客户拜访"]
        )
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/v1/trips/trip-1/visits")
        XCTAssertNotNil(request.idempotencyKey)
    }

    func testVisitCreateRequestEncodesCRMCustomerId() throws {
        let request = VisitCreateRequest(
            customerId: "customer-1",
            placeName: "ACME Berlin Factory",
            title: "年度复审"
        )
        let data = try DateCoding.encode(request)
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(payload["customer_id"] as? String, "customer-1")
    }

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

    func testPageDecodesBareArrayResponse() throws {
        let json = """
        [{
          "id": "p1", "trip_id": "t1", "version_number": 1, "status": "DRAFT",
          "problem_hash": "sha256:x", "solver": "heuristic", "objective_value": 0,
          "solution_status": "FEASIBLE", "cost_unknown_count": 0, "degraded": false,
          "trip_version": 1, "validation": {"feasible": true}, "unplaced": [],
          "policy_violations": [], "explanation_ref": null, "job_id": null,
          "stop_count": 1, "day_count": 1, "activated_at": null, "superseded_at": null,
          "created_at": "2026-10-28T10:00:00+00:00", "updated_at": "2026-10-28T10:00:00+00:00"
        }]
        """
        let page = try DateCoding.decode(Page<PlanVersion>.self, fromJSON: json)
        XCTAssertEqual(page.items.count, 1)
        XCTAssertEqual(page.items.first?.versionLabel, "v1")
        XCTAssertNil(page.nextCursor)
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

    func testNotificationFeedDecodesPriorities() throws {
        let json = """
        {
          "items": [{
            "id": "n1", "suggestion_id": "s1", "kind": "PROACTIVE_SUGGESTION",
            "title": "航班状态变化", "body": "原航班取消，可能错过客户会议",
            "status": "SENT", "provider": "inapp", "priority": "P0",
            "issue_key": "flight:CA123", "attempts": 1,
            "sent_at": "2026-10-29T10:00:00+00:00", "read_at": null,
            "created_at": "2026-10-29T09:59:00+00:00"
          }],
          "unread": 1,
          "unread_by_priority": {"P0": 1, "P1": 0, "P2": 0, "P3": 0}
        }
        """
        let feed = try DateCoding.decode(NotificationFeed.self, fromJSON: json)
        XCTAssertEqual(feed.unreadCount, 1)
        XCTAssertEqual(feed.items.first?.priorityLabel, "紧急")
        XCTAssertTrue(feed.items.first?.isUnread == true)
        XCTAssertEqual(feed.unreadBreakdown, "未读 P0×1")
    }

    func testAssistantDetailDecodesMessagesAndCards() throws {
        let json = """
        {
          "conversation": {
            "id": "c1", "trip_id": "t1", "title": "这趟行程现在怎么样？",
            "created_at": "2026-10-29T10:00:00+00:00",
            "updated_at": "2026-10-29T10:01:00+00:00"
          },
          "messages": [
            {"id": "m1", "role": "USER", "content": "需要我做什么？", "cards": [],
             "llm_invocation_id": null, "created_at": "2026-10-29T10:00:00+00:00"},
            {"id": "m2", "role": "ASSISTANT", "content": "有 2 项需要你处理。",
             "cards": [{"type": "TRIP_STATUS", "title": "行程", "detail": "ACTIVE",
                        "entity_id": "t1", "link": "trip"}],
             "llm_invocation_id": "i1", "created_at": "2026-10-29T10:01:00+00:00"}
          ]
        }
        """
        let detail = try DateCoding.decode(AssistantConversationDetail.self, fromJSON: json)
        XCTAssertEqual(detail.messageList.count, 2)
        XCTAssertEqual(detail.messageList[1].cardList.first?.type, "TRIP_STATUS")
        XCTAssertTrue(detail.messageList[1].llmInvocationId != nil)
        XCTAssertTrue(detail.messageList[0].isFromUser)
    }

    func testTripOutcomeDecodesVisitsAndSignals() throws {
        let json = """
        {
          "trip_id": "t1", "status": "COMPLETED",
          "window": {"start": "2026-11-01T00:00:00+00:00", "end": "2026-11-05T00:00:00+00:00"},
          "visits": {"DONE": 2, "MISSED": 1},
          "visit_labels": {"DONE": "已完成", "MISSED": "未会面"},
          "record_outcomes": {"MET": 1}, "record_outcome_labels": {"MET": "已会面"},
          "plan_cost": {"total": 400.0, "currency": "EUR", "unknown_count": 0},
          "actual_cost": [{"currency": "EUR", "amount": 150.0, "count": 1}],
          "insights_by_kind": {"OPPORTUNITY_SIGNAL": 1},
          "insight_kind_labels": {"OPPORTUNITY_SIGNAL": "商机信号"},
          "opportunity_signals": [{
            "kind": "OPPORTUNITY_SIGNAL", "kind_label": "商机信号",
            "text": "客户计划明年扩产", "visit_id": "v1", "requires_confirmation": true
          }],
          "memory_summaries": [{"kind": "TRIP_REVIEW", "summary": "完成 3 次拜访"}]
        }
        """
        let outcome = try DateCoding.decode(TripOutcome.self, fromJSON: json)
        XCTAssertEqual(outcome.visitList.count, 2)
        XCTAssertEqual(outcome.planCost?.label, "EUR 400.00")
        XCTAssertEqual(outcome.signalList.first?.kindLabel, "商机信号")
        XCTAssertTrue(outcome.signalList.first?.requiresConfirmation == true)
    }

    func testLocalGuideDecodesSectionsAndGaps() throws {
        let json = """
        {
          "trip_id": "t1",
          "countries": [{
            "country_code": "DE", "name": "德国",
            "sections": [{"key": "etiquette", "title": "商务会面礼仪", "items": ["守时是底线"]}],
            "cities": [{"city": "Berlin", "tips": ["会议预留缓冲"]}]
          }],
          "cities_covered": ["Berlin"],
          "data_gaps": [{"code": "LIVE_POI_NOT_CONNECTED", "message": "实时推荐未接入"}]
        }
        """
        let guide = try DateCoding.decode(LocalGuide.self, fromJSON: json)
        XCTAssertEqual(guide.countryList.first?.sectionList.first?.itemList.count, 1)
        XCTAssertEqual(guide.countryList.first?.cityList.first?.city, "Berlin")
        XCTAssertEqual(guide.gapList.first?.code, "LIVE_POI_NOT_CONNECTED")
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

    func testBudgetSummaryDecodesChainPayload() throws {
        // 链式用例实际返回的 payload（2026-10-30 抓取）：无预算、无计划成本、CNY 折算。
        let json = """
        {
          "budget_amount": null, "budget_currency": "CNY", "planned_cost_total": null,
          "planned_cost_currency": null, "planned_cost_unknown_count": 7,
          "actual_by_currency": [], "actual_total_in_budget_currency": "0",
          "variance_vs_budget": null, "variance_vs_plan": null,
          "conversion_base_currency": "CNY", "converted_actual_total": "0.00",
          "converted_planned_cost_total": null, "converted_variance_vs_budget": null,
          "conversion_complete": false, "unconverted_currencies": [], "fx_conversions": []
        }
        """
        let budget = try DateCoding.decode(BudgetSummary.self, fromJSON: json)
        XCTAssertEqual(budget.budgetCurrency, "CNY")
        XCTAssertEqual(budget.convertedActualTotal, Decimal(string: "0.00"))
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

    /// 回归：本地库与离线缓存都靠 Trip 的 payload 还原，encode 漏 countries 会让
    /// 驾驶舱的签证风险与目的国展示静默失效。
    func testTripEncodeRoundTripKeepsCountries() throws {
        let trip = Trip(
            id: "t1",
            name: "柏林客户周",
            status: "DRAFT",
            version: 1,
            window: TripWindow(start: Date(), end: Date().addingTimeInterval(86_400)),
            originPlace: Place(name: "上海总部", countryCode: "CN", timezone: "Asia/Shanghai"),
            countries: ["DE", "NL"]
        )
        let data = try DateCoding.encode(trip)
        let decoded = try DateCoding.decode(Trip.self, from: data)
        XCTAssertEqual(decoded.countries, ["DE", "NL"])
    }

    func testResearchPlanRequestEncodesScopeAndLinkedContext() throws {
        let request = try Endpoints.createResearchPlan(
            ResearchPlanCreateRequest(
                request: "研究目标企业背景",
                sourceScope: .internalAndExternal,
                tripId: "trip-1",
                customerId: "customer-1",
                contextText: "用户提供的补充材料",
                locale: "zh-CN"
            )
        )
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/v1/research/plans")
        XCTAssertNotNil(request.idempotencyKey)

        let data = try XCTUnwrap(request.body)
        let payload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(payload["request"] as? String, "研究目标企业背景")
        XCTAssertEqual(payload["source_scope"] as? String, "INTERNAL_AND_EXTERNAL")
        XCTAssertEqual(payload["trip_id"] as? String, "trip-1")
        XCTAssertEqual(payload["customer_id"] as? String, "customer-1")
        XCTAssertEqual(payload["context_text"] as? String, "用户提供的补充材料")
        XCTAssertEqual(payload["locale"] as? String, "zh-CN")

        let detail = Endpoints.researchPlan(id: "plan-1")
        XCTAssertEqual(detail.method, .get)
        XCTAssertEqual(detail.path, "/v1/research/plans/plan-1")

        let execute = try Endpoints.executeResearchPlan(
            id: "plan-1",
            body: ResearchExecutionRequest(
                searchQueries: ["reviewed query one", "reviewed query two"]
            )
        )
        XCTAssertEqual(execute.method, .post)
        XCTAssertEqual(execute.path, "/v1/research/plans/plan-1:execute")
        XCTAssertNotNil(execute.idempotencyKey)
        let executeBody = try XCTUnwrap(execute.body)
        let executePayload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: executeBody) as? [String: Any]
        )
        XCTAssertEqual(
            executePayload["search_queries"] as? [String],
            ["reviewed query one", "reviewed query two"]
        )
    }

    func testResearchExecutionAcceptedDecodesQueuedJob() throws {
        let json = """
        {
          "plan_id": "plan-1",
          "job_id": "job-1",
          "status": "PENDING",
          "reused": false
        }
        """
        let accepted = try DateCoding.decode(ResearchExecutionAccepted.self, fromJSON: json)
        XCTAssertEqual(accepted.planId, "plan-1")
        XCTAssertEqual(accepted.jobId, "job-1")
        XCTAssertEqual(accepted.status, "PENDING")
        XCTAssertFalse(accepted.reused)
    }

    func testResearchPlanDecodesIntentAuditAndEvidenceWithoutClaimingRetrieval() throws {
        let json = """
        {
          "id": "plan-1",
          "trip_id": "trip-1",
          "customer_id": "customer-1",
          "request": "研究目标企业背景",
          "requested_scope": "INTERNAL_AND_EXTERNAL",
          "recommended_scope": "INTERNAL_AND_EXTERNAL",
          "status": "PLANNED",
          "retrieval_status": "NOT_STARTED",
          "job_id": null,
          "retrieval_error": null,
          "results_count": 0,
          "reviewed_search_queries": [],
          "plan": {
            "intent_summary": "研究目标企业并准备拜访",
            "objective": "为下周拜访准备事实与问题",
            "subject": "ACME GmbH",
            "domain": null,
            "geographies": ["Germany"],
            "entities": ["ACME GmbH"],
            "time_scope": "最新公开信息",
            "research_questions": ["公司业务与组织背景是什么？"],
            "search_queries": ["ACME GmbH company background"],
            "source_types": ["公司官网", "官方登记资料"],
            "deliverables": ["拜访前研究简报"],
            "missing_information": ["尚未提供客户联系人"],
            "external_research_needed": true
          },
          "planner": {
            "agent_run_id": "11111111-1111-1111-1111-111111111111",
            "llm_invocation_id": "22222222-2222-2222-2222-222222222222",
            "model": "test-model",
            "degraded": false,
            "error": null
          },
          "evidence": [
            {
              "id": "33333333-3333-3333-3333-333333333333",
              "kind": "USER_REQUEST",
              "ref": "request:sha256:abc",
              "source": "user_input",
              "payload": {"character_count": 9},
              "retrieved_at": "2026-11-21T09:00:00+00:00",
              "created_at": "2026-11-21T09:00:00+00:00"
            },
            {
              "id": "44444444-4444-4444-4444-444444444444",
              "kind": "WEB_SEARCH_RESULT",
              "ref": "https://example.com/company",
              "source": "searxng",
              "payload": {
                "url": "https://example.com/company",
                "title": "Example company profile",
                "snippet": "A company profile search snippet.",
                "engine": "general",
                "published_at": "2026-11-20",
                "evidence_state": "UNVERIFIED_SEARCH_RESULT"
              },
              "retrieved_at": "2026-11-21T09:05:00+00:00",
              "created_at": "2026-11-21T09:05:00+00:00"
            }
          ],
          "created_at": "2026-11-21T09:00:00+00:00"
        }
        """
        let plan = try DateCoding.decode(ResearchPlan.self, fromJSON: json)
        XCTAssertEqual(plan.id, "plan-1")
        XCTAssertEqual(plan.customerId, "customer-1")
        XCTAssertEqual(plan.plan.domain, nil)
        XCTAssertEqual(plan.plan.geographies, ["Germany"])
        XCTAssertEqual(plan.plan.researchQuestions.count, 1)
        XCTAssertEqual(plan.plan.searchQueries.first, "ACME GmbH company background")
        XCTAssertEqual(plan.planner.model, "test-model")
        XCTAssertEqual(plan.evidence.first?.kind, "USER_REQUEST")
        let searchEvidence = try XCTUnwrap(
            plan.evidence.first { $0.kind == "WEB_SEARCH_RESULT" }
        )
        XCTAssertEqual(searchEvidence.title, "Example company profile")
        XCTAssertEqual(searchEvidence.snippet, "A company profile search snippet.")
        XCTAssertEqual(searchEvidence.source, "searxng")
        XCTAssertEqual(searchEvidence.engine, "general")
        XCTAssertTrue(searchEvidence.isUnverifiedSearchResult)
        XCTAssertEqual(searchEvidence.sourceURL?.absoluteString, "https://example.com/company")
        XCTAssertEqual(plan.retrievalStatus, "NOT_STARTED")
        XCTAssertEqual(plan.retrievalStatusLabel, "尚未采集外部证据")
    }

    func testMarketContextSnapshotDecodesProvenanceAndStructuredFields() throws {
        let json = """
        {
          "id": "context-1",
          "workspace_id": "workspace-1",
          "version": 3,
          "raw_input": "We provide design services to clinics in Kenya.",
          "extracted_context": {
            "offerings": [{
              "label": "Design services",
              "normalized_label": "design",
              "description": "User-provided service",
              "source_quote": "We provide design services",
              "confidence": 0.95
            }]
          },
          "user_overrides": {},
          "unknowns": ["Customer size is not specified."],
          "provenance": {
            "offerings": {
              "source": "MODEL",
              "prompt_id": "market_context_extract",
              "prompt_version": "v1"
            }
          },
          "extraction_status": "COMPLETED",
          "extraction_prompt_id": "market_context_extract",
          "extraction_prompt_version": "v1",
          "is_current": true,
          "created_by_user_id": "user-1",
          "created_at": "2026-11-22T08:00:00+00:00"
        }
        """
        let context = try DateCoding.decode(
            MarketContextSnapshotRead.self,
            fromJSON: json
        )

        XCTAssertEqual(context.id, "context-1")
        XCTAssertEqual(context.version, 3)
        XCTAssertEqual(context.statusLabel, "已提取")
        XCTAssertEqual(context.fieldSummaries.first?.value, "Design services")
        XCTAssertEqual(context.unknowns, ["Customer size is not specified."])
        XCTAssertTrue(context.isCurrent)
    }


    func testMarketEvidenceIntelligenceReportDecodesDuplicateAndVersionSignals() throws {
        let json = """
        {
          "workspace_id": "workspace-1",
          "generated_at": "2026-11-22T12:00:00+00:00",
          "total_evidence_count": 2,
          "analyzed_evidence_count": 2,
          "is_truncated": false,
          "distinct_host_count": 2,
          "duplicate_content_group_count": 1,
          "duplicate_content_evidence_count": 2,
          "multi_version_source_count": 0,
          "multi_version_evidence_count": 0,
          "unverified_count": 2,
          "partially_verified_count": 0,
          "verified_count": 0,
          "disputed_count": 0,
          "rejected_count": 0,
          "missing_publication_date_count": 1,
          "missing_content_hash_count": 0,
          "unextracted_search_result_count": 1,
          "items": [{
            "evidence_id": "evidence-1",
            "kind": "WEB_SEARCH_RESULT",
            "source": "Publisher",
            "canonical_ref": "https://example.com/report",
            "source_host": "example.com",
            "content_fingerprint": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "fingerprint_source": "STORED_HASH",
            "verification_status": "UNVERIFIED",
            "published_at": null,
            "retrieved_at": "2026-11-22T11:00:00+00:00",
            "age_days": null,
            "origin_assessment": "DUPLICATE_CONTENT",
            "duplicate_content_evidence_ids": ["evidence-2"],
            "same_host_evidence_ids": [],
            "same_source_version_evidence_ids": [],
            "derived_page_evidence_ids": [],
            "issues": ["UNVERIFIED_SOURCE", "MISSING_PUBLICATION_DATE", "DUPLICATE_CONTENT"]
          }],
          "caveats": ["不同 host 不能证明独立来源。"]
        }
        """
        let report = try DateCoding.decode(
            MarketEvidenceIntelligenceReport.self,
            fromJSON: json
        )

        XCTAssertEqual(report.totalEvidenceCount, 2)
        XCTAssertEqual(report.duplicateContentGroupCount, 1)
        XCTAssertEqual(report.items.first?.originLabel, "内容重复")
        XCTAssertEqual(report.items.first?.issueLabels.count, 3)
        XCTAssertEqual(report.caveats.count, 1)
    }

}
