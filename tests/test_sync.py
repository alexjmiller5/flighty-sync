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
        if path.endswith("/push"):
            # Sparse last-write-wins, like the hub: stale rows are accepted unchanged.
            for pushed in data["rows"]:
                stored = self.rows[pushed["id"]]
                if pushed["updated_at"] > stored["updated_at"]:
                    stored.update({c: pushed[c] for c in data["columns"] if c in pushed})
            return httpx.Response(200, json={"upserted": len(data["rows"]), "rejected": []})
        if path.endswith("/patch"):
            if "deleted_at" in data["values"]:
                return httpx.Response(400, json={"error": "invalid_patch"})
            if self.conflict or self.rows[data["id"]].get("deleted_at"):
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


def stored(identifier, **values):
    clock = "2026-01-01T00:00:00.000Z"
    return {**row(identifier), "updated_at": clock, "hub_at": clock, "deleted_at": None, **values}


def mirror(hub, identifiers, complete=True):
    rows = [row(i) for i in identifiers]
    return sync_flights(hub, rows, table="flights", prefix="raw/flighty/", complete=complete)


def test_absent_flight_is_soft_deleted_with_user_fields_kept():
    service, hub = setup()
    service.rows = {i: stored(i) for i in "abcdefgh"}
    service.rows["a"]["source_state"] = "missing"
    service.rows["b"].update(travel_status="confirmed", trip_id="trip", notes="kept")
    # Two of eight is exactly the 25% shrink limit, which is still allowed.
    result = mirror(hub, "cdefgh")
    assert result["deleted"] == 2
    for gone in (service.rows["a"], service.rows["b"]):
        assert gone["deleted_at"] is not None
        assert gone["deleted_at"] == gone["updated_at"]
        assert gone["source_state"] == "missing"
        assert gone["source_archive_key"] == result["archive_key"]
    b = service.rows["b"]
    assert (b["travel_status"], b["trip_id"], b["notes"]) == ("confirmed", "trip", "kept")
    assert all(service.rows[i]["deleted_at"] is None for i in "cdefgh")
    assert mirror(hub, "cdefgh")["deleted"] == 0


def test_reappearing_flight_is_restored_with_fresh_source_and_user_fields():
    service, hub = setup()
    service.rows["a"] = stored(
        "a",
        flight_number="old",
        source_state="missing",
        deleted_at="2026-01-01T00:00:00.000Z",
        travel_status="not_flown",
        trip_id="trip",
        notes="kept",
    )
    result = mirror(hub, "a")
    a = service.rows["a"]
    assert result["restored"] == 1
    assert a["deleted_at"] is None
    assert (a["source_state"], a["flight_number"]) == ("present", "12")
    assert a["source_archive_key"] == result["archive_key"]
    assert (a["travel_status"], a["trip_id"], a["notes"]) == ("not_flown", "trip", "kept")


def test_restore_refuses_a_conflicting_source_identity():
    service, hub = setup()
    service.rows["a"] = stored("a", source_id="other", deleted_at="2026-01-01T00:00:00.000Z")
    with pytest.raises(SyncError, match="identity"):
        mirror(hub, "a")
    assert service.rows["a"]["deleted_at"] is not None


def test_shrink_over_limit_stops_before_any_row_write():
    service, hub = setup()
    service.rows = {i: stored(i) for i in "abcdefgh"}
    with pytest.raises(SyncError, match="shrink"):
        mirror(hub, "defgh" + "z")
    assert not any(c[1].endswith(("/insert", "/patch", "/push")) for c in service.calls)
    assert all(r["deleted_at"] is None for r in service.rows.values())


def test_incomplete_read_marks_missing_but_deletes_nothing():
    service, hub = setup()
    service.rows = {i: stored(i) for i in "ab"}
    result = mirror(hub, "b", complete=False)
    assert (result["deleted"], result["missing"]) == (0, 1)
    assert service.rows["a"]["deleted_at"] is None
    assert service.rows["a"]["source_state"] == "missing"


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


def test_existing_foreign_identity_is_not_overwritten():
    service, hub = setup()
    service.rows["flight"] = dict(
        row(), source_id="different", updated_at="x", hub_at="y", deleted_at=None
    )
    with pytest.raises(SyncError):
        sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
    assert not any(c[1].endswith("/patch") for c in service.calls)


def test_retained_file_mismatch_fails():
    service, hub = setup()
    original = hub.client
    hub.client = httpx.Client(
        base_url="https://example.test",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"corrupt")),
    )
    original.close()
    with pytest.raises(SyncError, match="readback"):
        sync_flights(hub, [row()], table="flights", prefix="raw/flighty/")
