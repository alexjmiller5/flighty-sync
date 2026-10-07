import json

import httpx
import pytest

from job.sync import Hub, SyncError, sync_flights


class Service:
    def __init__(self):
        self.rows = {}
        self.archive = {}
        self.calls = []
        self.fail_archive = False
        self.reject = False
        self.conflict = False

    def handle(self, request):
        path = request.url.path
        data = json.loads(request.content) if request.content and "/files/" not in path else None
        self.calls.append((request.method, path, data))
        if "/files/" in path:
            if self.fail_archive:
                return httpx.Response(500)
            if request.method == "PUT":
                self.archive[path] = request.content
            return (
                httpx.Response(200, content=self.archive[path])
                if path in self.archive
                else httpx.Response(404)
            )
        if path.endswith("/pull"):
            rows = sorted(self.rows.values(), key=lambda r: r["id"])
            if data.get("after"):
                rows = [r for r in rows if r["id"] > data["after"]]
            # Force pagination in all tests.
            return httpx.Response(
                200,
                json={"rows": rows[:1], "next_cursor": rows[0]["id"] if len(rows) > 1 else None},
            )
        if path.endswith("/insert"):
            row = data["rows"][0]
            if self.reject:
                return httpx.Response(
                    200, json={"inserted": [], "existing": [], "rejected": [{"id": row["id"]}]}
                )
            self.rows[row["id"]] = dict(row, hub_at="2026-01-01T00:00:00.000Z", deleted_at=None)
            return httpx.Response(
                200, json={"inserted": [row["id"]], "existing": [], "rejected": []}
            )
        if path.endswith("/patch"):
            if self.conflict:
                return httpx.Response(409, json={"error": "conflict"})
            self.rows[data["id"]].update(data["values"])
            return httpx.Response(
                200,
                json={
                    "id": data["id"],
                    "revision": {
                        "updated_at": "2026-01-02T00:00:00.000Z",
                        "hub_at": "2026-01-02T00:00:00.000Z",
                    },
                },
            )
        raise AssertionError(path)


def setup():
    service = Service()
    hub = Hub("https://example.test", "fixture", transport=httpx.MockTransport(service.handle))
    return service, hub


def row(identifier="flight"):
    return {
        "id": identifier,
        "source_id": identifier,
        "source_state": "present",
        "flight_number": "12",
        "source_payload": "{}",
        "travel_status": "unverified",
    }


def test_archive_before_writes_idempotency_and_preserve_user_fields():
    service, hub = setup()
    first = sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
    assert first["inserted"] == 1
    assert next(i for i, c in enumerate(service.calls) if c[0] == "PUT") < next(
        i for i, c in enumerate(service.calls) if c[1].endswith("/insert")
    )
    service.rows["flight"].update(trip_id="trip", travel_status="confirmed", txn_links="owned")
    second = sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
    assert second["unchanged"] == 1
    changed = row()
    changed["flight_number"] = "34"
    assert sync_flights(hub, [changed], table="flights", prefix="raw/flighty/")["updated"] == 1
    assert service.rows["flight"]["trip_id"] == "trip"
    assert service.rows["flight"]["travel_status"] == "confirmed"
    assert service.rows["flight"]["txn_links"] == "owned"


def test_paginate_and_preserve_tombstone_and_missing_source():
    service, hub = setup()
    service.rows = {
        "a": dict(row("a"), updated_at="x", hub_at="y", deleted_at="gone"),
        "z": dict(row("z"), updated_at="x", hub_at="y", deleted_at=None),
    }
    result = sync_flights(hub, [row("a")], table="flights", prefix="raw/flighty/")
    assert result["tombstones"] == 1
    assert service.rows["a"]["deleted_at"] == "gone"
    assert service.rows["z"]["source_state"] == "missing"
    assert service.rows["z"]["deleted_at"] is None


def test_empty_and_duplicate_source_fail_before_network():
    service, hub = setup()
    for rows in [[], [row(), row()]]:
        with pytest.raises(SyncError):
            sync_flights(hub, rows, table="flights", prefix="raw/flighty/")
    assert not service.calls


def test_failed_or_corrupt_archive_prevents_any_row_writes():
    service, hub = setup()
    service.fail_archive = True
    with pytest.raises((SyncError, httpx.HTTPError)):
        sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
    assert not any(c[1].endswith(("/insert", "/patch")) for c in service.calls)


def test_rejected_insert_and_revision_conflict_are_failures():
    service, hub = setup()
    service.reject = True
    with pytest.raises(SyncError):
        sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
    service.reject = False
    sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
    service.conflict = True
    changed = row()
    changed["flight_number"] = "changed"
    with pytest.raises(httpx.HTTPStatusError):
        sync_flights(hub, [changed], table="flights", prefix="raw/flighty/")
    assert service.rows["flight"]["flight_number"] == "12"


def test_refuse_redirect_and_non_https():
    with pytest.raises(ValueError):
        Hub("http://example.test", "fixture")
    hub = Hub(
        "https://example.test",
        "fixture",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(302, headers={"Location": "https://other.test"})
        ),
    )
    with pytest.raises(httpx.HTTPStatusError):
        hub.post("/v1/rows/pull", {})
