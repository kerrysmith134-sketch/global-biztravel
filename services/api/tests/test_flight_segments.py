"""Flight booking segments bind only through explicit human confirmation."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from app.api.errors import Unprocessable
from app.db import org_session
from app.integrations.flights import FlightState
from app.main import app
from app.models.flight_operations import FlightOperationalUpdate
from app.models.geo import Place
from app.models.integrations import ExternalSignal
from app.models.planning import PlanStop, PlanTravelLeg, PlanVersion
from app.models.proactive import ProactiveSuggestion
from app.models.records import VisitFollowUp
from app.models.reliability import BackgroundJob
from app.models.trip import Trip, Visit
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import (
    external_impact_service,
    flight_segment_service,
    proactive_service,
    signal_pull_service,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(**extra: str) -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A, **extra}


def create_trip_and_place(client: TestClient) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"Flight binding {uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=1)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": [],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"Flight destination {uuid.uuid4().hex[:8]}",
            "city": "Frankfurt",
            "country_code": "DE",
            "lat": 50.1109,
            "lon": 8.6821,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "Flight-linked customer visit"},
    )
    assert visit.status_code == 201, visit.text
    return trip_id, place_id, visit.json()["id"]


def create_plan(
    trip_id: str,
    visit_id: str,
    *,
    version_number: int,
    mode: str = "AIR",
    arrive_at: datetime | None = None,
    stale: bool = True,
) -> tuple[str, str]:
    now = datetime.now(UTC)
    with org_session(UUID(ORG_A)) as session:
        trip = session.get(Trip, UUID(trip_id))
        assert trip is not None
        if version_number > 1:
            previous = session.scalars(
                select(PlanVersion).where(
                    PlanVersion.organization_id == UUID(ORG_A),
                    PlanVersion.trip_id == UUID(trip_id),
                    PlanVersion.status == "ACTIVE",
                )
            ).one()
            previous.status = "SUPERSEDED"
            previous.superseded_at = now
            session.flush()

        plan = PlanVersion(
            organization_id=UUID(ORG_A),
            trip_id=trip.id,
            version_number=version_number,
            status="ACTIVE",
            problem_hash=f"flight-binding:{uuid.uuid4().hex}",
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

        arrive_at = arrive_at or now + timedelta(days=2, hours=5)
        stop = PlanStop(
            organization_id=UUID(ORG_A),
            plan_version_id=plan.id,
            visit_id=UUID(visit_id),
            day_index=0,
            sequence=1,
            day_date=arrive_at.date(),
            arrive_at=arrive_at,
            depart_at=arrive_at + timedelta(hours=1),
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
            mode=mode,
            travel_minutes=600,
            distance_km=900,
            source="test-flight-estimate",
            stale=stale,
        )
        session.add(leg)
        session.flush()
        return str(plan.id), str(leg.id)


def flight_payload() -> dict:
    departure = datetime.now(UTC) + timedelta(days=2)
    return {
        "flight_designator": "lh123",
        "departure_airport_iata": "jfk",
        "arrival_airport_iata": "fra",
        "scheduled_departure_at": departure.isoformat(),
        "scheduled_arrival_at": (departure + timedelta(hours=7)).isoformat(),
        "departure_timezone": "America/New_York",
        "arrival_timezone": "Europe/Berlin",
        "booking_status": "TICKETED",
        "tracking_provider": "OpenSky",
        "tracking_reference": "DLH123",
    }


def cleanup(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))


def test_flight_segment_binding_is_explicit_versioned_and_auditable(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    try:
        plan_one_id, leg_one_id = create_plan(
            trip_id, visit_id, version_number=1, mode="AIR"
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment = created.json()
        assert segment["flight_designator"] == "LH123"
        assert segment["departure_airport_iata"] == "JFK"
        assert segment["arrival_airport_iata"] == "FRA"
        assert segment["tracking_provider"] == "opensky"
        assert segment["binding_state"] == "UNLINKED"
        segment_id = segment["id"]

        linked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": leg_one_id},
        )
        assert linked.status_code == 200, linked.text
        body = linked.json()
        assert body["binding_state"] == "CURRENT_PLAN"
        assert len(body["bindings"]) == 1
        assert body["bindings"][0]["plan_version_id"] == plan_one_id
        assert body["bindings"][0]["plan_travel_leg_id"] == leg_one_id
        assert body["bindings"][0]["status"] == "ACTIVE"
        assert body["bindings"][0]["linked_by_user_id"] == USER_A

        plan_two_id, leg_two_id = create_plan(
            trip_id, visit_id, version_number=2, mode="AIR"
        )
        listed = client.get(
            f"/v1/trips/{trip_id}/flight-segments", headers=headers()
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["binding_state"] == "STALE_PLAN_BINDING"

        rebound = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": leg_two_id},
        )
        assert rebound.status_code == 200, rebound.text
        rebound_body = rebound.json()
        assert rebound_body["binding_state"] == "CURRENT_PLAN"
        assert len(rebound_body["bindings"]) == 2
        assert rebound_body["bindings"][0]["status"] == "RELEASED"
        assert rebound_body["bindings"][0]["release_reason"] == "RELINKED"
        assert rebound_body["bindings"][0]["plan_version_id"] == plan_one_id
        assert rebound_body["bindings"][1]["status"] == "ACTIVE"
        assert rebound_body["bindings"][1]["plan_version_id"] == plan_two_id
        assert rebound_body["bindings"][1]["plan_travel_leg_id"] == leg_two_id

        unlinked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:unlink",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert unlinked.status_code == 200, unlinked.text
        assert unlinked.json()["binding_state"] == "UNLINKED"
        assert [item["status"] for item in unlinked.json()["bindings"]] == [
            "RELEASED",
            "RELEASED",
        ]
        assert unlinked.json()["bindings"][1]["release_reason"] == "USER_UNLINKED"
    finally:
        cleanup(trip_id, place_id)


def test_flight_segment_cannot_bind_to_ground_leg(client: TestClient) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    try:
        _plan_id, ground_leg_id = create_plan(
            trip_id, visit_id, version_number=1, mode="GROUND"
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment_id = created.json()["id"]

        invalid = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": ground_leg_id},
        )
        assert invalid.status_code == 422, invalid.text
        assert invalid.json()["error"]["code"] == "UNPROCESSABLE_ENTITY"

        listed = client.get(
            f"/v1/trips/{trip_id}/flight-segments", headers=headers()
        )
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["binding_state"] == "UNLINKED"
        assert listed.json()[0]["bindings"] == []
    finally:
        cleanup(trip_id, place_id)


def test_flight_segment_link_refreshes_stale_trip_plan_pointer_under_lock(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    try:
        plan_one_id, _leg_one_id = create_plan(
            trip_id, visit_id, version_number=1, mode="AIR"
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment_id = created.json()["id"]

        # Mimic the route preloading Trip before a concurrent plan activation.
        # A second session supersedes plan v1 and installs a new ACTIVE plan v2.
        with org_session(UUID(ORG_A)) as stale_session:
            stale_trip = stale_session.get(Trip, UUID(trip_id))
            assert stale_trip is not None
            assert stale_trip.active_plan_version_id == UUID(plan_one_id)

            plan_two_id, leg_two_id = create_plan(
                trip_id, visit_id, version_number=2, mode="AIR"
            )

            # The locking query must refresh the already-loaded Trip identity-map
            # entry; otherwise this could bind against the now-SUPERSEDED v1.
            flight_segment_service.link_flight_segment_to_active_plan_leg(
                stale_session,
                UUID(ORG_A),
                UUID(trip_id),
                UUID(segment_id),
                UUID(leg_two_id),
                actor_user_id=UUID(USER_A),
            )
            bindings = flight_segment_service.list_segment_bindings(
                stale_session,
                UUID(ORG_A),
                UUID(trip_id),
                UUID(segment_id),
            )
            active = [binding for binding in bindings if binding.status == "ACTIVE"]
            assert len(active) == 1
            assert active[0].plan_version_id == UUID(plan_two_id)
            assert active[0].plan_travel_leg_id == UUID(leg_two_id)
    finally:
        cleanup(trip_id, place_id)


def test_open_sky_tracking_requires_explicit_segment_provider_reference_and_current_binding(
    client: TestClient,
    monkeypatch,
) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    try:
        plan_id, leg_id = create_plan(trip_id, visit_id, version_number=1, mode="AIR")
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment_id = UUID(created.json()["id"])
        linked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": leg_id},
        )
        assert linked.status_code == 200, linked.text

        class StubOpenSky:
            name = "opensky"

            def fetch_by_callsign(self, callsign: str):
                return [
                    FlightState(
                        icao24="3c675a",
                        callsign=callsign,
                        origin_country="Germany",
                        latitude=52.52,
                        longitude=13.405,
                        baro_altitude_m=11887.2,
                        velocity_ms=241.5,
                        on_ground=False,
                        last_contact=datetime.now(UTC),
                    )
                ]

        monkeypatch.setattr(
            signal_pull_service, "get_flight_provider", lambda: StubOpenSky()
        )
        pulled = client.post(
            "/v1/integrations/signals:pull-flight",
            headers=headers(),
            json={
                "trip_id": trip_id,
                "flight_segment_id": str(segment_id),
            },
        )
        assert pulled.status_code == 200, pulled.text
        assert pulled.json()["flight_segment_id"] == str(segment_id)
        assert pulled.json()["callsign"] == "DLH123"

        with org_session(UUID(ORG_A)) as session:
            signal = session.scalars(
                select(ExternalSignal).where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.subject_ref == trip_id,
                    ExternalSignal.signal_type == "FLIGHT_STATUS",
                )
            ).one()
            assert signal.payload["flight_segment_id"] == str(segment_id)
            assert signal.dedupe_key.startswith(f"opensky:{trip_id}:{segment_id}:")
            current_trip = session.get(Trip, UUID(trip_id))
            assert current_trip is not None
            impact = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), current_trip, signal
            )
            assert impact["status"] == "TRACKING_MATCHED_TO_CURRENT_AIR_LEG"
            assert impact["plan_version_id"] == plan_id
            assert impact["linked_air_leg_ids"] == [leg_id]
            assert impact["flight_designator"] == "LH123"
            assert impact["operational_delay_assessment"] == (
                "NOT_DERIVABLE_FROM_AIRCRAFT_TELEMETRY"
            )
            assert impact["telemetry_snapshot"]["on_ground"] is False

        legacy_signal = ExternalSignal(
            id=uuid.uuid4(),
            organization_id=UUID(ORG_A),
            provider="opensky",
            signal_type="FLIGHT_STATUS",
            subject_type="TRIP",
            subject_ref=trip_id,
            payload={"callsign": "DLH123", "icao24": "3c675a"},
            confidence=0.95,
            observed_at=datetime.now(UTC),
            valid_from=datetime.now(UTC),
            valid_until=datetime.now(UTC) + timedelta(hours=1),
            dedupe_key=f"legacy-unlinked:{uuid.uuid4().hex}",
        )
        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, UUID(trip_id))
            assert trip is not None
            impact = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), trip, legacy_signal
            )
            assert impact["status"] == "AIR_LEGS_REQUIRE_EXPLICIT_LINK"
            assert impact["linked_air_leg_count"] == 0
    finally:
        cleanup(trip_id, place_id)


def test_open_sky_tracking_rejects_provider_and_reference_mismatch(
    client: TestClient,
) -> None:
    trip_id, place_id, _visit_id = create_trip_and_place(client)
    try:
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=flight_payload(),
        )
        assert created.status_code == 201, created.text
        segment_id = UUID(created.json()["id"])

        class StubProvider:
            def __init__(self, name: str):
                self.name = name

            def fetch_by_callsign(self, callsign: str):
                pytest.fail("provider mismatch must be rejected before network lookup")

        with org_session(UUID(ORG_A)) as session:
            with pytest.raises(Unprocessable, match="tracking_provider"):
                signal_pull_service.pull_flight(
                    session,
                    ORG_A,
                    trip_id=UUID(trip_id),
                    flight_segment_id=segment_id,
                    provider=StubProvider("other-provider"),
                )
            with pytest.raises(Unprocessable, match="tracking_reference"):
                signal_pull_service.pull_flight(
                    session,
                    ORG_A,
                    trip_id=UUID(trip_id),
                    flight_segment_id=segment_id,
                    callsign="BAW456",
                    provider=StubProvider("opensky"),
                )
            assert session.scalar(
                select(ExternalSignal.id).where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.subject_ref == trip_id,
                    ExternalSignal.signal_type == "FLIGHT_STATUS",
                )
            ) is None
    finally:
        cleanup(trip_id, place_id)


def test_provider_operational_update_propagates_delay_through_plan_buffers_and_windows(
    client: TestClient,
) -> None:
    trip_id, place_id, first_visit_id = create_trip_and_place(client)
    provider = "sample-airline-feed"
    event_id = f"ops-{uuid.uuid4().hex}"
    signal_id = None
    try:
        booking = flight_payload()
        scheduled_arrival = datetime.fromisoformat(booking["scheduled_arrival_at"])
        first_planned_arrival = scheduled_arrival + timedelta(minutes=30)
        second_planned_arrival = first_planned_arrival + timedelta(minutes=120)
        with org_session(UUID(ORG_A)) as session:
            first_visit = session.get(Visit, UUID(first_visit_id))
            assert first_visit is not None
            first_visit.time_window_start = first_planned_arrival - timedelta(minutes=5)
            first_visit.time_window_end = first_planned_arrival + timedelta(minutes=45)
            second_visit = Visit(
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                place_id=UUID(place_id),
                title="Second customer appointment",
                service_minutes=60,
                time_window_start=second_planned_arrival - timedelta(minutes=5),
                time_window_end=second_planned_arrival + timedelta(minutes=20),
                priority=1,
                movement_mode="GROUND",
                is_immovable=True,
                status="PLANNED",
            )
            session.add(second_visit)
            session.flush()
            second_visit_id = str(second_visit.id)

        plan_id, air_leg_id = create_plan(
            trip_id,
            first_visit_id,
            version_number=1,
            mode="AIR",
            arrive_at=first_planned_arrival,
            stale=False,
        )
        with org_session(UUID(ORG_A)) as session:
            first_stop = session.scalars(
                select(PlanStop).where(
                    PlanStop.organization_id == UUID(ORG_A),
                    PlanStop.plan_version_id == UUID(plan_id),
                    PlanStop.visit_id == UUID(first_visit_id),
                )
            ).one()
            second_stop = PlanStop(
                organization_id=UUID(ORG_A),
                plan_version_id=UUID(plan_id),
                visit_id=UUID(second_visit_id),
                day_index=0,
                sequence=2,
                day_date=second_planned_arrival.date(),
                arrive_at=second_planned_arrival,
                depart_at=second_planned_arrival + timedelta(hours=1),
                travel_minutes=30,
                service_minutes=60,
                travel_source="test-schedule",
            )
            session.add(second_stop)
            session.flush()
            ground_leg = PlanTravelLeg(
                organization_id=UUID(ORG_A),
                plan_version_id=UUID(plan_id),
                from_stop_id=first_stop.id,
                to_stop_id=second_stop.id,
                day_index=0,
                mode="GROUND",
                travel_minutes=30,
                distance_km=15,
                source="test-ground-transfer",
                stale=False,
            )
            session.add(ground_leg)
            session.flush()
            original_trip_version = session.get(Trip, UUID(trip_id)).version
            original_active_plan_id = session.get(Trip, UUID(trip_id)).active_plan_version_id

        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=booking,
        )
        assert created.status_code == 201, created.text
        flight_segment_id = created.json()["id"]
        linked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{flight_segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": air_leg_id},
        )
        assert linked.status_code == 200, linked.text

        observed_at = datetime.now(UTC)
        operational_update = {
            "flight_segment_id": flight_segment_id,
            "provider_event_id": event_id,
            "update_type": "ARRIVAL_DELAY",
            "flight_designator": "LH123",
            "departure_airport_iata": "JFK",
            "arrival_airport_iata": "FRA",
            "estimated_arrival_at": (scheduled_arrival + timedelta(minutes=90)).isoformat(),
            "source_payload_sha256": "a" * 64,
        }
        signal_body = {
            "provider": provider,
            "signal_type": "FLIGHT_STATUS",
            "subject_type": "TRIP",
            "subject_ref": trip_id,
            "payload": {
                "title": "LH123 estimated arrival delayed",
                "summary": "Provider reports a 90 minute arrival delay",
                "severity": "HIGH",
                "flight_operational_update": operational_update,
            },
            "confidence": 0.95,
            "observed_at": observed_at.isoformat(),
            "valid_until": (observed_at + timedelta(hours=24)).isoformat(),
            "dedupe_key": f"flight_ops:{event_id}",
        }
        accepted = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json=signal_body,
        )
        assert accepted.status_code == 201, accepted.text
        signal_id = accepted.json()["id"]

        # A caller must not be able to re-wrap an old immutable event ID in a
        # fresh ExternalSignal envelope with a later observed_at / valid_until.
        forged_reference = {
            "provider": provider,
            "signal_type": "FLIGHT_STATUS",
            "subject_type": "TRIP",
            "subject_ref": trip_id,
            "payload": {
                "summary": "forged replay",
                "operational_update_id": accepted.json()["payload"]["operational_update_id"],
                "operational_update_content_sha256": accepted.json()["payload"]["operational_update_content_sha256"],
                "operational_update_type": "ARRIVAL_DELAY",
            },
            "confidence": 0.95,
            "observed_at": (observed_at + timedelta(days=30)).isoformat(),
            "valid_until": (observed_at + timedelta(days=31)).isoformat(),
            "dedupe_key": f"flight_ops:{event_id}:forged",
        }
        forged = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json=forged_reference,
        )
        assert forged.status_code == 400, forged.text
        assert "server-managed" in forged.text

        replay = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json=signal_body,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == signal_id

        conflicting_body = {
            **signal_body,
            "payload": {
                **signal_body["payload"],
                "flight_operational_update": {
                    **operational_update,
                    "estimated_arrival_at": (
                        scheduled_arrival + timedelta(minutes=100)
                    ).isoformat(),
                },
            },
        }
        conflict = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json=conflicting_body,
        )
        assert conflict.status_code == 409, conflict.text

        mismatched_body = {
            **signal_body,
            "dedupe_key": f"flight_ops:{event_id}-mismatch",
            "payload": {
                **signal_body["payload"],
                "flight_operational_update": {
                    **operational_update,
                    "provider_event_id": f"{event_id}-mismatch",
                    "flight_designator": "BA123",
                },
            },
        }
        mismatched = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json=mismatched_body,
        )
        assert mismatched.status_code == 422, mismatched.text

        opensky_body = {
            **signal_body,
            "provider": "opensky",
            "dedupe_key": f"flight_ops:{event_id}-telemetry",
            "payload": {
                **signal_body["payload"],
                "flight_operational_update": {
                    **operational_update,
                    "provider_event_id": f"{event_id}-telemetry",
                },
            },
        }
        opensky_rejected = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json=opensky_body,
        )
        assert opensky_rejected.status_code == 400, opensky_rejected.text
        assert opensky_rejected.json()["error"]["code"] == "VALIDATION_ERROR"

        with org_session(UUID(ORG_A)) as session:
            stored_count = session.scalar(
                select(func.count())
                .select_from(FlightOperationalUpdate)
                .where(
                    FlightOperationalUpdate.organization_id == UUID(ORG_A),
                    FlightOperationalUpdate.trip_id == UUID(trip_id),
                    FlightOperationalUpdate.provider_event_id == event_id,
                )
            )
            assert stored_count == 1
            trip = session.get(Trip, UUID(trip_id))
            assert trip is not None
            signal = session.get(ExternalSignal, UUID(signal_id))
            assert signal is not None

            # Defence in depth: even if an envelope is corrupted after ingestion,
            # impact assessment must not trust a digest or time window that no
            # longer matches the append-only operational event.
            original_payload = signal.payload
            signal.payload = {
                **original_payload,
                "operational_update_content_sha256": "0" * 64,
            }
            tampered_digest = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), trip, signal
            )
            assert tampered_digest["status"] == "OPERATIONAL_UPDATE_EVIDENCE_MISMATCH"

            signal.payload = {
                **original_payload,
                "flight_operational_update": {
                    **original_payload["flight_operational_update"],
                    "estimated_arrival_at": (
                        scheduled_arrival + timedelta(minutes=95)
                    ).isoformat(),
                },
            }
            tampered_event = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), trip, signal
            )
            assert tampered_event["status"] == "OPERATIONAL_UPDATE_EVIDENCE_MISMATCH"

            signal.payload = original_payload
            original_observed_at = signal.observed_at
            signal.observed_at = original_observed_at + timedelta(minutes=1)
            tampered_window = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), trip, signal
            )
            assert tampered_window["status"] == "OPERATIONAL_UPDATE_ENVELOPE_MISMATCH"
            signal.observed_at = original_observed_at

            impact = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), trip, signal
            )
            assert impact["status"] == "OPERATIONAL_DELAY_ASSESSED"
            assert impact["flight_arrival_delta_minutes"] == 90
            assert impact["post_landing_buffer_minutes"] == 30
            assert impact["buffer_consumed_minutes"] == 30
            assert impact["delay_at_first_visit_minutes"] == 60
            assert impact["affected_visit_ids"] == [first_visit_id, second_visit_id]
            assert impact["missed_visit_ids"] == [first_visit_id, second_visit_id]
            assert impact["propagation_complete"] is True
            assert impact["plan_mutated"] is False
            assert [item["delay_minutes"] for item in impact["stop_impacts"]] == [60, 30]
            assert [item["appointment_status"] for item in impact["stop_impacts"]] == [
                "MISSED_WINDOW",
                "MISSED_WINDOW",
            ]
            assert impact["related_travel_legs"][0]["schedule_buffer_minutes"] == 30
            assert impact["related_travel_legs"][0]["delay_after_buffer_minutes"] == 30
            assert trip.version == original_trip_version
            assert trip.active_plan_version_id == original_active_plan_id == UUID(plan_id)
            assert session.scalar(
                select(func.count())
                .select_from(PlanStop)
                .where(PlanStop.plan_version_id == UUID(plan_id))
            ) == 2

            # Removing the server-owned operational event link must suppress
            # this signal instead of falling back to generic telemetry handling.
            original_signal_payload = signal.payload
            signal.payload = {
                key: value
                for key, value in original_signal_payload.items()
                if key not in {
                    "operational_update_id",
                    "flight_operational_update",
                }
            }
            invalid_report = proactive_service.scan_trip(
                session, UUID(ORG_A), UUID(trip_id)
            )
            assert invalid_report.errors == []
            assert invalid_report.suggestions == 0

            # Provider text cannot choose urgency: actual missed appointments
            # drive HIGH severity even if an envelope claims LOW severity.
            signal.payload = {
                **original_signal_payload,
                "title": "No operational impact",
                "severity": "LOW",
            }
            report = proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            assert report.errors == [], report.errors

            suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.organization_id == UUID(ORG_A),
                    ProactiveSuggestion.trip_id == UUID(trip_id),
                    ProactiveSuggestion.suggestion_type == "FLIGHT_STATUS_ALERT",
                )
            ).one()
            assert suggestion.title == "LH123 到达延误"
            assert suggestion.severity == "HIGH"
            evidence_kinds = {item["kind"] for item in suggestion.evidence}
            assert "FLIGHT_OPERATIONAL_UPDATE" in evidence_kinds
            assert "PLAN_STOP_IMPACT" in evidence_kinds
            assert "PLAN_TRAVEL_LEG_BUFFER" in evidence_kinds
            params = suggestion.proposed_action["params"]
            assessment = params["impact_assessment"]
            assert assessment["missed_visit_ids"] == [first_visit_id, second_visit_id]
            assert assessment["plan_mutated"] is False

            recovery = params["recovery_brief"]
            assert recovery["agent_type"] == "APPOINTMENT_RECOVERY"
            assert recovery["execution_mode"] == "DETERMINISTIC"
            assert recovery["status"] == "RECOVERY_REQUIRED"
            assert len(recovery["actions"]) == 2
            assert all(
                item["action_code"] == "REQUEST_CUSTOMER_RESCHEDULE_WINDOW"
                for item in recovery["actions"]
            )
            assert all(item["candidate_windows"] == [] for item in recovery["actions"])
            assert all(
                item["customer_message"]["status"] == "NOT_DRAFTED"
                for item in recovery["actions"]
            )
            assert recovery["guardrails"] == {
                "human_decision_required": True,
                "plan_mutated": False,
                "booking_changed": False,
                "customer_contacted": False,
                "customer_message_sent": False,
                "candidate_windows_inferred": False,
                "candidate_windows": [],
            }

            workflow_id = UUID(params["recovery_workflow_id"])
            agent_run_id = UUID(params["recovery_agent_run_id"])
            recovery_workflow = session.get(WorkflowInstance, workflow_id)
            recovery_run = session.get(AgentRun, agent_run_id)
            assert recovery_workflow is not None
            assert recovery_workflow.workflow_type == "APPOINTMENT_RECOVERY"
            assert recovery_workflow.status == "COMPLETED"
            assert recovery_workflow.trip_id == UUID(trip_id)
            assert recovery_workflow.payload["source_suggestion_id"] == str(suggestion.id)
            assert recovery_workflow.payload["agent_run_id"] == str(agent_run_id)
            assert recovery_run is not None
            assert recovery_run.workflow_id == workflow_id
            assert recovery_run.agent_type == "APPOINTMENT_RECOVERY"
            assert recovery_run.status == "SUCCEEDED"
            assert recovery_run.input["content_sha256"] == assessment["content_sha256"]
            assert recovery_run.input["trip_version"] == assessment["trip_version"]
            assert recovery_run.input["plan_trip_version"] == assessment["plan_trip_version"]
            assert recovery_run.input["plan_input_current"] is True
            assert recovery_run.output == recovery
            assert {item["kind"] for item in suggestion.evidence}.issuperset(
                {
                    "APPOINTMENT_RECOVERY_WORKFLOW",
                    "APPOINTMENT_RECOVERY_AGENT_RUN",
                }
            )

        # Trip Risk must reuse the same normalized flight event and merge the
        # linked suggestion rather than creating a second operational-risk item.
        risk_reconciliation = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert risk_reconciliation.status_code == 200, risk_reconciliation.text
        assert risk_reconciliation.json()["cases_created"] == 1
        assert risk_reconciliation.json()["errors"] == []
        risk_feed = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert risk_feed.status_code == 200, risk_feed.text
        assert len(risk_feed.json()["items"]) == 1
        risk_case = risk_feed.json()["items"][0]
        assert risk_case["case_key"] == f"external_signal:{signal_id}"
        assert risk_case["risk_type"] == "FLIGHT"
        assert risk_case["severity"] == "HIGH"
        assert {
            "PROACTIVE_SUGGESTION",
            "EXTERNAL_SIGNAL",
        } <= {item["type"] for item in risk_case["source_refs"]}
        risk_impact = risk_case["context"]["external_signal_assessments"][0]["impact_assessment"]
        assert risk_impact["status"] == "OPERATIONAL_DELAY_ASSESSED"
        assert risk_impact["missed_visit_ids"] == [first_visit_id, second_visit_id]
        risk_evidence_types = {
            item.get("type") or item.get("kind")
            for item in risk_case["evidence_refs"]
        }
        assert {
            "EXTERNAL_SIGNAL_PAYLOAD",
            "IMPACT_ASSESSMENT",
            "FLIGHT_OPERATIONAL_UPDATE",
            "FLIGHT_SEGMENT",
            "FLIGHT_SEGMENT_PLAN_BINDING",
            "PLAN_STOP_IMPACT",
            "PLAN_TRAVEL_LEG",
        } <= risk_evidence_types

        risk_replay = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert risk_replay.status_code == 200, risk_replay.text
        assert risk_replay.json()["cases_created"] == 0
        assert risk_replay.json()["cases_updated"] == 0

        # ACTIVE is a lifecycle label, not proof that a plan still represents
        # current trip inputs. A plan change increments Trip.version before a
        # replacement PlanVersion is generated and activated.
        with org_session(UUID(ORG_A)) as session:
            persisted_trip = session.get(Trip, UUID(trip_id))
            persisted_signal = session.get(ExternalSignal, UUID(signal_id))
            persisted_plan = session.get(PlanVersion, UUID(plan_id))
            assert persisted_trip is not None
            assert persisted_signal is not None
            assert persisted_plan is not None
            original_plan_trip_version = persisted_plan.trip_version
            persisted_trip.version += 1
            session.flush()
            stale_impact = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), persisted_trip, persisted_signal
            )
            assert stale_impact["status"] == "STALE_ACTIVE_PLAN_INPUT"
            assert stale_impact["trip_version"] == original_plan_trip_version + 1
            assert stale_impact["plan_trip_version"] == original_plan_trip_version
            assert stale_impact["plan_input_current"] is False

        stale_reconciliation = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert stale_reconciliation.status_code == 200, stale_reconciliation.text
        stale_feed = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert stale_feed.status_code == 200, stale_feed.text
        stale_case = next(
            item for item in stale_feed.json()["items"]
            if item["case_key"] == f"external_signal:{signal_id}"
        )
        stale_readiness = stale_case["context"]["recovery_readiness"]
        assert stale_readiness["status"] == "STALE_BRIEF"
        assert stale_readiness["integrity_status"] == "VERIFIED"
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.agent_type == "APPOINTMENT_RECOVERY",
                    AgentRun.input["trip_id"].as_string() == trip_id,
                )
            )
            session.execute(
                delete(ExternalSignal).where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.subject_ref == trip_id,
                    ExternalSignal.signal_type == "FLIGHT_STATUS",
                )
            )
        cleanup(trip_id, place_id)


def test_departure_delay_without_arrival_estimate_routes_visits_to_recovery_review(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    event_id = f"departure-only-{uuid.uuid4().hex}"
    try:
        booking = flight_payload()
        scheduled_departure = datetime.fromisoformat(booking["scheduled_departure_at"])
        plan_id, air_leg_id = create_plan(
            trip_id, visit_id, version_number=1, mode="AIR", stale=False
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=booking,
        )
        assert created.status_code == 201, created.text
        segment_id = created.json()["id"]
        linked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": air_leg_id},
        )
        assert linked.status_code == 200, linked.text

        observed_at = datetime.now(UTC)
        operational_update = {
            "flight_segment_id": segment_id,
            "provider_event_id": event_id,
            "update_type": "DEPARTURE_DELAY",
            "flight_designator": "LH123",
            "departure_airport_iata": "JFK",
            "arrival_airport_iata": "FRA",
            "estimated_departure_at": (
                scheduled_departure + timedelta(minutes=45)
            ).isoformat(),
        }
        signal_response = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json={
                "provider": "sample-airline-feed",
                "signal_type": "FLIGHT_STATUS",
                "subject_type": "TRIP",
                "subject_ref": trip_id,
                "payload": {
                    "summary": "Provider reports departure delay without arrival estimate",
                    "flight_operational_update": operational_update,
                },
                "confidence": 0.9,
                "observed_at": observed_at.isoformat(),
                "valid_until": (observed_at + timedelta(hours=24)).isoformat(),
                "dedupe_key": f"flight_ops:{event_id}",
            },
        )
        assert signal_response.status_code == 201, signal_response.text
        signal_id = signal_response.json()["id"]

        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, UUID(trip_id))
            signal = session.get(ExternalSignal, UUID(signal_id))
            assert trip is not None
            assert signal is not None
            impact = external_impact_service.assess_flight_signal_impact(
                session, UUID(ORG_A), trip, signal
            )
            assert impact["status"] == "ARRIVAL_ESTIMATE_MISSING"
            assert impact["departure_delay_minutes"] == 45
            assert impact["unassessed_visit_ids"] == [visit_id]
            assert impact["propagation_complete"] is False
            assert impact["stop_impacts"][0]["appointment_status"] == "UNASSESSED"
            assert impact["stop_impacts"][0]["reason"] == "ARRIVAL_ESTIMATE_MISSING"

            report = proactive_service.scan_trip(
                session,
                UUID(ORG_A),
                UUID(trip_id),
                now=observed_at + timedelta(seconds=2),
            )
            assert report.errors == []
            assert report.suggestions == 1
            suggestions = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.organization_id == UUID(ORG_A),
                    ProactiveSuggestion.trip_id == UUID(trip_id),
                    ProactiveSuggestion.suggestion_type == "FLIGHT_STATUS_ALERT",
                )
            ).all()
            suggestion = next(
                item
                for item in suggestions
                if item.proposed_action["params"]["signal_id"] == signal_id
            )
            recovery = suggestion.proposed_action["params"]["recovery_brief"]
            assert recovery["status"] == "REVIEW_REQUIRED"
            assert recovery["unassessed_visit_ids"] == [visit_id]
            assert recovery["actions"][0]["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
            assert recovery["actions"][0]["decision_status"] == "DATA_REVALIDATION_REQUIRED"
            assert recovery["guardrails"]["plan_mutated"] is False
            assert recovery["guardrails"]["customer_contacted"] is False
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.agent_type == "APPOINTMENT_RECOVERY",
                    AgentRun.input["trip_id"].as_string() == trip_id,
                )
            )
            session.execute(
                delete(ExternalSignal).where(
                    ExternalSignal.organization_id == UUID(ORG_A),
                    ExternalSignal.subject_ref == trip_id,
                    ExternalSignal.signal_type == "FLIGHT_STATUS",
                )
            )
        cleanup(trip_id, place_id)

def test_accepted_recovery_follow_up_reassesses_with_fresh_flight_and_attested_window(
    client: TestClient,
) -> None:
    trip_id, place_id, visit_id = create_trip_and_place(client)
    event_prefix = f"reassess-{uuid.uuid4().hex}"
    follow_up_id: str | None = None
    try:
        booking = flight_payload()
        scheduled_departure = datetime.fromisoformat(booking["scheduled_departure_at"])
        scheduled_arrival = datetime.fromisoformat(booking["scheduled_arrival_at"])
        plan_id, air_leg_id = create_plan(
            trip_id, visit_id, version_number=1, mode="AIR",
            arrive_at=scheduled_arrival + timedelta(hours=2), stale=False,
        )
        created = client.post(
            f"/v1/trips/{trip_id}/flight-segments",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=booking,
        )
        assert created.status_code == 201, created.text
        segment_id = created.json()["id"]
        linked = client.post(
            f"/v1/trips/{trip_id}/flight-segments/{segment_id}:link",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={"plan_travel_leg_id": air_leg_id},
        )
        assert linked.status_code == 200, linked.text

        original_observed_at = datetime.now(UTC)
        original_event_id = f"{event_prefix}-missing-eta"
        original_response = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json={
                "provider": "sample-airline-feed",
                "signal_type": "FLIGHT_STATUS",
                "subject_type": "TRIP",
                "subject_ref": trip_id,
                "payload": {
                    "summary": "Departure delay without arrival estimate",
                    "flight_operational_update": {
                        "flight_segment_id": segment_id,
                        "provider_event_id": original_event_id,
                        "update_type": "DEPARTURE_DELAY",
                        "flight_designator": "LH123",
                        "departure_airport_iata": "JFK",
                        "arrival_airport_iata": "FRA",
                        "estimated_departure_at": (
                            scheduled_departure + timedelta(minutes=45)
                        ).isoformat(),
                    },
                },
                "confidence": 0.92,
                "observed_at": original_observed_at.isoformat(),
                "valid_until": (original_observed_at + timedelta(hours=24)).isoformat(),
                "dedupe_key": f"flight_ops:{original_event_id}",
            },
        )
        assert original_response.status_code == 201, original_response.text
        original_signal_id = original_response.json()["id"]

        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, UUID(trip_id))
            signal = session.get(ExternalSignal, UUID(original_signal_id))
            assert trip is not None and signal is not None
            scan = proactive_service.scan_trip(
                session, UUID(ORG_A), UUID(trip_id),
                now=original_observed_at + timedelta(seconds=2),
            )
            assert scan.errors == []
            assert scan.suggestions == 1
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(
                    ProactiveSuggestion.organization_id == UUID(ORG_A),
                    ProactiveSuggestion.trip_id == UUID(trip_id),
                    ProactiveSuggestion.suggestion_type == "FLIGHT_STATUS_ALERT",
                )
            ).one()
            recovery_run_id = suggestion.proposed_action["params"]["recovery_agent_run_id"]
            recovery_suggestion_id = suggestion.id

        initial_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert initial_reconcile.status_code == 200, initial_reconcile.text
        initial_feed = client.get(
            f"/v1/trips/{trip_id}/risk-cases",
            headers=headers(),
        )
        assert initial_feed.status_code == 200, initial_feed.text
        initial_case = next(
            item for item in initial_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        initial_readiness = initial_case["context"]["recovery_readiness"]
        assert initial_readiness["integrity_status"] == "VERIFIED"
        assert initial_readiness["status"] == "AWAITING_HUMAN_DECISION"
        assert initial_readiness["expected_action_count"] == 1
        assert initial_readiness["accepted_action_count"] == 0


        # A mutable Suggestion/AgentRun pair agreeing with each other is not enough:
        # the action manifest must also be reproducible from the current verified impact.
        with org_session(UUID(ORG_A)) as session:
            persisted_suggestion = session.get(
                ProactiveSuggestion, recovery_suggestion_id
            )
            persisted_run = session.get(AgentRun, UUID(recovery_run_id))
            assert persisted_suggestion is not None
            assert persisted_run is not None
            original_action = deepcopy(persisted_suggestion.proposed_action)
            original_brief = deepcopy(
                persisted_suggestion.proposed_action["params"]["recovery_brief"]
            )
            tampered_brief = deepcopy(original_brief)
            tampered_brief["actions"][0]["rationale"] = "Altered after AgentRun completion."
            tampered_action = deepcopy(original_action)
            tampered_action["params"]["recovery_brief"] = tampered_brief
            persisted_suggestion.proposed_action = tampered_action
            persisted_run.output = tampered_brief

        tampered_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert tampered_reconcile.status_code == 200, tampered_reconcile.text
        tampered_feed = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert tampered_feed.status_code == 200, tampered_feed.text
        tampered_case = next(
            item for item in tampered_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        tampered_readiness = tampered_case["context"]["recovery_readiness"]
        assert tampered_readiness["integrity_status"] == "FAILED"
        assert "RECOVERY_BRIEF_RECOMPUTATION_MISMATCH" in tampered_readiness["reasons"]

        with org_session(UUID(ORG_A)) as session:
            persisted_suggestion = session.get(
                ProactiveSuggestion, recovery_suggestion_id
            )
            persisted_run = session.get(AgentRun, UUID(recovery_run_id))
            assert persisted_suggestion is not None
            assert persisted_run is not None
            persisted_suggestion.proposed_action = original_action
            persisted_run.output = original_brief

        restored_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert restored_reconcile.status_code == 200, restored_reconcile.text
        restored_feed = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert restored_feed.status_code == 200, restored_feed.text
        restored_case = next(
            item for item in restored_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        assert restored_case["context"]["recovery_readiness"]["integrity_status"] == "VERIFIED"

        feed = client.get(f"/v1/visits/{visit_id}/follow-ups", headers=headers())
        assert feed.status_code == 200, feed.text
        candidates = [
            item for item in feed.json()["suggestions"]
            if item["source_type"] == "APPOINTMENT_RECOVERY"
        ]
        assert candidates
        candidate = candidates[0]
        assert candidate["action_code"] == "REVALIDATE_TRAVEL_CHAIN"
        accepted = client.post(
            f"/v1/visits/{visit_id}/follow-ups",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "source_type": "APPOINTMENT_RECOVERY",
                "decision": "ACCEPT",
                "source_agent_run_id": recovery_run_id,
                "source_index": candidate["source_index"],
            },
        )
        assert accepted.status_code == 201, accepted.text
        follow_up_id = accepted.json()["id"]
        accepted_risk_feed = client.get(
            f"/v1/trips/{trip_id}/risk-cases",
            headers=headers(),
        )
        assert accepted_risk_feed.status_code == 200, accepted_risk_feed.text
        accepted_case = next(
            item for item in accepted_risk_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        accepted_readiness = accepted_case["context"]["recovery_readiness"]
        assert accepted_readiness["status"] == "AWAITING_HUMAN_ACTION"
        assert accepted_readiness["accepted_action_count"] == 1
        assert accepted_readiness["actions"][0]["follow_up_id"] == follow_up_id

        request_payload = {
            "customer_window": {
                "start_at": (
                    scheduled_arrival + timedelta(hours=1, minutes=30)
                ).astimezone(ZoneInfo("Europe/Berlin")).isoformat(),
                "end_at": (
                    scheduled_arrival + timedelta(hours=4)
                ).astimezone(ZoneInfo("Europe/Berlin")).isoformat(),
                "timezone": "Europe/Berlin",
                "confirmation_method": "EMAIL",
                "confirmation_reference": "CRM-ACT-2026-117",
            }
        }
        no_new_evidence = client.post(
            f"/v1/visit-follow-ups/{follow_up_id}:reassess",
            headers=headers(key=str(uuid.uuid4())),
            json=request_payload,
        )
        assert no_new_evidence.status_code == 409, no_new_evidence.text

        trip_before = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        fresh_observed_at = original_observed_at + timedelta(minutes=3)
        fresh_event_id = f"{event_prefix}-arrival-eta"
        fresh_response = client.post(
            "/v1/integrations/signals",
            headers=headers(),
            json={
                "provider": "sample-airline-feed",
                "signal_type": "FLIGHT_STATUS",
                "subject_type": "TRIP",
                "subject_ref": trip_id,
                "payload": {
                    "summary": "Provider now supplies a later arrival estimate",
                    "flight_operational_update": {
                        "flight_segment_id": segment_id,
                        "provider_event_id": fresh_event_id,
                        "update_type": "ARRIVAL_DELAY",
                        "flight_designator": "LH123",
                        "departure_airport_iata": "JFK",
                        "arrival_airport_iata": "FRA",
                        "estimated_departure_at": (
                            scheduled_departure + timedelta(minutes=45)
                        ).isoformat(),
                        "estimated_arrival_at": (
                            scheduled_arrival + timedelta(minutes=45)
                        ).isoformat(),
                    },
                },
                "confidence": 0.96,
                "observed_at": fresh_observed_at.isoformat(),
                "valid_until": (fresh_observed_at + timedelta(hours=24)).isoformat(),
                "dedupe_key": f"flight_ops:{fresh_event_id}",
            },
        )
        assert fresh_response.status_code == 201, fresh_response.text

        reassessed = client.post(
            f"/v1/visit-follow-ups/{follow_up_id}:reassess",
            headers=headers(key=str(uuid.uuid4())),
            json=request_payload,
        )
        assert reassessed.status_code == 201, reassessed.text
        result = reassessed.json()
        assert result["outcome"] == "REASSESSED"
        assert result["decision"] == "PLAN_CHANGE_RECOMMENDATION_READY"
        assert result["flight_segment_id"] == segment_id
        assert result["customer_window_verification_status"] == "PARTIALLY_VERIFIED"
        assert result["plan_change_recommendation"]["requires_human_submission"] is True
        assert result["plan_change_recommendation"]["base_plan_version_id"] == plan_id
        assert result["guardrails"] == {
            "plan_mutated": False,
            "visit_mutated": False,
            "trip_mutated": False,
            "booking_changed": False,
            "customer_contacted": False,
            "customer_message_sent": False,
            "follow_up_completed": False,
            "customer_window_independently_verified": False,
        }
        assert result["evidence_ids"] and result["input_sha256"]

        reassessment_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert reassessment_reconcile.status_code == 200, reassessment_reconcile.text
        reassessed_risk_feed = client.get(
            f"/v1/trips/{trip_id}/risk-cases",
            headers=headers(),
        )
        assert reassessed_risk_feed.status_code == 200, reassessed_risk_feed.text
        reassessed_case = next(
            item for item in reassessed_risk_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        reassessed_readiness = reassessed_case["context"]["recovery_readiness"]
        reassessed_signal_ids = {
            item["id"]
            for item in reassessed_case["source_refs"]
            if item["type"] == "EXTERNAL_SIGNAL"
        }
        assert {
            original_signal_id,
            fresh_response.json()["id"],
        } <= reassessed_signal_ids
        assert (
            reassessed_readiness["status"] == "PLAN_CHANGE_HUMAN_SUBMISSION_REQUIRED"
        ), reassessed_readiness
        reassessment = reassessed_readiness["actions"][0]["reassessment"]
        assert reassessment["integrity_status"] == "VERIFIED"
        assert reassessment["decision"] == "PLAN_CHANGE_RECOMMENDATION_READY"
        assert reassessment["customer_window_verification_status"] == "PARTIALLY_VERIFIED"
        assert reassessed_case["status"] != "RESOLVED"

        replay = client.post(
            f"/v1/visit-follow-ups/{follow_up_id}:reassess",
            headers=headers(key=str(uuid.uuid4())),
            json=request_payload,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["agent_run_id"] == result["agent_run_id"]

        # A successful reassessment is not a permanent authorization: its provider envelope
        # must still be valid at the exact moment the plan change is submitted.
        fresh_signal_id = fresh_response.json()["id"]
        with org_session(UUID(ORG_A)) as session:
            fresh_signal = session.get(ExternalSignal, UUID(fresh_signal_id))
            assert fresh_signal is not None
            valid_until_before_expiry = fresh_signal.valid_until
            fresh_signal.valid_until = datetime.now(UTC) - timedelta(seconds=1)

        expired_signal_submit = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json={
                "base_plan_version_id": result["plan_change_recommendation"]["base_plan_version_id"],
                "base_trip_version": result["plan_change_recommendation"]["base_trip_version"],
                "source": "APPOINTMENT_RECOVERY",
                "operation_type": "CHANGE_VISIT_TIME",
                "payload": {
                    "visit_id": visit_id,
                    "recovery_follow_up_id": follow_up_id,
                    "new_window_start": result["plan_change_recommendation"]["new_window_start"],
                    "new_window_end": result["plan_change_recommendation"]["new_window_end"],
                },
                "reason": result["plan_change_recommendation"]["reason"],
            },
        )
        assert expired_signal_submit.status_code == 409, expired_signal_submit.text

        with org_session(UUID(ORG_A)) as session:
            fresh_signal = session.get(ExternalSignal, UUID(fresh_signal_id))
            assert fresh_signal is not None
            fresh_signal.valid_until = valid_until_before_expiry

        recommendation = result["plan_change_recommendation"]
        plan_change_payload = {
            "base_plan_version_id": recommendation["base_plan_version_id"],
            "base_trip_version": recommendation["base_trip_version"],
            "source": "APPOINTMENT_RECOVERY",
            "operation_type": "CHANGE_VISIT_TIME",
            "payload": {
                "visit_id": visit_id,
                "recovery_follow_up_id": follow_up_id,
                "new_window_start": recommendation["new_window_start"],
                "new_window_end": recommendation["new_window_end"],
            },
            "reason": recommendation["reason"],
        }

        # A client cannot alter the proposed appointment window while reusing the same evidence.
        forged_payload = {
            **plan_change_payload,
            "payload": {
                **plan_change_payload["payload"],
                "new_window_start": (
                    datetime.fromisoformat(
                        recommendation["new_window_start"].replace("Z", "+00:00")
                    ) + timedelta(minutes=1)
                ).isoformat(),
            },
        }
        forged = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=forged_payload,
        )
        assert forged.status_code == 409, forged.text

        wrong_offset_payload = {
            **plan_change_payload,
            "payload": {
                **plan_change_payload["payload"],
                "new_window_start": datetime.fromisoformat(
                    recommendation["new_window_start"]
                ).astimezone(UTC).isoformat(),
            },
        }
        wrong_offset = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=wrong_offset_payload,
        )
        assert wrong_offset.status_code == 409, wrong_offset.text
        assert recommendation["timezone"] == "Europe/Berlin"
        assert datetime.fromisoformat(
            recommendation["new_window_start"]
        ).utcoffset() == timedelta(hours=1)

        # Reassessment approval is not trusted independently of its stored Evidence hash.
        with org_session(UUID(ORG_A)) as session:
            attestation_evidence = session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "visit_follow_up",
                    Evidence.subject_id == UUID(follow_up_id),
                    Evidence.kind == "APPOINTMENT_WINDOW_ATTESTATION",
                    Evidence.ref == f"customer-window-attestation:{result['agent_run_id']}",
                )
            ).one()
            original_attestation_payload = dict(attestation_evidence.payload)
            tampered_attestation_payload = dict(original_attestation_payload)
            tampered_customer_window = dict(tampered_attestation_payload["customer_window"])
            tampered_customer_window["timezone"] = "UTC"
            tampered_attestation_payload["customer_window"] = tampered_customer_window
            attestation_evidence.payload = tampered_attestation_payload

        tampered_evidence_submit = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=plan_change_payload,
        )
        assert tampered_evidence_submit.status_code == 409, tampered_evidence_submit.text

        with org_session(UUID(ORG_A)) as session:
            attestation_evidence = session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "visit_follow_up",
                    Evidence.subject_id == UUID(follow_up_id),
                    Evidence.kind == "APPOINTMENT_WINDOW_ATTESTATION",
                    Evidence.ref == f"customer-window-attestation:{result['agent_run_id']}",
                )
            ).one()
            attestation_evidence.payload = original_attestation_payload

        submitted = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=plan_change_payload,
        )
        assert submitted.status_code == 202, submitted.text
        plan_change_result = submitted.json()
        assert plan_change_result["status"] == "ACCEPTED"
        assert plan_change_result["trip_version"] > recommendation["base_trip_version"]

        stale_recovery_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert stale_recovery_reconcile.status_code == 200, stale_recovery_reconcile.text
        stale_recovery_feed = client.get(
            f"/v1/trips/{trip_id}/risk-cases",
            headers=headers(),
        )
        assert stale_recovery_feed.status_code == 200, stale_recovery_feed.text
        stale_recovery_case = next(
            item for item in stale_recovery_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        stale_readiness = stale_recovery_case["context"]["recovery_readiness"]
        assert stale_readiness["status"] == "PLAN_CHANGE_OPTIMIZATION_PENDING"
        assert stale_readiness["integrity_status"] == "VERIFIED"
        assert stale_recovery_case["status"] != "RESOLVED"
        initial_plan_change = stale_readiness["actions"][0]["plan_change"]
        assert initial_plan_change["status"] == "PLAN_CHANGE_OPTIMIZATION_PENDING"
        assert initial_plan_change["workflow_status"] == "ACCEPTED"
        assert initial_plan_change["job_status"] == "PENDING"

        with org_session(UUID(ORG_A)) as session:
            plan_change_workflow = session.get(
                WorkflowInstance, UUID(plan_change_result["change_request_id"])
            )
            assert plan_change_workflow is not None
            assert plan_change_workflow.workflow_type == "PLAN_CHANGE"
            assert plan_change_workflow.payload["source"] == "APPOINTMENT_RECOVERY"
            assert plan_change_workflow.payload["recovery_follow_up_id"] == follow_up_id
            assert (
                plan_change_workflow.payload["operation_payload"]["recovery_follow_up_id"]
                == follow_up_id
            )
            assert plan_change_workflow.payload["result_trip_version"] == plan_change_result["trip_version"]
            plan_job = session.get(BackgroundJob, plan_change_workflow.job_id)
            assert plan_job is not None
            assert plan_job.status == "PENDING"
            trip_row = session.get(Trip, UUID(trip_id))
            persisted_visit = session.get(Visit, UUID(visit_id))
            assert trip_row is not None and persisted_visit is not None
            candidate_plan = PlanVersion(
                organization_id=UUID(ORG_A),
                trip_id=trip_row.id,
                version_number=2,
                status="DRAFT",
                problem_hash=f"recovery-plan-change:{uuid.uuid4().hex}",
                solver="heuristic",
                objective_value=0.0,
                solution_status="FEASIBLE",
                trip_version=plan_change_result["trip_version"],
                validation={"feasible": True},
                unplaced=[],
                policy_violations=[],
                job_id=plan_job.id,
            )
            session.add(candidate_plan)
            session.flush()
            assert persisted_visit.time_window_start is not None
            assert persisted_visit.time_window_end is not None
            session.add(
                PlanStop(
                    organization_id=UUID(ORG_A),
                    plan_version_id=candidate_plan.id,
                    visit_id=persisted_visit.id,
                    day_index=0,
                    sequence=1,
                    day_date=persisted_visit.time_window_start.date(),
                    arrive_at=persisted_visit.time_window_start,
                    depart_at=persisted_visit.time_window_end,
                    travel_minutes=0,
                    service_minutes=persisted_visit.service_minutes,
                )
            )
            plan_job.status = "SUCCEEDED"
            plan_job.result = {
                "plan_version_id": str(candidate_plan.id),
                "version_number": candidate_plan.version_number,
            }
            plan_job.finished_at = datetime.now(UTC)
            candidate_plan_id = str(candidate_plan.id)

        generated_plan_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert generated_plan_reconcile.status_code == 200, generated_plan_reconcile.text
        generated_plan_feed = client.get(
            f"/v1/trips/{trip_id}/risk-cases", headers=headers()
        )
        assert generated_plan_feed.status_code == 200, generated_plan_feed.text
        generated_plan_case = next(
            item for item in generated_plan_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        generated_readiness = generated_plan_case["context"]["recovery_readiness"]
        assert generated_readiness["status"] == "PLAN_CHANGE_CANDIDATE_READY_FOR_ACTIVATION"
        generated_plan_state = generated_readiness["actions"][0]["plan_change"]
        assert generated_plan_state["job_status"] == "SUCCEEDED"
        assert generated_plan_state["candidate_plan_version_id"] == candidate_plan_id
        assert generated_plan_state["candidate_plan_status"] == "DRAFT"
        assert generated_plan_case["status"] != "RESOLVED"

        activated_plan = client.post(
            f"/v1/trips/{trip_id}/plans/{candidate_plan_id}:activate",
            headers=headers(
                **{
                    "Idempotency-Key": str(uuid.uuid4()),
                    "If-Match": str(plan_change_result["trip_version"]),
                }
            ),
        )
        assert activated_plan.status_code == 200, activated_plan.text
        assert activated_plan.json()["plan_version_id"] == candidate_plan_id

        activation_reconcile = client.post(
            f"/v1/trips/{trip_id}/risk-cases:reconcile",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
        )
        assert activation_reconcile.status_code == 200, activation_reconcile.text
        activated_feed = client.get(f"/v1/trips/{trip_id}/risk-cases", headers=headers())
        assert activated_feed.status_code == 200, activated_feed.text
        activated_case = next(
            item for item in activated_feed.json()["items"]
            if item["case_key"] == f"external_signal:{original_signal_id}"
        )
        activated_readiness = activated_case["context"]["recovery_readiness"]
        assert activated_readiness["status"] == "PLAN_CHANGE_ACTIVATED_REASSESSMENT_REQUIRED"
        activated_plan_state = activated_readiness["actions"][0]["plan_change"]
        assert activated_plan_state["status"] == "PLAN_CHANGE_ACTIVATED_REASSESSMENT_REQUIRED"
        assert activated_plan_state["candidate_plan_status"] == "ACTIVE"
        assert activated_plan_state["active_plan_version_id"] == candidate_plan_id
        assert activated_case["status"] != "RESOLVED"

        # A second request using the original recommendation/version must fail closed.
        duplicate_submission = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(**{"Idempotency-Key": str(uuid.uuid4())}),
            json=plan_change_payload,
        )
        assert duplicate_submission.status_code == 409, duplicate_submission.text

        read_feed = client.get(f"/v1/visits/{visit_id}/follow-ups", headers=headers())
        assert read_feed.status_code == 200, read_feed.text
        item = next(row for row in read_feed.json()["items"] if row["id"] == follow_up_id)
        assert item["latest_reassessment"]["decision"] == "PLAN_CHANGE_RECOMMENDATION_READY"
        assert any(event["event_type"] == "REASSESSED" for event in item["events"])
        assert item["status"] == "OPEN"
        after_trip = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        assert after_trip["version"] == plan_change_result["trip_version"]
        assert after_trip["version"] > trip_before["version"]

        with org_session(UUID(ORG_A)) as session:
            persisted_visit = session.get(Visit, UUID(visit_id))
            assert persisted_visit is not None
            assert persisted_visit.time_window_start is not None
            assert persisted_visit.time_window_end is not None
            assert persisted_visit.time_window_end > persisted_visit.time_window_start
            evidence = list(session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "visit_follow_up",
                    Evidence.subject_id == UUID(follow_up_id),
                )
            ))
            assert {row.kind for row in evidence}.issuperset({
                "FLIGHT_OPERATIONAL_UPDATE",
                "APPOINTMENT_RECOVERY_IMPACT_SNAPSHOT",
                "APPOINTMENT_WINDOW_ATTESTATION",
                "APPOINTMENT_RECOVERY_REASSESSMENT",
            })
            attestation = next(row for row in evidence if row.kind == "APPOINTMENT_WINDOW_ATTESTATION")
            assert attestation.verification_status == "PARTIALLY_VERIFIED"
            assert attestation.quality["externally_verified"] is False
    finally:
        if follow_up_id is not None:
            with org_session(UUID(ORG_A)) as session:
                session.execute(delete(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "visit_follow_up",
                    Evidence.subject_id == UUID(follow_up_id),
                ))
                session.execute(delete(VisitFollowUp).where(
                    VisitFollowUp.organization_id == UUID(ORG_A),
                    VisitFollowUp.id == UUID(follow_up_id),
                ))
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(AgentRun).where(
                AgentRun.organization_id == UUID(ORG_A),
                AgentRun.agent_type.in_([
                    "APPOINTMENT_RECOVERY", "APPOINTMENT_RECOVERY_REASSESSMENT"
                ]),
                AgentRun.input["trip_id"].as_string() == trip_id,
            ))
            session.execute(delete(WorkflowInstance).where(
                WorkflowInstance.organization_id == UUID(ORG_A),
                WorkflowInstance.trip_id == UUID(trip_id),
            ))
            session.execute(delete(BackgroundJob).where(
                BackgroundJob.organization_id == UUID(ORG_A),
                BackgroundJob.payload["trip_id"].as_string() == trip_id,
            ))
            session.execute(delete(ProactiveSuggestion).where(
                ProactiveSuggestion.organization_id == UUID(ORG_A),
                ProactiveSuggestion.trip_id == UUID(trip_id),
            ))
            session.execute(delete(ExternalSignal).where(
                ExternalSignal.organization_id == UUID(ORG_A),
                ExternalSignal.subject_ref == trip_id,
                ExternalSignal.signal_type == "FLIGHT_STATUS",
            ))
        cleanup(trip_id, place_id)


