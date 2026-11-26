"""EXTERNAL 触发源验收：外部信号接入 → 主动建议 → 人审执行（真实 PostgreSQL）。

对应设计文档 §8.1（EXTERNAL 触发源）与第 14 章（外部集成边界）。信号由测试以真实结构注入
（模拟供应商回调），不使用伪造的天气/汇率数据。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.integrations import ExternalSignal
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion
from app.models.proactive import ProactiveSuggestion, ProactiveTrigger
from app.models.trip import Trip
from app.services import proactive_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A2 = "55555555-5555-5555-5555-555555555555"  # ADMIN
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A2) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def create_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"EXTERNAL 验收·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=20)).isoformat(),
            "window_end": (now + timedelta(days=23)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def ingest(client: TestClient, dedupe_key: str, *, summary: str) -> dict:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/integrations/signals",
        headers=headers(),
        json={
            "provider": "official-visa-feed",
            "signal_type": "VISA_POLICY_CHANGE",
            "subject_type": "COUNTRY",
            "subject_ref": "DE",
            "payload": {
                "title": "德国商务签证受理周期变化",
                "summary": summary,
                "severity": "HIGH",
            },
            "confidence": 0.9,
            "observed_at": (now - timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(days=30)).isoformat(),
            "dedupe_key": dedupe_key,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def cleanup(trip_id: str, dedupe_prefix: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(
            delete(ExternalSignal).where(
                ExternalSignal.organization_id == UUID(ORG_A),
                ExternalSignal.dedupe_key.like(f"{dedupe_prefix}%"),
            )
        )


def test_external_signal_becomes_reviewable_suggestion(client: TestClient) -> None:
    trip_id = create_trip(client)
    dedupe = f"ext-{uuid.uuid4().hex[:8]}"
    try:
        signal = ingest(client, dedupe, summary="受理周期由 10 个工作日延长至 15 个工作日")
        assert signal["dedupe_key"] == dedupe

        with org_session(UUID(ORG_A)) as session:
            report = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
        assert report.errors == []
        assert report.suggestions == 1

        feed = client.get(
            "/v1/proactive/suggestions", headers=headers(), params={"trip_id": trip_id}
        )
        assert feed.status_code == 200, feed.text
        items = [item for item in feed.json()["items"] if item["suggestion_type"] == "VISA_POLICY_UPDATE"]
        assert len(items) == 1
        item = items[0]
        assert item["status"] == "PENDING"
        assert item["requires_approval"] is True
        assert item["evidence"][0]["kind"] == "EXTERNAL_SIGNAL"
        assert "官方渠道复核" in item["rationale"]

        with org_session(UUID(ORG_A)) as session:
            trigger = session.scalars(
                select(ProactiveTrigger).where(ProactiveTrigger.trip_id == UUID(trip_id))
            ).one()
        assert trigger.type == "EXTERNAL"
        assert trigger.rule_id == "external_signal"

        # 人审后只准备摘要，不对外改签/发送。
        approved = client.post(
            f"/v1/proactive/suggestions/{item['id']}:approve", headers=headers()
        )
        assert approved.status_code == 200, approved.text
        body = approved.json()
        assert body["status"] == "EXECUTED"
        assert body["execution"]["status"] == "PREPARED"
        assert "未对外发送" in body["execution"]["detail"]

        # 同一 provider + dedupe_key 重推：更新原信号，不新增行。
        ingest(client, dedupe, summary="受理周期由 10 周延长至 15 周（更正）")
        with org_session(UUID(ORG_A)) as session:
            count = session.scalar(
                select(func.count())
                .select_from(ExternalSignal)
                .where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.dedupe_key == dedupe,
                )
            )
        assert count == 1

        # 内容变化会形成新的建议，但同类型在 24h 冷却内应被抑制（入库不打扰）。
        with org_session(UUID(ORG_A)) as session:
            again = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
        assert again.suppressed == 1
        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(ProactiveSuggestion).where(
                        ProactiveSuggestion.trip_id == UUID(trip_id),
                        ProactiveSuggestion.suggestion_type == "VISA_POLICY_UPDATE",
                    )
                )
            )
        assert len(rows) == 2
        assert sorted(row.status for row in rows) == ["EXECUTED", "SUPPRESSED"]
    finally:
        cleanup(trip_id, dedupe)


def test_weather_signal_maps_exact_place_and_time_window_to_active_plan(
    client: TestClient,
) -> None:
    now = datetime.now(UTC)
    trip_response = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"天气影响评估·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(minutes=5)).isoformat(),
            "window_end": (now + timedelta(hours=4)).isoformat(),
            "countries": [],
        },
    )
    assert trip_response.status_code == 201, trip_response.text
    trip_id = trip_response.json()["id"]
    dedupe = f"weather-impact-{uuid.uuid4().hex[:8]}"
    place_ids: list[str] = []
    visit_ids: list[str] = []
    try:
        for name, city, latitude, longitude in (
            ("Weather target", "Berlin", 52.52, 13.405),
            ("Different location", "Potsdam", 52.40, 13.06),
        ):
            place = client.post(
                "/v1/places",
                headers=headers(),
                json={
                    "name": name,
                    "city": city,
                    "country_code": "DE",
                    "lat": latitude,
                    "lon": longitude,
                },
            )
            assert place.status_code == 201, place.text
            place_ids.append(place.json()["id"])

        windows = [
            (place_ids[0], now + timedelta(minutes=20), now + timedelta(minutes=50)),
            (place_ids[0], now + timedelta(hours=2), now + timedelta(hours=2, minutes=30)),
            (place_ids[1], now + timedelta(minutes=25), now + timedelta(minutes=55)),
        ]
        for index, (place_id, start, end) in enumerate(windows, start=1):
            visit = client.post(
                f"/v1/trips/{trip_id}/visits",
                headers=headers(),
                json={
                    "place_id": place_id,
                    "title": f"Weather impact visit {index}",
                    "service_minutes": 30,
                    "time_window_start": start.isoformat(),
                    "time_window_end": end.isoformat(),
                },
            )
            assert visit.status_code == 201, visit.text
            visit_ids.append(visit.json()["id"])

        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, UUID(trip_id))
            assert trip is not None
            plan = PlanVersion(
                organization_id=UUID(ORG_A),
                trip_id=trip.id,
                version_number=1,
                status="ACTIVE",
                problem_hash=f"weather-impact:{uuid.uuid4().hex}",
                solver="heuristic",
                objective_value=0.0,
                solution_status="FEASIBLE",
                trip_version=trip.version,
                validation={"feasible": True},
                unplaced=[],
                activated_at=now,
            )
            session.add(plan)
            session.flush()
            trip.active_plan_version_id = plan.id
            trip.status = "ACTIVE"
            plan_stops: list[PlanStop] = []
            for index, (visit_id, (_, start, end)) in enumerate(
                zip(visit_ids, windows, strict=True), start=1
            ):
                plan_stop = PlanStop(
                    organization_id=UUID(ORG_A),
                    plan_version_id=plan.id,
                    visit_id=UUID(visit_id),
                    day_index=0,
                    sequence=index,
                    day_date=start.date(),
                    arrive_at=start,
                    depart_at=end,
                    travel_minutes=0,
                    service_minutes=30,
                    travel_source="test-fixture",
                )
                session.add(plan_stop)
                plan_stops.append(plan_stop)
            session.flush()
            # The inbound AIR leg has no departure timestamp in the current schema;
            # the outbound leg permits an estimated schedule-buffer calculation.
            session.add_all(
                [
                    PlanTravelLeg(
                        organization_id=UUID(ORG_A),
                        plan_version_id=plan.id,
                        from_stop_id=None,
                        to_stop_id=plan_stops[0].id,
                        day_index=0,
                        mode="AIR",
                        travel_minutes=600,
                        distance_km=900,
                        source="test-flight-estimate",
                        stale=True,
                    ),
                    PlanTravelLeg(
                        organization_id=UUID(ORG_A),
                        plan_version_id=plan.id,
                        from_stop_id=plan_stops[0].id,
                        to_stop_id=plan_stops[1].id,
                        day_index=0,
                        mode="GROUND",
                        travel_minutes=20,
                        distance_km=8,
                        source="test-ground-estimate",
                        stale=False,
                    ),
                    PlanTravelLeg(
                        organization_id=UUID(ORG_A),
                        plan_version_id=plan.id,
                        from_stop_id=plan_stops[1].id,
                        to_stop_id=plan_stops[2].id,
                        day_index=0,
                        mode="GROUND",
                        travel_minutes=30,
                        distance_km=12,
                        source="test-ground-estimate",
                        stale=False,
                    ),
                ]
            )
            session.flush()
            plan_id = str(plan.id)
            trip_version = trip.version

        signal_response = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json={
                "provider": "open-meteo",
                "signal_type": "WEATHER_DISRUPTION",
                "subject_type": "TRIP",
                "subject_ref": trip_id,
                "payload": {
                    "place_id": place_ids[0],
                    "place_name": "Berlin",
                    "summary": "Severe weather observation near the scheduled visit",
                    "severity": "HIGH",
                    "wind_kmh": 62,
                    "precipitation_mm": 12,
                },
                "confidence": 0.9,
                "observed_at": (now - timedelta(minutes=5)).isoformat(),
                "valid_until": (now + timedelta(hours=1)).isoformat(),
                "dedupe_key": dedupe,
            },
        )
        assert signal_response.status_code == 201, signal_response.text

        with org_session(UUID(ORG_A)) as session:
            report = proactive_service.scan_trip(
                session, UUID(ORG_A), UUID(trip_id), now=now
            )
        assert report.errors == []
        assert report.suggestions == 1

        with org_session(UUID(ORG_A)) as session:
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.organization_id == UUID(ORG_A),
                    ProactiveSuggestion.trip_id == UUID(trip_id),
                    ProactiveSuggestion.suggestion_type == "WEATHER_DISRUPTION_ALERT",
                )
            ).one()
            impact = suggestion.proposed_action["params"]["impact_assessment"]
            assert impact["status"] == "PLACE_AND_TIME_OVERLAP"
            assert impact["plan_version_id"] == plan_id
            assert impact["matched_stop_count"] == 2
            assert impact["affected_stop_count"] == 1
            assert impact["matched_visit_ids"] == visit_ids[:2]
            assert impact["affected_visit_ids"] == [visit_ids[0]]
            assert impact["related_travel_leg_count"] == 2
            related_legs = impact["related_travel_legs"]
            inbound = next(item for item in related_legs if item["from_stop_id"] is None)
            outbound = next(item for item in related_legs if item["from_stop_id"] is not None)
            assert inbound["mode"] == "AIR"
            assert inbound["schedule_assessment"] == "ORIGIN_TIMING_UNKNOWN"
            assert outbound["relation"] == "OUTBOUND_FROM_AFFECTED_STOP"
            assert outbound["estimated_schedule_gap_minutes"] == 70
            assert outbound["estimated_schedule_slack_minutes"] == 50
            assert outbound["schedule_assessment"] == "ESTIMATED_BUFFER_AVAILABLE"
            assert any(item["kind"] == "PLAN_STOP" for item in suggestion.evidence)
            assert any(item["kind"] == "PLAN_TRAVEL_LEG" for item in suggestion.evidence)
            suggestion_id = str(suggestion.id)
            original_dedupe_key = suggestion.dedupe_key

        approved = client.post(
            f"/v1/proactive/suggestions/{suggestion_id}:approve",
            headers=headers(),
        )
        assert approved.status_code == 200, approved.text
        with org_session(UUID(ORG_A)) as session:
            persisted = session.get(ProactiveSuggestion, UUID(suggestion_id))
            trip = session.get(Trip, UUID(trip_id))
            assert persisted is not None
            assert trip is not None
            assert persisted.execution_result["status"] == "PREPARED"
            assert persisted.execution_result["impact_assessment"]["affected_visit_ids"] == [
                visit_ids[0]
            ]
            # Approval prepares an impact brief only; it does not mutate the plan or Trip inputs.
            assert trip.active_plan_version_id == UUID(plan_id)
            assert trip.version == trip_version

        # A new immutable plan revision changes the weather impact context even
        # though the provider signal payload remains identical.
        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, UUID(trip_id))
            previous_plan = session.get(PlanVersion, UUID(plan_id))
            assert trip is not None
            assert previous_plan is not None
            previous_plan.status = "SUPERSEDED"
            session.flush()
            revised_plan = PlanVersion(
                organization_id=UUID(ORG_A),
                trip_id=trip.id,
                version_number=2,
                status="ACTIVE",
                problem_hash=f"weather-impact-revision:{uuid.uuid4().hex}",
                solver="heuristic",
                objective_value=0.0,
                solution_status="FEASIBLE",
                trip_version=trip.version,
                validation={"feasible": True},
                unplaced=[],
                activated_at=now + timedelta(minutes=1),
            )
            session.add(revised_plan)
            session.flush()
            trip.active_plan_version_id = revised_plan.id
            for index, (visit_id, (_, start, end)) in enumerate(
                zip(visit_ids, windows, strict=True), start=1
            ):
                session.add(
                    PlanStop(
                        organization_id=UUID(ORG_A),
                        plan_version_id=revised_plan.id,
                        visit_id=UUID(visit_id),
                        day_index=0,
                        sequence=index,
                        day_date=start.date(),
                        arrive_at=start,
                        depart_at=end,
                        travel_minutes=0,
                        service_minutes=30,
                        travel_source="test-fixture",
                    )
                )
            revised_plan_id = str(revised_plan.id)

        with org_session(UUID(ORG_A)) as session:
            refreshed = proactive_service.scan_trip(
                session,
                UUID(ORG_A),
                UUID(trip_id),
                now=now + timedelta(minutes=1),
            )
        assert refreshed.errors == []
        assert refreshed.suggestions == 1
        assert refreshed.suppressed == 1  # Same alert type remains under cooldown.

        with org_session(UUID(ORG_A)) as session:
            revised_suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.organization_id == UUID(ORG_A),
                    ProactiveSuggestion.trip_id == UUID(trip_id),
                    ProactiveSuggestion.suggestion_type == "WEATHER_DISRUPTION_ALERT",
                    ProactiveSuggestion.dedupe_key != original_dedupe_key,
                )
            ).one()
            revised_impact = revised_suggestion.proposed_action["params"]["impact_assessment"]
            assert revised_impact["plan_version_id"] == revised_plan_id
            assert revised_impact["affected_visit_ids"] == [visit_ids[0]]
            assert revised_suggestion.status == "SUPPRESSED"
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            if place_ids:
                session.execute(
                    delete(Place).where(Place.id.in_([UUID(item) for item in place_ids]))
                )
            session.execute(
                delete(ExternalSignal).where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.dedupe_key == dedupe,
                )
            )


def test_external_signals_are_tenant_isolated(client: TestClient) -> None:
    trip_id = create_trip(client)
    dedupe = f"ext-{uuid.uuid4().hex[:8]}"
    try:
        ingest(client, dedupe, summary="仅供组织 A")
        listing = client.get(
            "/v1/integrations/signals", headers=headers(ORG_B, USER_B)
        )
        assert listing.status_code == 200
        assert listing.json() == []
        with org_session(UUID(ORG_B)) as session:
            report = proactive_service.scan_trip(session, UUID(ORG_B), UUID(trip_id))
        assert report.suggestions == 0
    finally:
        cleanup(trip_id, dedupe)


def test_flight_signal_does_not_auto_match_air_leg_without_booking_link(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip_response = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"航班关联边界·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=1)).isoformat(),
            "window_end": (now + timedelta(days=2)).isoformat(),
            "countries": [],
        },
    )
    assert trip_response.status_code == 201, trip_response.text
    trip_id = trip_response.json()["id"]
    dedupe = f"flight-unlinked-{uuid.uuid4().hex[:8]}"
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"Flight destination {uuid.uuid4().hex[:6]}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]
    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={
            "place_id": place_id,
            "title": "Flight destination visit",
            "service_minutes": 60,
        },
    )
    assert visit.status_code == 201, visit.text
    try:
        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, UUID(trip_id))
            assert trip is not None
            plan = PlanVersion(
                organization_id=UUID(ORG_A),
                trip_id=trip.id,
                version_number=1,
                status="ACTIVE",
                problem_hash=f"flight-impact:{uuid.uuid4().hex}",
                solver="heuristic",
                objective_value=0.0,
                solution_status="FEASIBLE",
                trip_version=trip.version,
                validation={"feasible": True},
                unplaced=[],
                activated_at=now,
            )
            session.add(plan)
            session.flush()
            trip.active_plan_version_id = plan.id
            trip.status = "ACTIVE"
            stop = PlanStop(
                organization_id=UUID(ORG_A),
                plan_version_id=plan.id,
                visit_id=UUID(visit.json()["id"]),
                day_index=0,
                sequence=1,
                day_date=(now + timedelta(hours=5)).date(),
                arrive_at=now + timedelta(hours=5),
                depart_at=now + timedelta(hours=6),
                travel_minutes=0,
                service_minutes=60,
                travel_source="test-fixture",
            )
            session.add(stop)
            session.flush()
            leg = PlanTravelLeg(
                organization_id=UUID(ORG_A),
                plan_version_id=plan.id,
                from_stop_id=None,
                to_stop_id=stop.id,
                day_index=0,
                mode="AIR",
                travel_minutes=600,
                distance_km=900,
                source="test-flight-estimate",
                stale=True,
            )
            session.add(leg)
            session.flush()
            plan_id = str(plan.id)
            leg_id = str(leg.id)

        signal_response = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json={
                "provider": "opensky",
                "signal_type": "FLIGHT_STATUS",
                "subject_type": "TRIP",
                "subject_ref": trip_id,
                "payload": {
                    "callsign": "DLH123",
                    "icao24": "3c675a",
                    "latitude": 52.52,
                    "longitude": 13.405,
                    "on_ground": False,
                    "last_contact": now.isoformat(),
                },
                "confidence": 0.95,
                "observed_at": now.isoformat(),
                "valid_until": (now + timedelta(hours=2)).isoformat(),
                "dedupe_key": dedupe,
            },
        )
        assert signal_response.status_code == 201, signal_response.text

        with org_session(UUID(ORG_A)) as session:
            report = proactive_service.scan_trip(
                session, UUID(ORG_A), UUID(trip_id), now=now
            )
        assert report.errors == []
        assert report.suggestions == 1

        with org_session(UUID(ORG_A)) as session:
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.organization_id == UUID(ORG_A),
                    ProactiveSuggestion.trip_id == UUID(trip_id),
                    ProactiveSuggestion.suggestion_type == "FLIGHT_STATUS_ALERT",
                )
            ).one()
            impact = suggestion.proposed_action["params"]["impact_assessment"]
            assert impact["status"] == "AIR_LEGS_REQUIRE_EXPLICIT_LINK"
            assert impact["plan_version_id"] == plan_id
            assert impact["callsign"] == "DLH123"
            assert impact["candidate_air_leg_count"] == 1
            assert impact["linked_air_leg_count"] == 0
            assert impact["linked_air_leg_ids"] == []
            assert impact["unlinked_air_legs"][0]["plan_travel_leg_id"] == leg_id
            assert any(
                item["kind"] == "UNLINKED_AIR_TRAVEL_LEG"
                for item in suggestion.evidence
            )
            assert "不会仅凭这些标识猜测" in suggestion.rationale
    finally:
        cleanup(trip_id, dedupe)
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Place).where(Place.id == UUID(place_id)))
